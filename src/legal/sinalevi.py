# src/legal/sinalevi.py
"""SINALEVI (Costa Rican legislation) adapter — Cluster L / L1.

Query-first: POST-simulates the Búsqueda Avanzada and parses the result table
with BeautifulSoup. When the page structure changes (the exact failure mode
community scrapers rot on), it falls back to a `site:sinalevi.go.cr` web
search so targeted queries keep working. Politeness: ≥2 s between requests,
browser UA, no mass scraping by default (bulk is opt-in via corpus.py).
"""

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://sinalevi.go.cr/BusquedaAvanzada/BusquedaLibre"
BASE = "https://sinalevi.go.cr"
MIN_INTERVAL_S = 2.0
USER_AGENT = "Mozilla/5.0 (compatible; OdysseusLegal/1.0; research bot)"

_last_request = 0.0


@dataclass
class LawHit:
    title: str
    number: str
    url: str
    pdf_url: Optional[str] = None
    date: Optional[str] = None
    theme: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


async def _polite() -> None:
    global _last_request
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = time.monotonic()


def _parse_results(html: str) -> List[LawHit]:
    """Parse the Búsqueda Avanzada result table into LawHits. Tolerant: any
    anchor with text is a candidate; pdf links are captured separately."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    hits: List[LawHit] = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ", strip=True) or "").strip()
        href = a["href"]
        if not text or len(text) < 4:
            continue
        pdf = None
        # Result rows often pair a title link with a nearby PDF icon/link.
        row = a.find_parent("tr") or a.find_parent("li") or a.parent
        if row is not None:
            pdf_a = row.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
            if pdf_a and pdf_a.get("href"):
                pdf = pdf_a["href"] if pdf_a["href"].startswith("http") else BASE.rstrip("/") + "/" + pdf_a["href"].lstrip("/")
        url = href if href.startswith("http") else BASE.rstrip("/") + "/" + href.lstrip("/")
        # Unique key: prefer an explicit law number pattern like N°12345 / Ley 1234.
        number = ""
        m = re.search(r"(?:N[°º]|Ley|Decreto|No\.?)\s*#?\s*([0-9]{2,6}(?:-[A-Z0-9]+)?)", text, re.I)
        if m:
            number = m.group(0).strip()
        hits.append(LawHit(title=text[:200], number=number, url=url, pdf_url=pdf))
    return hits


async def _fallback_search(query: str, client: httpx.AsyncClient) -> List[LawHit]:
    """Site-restricted web search fallback (form rot). Best-effort."""
    if not query or not query.strip():
        return []
    try:
        from src.search.universal import comprehensive_web_search
        results = await comprehensive_web_search(f"site:sinalevi.go.cr {query.strip()}", limit=8)
        out = []
        for r in results or []:
            url = r.get("url") if isinstance(r, dict) else None
            title = r.get("title") if isinstance(r, dict) else str(r)
            if url and "sinalevi" in str(url).lower():
                out.append(LawHit(title=str(title)[:200], number="", url=str(url)))
        return out
    except Exception as e:
        logger.debug("[legal/sinalevi] fallback search failed: %s", e)
        return []


async def search_sinalevi(*, term: Optional[str] = None, theme: Optional[str] = None,
                          number: Optional[str] = None, timeout: float = 30.0) -> List[LawHit]:
    """Targeted SINALEVI query. Returns LawHits (possibly empty)."""
    query = (term or theme or number or "").strip()
    await _polite()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
        ) as client:
            payload = {}
            if theme:
                payload["tema"] = theme
            if term:
                payload["texto"] = term
            if number:
                payload["numero"] = number
            r = await client.post(SEARCH_URL, data=payload or {"texto": query})
            r.raise_for_status()
            hits = _parse_results(r.text)
            if hits:
                return hits
            # Form changed / different markup → site-restricted search fallback.
            return await _fallback_search(query, client)
    except httpx.HTTPError as e:
        logger.warning("[legal/sinalevi] request failed: %s", e)
        return []
