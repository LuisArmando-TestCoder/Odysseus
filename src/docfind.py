"""src/docfind.py — fetch a primary document and grep it locally (DSH technique).

Turns DSH's "primary source + unique identifier + grep" strategy into an agent
tool. Instead of web_search returning homonym noise, fetch the authoritative
document (government PDF, site REST API, sitemap) and grep it with an exact
identifier (cédula, case number, finca, slug) — deterministic, cannot be
bot-blocked, cannot return a homonym.

Tool JSON:
    {"url": "...", "terms": ["3-101-229291", "LEDEZMA"], "context": 2, "json": false}
    {"file": "/abs/path.pdf", "terms": ["602600212"]}   # already-downloaded file

Returns {"output": <grep results>, "chars_extracted": N, "exit_code": 0|1|2}.
exit_code: 0 = hits, 1 = no hits, 2 = error.
"""
import io
import json
import re
from typing import Any, Dict, List, Optional

import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)


def _fetch(url: str, timeout: int = 30) -> bytes:
    """Prefer curl_cffi (Chrome TLS impersonation — defeats plain-httpx 403s
    like Wikipedia); fall back to httpx."""
    try:
        from curl_cffi import requests as creq
        r = creq.get(url, headers={"User-Agent": _UA}, impersonate="chrome", timeout=timeout)
        return r.content
    except Exception:
        pass
    r = httpx.get(url, headers={"User-Agent": _UA}, follow_redirects=True, timeout=timeout)
    r.raise_for_status()
    return r.content


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in r.pages)


def _html_text(data: bytes) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()
    return soup.get_text("\n")


def _json_text(data: bytes) -> str:
    try:
        return json.dumps(json.loads(data.decode("utf-8", "replace")), ensure_ascii=False, indent=1)
    except Exception:
        return data.decode("utf-8", "replace")


def _extract(data: bytes, as_json: bool) -> str:
    if not data:
        return ""
    if data[:5].lower() == b"%pdf-":
        return _pdf_text(data)
    stripped = data.lstrip()
    if as_json or stripped[:1] in (b"{", b"["):
        return _json_text(data)
    txt = data.decode("utf-8", "replace")
    if re.search(r"<[a-z][\s\S]*>", txt[:4000], re.I):
        return _html_text(data)
    return txt


def _grep(text: str, terms: List[str], context: int) -> List[str]:
    lines = text.splitlines()
    out: List[str] = []
    shown = set()
    for i, ln in enumerate(lines):
        low = ln.lower()
        if not any(t.lower() in low for t in terms):
            continue
        for j in range(max(0, i - context), min(len(lines), i + context + 1)):
            if j in shown:
                continue
            shown.add(j)
            out.append(f"{j + 1}: {lines[j]}")
    return out


async def do_docfind(content: str, owner: Optional[str] = None,
                     session_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch a primary document and grep it for exact identifiers (DSH technique)."""
    try:
        parsed = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    url = parsed.get("url")
    file_path = parsed.get("file")
    terms = parsed.get("terms") or parsed.get("term")
    if isinstance(terms, str):
        terms = [terms]
    terms = [str(t) for t in (terms or []) if str(t).strip()]
    context = int(parsed.get("context", 2) or 2)
    as_json = bool(parsed.get("json", False))

    if not terms:
        return {"error": "docfind requires `terms` (exact identifiers to grep)", "exit_code": 2}
    if not url and not file_path:
        return {"error": "docfind requires `url` (fetch) or `file` (local path)", "exit_code": 2}

    try:
        if url:
            data = _fetch(url)
        else:
            with open(file_path, "rb") as f:
                data = f.read()
    except Exception as e:
        return {"error": f"docfind fetch failed: {e}", "exit_code": 2}

    text = _extract(data, as_json)
    if not text:
        return {"error": "no extractable text (empty or unsupported format)", "exit_code": 2}

    hits = _grep(text, terms, context)
    if not hits:
        return {"output": f"NO MATCHES for: {', '.join(terms)}", "exit_code": 1}
    summary = f"{len(hits)} context line(s) for: {', '.join(terms)}\n" + "\n".join(hits[:200])
    return {"output": summary, "chars_extracted": len(text), "exit_code": 0}
