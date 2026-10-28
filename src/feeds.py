"""
feeds.py — tiny, stdlib-only parser for RSS 2.0, RSS 1.0 (RDF), Atom and
JSON feeds (JSON Feed 1.x or a plain JSON array of objects / ``{"items": [...]}``).

Used by the Watchlist so a watch on a feed reports *new items* (title + link)
instead of a noisy line diff of the whole document.

Safety: feed content is untrusted. Documents carrying a DOCTYPE / ENTITY
declaration are refused outright (no entity-expansion games), every field is
length-capped and stripped of markup, and nothing here ever fetches a URL.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

MAX_ITEMS = 200
MAX_TITLE = 300
MAX_SUMMARY = 500
MAX_LINK = 1000

_TAG_RX = re.compile(r"(?s)<[^>]+>")
_WS_RX = re.compile(r"\s+")


def _clean(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = _html.unescape(_TAG_RX.sub(" ", text))
    return _WS_RX.sub(" ", text).strip()[:limit]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child(el, *names: str):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el, *names: str) -> str:
    c = _child(el, *names)
    return "".join(c.itertext()) if c is not None else ""


def _item_id(*parts: str) -> str:
    for p in parts:
        if p and p.strip():
            return p.strip()[:MAX_LINK]
    return ""


def _finalize(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out, seen = [], set()
    for it in items:
        if not it["id"]:
            it["id"] = "h:" + hashlib.sha1(
                (it["title"] + "|" + it["summary"]).encode("utf-8", "ignore")).hexdigest()[:16]
        if it["id"] in seen or not (it["title"] or it["link"] or it["summary"]):
            continue
        seen.add(it["id"])
        out.append(it)
        if len(out) >= MAX_ITEMS:
            break
    return out


def _safe_link(link: str) -> str:
    link = (link or "").strip()
    return link[:MAX_LINK] if re.match(r"^https?://", link, re.I) else ""


def _parse_xml(raw: str) -> Optional[List[Dict[str, str]]]:
    head = raw[:4096].lower()
    if "<!doctype" in head or "<!entity" in raw.lower():
        raise ValueError("feed declares a DOCTYPE/ENTITY; refused")
    try:
        root = ET.fromstring(raw.encode("utf-8", "ignore"))
    except ET.ParseError:
        return None
    kind = _local(root.tag)
    items: List[Dict[str, str]] = []
    if kind in ("rss", "rdf"):
        nodes = [el for el in root.iter() if _local(el.tag) == "item"]
        for el in nodes:
            link = _text(el, "link")
            guid = _text(el, "guid")
            items.append({
                "id": _item_id(guid, link, el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")),
                "title": _clean(_text(el, "title"), MAX_TITLE),
                "link": _safe_link(link or (guid if guid.startswith("http") else "")),
                "summary": _clean(_text(el, "description", "summary", "encoded"), MAX_SUMMARY),
                "published": _clean(_text(el, "pubdate", "date", "published"), 64),
            })
    elif kind == "feed":
        for el in root:
            if _local(el.tag) != "entry":
                continue
            link = ""
            for c in el:
                if _local(c.tag) == "link" and c.get("rel", "alternate") == "alternate":
                    link = c.get("href", "")
                    break
            items.append({
                "id": _item_id(_text(el, "id"), link),
                "title": _clean(_text(el, "title"), MAX_TITLE),
                "link": _safe_link(link),
                "summary": _clean(_text(el, "summary", "content"), MAX_SUMMARY),
                "published": _clean(_text(el, "published", "updated"), 64),
            })
    else:
        return None
    return _finalize(items)


def _parse_json(raw: str) -> Optional[List[Dict[str, str]]]:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = data.get("items") if isinstance(data.get("items"), list) else None
    if not isinstance(data, list) or not data or not all(isinstance(x, dict) for x in data[:20]):
        return None
    items = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        link = str(obj.get("url") or obj.get("link") or obj.get("external_url") or "")
        title = obj.get("title") or obj.get("name") or obj.get("headline") or ""
        summary = (obj.get("summary") or obj.get("content_text") or obj.get("description")
                   or obj.get("content_html") or "")
        ident = obj.get("id") if obj.get("id") not in (None, "") else obj.get("guid")
        items.append({
            "id": _item_id(str(ident) if ident not in (None, "") else "", link),
            "title": _clean(title, MAX_TITLE),
            "link": _safe_link(link),
            "summary": _clean(summary, MAX_SUMMARY),
            "published": _clean(obj.get("date_published") or obj.get("published") or obj.get("date") or "", 64),
        })
    return _finalize(items)


def looks_like_feed(raw: str) -> bool:
    head = (raw or "").lstrip()[:600].lower()
    if head.startswith("{") or head.startswith("["):
        return True
    return head.startswith("<?xml") or head.startswith("<rss") or head.startswith("<feed") \
        or head.startswith("<rdf:rdf") or ("<rss" in head) or ("<feed" in head and "xmlns" in head)


def parse_feed(raw: str) -> Optional[List[Dict[str, str]]]:
    """Return a list of items ``{id, title, link, summary, published}`` or
    ``None`` when ``raw`` is not a recognisable feed. Raises ``ValueError`` for
    XML that declares a DOCTYPE/ENTITY (refused for safety)."""
    raw = (raw or "").lstrip("﻿ \t\r\n")
    if not raw or not looks_like_feed(raw):
        return None
    if raw[0] in "[{":
        return _parse_json(raw)
    return _parse_xml(raw)
