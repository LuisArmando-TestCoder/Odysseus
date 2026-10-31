"""Webpage content fetching with caching, PDF extraction, and summarization helpers."""

import copy
import io
import ipaddress
import json
import os
import re
import logging
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .analytics import RateLimitError, error_logger
from .cache import (
    CONTENT_CACHE_DIR,
    content_cache_index,
    generate_cache_key,
    cleanup_cache,
)

logger = logging.getLogger(__name__)

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _is_private_address(addr: ipaddress._BaseAddress) -> bool:
    return any(addr in net for net in _PRIVATE_NETWORKS) or addr.is_private or addr.is_loopback


def _resolve_hostname_ips(hostname: str) -> List[ipaddress._BaseAddress]:
    ips = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
        if family in (socket.AF_INET, socket.AF_INET6):
            ips.append(ipaddress.ip_address(sockaddr[0]))
    return ips


def _public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host in ("localhost", "metadata.google.internal", "metadata"):
        return False
    try:
        return not _is_private_address(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        ips = _resolve_hostname_ips(host)
    except OSError:
        return False
    # Fail closed: a hostname that resolves to nothing is treated as
    # non-public (an empty all(...) would otherwise return True).
    return bool(ips) and all(not _is_private_address(ip) for ip in ips)


def _curl_cffi_fetch(url: str, *, headers: dict, timeout: int):
    """Fetch ``url`` via curl_cffi with a real browser TLS fingerprint.

    Some sites (e.g. Wikipedia) 403 plain httpx requests based on their TLS
    fingerprint, even with a browser User-Agent. curl_cffi impersonates Chrome's
    TLS handshake, which passes those bot checks. Returns an ``httpx.Response``
    (so callers keep a single response type), or ``None`` if curl_cffi is
    unavailable or the request fails. Redirects are NOT followed here — the
    caller has already validated the URL as public, and we must not bypass the
    SSRF redirect guard.
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None
    try:
        r = curl_requests.get(
            url, headers=headers, timeout=timeout,
            impersonate="chrome", allow_redirects=False,
        )
    except Exception:
        return None
    # curl_cffi already decompresses the body, so drop the Content-Encoding /
    # Content-Length headers before wrapping in httpx.Response — otherwise httpx
    # tries to decompress the already-decompressed bytes and fails.
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in ("content-encoding", "content-length")}
    return httpx.Response(
        status_code=r.status_code,
        headers=resp_headers,
        content=r.content,
        request=httpx.Request("GET", url),
    )


def _get_public_url(url: str, *, headers: dict, timeout: int) -> httpx.Response:
    if not _public_http_url(url):
        raise httpx.RequestError(f"Blocked non-public URL: {url}")

    current = url
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=False) as client:
        for _ in range(8):
            response = client.get(current)
            if response.status_code not in (301, 302, 303, 307, 308):
                # Bot-blocked (403) by TLS fingerprint — retry with a browser
                # TLS impersonation before giving up.
                if response.status_code == 403:
                    fallback = _curl_cffi_fetch(current, headers=headers, timeout=timeout)
                    if fallback is not None and fallback.status_code not in (301, 302, 303, 307, 308):
                        return fallback
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(current, location)
            if not _public_http_url(current):
                raise httpx.RequestError(f"Blocked redirect to non-public URL: {current}")
    raise httpx.RequestError("Too many redirects")

# PDF extraction (optional dependency)
try:
    from pdfminer.high_level import extract_text as pdf_extract_text
except ImportError:
    pdf_extract_text = None  # type: ignore


# ----------------------------------------------------------------------
# HTML extraction helpers
# ----------------------------------------------------------------------
def _extract_meta(soup: BeautifulSoup) -> dict:
    """Pull meta description and keywords if present."""
    description = ""
    keywords = ""
    desc_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    if desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip()
    kw_tag = soup.find("meta", attrs={"name": re.compile("keywords", re.I)})
    if kw_tag and kw_tag.get("content"):
        keywords = kw_tag["content"].strip()
    return {"description": description, "keywords": keywords}


def _extract_og_image(soup: BeautifulSoup) -> str:
    """Extract the best representative image URL from meta tags.

    Only returns absolute http(s) URLs — skips relative paths and data URIs.
    """
    candidates = []
    # Open Graph image (most reliable)
    for prop in ("og:image", "og:image:url", "og:image:secure_url"):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content", "").strip():
            candidates.append(tag["content"].strip())
    # Twitter card image
    tag = soup.find("meta", attrs={"name": "twitter:image"})
    if tag and tag.get("content", "").strip():
        candidates.append(tag["content"].strip())
    # Thumbnail meta
    tag = soup.find("meta", attrs={"name": "thumbnail"})
    if tag and tag.get("content", "").strip():
        candidates.append(tag["content"].strip())
    # Return first absolute http(s) URL
    for url in candidates:
        if url.startswith(("https://", "http://")) and not url.endswith((".svg", ".ico")):
            return url
    return ""


def _extract_lists(soup: BeautifulSoup) -> List[List[str]]:
    """Return a list of lists, each inner list representing a <ul>/<ol>."""
    all_lists = []
    for lst in soup.find_all(["ul", "ol"]):
        items = [li.get_text(separator=" ", strip=True) for li in lst.find_all("li")]
        if items:
            all_lists.append(items)
    return all_lists


def _extract_tables(soup: BeautifulSoup) -> List[List[List[str]]]:
    """Return a list of tables, each table is a list of rows, each row a list of cell texts."""
    tables_data = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables_data.append(rows)
    return tables_data


def _extract_code_blocks(soup: BeautifulSoup) -> List[str]:
    """Collect text from <pre> and <code> blocks."""
    blocks = []
    for tag in soup.find_all(["pre", "code"]):
        txt = tag.get_text(separator=" ", strip=True)
        if txt:
            blocks.append(txt)
    return blocks


def _detect_js_frameworks(soup: BeautifulSoup) -> bool:
    """Very naive detection of common JS frameworks.

    Kept only as a dependency-free fallback when JEV is unreachable. The
    authoritative signal is ``_jev_need_js_render`` (a JEV ``noul`` decision
    over the fetched HTML), which is language/framework-agnostic and returns a
    calibrated probability rather than a hardcoded keyword match.
    """
    js_indicators = [
        "react", "angular", "vue", "svelte", "next", "nuxt",
        "ember", "backbone", "jquery", "polymer", "mithril",
    ]
    for script in soup.find_all("script"):
        src = script.get("src", "").lower()
        if any(fr in src for fr in js_indicators):
            return True
        if script.string:
            content = script.string.lower()
            if any(fr in content for fr in js_indicators):
                return True
    if soup.find(attrs={"data-reactroot": True}) or soup.find(attrs={"ng-app": True}):
        return True
    return False


# Threshold above which JEV concludes the page's main article body is only
# populated after client-side JS runs (and therefore a plain fetch returned a
# nav shell / title but not the article text).
_WAIT_FOR_LOAD_CONFIRM_THRESHOLD = 0.5

_WAIT_FOR_LOAD_QUESTION = (
    "Does fetching this page with a plain HTTP GET (no JavaScript execution) "
    "return the complete main article body, or is the substantive article "
    "content populated only after client-side JavaScript renders and the page "
    "is allowed to finish loading?"
)
_WAIT_FOR_LOAD_CRITERIA = {
    "true": "The page requires waiting for client-side JavaScript to render the main article body (plain fetch returned only a nav shell / title / placeholder).",
    "false": "The plain fetch already contains the full article body; no need to wait for JavaScript.",
}


def _jev_need_js_render(url: str, html_text: str) -> Optional[float]:
    """Ask JEV whether this page needs to wait for client-side JS to render.

    Returns the ``noul`` probability that the page requires a JS render wait,
    or ``None`` when JEV is unavailable/disabled (caller falls back). Mirrors
    the ``noul`` + ``criteria`` schema used by ``src.decisions``.
    """
    if not html_text or not html_text.strip():
        return None
    # Bound the state to a tractable window so the decision stays off any hot
    # path memory limits but retains enough markup/script hints for JEV.
    state = {
        "url": url,
        "html_sample": html_text[:20000],
        "html_length": len(html_text),
    }
    try:
        from src.decisions import jev_decide_sync, DecisionsUnavailable
        answers = jev_decide_sync(
            state=state,
            questions={
                "wait_for_load": {
                    "type": "noul",
                    "instructions": _WAIT_FOR_LOAD_QUESTION,
                    "criteria": _WAIT_FOR_LOAD_CRITERIA,
                }
            },
        )
    except DecisionsUnavailable as e:
        logger.debug(f"[content] JEV wait-for-load unavailable ({e}); using fallback")
        return None
    except Exception as e:
        logger.warning(f"[content] JEV wait-for-load errored ({e}); using fallback")
        return None

    ans = (answers or {}).get("wait_for_load")
    if isinstance(ans, dict) and ans.get("type") == "noul":
        v = ans.get("value")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _resolve_content_endpoint() -> Optional[Tuple[str, str, Dict]]:
    """Resolve a chat endpoint for LLM article-body extraction (utility endpoint)."""
    try:
        from src.endpoint_resolver import resolve_endpoint
        url, model, headers = resolve_endpoint("utility")
        if url and model:
            return url, model, headers
    except Exception as e:
        logger.debug(f"content extraction endpoint resolve failed: {e}")
    return None


_EXTRACT_ARTICLE_PROMPT = """\
You are an article-extraction engine. Given the raw HTML of a webpage, return \
the main article body text as clean, continuous prose.

Rules:
- Strip ALL navigation, header, footer, ads, cookie banners, sidebars, and \
boilerplate. Return only the substantive article content.
- Preserve the article's own language and wording verbatim — do not translate \
or summarise.
- Join paragraphs in reading order with a single blank line between them.
- If the HTML contains only a nav shell / title with no real article body \
(e.g. a JavaScript single-page app that has not rendered), return an empty \
string.
- Output ONLY the extracted article text, with no preamble, no explanation, and \
no code fences.

HTML:
\"\"\"
{html}
\"\"\"
"""


def _extract_article_body_llm(url: str, html_text: str, title_text: str) -> Optional[str]:
    """Extract the main article body via the LLM (readability-style).

    Returns the extracted prose, or ``None`` on any failure so the caller can
    fall back to the deterministic heuristic. Passing the page title lets the
    model disambiguate the article from a header-only shell.
    """
    if not html_text or not html_text.strip():
        return None
    resolved = _resolve_content_endpoint()
    if not resolved:
        return None
    c_url, model, headers = resolved
    # Keep the model's input bounded; the article body is almost always within
    # the first chunk, but we prefer completeness over token economy here.
    bounded = html_text[:60000]
    try:
        from src.llm_core import _llm_call_raw
        raw = _llm_call_raw(
            c_url,
            model,
            messages=[
                {
                    "role": "user",
                    "content": _EXTRACT_ARTICLE_PROMPT.format(html=bounded),
                }
            ],
            headers=headers,
            temperature=0.0,
            max_tokens=1500,
            timeout=30,
        )
    except Exception as e:
        logger.debug(f"LLM article extraction failed, using heuristic fallback: {e}")
        return None

    raw = (raw or "").strip()
    # A fenced response is a model error to correct defensively.
    if raw.startswith("```"):
        raw = re.sub(r"^```[^\n]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    raw = raw.strip()
    if not raw:
        return None
    return raw


# ----------------------------------------------------------------------
# Client-side render fallback (headless browser)
# ----------------------------------------------------------------------
_render_lock = None
_render_driver = None


def _render_page_js(url: str, timeout: int) -> Optional[str]:
    """Render a JS-heavy page with a headless browser and return its HTML.

    Called only when JEV's wait-for-load decision concludes a plain fetch
    returned a nav shell (the substantive article is populated client-side).
    A single shared driver (protected by a lock, mirroring ``src.scraper_llm``)
    is reused across calls. SSRF-safe: the URL must already have passed
    ``_public_http_url`` in ``_get_public_url``, and we re-check before loading.
    Returns the rendered HTML (``driver.page_source``), or ``None`` if Selenium
    is unavailable or the render fails — the caller then falls back to the
    plain-fetch HTML.

    Loading an arbitrary page in the user's browser is a heavier operation, so
    this only runs on the explicit JEV "needs JS" signal, never on every fetch.
    """
    global _render_lock, _render_driver
    if not _public_http_url(url):
        return None
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        logger.debug("[content] selenium unavailable; skipping JS render")
        return None

    if _render_lock is None:
        import threading
        _render_lock = threading.Lock()

    try:
        with _render_lock:
            if _render_driver is None:
                opts = Options()
                opts.add_argument("--headless=new")
                opts.add_argument("--disable-gpu")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                opts.add_argument("--window-size=1200,1000")
                opts.add_argument("--disable-blink-features=AutomationControlled")
                try:
                    _render_driver = webdriver.Chrome(options=opts)
                except Exception as e:
                    logger.warning(f"[content] could not start headless Chrome: {e}")
                    return None
            _render_driver.set_page_load_timeout(max(timeout, 1) or 5)
            _render_driver.get(url)
            # Small settle window so client-side fetch/render completes before
            # we read the DOM.
            import time
            time.sleep(1.0)
            html = _render_driver.page_source
    except Exception as e:
        logger.debug(f"[content] JS render failed for {url}: {e}")
        return None
    return html or None


def _empty_result(url: str, error: str = "") -> dict:
    """Build a standard failure result dict."""
    return {
        "url": url,
        "title": "",
        "content": "",
        "lists": [],
        "tables": [],
        "code_blocks": [],
        "meta_description": "",
        "meta_keywords": "",
        "js_rendered": False,
        "js_message": "",
        "success": False,
        "error": error,
    }


# ----------------------------------------------------------------------
# Main content fetcher
# ----------------------------------------------------------------------
def fetch_webpage_content(url: str, timeout: int = 5, retry_attempt: int = 0) -> dict:
    """Fetch and extract meaningful content from a webpage with caching."""
    cache_key = generate_cache_key(url)
    cache_file = CONTENT_CACHE_DIR / f"{cache_key}.cache"

    # Check cache
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            timestamp = datetime.fromisoformat(cached_data["timestamp"])
            if datetime.now() - timestamp < timedelta(hours=2):
                logger.debug(f"Content cache hit for URL: {url}")
                return cached_data["data"]
            else:
                cache_file.unlink(missing_ok=True)
                content_cache_index.pop(cache_key, None)
        except Exception as e:
            logger.warning(f"Failed to read content cache for {url}: {e}")
            cache_file.unlink(missing_ok=True)
            content_cache_index.pop(cache_key, None)

    # Fetch
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        response = _get_public_url(url, headers=headers, timeout=timeout)

        if response.status_code == 429:
            raise RateLimitError(f"Rate limit hit for {url} (attempt {retry_attempt})")

        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        # Non-2xx responses (403, 404, and non-standard codes like LinkedIn's
        # 999 anti-bot wall) must not crash the whole chat turn — treat them as
        # an unfetchable page instead.
        error_logger.error(f"HTTP {e.response.status_code} fetching {url} (attempt {retry_attempt}): {e}")
        return _empty_result(url, f"HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError fetching {url} (attempt {retry_attempt}): {e}")
        return _empty_result(url, f"NetworkError: {e}")
    except RateLimitError as e:
        error_logger.error(str(e))
        return _empty_result(url, str(e))

    # PDF handling
    content_type = response.headers.get("Content-Type", "").lower()
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        if pdf_extract_text is None:
            logger.error("pdfminer.six is not installed; cannot extract PDF text.")
            pdf_text = ""
        else:
            try:
                pdf_bytes = io.BytesIO(response.content)
                pdf_text = pdf_extract_text(pdf_bytes)
            except Exception as e:
                logger.warning(f"PDF extraction failed for {url}: {e}")
                pdf_text = ""
        result = {
            "url": url,
            "title": os.path.basename(url),
            "content": pdf_text,
            "lists": [],
            "tables": [],
            "code_blocks": [],
            "meta_description": "",
            "meta_keywords": "",
            "js_rendered": False,
            "js_message": "",
            "success": bool(pdf_text),
            "error": "" if pdf_text else "Failed to extract PDF text",
        }
        _cache_result(cache_file, cache_key, result, url)
        return result

    # HTML handling
    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        error_logger.error(f"ParseError parsing HTML from {url} (attempt {retry_attempt}): {e}")
        result = _empty_result(url, f"ParseError: {e}")
        _cache_result(cache_file, cache_key, result, url)
        return result

    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    meta_info = _extract_meta(soup)
    og_image = _extract_og_image(soup)

    # --- JEV wait-for-load decision -----------------------------------------
    # Rather than brittle regex against script src / data-reactroot markers, the
    # authoritative "do we need to wait for JS to render the article body?" is a
    # JEV `noul` decision over the fetched HTML. Below the confirmation threshold
    # → plain fetch is sufficient; at/above it → the page is likely a JS SPA that
    # returned only a nav shell, so we mark it rendered and prefer an LLM body
    # extraction that can see past the wrapper.
    html_text = response.text or ""
    wait_prob = _jev_need_js_render(url, html_text)
    jev_available = wait_prob is not None

    if wait_prob is None:
        # JEV unavailable — keep the deterministic framework heuristic as a
        # conservative fallback signal only.
        js_rendered = _detect_js_frameworks(soup)
        js_message = ("Page appears to be rendered by a JavaScript framework; content may be incomplete."
                      if js_rendered else "")
    else:
        js_rendered = wait_prob >= _WAIT_FOR_LOAD_CONFIRM_THRESHOLD
        js_message = (
            f"JEV wait-for-load p={wait_prob:.3f}: the article body likely requires "
            "client-side JavaScript to render (plain fetch returned a nav shell)."
            if js_rendered else
            f"JEV wait-for-load p={wait_prob:.3f}: plain fetch suffices."
        )

    # --- Act on the JEV "wait for load" decision -----------------------------
    # JEV concluded the plain fetch is only a nav shell and the article body is
    # populated client-side. Rather than leaving it half-baked (title + nav), we
    # render the page in a headless browser and re-parse the *rendered* DOM so
    # the article body is actually recoverable (this is the La Nación case).
    did_render = False
    if js_rendered:
        rendered_html = _render_page_js(url, timeout)
        if rendered_html and len(rendered_html) > len(html_text):
            try:
                soup = BeautifulSoup(rendered_html, "html.parser")
                html_text = rendered_html
                js_message += f" Rendered via headless browser ({len(html_text)} chars)."
                did_render = True
            except Exception as e:
                logger.debug(f"[content] failed to parse rendered HTML for {url}: {e}")

    THIN_CONTENT_CHARS = 600  # below this the heuristic likely missed the page

    # --- LLM article-body extraction (readability-style) ---------------------
    # Use the model to isolate the substantive article from nav/footer/boilerplate.
    # Falls back to the deterministic heuristic when the LLM is unreachable or
    # returns nothing useful, so fetch_webpage_content never regresses.
    main_content = _extract_article_body_llm(url, html_text, title_text)
    if not main_content:
        # Deterministic heuristic fallback (unchanged semantics).
        content_areas = soup.find_all(
            ["main", "article", "section", "div"],
            class_=re.compile("content|main|body|article|post|entry|text", re.I),
        )
        fallback_parts = []
        for area in content_areas[:3]:
            fallback_parts.append(area.get_text(separator=" ", strip=True))
        main_content = re.sub(r"\s+", " ", " ".join(fallback_parts)).strip()

        THIN_CONTENT_CHARS = 600  # below this the heuristic likely missed the page
        if len(main_content) < THIN_CONTENT_CHARS:
            body = soup.find("body")
            if body:
                # Strip from a copy so the later list/table/code extractors still
                # see the original soup unmodified.
                body_copy = copy.copy(body)
                for _noise in body_copy.find_all(
                    ["script", "style", "noscript", "template", "nav", "header", "footer", "aside"]
                ):
                    _noise.extract()
                body_text = re.sub(r"\s+", " ", body_copy.get_text(separator=" ", strip=True)).strip()
                if len(body_text) > len(main_content):
                    main_content = body_text

    # --- Last-resort JS render fallback --------------------------------------
    # Some JS-rendered SPA shells don't carry the framework markers JEV/the
    # heuristic look for, so the extracted content stays empty/thin even though
    # the page renders fine in a browser. When we got a 2xx page but no useful
    # article text (and we didn't already render), do ONE headless render and
    # re-hydrate the body so `web_fetch` returns real content instead of
    # "no readable text content". Fail-safe: never runs if Selenium is absent.
    if (
        not jev_available
        and not did_render
        and not (main_content and main_content.strip())
        and html_text
    ):
        rendered_html = _render_page_js(url, timeout)
        if rendered_html and len(rendered_html) > len(html_text):
            try:
                soup = BeautifulSoup(rendered_html, "html.parser")
                html_text = rendered_html
                js_message = (js_message + " " if js_message else "") + (
                    f"Rendered via headless browser fallback ({len(html_text)} chars)."
                )
                js_rendered = True
                body = soup.find("body")
                if body:
                    body_copy = copy.copy(body)
                    for _noise in body_copy.find_all(
                        ["script", "style", "noscript", "template", "nav", "header", "footer", "aside"]
                    ):
                        _noise.extract()
                    body_text = re.sub(r"\s+", " ", body_copy.get_text(separator=" ", strip=True)).strip()
                    if body_text and len(body_text) > len(main_content or ""):
                        main_content = body_text
            except Exception as e:
                logger.debug(f"[content] fallback JS render failed for {url}: {e}")

    result = {
        "url": url,
        "title": title_text,
        "content": main_content,
        "lists": _extract_lists(soup),
        "tables": _extract_tables(soup),
        "code_blocks": _extract_code_blocks(soup),
        "meta_description": meta_info.get("description", ""),
        "meta_keywords": meta_info.get("keywords", ""),
        "og_image": og_image,
        "js_rendered": js_rendered,
        "js_message": js_message,
        "success": True,
        "error": "",
    }
    _cache_result(cache_file, cache_key, result, url)
    return result


def _cache_result(cache_file, cache_key: str, result: dict, url: str):
    """Write a result to the content cache."""
    try:
        cache_data = {"timestamp": datetime.now().isoformat(), "data": result}
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f)
        content_cache_index[cache_key] = datetime.now()
        cleanup_cache(CONTENT_CACHE_DIR, content_cache_index, timedelta(hours=2))
    except Exception as e:
        logger.warning(f"Failed to write content cache for {url}: {e}")


# ----------------------------------------------------------------------
# Content summarization helpers
# ----------------------------------------------------------------------
def extract_key_points(text: str) -> List[str]:
    """Pull out bullet-style key points from a block of text."""
    points: List[str] = []
    bullet_pat = re.compile(r"^\s*[-*•]\s+(.*)")
    numbered_pat = re.compile(r"^\s*\d+[\.\)]\s+(.*)")
    for line in text.splitlines():
        m = bullet_pat.match(line) or numbered_pat.match(line)
        if m:
            points.append(m.group(1).strip())
    return points


def get_tldr(text: str, max_sentences: int = 3) -> str:
    """Produce a very short TL;DR by taking the first few sentences."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    selected = [s.strip() for s in sentences if s][:max_sentences]
    return " ".join(selected)


def extract_quotes(text: str) -> List[str]:
    """Return quoted excerpts that are at least 15 characters long."""
    # Backreference the opening quote so the closing quote must match it —
    # otherwise `"text'` (open double, close single) is treated as a quote.
    return [m.group(2).strip() for m in re.finditer(r'(["\'])([^"\']{15,}?)\1', text)]


def extract_statistics(text: str) -> List[str]:
    """Find numbers, percentages, dates and simple measurements."""
    # Match a comma-grouped number (1,000,000) OR a plain digit run (50000) —
    # the old `\d{1,3}(?:,\d{3})*` matched only the first 3 digits of a
    # comma-less number, and the trailing `\b` dropped a closing `%`.
    pattern = re.compile(
        r"\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(%|percent|‰|per cent|[a-zA-Z]+)?",
        re.IGNORECASE,
    )
    return [m.group(0).strip() for m in pattern.finditer(text)]
