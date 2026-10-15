"""
rag_index.py — unified ingest/delete helpers for the Odysseus RAG store.

This module gives every mutation path (documents, chat attachments, YouTube
transcripts, personal-docs directories) ONE way to put content into / remove
content from the ChromaDB-backed VectorRAG. It resolves the shared VectorRAG
singleton and degrades to a no-op when RAG is unavailable so a missing ChromaDB
never blocks a CRUD operation.

Source conventions
------------------
  * documents  : source = "doc:<id>"
  * attachments: source = "upload:<id>"
  * youtube    : source = "yt:<video_id>"
Each source is re-indexed atomically (prior chunks deleted first) so edits and
re-transcriptions update the index instead of piling up stale vectors.
"""

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# A tiny in-process marker so a caller can pass a VectorRAG-instance-like object
# to the *sync* helpers while deferring resolution to the real singleton in the
# caller's runtime (routes receive `rag_manager` via setup_* wiring).
_rag_override = None
_lock = threading.Lock()


def set_rag_manager(rag_manager) -> None:
    """Pin the shared VectorRAG instance (used by app wiring and tests)."""
    global _rag_override
    with _lock:
        _rag_override = rag_manager


def _resolve_rag():
    """Return the shared VectorRAG, or None if unavailable."""
    global _rag_override
    with _lock:
        if _rag_override is not None:
            return _rag_override
    try:
        from src.rag_singleton import get_rag_manager
        return get_rag_manager()
    except Exception:
        return None


def _doc_source(doc_id: str) -> str:
    return f"doc:{doc_id}"


def _upload_source(upload_id: str) -> str:
    return f"upload:{upload_id}"


def _youtube_source(video_id: str) -> str:
    return f"yt:{video_id}"


def index_document(doc_id: str, text: str, *, owner: Optional[str] = None,
                   extra_meta: Optional[Dict[str, Any]] = None) -> bool:
    """Index a document's markdown content, replacing its prior chunks.

    Returns True if the content was (re)indexed via `index_source`; returns
    False when RAG is unavailable or text is empty (not an error).
    """
    rag = _resolve_rag()
    if rag is None or not getattr(rag, "healthy", False):
        return False
    try:
        n = rag.index_source(_doc_source(doc_id), text, owner=owner,
                             kind="document", extra_meta=extra_meta or {})
        return n > 0
    except Exception as e:
        logger.warning(f"index_document({doc_id}) failed: {e}")
        return False


def remove_document(doc_id: str) -> int:
    """Remove a document's chunks from the index. Returns removed count."""
    rag = _resolve_rag()
    if rag is None:
        return 0
    try:
        return rag.delete_by_source(_doc_source(doc_id))
    except Exception as e:
        logger.warning(f"remove_document({doc_id}) failed: {e}")
        return 0


def index_upload(upload_id: str, text: str, *, owner: Optional[str] = None,
                 extra_meta: Optional[Dict[str, Any]] = None) -> bool:
    """Index the extracted text of a chat attachment (PDF/Office/image OCR)."""
    rag = _resolve_rag()
    if rag is None or not getattr(rag, "healthy", False):
        return False
    try:
        n = rag.index_source(_upload_source(upload_id), text, owner=owner,
                             kind="attachment", extra_meta=extra_meta or {})
        return n > 0
    except Exception as e:
        logger.warning(f"index_upload({upload_id}) failed: {e}")
        return False


def remove_upload(upload_id: str) -> int:
    rag = _resolve_rag()
    if rag is None:
        return 0
    try:
        return rag.delete_by_source(_upload_source(upload_id))
    except Exception as e:
        logger.warning(f"remove_upload({upload_id}) failed: {e}")
        return 0


def index_youtube(video_id: str, text: str, *, owner: Optional[str] = None,
                  title: str = "", channel: str = "", url: str = "") -> bool:
    """Index a YouTube transcript (timestamped segments preferred), keyed by video id."""
    rag = _resolve_rag()
    if rag is None or not getattr(rag, "healthy", False):
        return False
    extra = {"video_id": video_id, "title": title, "channel": channel, "url": url}
    try:
        n = rag.index_source(_youtube_source(video_id), text, owner=owner,
                             kind="youtube", extra_meta=extra)
        return n > 0
    except Exception as e:
        logger.warning(f"index_youtube({video_id}) failed: {e}")
        return False


def remove_youtube(video_id: str) -> int:
    rag = _resolve_rag()
    if rag is None:
        return 0
    try:
        return rag.delete_by_source(_youtube_source(video_id))
    except Exception as e:
        logger.warning(f"remove_youtube({video_id}) failed: {e}")
        return 0


def index_directory(directory: str, *, owner: Optional[str] = None) -> Dict[str, Any]:
    """Index every supported file under a directory (used by backfill)."""
    rag = _resolve_rag()
    if rag is None:
        return {"success": False, "message": "RAG unavailable"}
    try:
        return rag.index_personal_documents(directory, owner=owner)
    except Exception as e:
        logger.warning(f"index_directory({directory}) failed: {e}")
        return {"success": False, "message": str(e)}