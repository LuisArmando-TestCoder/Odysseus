"""
rag_backfill.py — one-time startup re-index of existing documents and cached
YouTube transcripts into the vector store.

Because documents imported/created before the live-index hooks existed have no
chunks, and because the one-time startup directory scan indexed them
ownerless, we re-index everything with correct owner metadata here so the chat
RAG can actually retrieve them. Idempotent: `index_source` replaces any prior
chunks for the same logical source.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _resolve_owner() -> Optional[str]:
    """Determine the primary owner for ownerless legacy data."""
    try:
        import json
        auth_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "auth.json",
        )
        if os.path.exists(auth_path):
            with open(auth_path, encoding="utf-8") as f:
                users = json.load(f).get("users", {})
            for uname, udata in users.items():
                if udata.get("is_admin"):
                    return uname
            if users:
                return next(iter(users))
    except Exception:
        pass
    return None


def backfill_documents(rag_manager) -> int:
    """Index every active document row with its stored owner. Returns count."""
    if rag_manager is None or not getattr(rag_manager, "healthy", False):
        return 0
    from core.database import SessionLocal, Document
    from src import rag_index

    indexed = 0
    db = SessionLocal()
    try:
        rows = db.query(Document).filter(Document.is_active == True).all()
        fallback_owner = _resolve_owner()
        for doc in rows:
            content = getattr(doc, "current_content", "") or ""
            if not content.strip():
                continue
            owner = getattr(doc, "owner", None) or fallback_owner
            if rag_index.index_document(
                doc.id, content, owner=owner,
                extra_meta={
                    "title": getattr(doc, "title", "") or "",
                    "language": getattr(doc, "language", "") or "",
                },
            ):
                indexed += 1
    except Exception as e:
        logger.warning(f"backfill_documents failed: {e}")
    finally:
        db.close()
    return indexed


def backfill_youtube(rag_manager) -> int:
    """Index every cached YouTube transcript under data/youtube/. Returns count."""
    if rag_manager is None or not getattr(rag_manager, "healthy", False):
        return 0
    import json
    from src.youtube_handler import _transcript_cache_dir, index_transcript_to_rag

    indexed = 0
    d = _transcript_cache_dir()
    try:
        for fname in os.listdir(d):
            if not fname.endswith(".json"):
                continue
            video_id = fname[:-5]
            try:
                with open(os.path.join(d, fname), encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            owner = data.get("owner")
            if index_transcript_to_rag(
                video_id, data, owner=owner,
                title=data.get("title", ""), channel=data.get("channel", ""),
                url=data.get("url", ""),
            ):
                indexed += 1
    except Exception as e:
        logger.warning(f"backfill_youtube failed: {e}")
    return indexed


def run_backfill(rag_manager) -> None:
    """Run the full backfill. Safe to call at startup (no-ops when RAG is down)."""
    try:
        d = backfill_documents(rag_manager)
        logger.info(f"RAG backfill: indexed {d} document(s)")
    except Exception as e:
        logger.warning(f"document backfill errored: {e}")
    try:
        y = backfill_youtube(rag_manager)
        logger.info(f"RAG backfill: indexed {y} youtube transcript(s)")
    except Exception as e:
        logger.warning(f"youtube backfill errored: {e}")