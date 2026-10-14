"""Reddit scraping for the agent tool surface.

Zero-login Reddit access. Primary path is Reddit's JSON API fetched via
``curl_cffi`` with a browser TLS fingerprint (Reddit 403s plain httpx/bot
requests), which is already a dependency of ``src.search.content``. If the
optional ``reddrix`` library is installed it is preferred (it handles Reddit's
modern "shreddit" frontend and anti-fingerprinting natively).

The tool returns structured posts (title, author, score, url, subreddit,
selftext/snippet). It is deliberately dependency-degradable: when neither the
network path nor reddrix is available, it returns a clear error the agent can
reason around rather than crashing the turn.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _curl_cffi_get_json(url: str, timeout: int = 15) -> Optional[Any]:
    """GET a URL via curl_cffi (Chrome impersonation) and parse JSON.

    Returns parsed JSON, or None on any failure. curl_cffi is the only client
    that reliably sneaks past Reddit's 403 bot-wall for the public JSON API.
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None
    try:
        r = curl_requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.5",
            },
            timeout=timeout,
            impersonate="chrome",
        )
    except Exception as e:
        logger.debug(f"reddit curl_cffi GET failed: {e}")
        return None
    if r.status_code != 200:
        logger.debug(f"reddit curl_cffi status {r.status_code}")
        return None
    try:
        return json.loads(r.content)
    except (json.JSONDecodeError, ValueError):
        return None


def _posts_from_json(data: Any, limit: int) -> List[Dict[str, Any]]:
    """Flatten a Reddit JSON listing into a normalized list of post dicts."""
    out: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return out
    children = data.get("data", {}).get("children", [])
    for child in children:
        if not isinstance(child, dict):
            continue
        d = child.get("data") or {}
        if not isinstance(d, dict):
            continue
        title = (d.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "author": d.get("author") or d.get("author_fullname") or "Unknown",
            "score": d.get("score"),
            "num_comments": d.get("num_comments"),
            "subreddit": d.get("subreddit", ""),
            "url": d.get("url", ""),
            "permalink": d.get("permalink", ""),
            "selftext": (d.get("selftext") or "").strip(),
            "created_utc": d.get("created_utc"),
        })
        if len(out) >= limit:
            break
    return out


def _reddrix_posts(subreddit: str, query: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    """Use the optional reddrix library if installed (async, curl_cffi engine)."""
    try:
        import asyncio
        from reddrix import ReddrixScraper
    except ImportError:
        return None

    async def _run():
        async with ReddrixScraper(engine="curl_cffi") as scraper:
            rows: List[Dict[str, Any]] = []
            if query:
                async for r in scraper.search_keywords(query, limit=limit):
                    rows.append(_normalize_reddrix(r))
            else:
                async for r in scraper.get_subreddit_posts(subreddit, sort="new", limit=limit):
                    rows.append(_normalize_reddrix(r))
            return rows

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.debug(f"reddrix scrape failed: {e}")
        return None


def _normalize_reddrix(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": (r.get("title") or "").strip(),
        "author": r.get("author") or "Unknown",
        "score": r.get("score"),
        "num_comments": r.get("num_comments") or r.get("comments"),
        "subreddit": r.get("subreddit", ""),
        "url": r.get("url", ""),
        "permalink": r.get("permalink", ""),
        "selftext": (r.get("selftext") or "").strip(),
    }


def reddit_search(
    query: str = "",
    subreddit: str = "",
    limit: int = 5,
) -> Dict[str, Any]:
    """Search Reddit by keyword and/or within a subreddit.

    Returns ``{"success": bool, "posts": [...], "error": ""}``. Posts are
    normalized dicts with title/author/score/subreddit/url/selftext.
    """
    limit = max(1, min(int(limit or 5), 25))
    query = (query or "").strip()
    subreddit = (subreddit or "").strip().lstrip("r/")

    # 1. Prefer reddrix (richer, anti-fingerprinted) when installed.
    if query or subreddit:
        posts = _reddrix_posts(subreddit, query, limit)
        if posts is not None:
            if posts:
                return {"success": True, "posts": posts, "error": ""}
            return {"success": False, "posts": [], "error": "No posts found matching the query."}

    # 2. Fall back to Reddit's public JSON API via curl_cffi.
    if subreddit:
        url = f"https://www.reddit.com/r/{quote(subreddit)}/search.json?q={quote(query or '')}&restrict_sr=1&limit={limit}&sort=relevance"
    elif query:
        url = f"https://www.reddit.com/search.json?q={quote(query)}&limit={limit}&sort=relevance"
    else:
        return {"success": False, "posts": [], "error": "Provide a query and/or subreddit to search Reddit."}

    data = _curl_cffi_get_json(url)
    if data is None:
        return {
            "success": False,
            "posts": [],
            "error": (
                "Reddit returned no readable data (403 bot-wall or network error). "
                "Install the optional 'reddrix' package (pip install reddrix) for "
                "a more robust zero-login scraper, or provide a direct thread URL."
            ),
        }

    posts = _posts_from_json(data, limit)
    if not posts:
        return {"success": False, "posts": [], "error": "No posts found matching the query."}
    return {"success": True, "posts": posts, "error": ""}


def reddit_thread(thread_url: str, include_comments: bool = True, limit: int = 10) -> Dict[str, Any]:
    """Fetch a single Reddit thread (post + top comments) by URL.

    Uses Reddit's JSON API (append ``.json``). Returns the post plus an
    optional flat list of comment bodies for the agent to read.
    """
    url = (thread_url or "").strip()
    if not url:
        return {"success": False, "error": "Provide a Reddit thread URL."}
    if "reddit.com" not in url:
        return {"success": False, "error": "Not a reddit.com URL."}

    json_url = url.rstrip("/") + ".json"
    data = _curl_cffi_get_json(json_url)
    if data is None:
        return {"success": False, "error": "Reddit returned no readable data (403/JS wall). Try old.reddit.com or install reddrix."}

    # Reddit returns [post_listing, comments_listing] for a thread.
    post = None
    comments = []
    if isinstance(data, list):
        post = _posts_from_json(data[0], 1)
        if post:
            post = post[0]
        if include_comments and len(data) > 1:
            comments = _flatten_comments(data[1], limit)
    return {
        "success": bool(post),
        "post": post or {},
        "comments": comments,
        "error": "" if post else "Could not parse thread.",
    }


def _flatten_comments(node: Any, limit: int) -> List[str]:
    """Depth-first flatten of Reddit comment bodies into a plain list."""
    out: List[str] = []

    def _walk(n: Any):
        if len(out) >= limit:
            return
        if not isinstance(n, dict):
            return
        kind = n.get("kind")
        data = n.get("data") or {}
        if kind == "t1" and isinstance(data, dict):  # t1 = comment
            body = (data.get("body") or "").strip()
            if body and body not in ("[deleted]", "[removed]"):
                out.append(body)
        replies = data.get("replies")
        if isinstance(replies, dict):
            children = replies.get("data", {}).get("children", [])
            for c in children:
                _walk(c)
        children = data.get("children")
        if isinstance(children, list):
            for c in children:
                _walk(c)

    if isinstance(node, list):
        for item in node:
            _walk(item)
    else:
        _walk(node)
    return out[:limit]