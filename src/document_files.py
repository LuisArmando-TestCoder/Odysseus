"""
document_files.py — mirror Odysseus documents to real files on disk.

Documents are rows in the app database (backward-compatible, kept for the
editor UI / version history), but each one is ALSO written to a real file
under ``data/documents/`` so it has an absolute filesystem path and can be
referenced from other chats via read_file / RAG.

PDF/uploads keep pointing at their real upload file; every other document
(markdown/code/notes) is written to ``data/documents/<slug>.<ext>``.
"""

import logging
import os
import re

from src.constants import DATA_DIR

logger = logging.getLogger(__name__)

DOCUMENTS_DIR = os.path.join(DATA_DIR, "documents")


def _session_dir(session_id) -> str:
    """Carpeta por sesión: data/documents/<session_id>/."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "shared"))
    d = os.path.join(DOCUMENTS_DIR, safe)
    os.makedirs(d, exist_ok=True)
    return d

_EXT = {
    "markdown": "md", "text": "txt", "plain": "txt",
    "python": "py", "javascript": "js", "typescript": "ts",
    "html": "html", "css": "css", "json": "json", "yaml": "yml",
    "bash": "sh", "shell": "sh", "sql": "sql", "rust": "rs", "go": "go",
    "java": "java", "c": "c", "cpp": "cpp", "csharp": "cs",
    "xml": "xml", "svg": "svg", "toml": "toml", "ini": "ini",
    "ruby": "rb", "php": "php", "csv": "csv", "email": "eml",
    "pdf": "pdf", "kotlin": "kt", "swift": "swift",
}


def _slug(title: str) -> str:
    """Safe, human-readable filename stem from a document title."""
    s = re.sub(r"[^\w.\- ]+", " ", (title or "").strip(), flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s).strip(".-") or "Untitled"
    return s[:80]


def find_upload_id(doc) -> "str | None":
    """Return the upload_id if this document is backed by an uploaded file
    (PDF), else None. Mirrors pdf_form_doc.find_source_upload_id without a
    hard import at module load."""
    try:
        from src.pdf_form_doc import find_source_upload_id
        return find_source_upload_id(getattr(doc, "current_content", "") or "")
    except Exception:
        return None


def resolve_upload_abs_path(upload_id: str) -> "str | None":
    """Absolute path of an uploaded file by id, or None. Uploads live directly
    under ``data/uploads`` or in date-based subfolders (``YYYY/MM/DD``)."""
    from src.constants import UPLOAD_DIR
    direct = os.path.join(UPLOAD_DIR, upload_id)
    if os.path.exists(direct):
        return os.path.abspath(direct)
    for root, _dirs, files in os.walk(UPLOAD_DIR):
        if upload_id in files:
            return os.path.abspath(os.path.join(root, upload_id))
    return None


def doc_file_name(doc) -> str:
    """Filename (no dir) for a document's on-disk file. Stable across edits:
    once a doc has a ``file_path``, reuse its basename; otherwise derive a
    unique ``<slug>.<ext>`` (appending a short id on collision)."""
    existing = getattr(doc, "file_path", None)
    if existing:
        return os.path.basename(existing)

    title = getattr(doc, "title", None) or "Untitled"
    lang = (getattr(doc, "language", None) or "").lower()
    ext = _EXT.get(lang, "md")
    slug = _slug(title)
    # Avoid double extensions (e.g. title "browse.py" + language python).
    base = slug if slug.lower().endswith(f".{ext}") else f"{slug}.{ext}"

    if os.path.exists(os.path.join(_session_dir(getattr(doc, "session_id", None)), base)):
        did = (getattr(doc, "id", None) or "")[:8]
        stem = slug if slug.lower().endswith(f".{ext}") else slug
        base = f"{stem}-{did}.{ext}"
    return base


def document_path(doc) -> str:
    """Absolute path where a document's file lives (no write)."""
    uid = find_upload_id(doc)
    if uid:
        p = resolve_upload_abs_path(uid)
        if p:
            return p
    fp = getattr(doc, "file_path", None)
    if fp:
        return os.path.abspath(fp)
    # Not yet synced to a file (pre-migration) — point at the database.
    return os.path.abspath(os.path.join(DATA_DIR, "app.db"))


def write_document_file(doc) -> str:
    """Write a document's content to its on-disk file and return the absolute
    path. PDF-backed docs resolve to their existing upload file (no duplicate
    markdown written); everything else is written under DOCUMENTS_DIR."""
    uid = find_upload_id(doc)
    if uid:
        p = resolve_upload_abs_path(uid)
        if p:
            return p

    sdir = _session_dir(getattr(doc, "session_id", None))
    path = os.path.join(sdir, doc_file_name(doc))
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(getattr(doc, "current_content", "") or "")
    except OSError as e:
        logger.warning("document file write failed for %s: %s", getattr(doc, "id", "?"), e)
        raise
    return os.path.abspath(path)
