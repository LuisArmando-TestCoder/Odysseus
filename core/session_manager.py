# core/session_manager.py
"""
Session management — all session business logic and DB operations.

This is the single place that handles:
- Loading/saving sessions to database
- Adding messages to sessions
- Session lifecycle (create, archive, delete)
"""

import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List, Any

from .database import Session as DbSession, ChatMessage as DbChatMessage, Document as DbDocument, SessionLocal
from .models import Session, ChatMessage

logger = logging.getLogger(__name__)


def _message_timestamp_iso(value: Optional[datetime]) -> Optional[str]:
    """Return a stable ISO timestamp for chat message metadata."""
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _parse_msg_content(raw):
    """Parse message content from DB — deserialises JSON arrays back to lists
    (multimodal content with image/audio attachments)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.startswith('[{') and '"type"' in raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(p, dict) for p in parsed):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return raw


class SessionManager:
    """
    Manages chat sessions with database persistence.

    Usage:
        manager = SessionManager()
        session = manager.create_session(id, name, url, model)
        manager.add_message(session.id, ChatMessage("user", "hello"))
        session = manager.get_session(session_id)
    """

    def __init__(self, sessions_file: str = None):
        # sessions_file kept for backward compat, not used
        self.sessions: Dict[str, Session] = {}
        self.load_sessions()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_sessions(self):
        """Load recent session METADATA from the database — messages are
        hydrated on demand by `get_session`. Previously this walked every
        message of every session into RAM at boot, which on a long-running
        personal-server box could be tens of thousands of rows held forever
        in `self.sessions`.
        """
        db = SessionLocal()
        try:
            db_sessions = db.query(DbSession).filter(
                DbSession.archived == False,
                DbSession.message_count > 0,
            ).order_by(DbSession.last_accessed.desc()).limit(100).all()

            loaded_count = 0
            for db_session in db_sessions:
                try:
                    session = self._db_to_session_meta(db_session)
                    if session is not None:
                        self.sessions[db_session.id] = session
                        loaded_count += 1
                except Exception as e:
                    logger.error(f"Error loading session {db_session.id}: {e}")
                    continue

            logger.info(f"Loaded {loaded_count} session(s) (metadata only)")

        except Exception as e:
            logger.error(f"Error loading sessions: {e}")
            self.sessions = {}
        finally:
            db.close()

    def _db_to_session_meta(self, db_session: DbSession) -> Optional[Session]:
        """Build a Session with empty history. `get_session` will hydrate
        messages from the DB on first read."""
        headers = db_session.headers
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except json.JSONDecodeError:
                headers = {}
        session = Session(
            id=db_session.id,
            name=db_session.name,
            endpoint_url=db_session.endpoint_url,
            model=db_session.model,
            rag=db_session.rag,
            archived=db_session.archived,
            headers=headers,
            history=[],
            owner=getattr(db_session, "owner", None),
            is_important=getattr(db_session, "is_important", False) or False,
            active_branch=getattr(db_session, "active_branch", None) or "root",
        )
        session.message_count = getattr(db_session, "message_count", 0) or 0
        return session

    def _db_to_session(self, db_session: DbSession, db) -> Optional[Session]:
        """Convert a database session to a Session object."""
        active_branch = getattr(db_session, "active_branch", None) or "root"
        history = []

        db_messages = db.query(DbChatMessage).filter(
            DbChatMessage.session_id == db_session.id
        ).order_by(DbChatMessage.timestamp).all()

        for db_msg in db_messages:
            # Message-version tree: only hydrate the currently-active branch.
            branch = getattr(db_msg, "branch_id", None) or "root"
            if branch != active_branch:
                continue
            meta = json.loads(db_msg.meta_data) if db_msg.meta_data else {}
            if meta is None: meta = {}
            meta['_db_id'] = db_msg.id
            meta.setdefault('timestamp', _message_timestamp_iso(db_msg.timestamp))
            _content = _parse_msg_content(db_msg.content)
            # Self-heal: skip a row that exactly duplicates the previous
            # hydrated row (same role + non-empty content). Consecutive
            # duplicates are the signature of the double-submit / fork-copy
            # bugs; dropping them at read time keeps the feed clean without
            # mutating the DB.
            if history:
                _prev = history[-1]
                if (_prev.role == db_msg.role
                        and _prev.content == _content
                        and _content):
                    continue
            history.append(ChatMessage(
                role=db_msg.role,
                content=_content,
                metadata=meta,
            ))

        if not history:
            return None

        # Parse headers
        headers = db_session.headers
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except json.JSONDecodeError:
                headers = {}

        session = Session(
            id=db_session.id,
            name=db_session.name,
            endpoint_url=db_session.endpoint_url,
            model=db_session.model,
            rag=db_session.rag,
            archived=db_session.archived,
            headers=headers,
            history=history,
            owner=getattr(db_session, 'owner', None),
            is_important=getattr(db_session, 'is_important', False) or False,
            active_branch=active_branch,
        )

        session.message_count = getattr(db_session, 'message_count', len(history))
        return session

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def add_message(self, session_id: str, message: ChatMessage):
        """
        Add a message to a session and persist to database.

        Args:
            session_id: Session ID
            message: ChatMessage to add
        """
        session = self.get_session(session_id)
        session.history.append(message)
        session.message_count = len(session.history)

        self._persist_message(session_id, message)

    def _persist_message(self, session_id: str, message: ChatMessage):
        """Persist a single message to the database."""
        db = SessionLocal()
        try:
            msg_id = str(uuid.uuid4())
            msg_time = datetime.utcnow()
            _sess = self.sessions.get(session_id)
            _branch = getattr(_sess, "active_branch", "root") if _sess else "root"
            if message.metadata is None:
                message.metadata = {}
            message.metadata.setdefault('timestamp', _message_timestamp_iso(msg_time))
            # Multimodal content (image/audio attachments) is a list — serialize
            # to JSON so the Text column can store it.  On reload, _db_to_session
            # detects the JSON-array prefix and parses it back.
            _content = message.content
            if isinstance(_content, list):
                _content = json.dumps(_content)
            db_message = DbChatMessage(
                id=msg_id,
                session_id=session_id,
                role=message.role,
                content=_content,
                meta_data=json.dumps(message.metadata) if message.metadata else None,
                timestamp=msg_time,
                branch_id=_branch,
            )
            db.add(db_message)

            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.message_count = len(self.sessions.get(session_id, {}).history) if session_id in self.sessions else 0
                _now = datetime.now(timezone.utc)
                db_session.last_accessed = _now
                # Clean "last conversation" timestamp — only bumped here on a
                # real message persist, so it powers an accurate "Last active"
                # sort that ignores renames / model swaps / mere opens.
                db_session.last_message_at = _now

            db.commit()

            # Store DB ID on the in-memory message for edit/delete by ID
            message.metadata['_db_id'] = msg_id

            logger.debug(f"Persisted message to session {session_id}")

        except Exception as e:
            logger.error(f"Error persisting message: {e}")
            db.rollback()
        finally:
            db.close()

    def truncate_messages(self, session_id: str, keep_count: int) -> bool:
        """Truncate session history, keeping only the first `keep_count` messages."""
        session = self.get_session(session_id)

        if keep_count < 0:
            return False

        db = SessionLocal()
        try:
            db_messages = db.query(DbChatMessage).filter(
                DbChatMessage.session_id == session_id
            ).order_by(DbChatMessage.timestamp).all()

            # Only delete from the active branch — other branches are preserved.
            branch = getattr(session, "active_branch", "root") or "root"
            active_msgs = [m for m in db_messages if (getattr(m, "branch_id", None) or "root") == branch]

            deleted = 0
            for msg in active_msgs[keep_count:]:
                db.delete(msg)
                deleted += 1

            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.message_count = keep_count
                db_session.updated_at = datetime.now(timezone.utc)

            db.commit()

            # Update in-memory
            session.history = session.history[:keep_count]

            logger.info(f"Truncated session {session_id} to {keep_count} messages")
            return True

        except Exception as e:
            logger.error(f"Error truncating session: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def fork_branch(self, session_id: str, keep_count: int = 0) -> Optional[str]:
        """Fork the conversation: copy the first `keep_count` messages into a
        fresh branch and make it active. The old branch (with its full tail) is
        preserved so the user can navigate back to it later."""
        session = self.get_session(session_id)
        if not session:
            return None
        prefix = list(session.history[:keep_count])
        # Dedupe consecutive duplicate rows before copying so a version-tree
        # fork never re-multiplies duplicates that already exist in memory.
        _deduped = []
        for _m in prefix:
            if _deduped:
                _p = _deduped[-1]
                if (_p.role == _m.role and _p.content == _m.content
                        and _m.content):
                    continue
            _deduped.append(_m)
        prefix = _deduped
        new_branch = str(uuid.uuid4())
        session.active_branch = new_branch
        session.history = prefix
        session.message_count = len(prefix)

        db = SessionLocal()
        try:
            now = datetime.utcnow()
            for i, msg in enumerate(prefix):
                new_id = str(uuid.uuid4())
                _content = msg.content
                if isinstance(_content, list):
                    _content = json.dumps(_content)
                # Copy metadata but drop the stale DB id (new rows get new ids).
                _meta = {k: v for k, v in (msg.metadata or {}).items() if k != '_db_id'}
                db.add(DbChatMessage(
                    id=new_id,
                    session_id=session_id,
                    role=msg.role,
                    content=_content,
                    meta_data=json.dumps(_meta) if _meta else None,
                    timestamp=now + timedelta(microseconds=i),
                    branch_id=new_branch,
                ))
                # Keep the in-memory message's DB id in sync with its new row so
                # later edits/deletes target the NEW branch, not the old one.
                if msg.metadata is None:
                    msg.metadata = {}
                msg.metadata['_db_id'] = new_id
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.active_branch = new_branch
                db_session.message_count = keep_count
            db.commit()
        except Exception as e:
            logger.error(f"fork_branch {session_id}: {e}")
            db.rollback()
        finally:
            db.close()
        return new_branch

    def list_branches(self, session_id: str) -> List[Dict[str, Any]]:
        """Return the session's message branches (id, count, latest ts)."""
        db = SessionLocal()
        try:
            msgs = db.query(DbChatMessage).filter(
                DbChatMessage.session_id == session_id
            ).order_by(DbChatMessage.timestamp).all()
        finally:
            db.close()
        counts: Dict[str, int] = {}
        latest: Dict[str, Any] = {}
        for m in msgs:
            bid = getattr(m, "branch_id", None) or "root"
            counts[bid] = counts.get(bid, 0) + 1
            latest[bid] = m.timestamp
        branches = [
            {
                "branch_id": bid,
                "message_count": counts[bid],
                "latest": (latest[bid].isoformat() + "Z") if latest.get(bid) else None,
            }
            for bid in counts
        ]
        # Root first, then most-recently-active.
        branches.sort(key=lambda b: (b["branch_id"] == "root", b["latest"] or ""), reverse=True)
        return branches

    def activate_branch(self, session_id: str, branch_id: str) -> bool:
        """Switch the active branch and reload its message history."""
        branch_id = branch_id or "root"
        session = self.get_session(session_id)
        if not session:
            return False
        session.active_branch = branch_id
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.active_branch = branch_id
                db.commit()
        except Exception as e:
            logger.error(f"activate_branch {session_id}: {e}")
            db.rollback()
            return False
        finally:
            db.close()
        self._load_session_from_db(session_id)
        return True

    def replace_messages(self, session_id: str, messages: list) -> bool:
        """Replace a session's persisted and in-memory history atomically.

        Branch-aware: only the ACTIVE branch's messages are replaced; other
        branches are preserved (message-version tree)."""
        session = self.get_session(session_id)
        branch = getattr(session, "active_branch", "root") or "root"
        db = SessionLocal()
        try:
            rows = db.query(DbChatMessage).filter(DbChatMessage.session_id == session_id).all()
            for r in rows:
                if (getattr(r, "branch_id", None) or "root") == branch:
                    db.delete(r)
            now = datetime.now(timezone.utc)
            for i, message in enumerate(messages):
                msg_id = str(uuid.uuid4())
                db_message = DbChatMessage(
                    id=msg_id,
                    session_id=session_id,
                    role=message.role,
                    content=message.content,
                    meta_data=json.dumps(message.metadata) if message.metadata else None,
                    timestamp=now + timedelta(microseconds=i),
                    branch_id=branch,
                )
                db.add(db_message)
                if message.metadata is None:
                    message.metadata = {}
                message.metadata["_db_id"] = msg_id

            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.message_count = len(messages)
                db_session.updated_at = now
                db_session.last_accessed = now
                db_session.last_message_at = now

            db.commit()
            session.history = list(messages)
            session.message_count = len(messages)
            logger.info("Replaced session %s history with %d messages", session_id, len(messages))
            return True
        except Exception as e:
            logger.error("Error replacing session history: %s", e)
            db.rollback()
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID, loading from DB if needed.

        Sessions seeded by `load_sessions` start with empty history. The
        first read here hydrates them with the message rows.
        """
        if session_id not in self.sessions:
            self._load_session_from_db(session_id)
        else:
            cached = self.sessions[session_id]
            # Lazy hydrate: metadata-only entries get their messages on first read.
            if not cached.history and getattr(cached, "message_count", 0) > 0:
                self._load_session_from_db(session_id)

        # Keep model/endpoint metadata fresh. Endpoint deletion can clear the
        # DB row while a session object is still cached in RAM.
        self.sync_session_metadata(session_id)

        # Update last_accessed
        self._touch_session(session_id)

        return self.sessions[session_id]

    def sync_session_metadata(self, session_id: str) -> bool:
        """Refresh non-message session fields from the DB into the cached object."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                return False
            headers = db_session.headers
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except json.JSONDecodeError:
                    headers = {}
            session.name = db_session.name
            session.endpoint_url = db_session.endpoint_url or ""
            session.model = db_session.model or ""
            session.headers = headers or {}
            session.rag = db_session.rag
            session.archived = db_session.archived
            session.owner = getattr(db_session, "owner", None)
            session.is_important = getattr(db_session, "is_important", False) or False
            session.message_count = getattr(db_session, "message_count", session.message_count) or 0
            return True
        except Exception as e:
            logger.error(f"Error syncing session metadata {session_id}: {e}")
            return False
        finally:
            db.close()

    def _load_session_from_db(self, session_id: str):
        """Hydrate a single session (with messages) from the database."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                raise KeyError(f"Session {session_id} not found")

            session = self._db_to_session(db_session, db)
            if session:
                self.sessions[session_id] = session
            else:
                # No messages — fall back to metadata-only entry so callers
                # don't crash on KeyError for empty sessions.
                meta = self._db_to_session_meta(db_session)
                if meta is None:
                    raise KeyError(f"Session {session_id} could not be loaded")
                self.sessions[session_id] = meta

        except KeyError:
            raise
        except Exception as e:
            logger.error(f"Error loading session {session_id}: {e}")
            raise
        finally:
            db.close()

    def _touch_session(self, session_id: str):
        """Update last_accessed timestamp."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.last_accessed = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            logger.error(f"Error updating last_accessed: {e}")
            db.rollback()
        finally:
            db.close()

    def create_session(
        self,
        session_id: str,
        name: str,
        endpoint_url: str,
        model: str,
        rag: bool = False,
        owner: str = None
    ) -> Session:
        """Create a new session and save to database."""
        db = SessionLocal()
        try:
            db_session = DbSession(
                id=session_id,
                name=name,
                endpoint_url=endpoint_url,
                model=model,
                rag=rag,
                headers={},
                owner=owner,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(db_session)
            db.commit()

            session = Session(
                id=session_id,
                name=name,
                endpoint_url=endpoint_url,
                model=model,
                rag=rag,
                headers={},
                owner=owner,
            )

            self.sessions[session_id] = session
            return session

        except Exception as e:
            db.rollback()
            logger.error(f"Error creating session: {e}")
            raise
        finally:
            db.close()

    def delete_session(self, session_id: str) -> bool:
        """Permanently delete a session and all its messages."""
        db = SessionLocal()
        try:
            # Detach documents so they survive as orphans in the library
            db.query(DbDocument).filter(DbDocument.session_id == session_id).update(
                {DbDocument.session_id: None}, synchronize_session=False
            )

            # Delete messages
            db.query(DbChatMessage).filter(DbChatMessage.session_id == session_id).delete()

            # Delete session
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db.delete(db_session)

            # Drop the in-memory copy even when there is no DB row. A "ghost"
            # session lives only here (never persisted, or its row was removed
            # out-of-band); without this it can never be cleared and keeps
            # 404ing on every operation (issue #1044).
            removed_in_memory = self.sessions.pop(session_id, None) is not None

            if db_session or removed_in_memory:
                # Commit the document-detach / message-delete above (a no-op when
                # the ghost had no rows) together with the session delete.
                db.commit()
                logger.info(f"Deleted session {session_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Session updates
    # ------------------------------------------------------------------

    def update_session_name(self, session_id: str, name: str):
        """Update session name."""
        if session_id not in self.sessions:
            return

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.name = name
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()
                self.sessions[session_id].name = name
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating session name: {e}")
            raise
        finally:
            db.close()

    def archive_session(self, session_id: str):
        """Archive a session."""
        if session_id not in self.sessions:
            return

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.archived = True
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()
                self.sessions[session_id].archived = True
        except Exception as e:
            db.rollback()
            logger.error(f"Error archiving session: {e}")
            raise
        finally:
            db.close()

    def mark_important(self, session_id: str, important: bool = True):
        """Mark session as important."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.is_important = important
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()

                if session_id in self.sessions:
                    self.sessions[session_id].is_important = important
            else:
                raise KeyError(f"Session {session_id} not found")
        except Exception as e:
            db.rollback()
            logger.error(f"Error marking session important: {e}")
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_sessions_for_user(self, username: Optional[str] = None) -> Dict[str, Session]:
        """Return sessions for a specific user (or all if username is None)."""
        if username is None:
            return self.sessions
        return {
            sid: s for sid, s in self.sessions.items()
            if s.owner == username
        }

    def save_sessions(self):
        """No-op for DB compatibility."""

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_empty_sessions(self, auto_archive_days: int = 30) -> dict:
        """Clean up empty and old sessions."""
        db = SessionLocal()
        stats = {'deleted_empty': 0, 'archived_old': 0, 'total_checked': 0}

        try:
            all_sessions = db.query(DbSession).all()
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=auto_archive_days)

            for db_session in all_sessions:
                stats['total_checked'] += 1

                # Delete empty sessions
                if db_session.message_count == 0:
                    if db_session.id in self.sessions:
                        del self.sessions[db_session.id]
                    db.delete(db_session)
                    stats['deleted_empty'] += 1

                # Archive old sessions
                elif (not db_session.archived and
                      db_session.last_accessed and
                      db_session.last_accessed < cutoff_date and
                      db_session.message_count > 0 and
                      not getattr(db_session, 'is_important', False)):
                    db_session.archived = True
                    stats['archived_old'] += 1

            db.commit()
            logger.info(f"Cleanup: {stats['deleted_empty']} deleted, {stats['archived_old']} archived")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            db.rollback()
            raise
        finally:
            db.close()

        return stats
