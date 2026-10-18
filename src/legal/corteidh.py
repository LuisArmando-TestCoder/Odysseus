# src/legal/corteidh.py
"""Corte IDH (Inter-American Court of Human Rights) adapter — Cluster L / L2.

Plain link/metadata extraction: the court publishes fichas técnicas and
direct PDF links, so no headless browser is justified. Targeted queries only;
bulk is opt-in via corpus.py.
"""

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

CATALOG_URL = "https://corteidh.or.cr/instrumentos.cfm"
JURISPRUDENCE_URL = "https://jurisprudencia.corteidh.or.cr"
BASE = "https://www.corteidh.or.cr"
MIN_INTERVAL_S = 2.0
USER_AGENT = "Mozilla/5.0 (compatible; OdysseusLegal/1.0; research bot)"

_last_request = 0.0


@dataclass
class Instrument:
    id: str
    name: str
    kind: str
    url: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CaseHit:
    case_id: str
    title: str
    country: Optional[str] = None
    date: Optional[str] = None
    pdf_urls: List[str] = None
    summary: Optional[str] = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["pdf_urls"] = list(self.pdf_urls or [])
        return d


async def _polite() -> None:
    global _last_request
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = time.monotonic()


def _abs(href: str, base: str = BASE) -> str:
    href = (href or "").strip()
    if href.startswith("http"):
        return href
    return base.rstrip("/") + "/" + href.lstrip("/")


def _anchors(html: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        out.append(((a.get_text(" ", strip=True) or "").strip(), a["href"]))
    return out


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    await _polite()
    r = await client.get(url, **kw)
    r.raise_for_status()
    return r


async def corteidh_catalog(timeout: float = 30.0) -> List[Instrument]:
    """The full instrument catalog (verified navigable without scraping)."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
        ) as client:
            r = await _get(client, CATALOG_URL)
            instruments: List[Instrument] = []
            seen = set()
            for text, href in _anchors(r.text):
                if not text or len(text) < 3:
                    continue
                url = _abs(href)
                if url in seen:
                    continue
                seen.add(url)
                instruments.append(Instrument(
                    id=url.rsplit("=", 1)[-1][:40] or str(len(instruments)),
                    name=text[:200], kind="", url=url,
                ))
            return instruments
    except httpx.HTTPError as e:
        logger.warning("[legal/corteidh] catalog failed: %s", e)
        return []


def _parse_cases(html: str, base: str = JURISPRUDENCE_URL) -> List[CaseHit]:
    hits: List[CaseHit] = []
    for text, href in _anchors(html):
        if not text or len(text) < 4:
            continue
        pdfs = [href] if href.lower().endswith(".pdf") else []
        m = re.search(r"(?:Serie C[^\d]*No\.?\s*|Caso\s+)?([0-9]{1,6})", text)
        hits.append(CaseHit(
            case_id=m.group(1) if m else "",
            title=text[:200],
            pdf_urls=pdfs,
        ))
    return hits


async def corteidh_search(*, term: str, timeout: float = 30.0) -> List[CaseHit]:
    """Search the jurisprudence portal for cases matching `term`."""
    if not (term or "").strip():
        return []
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
        ) as client:
            r = await _get(client, JURISPRUDENCE_URL, params={"q": term.strip()})
            return _parse_cases(r.text)
    except httpx.HTTPError as e:
        logger.warning("[legal/corteidh] search failed: %s", e)
        return []


async def corteidh_case(case_id: str, timeout: float = 30.0) -> Optional[CaseHit]:
    """Fetch one case's metadata + direct PDF links."""
    if not (case_id or "").strip():
        return None
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
        ) as client:
            r = await _get(client, f"{JURISPRUDENCE_URL}/", params={"id": case_id.strip()})
            pdfs = [href for _, href in _anchors(r.text) if href.lower().endswith(".pdf")]
            title = ""
            for text, href in _anchors(r.text):
                if len(text) > len(title):
                    title = text
            return CaseHit(case_id=case_id.strip(), title=title[:200], pdf_urls=pdfs[:10])
    except httpx.HTTPError as e:
        logger.warning("[legal/corteidh] case fetch failed: %s", e)
        return None
