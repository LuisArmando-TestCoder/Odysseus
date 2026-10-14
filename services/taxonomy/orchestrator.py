"""Background orchestrator for the self-organizing chat taxonomy.

The *display* side already exists (routes + ``static/js/sessions.js``), but the
*population* side was never wired up: nothing calls Team A (place a chat) or
Team B (segment an over-threshold level) on a schedule, and ``segment_level``
only *returns* its groupings without applying them.

This module drives both agent teams on a periodic loop:

- **Team A (reactive)**: any chat without a breadcrumb is placed via
  ``place_chat`` (the LLM proposes a broad→specific tag path). Retroactively
  covers chats created before the taxonomy existed, and newly-created chats.
- **Team B (threshold)**: any folder level with more than
  ``TAXONOMY_THRESHOLD`` chats is handed to ``segment_level``, and the returned
  sub-categories are *applied* to each chat's breadcrumb (parent path + sub-tag),
  then the tree cache is reconciled.

All blocking LLM/DB work runs in worker threads via ``asyncio.to_thread`` so the
event loop never stalls. Every pass is best-effort and logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from .service import (
    TAXONOMY_THRESHOLD,
    get_session_breadcrumbs,
    parse_breadcrumb,
    place_chat,
    reconcile_tree,
    segment_level,
    serialize_breadcrumb,
)

logger = logging.getLogger(__name__)

# Seconds between background taxonomy passes.
PASS_INTERVAL_S = 120
# Delay before the first pass after startup (let the server settle).
FIRST_PASS_DELAY_S = 30


def _list_owners() -> List[str]:
    """Owners that have at least one active (non-archived) session."""
    from core.database import SessionLocal, Session as DbSession

    db = SessionLocal()
    try:
        rows = (
            db.query(DbSession.owner)
            .filter(DbSession.archived == False, DbSession.owner != None)  # noqa: E711
            .distinct()
            .all()
        )
        return [r[0] for r in rows if r[0]]
    finally:
        db.close()


def _active_sessions(owner: str) -> List[Dict[str, Optional[str]]]:
    """Return [{id, name, tags}] for an owner's active sessions, plus an
    excerpt of the first user message for untagged ones (single batched query)."""
    from core.database import SessionLocal, Session as DbSession, ChatMessage

    db = SessionLocal()
    try:
        rows = (
            db.query(DbSession.id, DbSession.name, DbSession.tags)
            .filter(DbSession.archived == False, DbSession.owner == owner)
            .all()
        )
        out: List[Dict[str, Optional[str]]] = [
            {"id": r[0], "name": r[1] or "", "tags": r[2]} for r in rows
        ]
        # Batch-fetch the first user message for sessions that need an excerpt
        # (untagged). One query instead of N.
        untagged_ids = [s["id"] for s in out if not parse_breadcrumb(s.get("tags"))]
        excerpt_map: Dict[str, str] = {}
        if untagged_ids:
            msgs = (
                db.query(ChatMessage.session_id, ChatMessage.content)
                .filter(
                    ChatMessage.session_id.in_(untagged_ids),
                    ChatMessage.role == "user",
                )
                .order_by(ChatMessage.session_id, ChatMessage.timestamp)
                .all()
            )
            for sid, content in msgs:
                if sid not in excerpt_map:
                    excerpt_map[sid] = (content or "")[:2000]
        for s in out:
            s["excerpt"] = excerpt_map.get(s["id"], "")
        return out
    finally:
        db.close()


def _apply_groups(owner: str, parent_path: List[str], groups: Dict[str, List[str]]) -> None:
    """Persist Team B's segmentation: set each chat's breadcrumb to
    ``parent_path + [sub_tag]``. This is the missing "apply" step — segment_level
    only returns its groupings."""
    from core.database import SessionLocal, Session as DbSession

    if not groups:
        return
    db = SessionLocal()
    try:
        changed = 0
        for sub, sids in groups.items():
            for sid in sids:
                row = db.query(DbSession).filter(
                    DbSession.id == sid, DbSession.owner == owner
                ).first()
                if not row:
                    continue
                row.tags = serialize_breadcrumb(list(parent_path) + [str(sub).strip()])
                changed += 1
        db.commit()
        if changed:
            logger.info("taxonomy: applied %d segmentation(s) for owner=%r", changed, owner)
    except Exception as e:
        db.rollback()
        logger.warning(f"taxonomy: apply_groups failed for owner={owner!r}: {e}")
    finally:
        db.close()


def _segment_tree(owner: str, node: Dict, path: List[str], title_map: Dict[str, str]) -> None:
    """Recursively segment over-threshold levels (Team B)."""
    direct = node.get("sessions") or []
    sessions = [{"id": sid, "title": title_map.get(sid, "")} for sid in direct
                if sid in title_map]
    if len(sessions) > TAXONOMY_THRESHOLD:
        try:
            groups = segment_level(owner, path, sessions)
        except Exception as e:
            logger.warning(f"taxonomy: segment_level failed at path={path!r}: {e}")
            groups = {}
        if groups:
            _apply_groups(owner, path, groups)
            # Don't recurse into the just-split level with stale children; the
            # next pass rebuilds the tree from the updated breadcrumbs.
            return
    for name, child in (node.get("children") or {}).items():
        _segment_tree(owner, child, path + [name], title_map)


def _pass_for_owner(owner: str) -> None:
    """One full pass for a single owner: Team A place, then Team B segment."""
    sessions = _active_sessions(owner)
    title_map = {s["id"]: s["name"] for s in sessions}

    # Team A — place chats that have no breadcrumb yet.
    placed = 0
    for s in sessions:
        if parse_breadcrumb(s.get("tags")):
            continue
        title = s["name"]
        excerpt = s.get("excerpt") or ""
        if not title and not excerpt:
            continue  # nothing to tag yet (empty placeholder chat)
        try:
            breadcrumb = place_chat(owner, s["id"], title, excerpt)
            if breadcrumb:
                placed += 1
        except Exception as e:
            logger.warning(f"taxonomy: place_chat failed for {s['id']}: {e}")
    if placed:
        logger.info("taxonomy: placed %d chat(s) for owner=%r", placed, owner)

    # Reload breadcrumbs + reconcile the tree, then segment over-threshold levels.
    breadcrumbs = get_session_breadcrumbs(owner)
    tree = reconcile_tree(owner, breadcrumbs)
    _segment_tree(owner, tree, [], title_map)

    # Final reconcile so any applied segmentation is reflected on disk.
    breadcrumbs = get_session_breadcrumbs(owner)
    reconcile_tree(owner, breadcrumbs)


async def run_taxonomy_pass() -> None:
    """One full background pass across every owner (best-effort)."""
    owners = await asyncio.to_thread(_list_owners)
    for owner in owners:
        try:
            await asyncio.to_thread(_pass_for_owner, owner)
        except Exception as e:
            logger.warning(f"taxonomy: background pass failed for owner={owner!r}: {e}")


async def start_taxonomy_loop() -> None:
    """Blocking loop that runs taxonomy passes forever. Start via
    ``asyncio.create_task(start_taxonomy_loop())``."""
    await asyncio.sleep(FIRST_PASS_DELAY_S)
    while True:
        try:
            await run_taxonomy_pass()
        except Exception as e:
            logger.debug(f"taxonomy: pass skipped: {e}")
        await asyncio.sleep(PASS_INTERVAL_S)