# src/legal/corpus.py
"""Legal corpus store — Cluster L / L3 + L5.

`data/legal/<source>/index.jsonl` (deduped by unique key: ley número / caso
id — the "unique identifier before name" rule) + `docs/` holding extracted
text (.txt) so retrieval is deterministic local grep (docfind-style), never a
live re-fetch. PDFs are fetched ON DEMAND; `bulk_inventory` is opt-in and
resumable (the index dedupe IS the checkpoint).
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOURCES = ("sinalevi", "corteidh")
MIN_INTERVAL_S = 2.0
_last_request = 0.0


def corpus_root(source: str) -> str:
    from src.constants import DATA_DIR
    safe = re.sub(r"[^a-z0-9_-]", "_", (source or "x").lower())
    root = os.path.join(DATA_DIR, "legal", safe)
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    return root


def _index_path(source: str) -> str:
    return os.path.join(corpus_root(source), "index.jsonl")


def index_rows(source: str) -> List[Dict[str, Any]]:
    p = _index_path(source)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _unique_key(hit: Dict[str, Any], source: str) -> str:
    if source == "sinalevi":
        return (hit.get("number") or "")[:80] or (hit.get("url") or "")[:200]
    return (hit.get("case_id") or "")[:80] or (hit.get("url") or "")[:200]


def record(hit: Dict[str, Any], source: str) -> bool:
    """Append a metadata row; False when the unique key already exists."""
    key = _unique_key(hit, source)
    if not key:
        return False
    for row in index_rows(source):
        if _unique_key(row, source) == key:
            return False
    row = dict(hit)
    row["recorded_at"] = time.time()
    with open(_index_path(source), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(key or "x"))[:80]


def save_document(source: str, key: str, title: str, text: str) -> str:
    """Persist extracted text for deterministic local grep. Returns the path.
    (Metadata rows come from the adapters/bulk via record() — documents are
    keyed by the same unique key so fetch_pdf can dedupe on disk.)"""
    root = corpus_root(source)
    path = os.path.join(root, "docs", f"{_safe_key(key)}.txt")
    header = f"# {title}\n# source: {source} · key: {key}\n# extracted_at: {time.time():.0f}\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + text)
    return path


def search_corpus(term: str, source: Optional[str] = None, context: int = 2) -> List[Dict[str, Any]]:
    """L5 — deterministic grep over stored corpus text (docfind-style)."""
    if not (term or "").strip():
        return []
    sources = [source] if source else list(SOURCES)
    hits: List[Dict[str, Any]] = []
    for src in sources:
        docs_dir = os.path.join(corpus_root(src), "docs")
        if not os.path.isdir(docs_dir):
            continue
        for name in sorted(os.listdir(docs_dir)):
            if not name.endswith(".txt"):
                continue
            path = os.path.join(docs_dir, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                for i, line in enumerate(lines):
                    if term.lower() in line.lower():
                        lo = max(0, i - context)
                        hi = min(len(lines), i + context + 1)
                        hits.append({
                            "source": src, "file": path, "line": i + 1,
                            "snippet": "".join(lines[lo:hi]).strip()[:800],
                        })
                        if len(hits) >= 20:
                            return hits
            except OSError:
                continue
    return hits


async def fetch_pdf(hit: Dict[str, Any], source: str, timeout: float = 60.0) -> Optional[str]:
    """Download a PDF on demand, extract its text (pypdf), and save a .txt
    sibling for corpus grep. Returns the .txt path or None on failure."""
    import httpx
    url = hit.get("pdf_url") or ""
    if not url:
        return None
    key = _unique_key(hit, source)
    txt_path = os.path.join(corpus_root(source), "docs", f"{_safe_key(key)}.txt")
    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 100:
        return txt_path  # already fetched
    global _last_request
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (compatible; OdysseusLegal/1.0)"},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.content
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        path = save_document(source, key, hit.get("title") or url, text)
        logger.info("[legal] fetched %s → %s (%d chars)", url, path, len(text))
        return path
    except Exception as e:
        logger.warning("[legal] fetch_pdf failed for %s: %s", url, e)
        return None


async def bulk_inventory(source: str, queries: List[str], *, adapter, limit_per_query: int = 10):
    """Opt-in mass inventory ("dímelo y lo monto"): run the adapter over each
    query and record new rows. Resumable — the index dedupe IS the checkpoint.
    `adapter` is an async fn(query: str) -> list[hit-dicts]."""
    total = 0
    for q in queries:
        try:
            hits = await adapter(q)
        except Exception as e:
            logger.warning("[legal] bulk query %r failed: %s", q, e)
            continue
        for hit in (hits or [])[:limit_per_query]:
            d = hit.as_dict() if hasattr(hit, "as_dict") else dict(hit)
            if record(d, source):
                total += 1
        await asyncio.sleep(0)
    return total
