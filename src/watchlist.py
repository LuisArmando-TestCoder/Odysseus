"""
watchlist.py — proactive change monitoring for pages the user cares about.

A *watch* is a URL plus a check interval (and optionally the case it belongs
to). The ``check_watchlist`` built-in scheduled action calls
``WatchStore.check_due`` every half hour; each due watch is fetched, reduced to
visible text, hashed, and diffed against the previous snapshot. When the page
changed, the store:

  1. records what was added/removed (a short, human-readable diff),
  2. files a finding in the linked case (so the change lands in the dossier
     with its source URL and any new selectors auto-extracted), and
  3. returns a change event that the scheduler delivers as a notification.

Optional ``keywords`` turn a watch into an alert: the change is only reported
when a keyword appears in the *added* text (e.g. watch a court docket for a
surname, a registry page for a company name, a release page for "security").

Stdlib only; the fetcher is injectable so tests never touch the network.
"""

from __future__ import annotations

import difflib
import hashlib
import html as _html
import json
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

MIN_INTERVAL_MIN = 15
DEFAULT_INTERVAL_MIN = 360
MAX_WATCHES_PER_OWNER = 200
MAX_SNAPSHOT_CHARS = 200_000
MAX_DIFF_LINES = 12

Fetcher = Callable[[str], str]


def _slug(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "_anon")).strip("._")
    return s[:80] or "_anon"


def html_to_text(raw: str) -> str:
    """Reduce an HTML document to its visible text, one block per line."""
    raw = raw or ""
    if "<" not in raw:
        return "\n".join(l.strip() for l in raw.splitlines() if l.strip())
    raw = re.sub(r"(?is)<(script|style|noscript|template|svg|head)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6]|/section|/article|/td)\b[^>]*>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = _html.unescape(raw).replace("\u00a0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", l).strip() for l in text.splitlines())
    return "\n".join(l for l in lines if l)


# Volatile tokens that change on every load and would cause false positives.
_VOLATILE = [
    re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", re.I),     # clock times
    re.compile(r"\b(csrf|nonce|token|session)[\w-]*[=:]\s*\S+", re.I),
    re.compile(r"\b[0-9a-f]{24,}\b", re.I),                        # hashes / ids
    re.compile(r"\b\d+\s+(seconds?|minutes?|hours?)\s+ago\b", re.I),
]


def fingerprint(text: str) -> str:
    norm = text
    for rx in _VOLATILE:
        norm = rx.sub("", norm)
    norm = re.sub(r"\s+", " ", norm).strip().lower()
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()


def summarize_diff(old: str, new: str) -> Dict[str, List[str]]:
    old_l = [l for l in (old or "").splitlines() if l.strip()]
    new_l = [l for l in (new or "").splitlines() if l.strip()]
    added, removed = [], []
    for line in difflib.unified_diff(old_l, new_l, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added.append(line[1:].strip())
        elif line.startswith("-"):
            removed.append(line[1:].strip())
    return {"added": added, "removed": removed}


def default_fetcher(url: str) -> str:
    """Fetch a URL with SSRF protection. Imported lazily so the module stays
    importable (and testable) without httpx installed."""
    from src.url_safety import check_outbound_url
    ok, reason = check_outbound_url(url, block_private=True)
    if not ok:
        raise ValueError(f"blocked URL: {reason}")
    import httpx  # noqa: WPS433 — lazy on purpose
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 Odysseus-Watch/1.0"),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        # Re-validate the final hop: a redirect must not land on a private host.
        final = str(resp.url)
        if final != url:
            ok, reason = check_outbound_url(final, block_private=True)
            if not ok:
                raise ValueError(f"redirect blocked: {reason}")
        resp.raise_for_status()
        return resp.text[: MAX_SNAPSHOT_CHARS * 3]


def _effective_interval_s(w: Dict[str, Any]) -> float:
    """Interval in seconds, backed off exponentially (max 8x) while failing."""
    errs = int(w.get("error_count") or 0)
    return w["interval_minutes"] * 60 * (min(8, 2 ** errs) if errs else 1)


class WatchStore:
    def __init__(self, data_dir: str):
        self.root = os.path.join(data_dir, "watchlist")
        self._lock = threading.RLock()

    # -- io ------------------------------------------------------------------
    def _path(self, owner) -> str:
        os.makedirs(self.root, exist_ok=True)
        return os.path.join(self.root, f"{_slug(owner or '_anon')}.json")

    def _load(self, owner) -> Dict[str, Any]:
        p = self._path(owner)
        if not os.path.exists(p):
            return {"watches": {}}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("watches", {})
            return data
        except (OSError, ValueError):
            return {"watches": {}}

    def _save(self, owner, data) -> None:
        p = self._path(owner)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".watch-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _public(w: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in w.items() if k != "snapshot"}

    # -- crud ----------------------------------------------------------------
    def add(self, owner, url: str, *, label: str = "", interval_minutes: int = DEFAULT_INTERVAL_MIN,
            case_id: str = "", keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        url = (url or "").strip()
        if not re.match(r"^https?://[^\s/]+", url, re.I):
            raise ValueError("url must start with http:// or https://")
        interval = max(MIN_INTERVAL_MIN, int(interval_minutes or DEFAULT_INTERVAL_MIN))
        with self._lock:
            data = self._load(owner)
            for w in data["watches"].values():
                if w["url"] == url:
                    w.update({"active": True, "interval_minutes": interval})
                    if label:
                        w["label"] = label[:200]
                    if case_id:
                        w["case_id"] = case_id
                    if keywords is not None:
                        w["keywords"] = [k.strip() for k in keywords if k and k.strip()][:20]
                    self._save(owner, data)
                    return self._public(w)
            if len(data["watches"]) >= MAX_WATCHES_PER_OWNER:
                raise ValueError(f"watch limit reached ({MAX_WATCHES_PER_OWNER})")
            w = {
                "id": "w_" + uuid.uuid4().hex[:8], "url": url, "label": (label or url)[:200],
                "interval_minutes": interval, "case_id": case_id or "",
                "keywords": [k.strip() for k in (keywords or []) if k and k.strip()][:20],
                "active": True, "created_at": time.time(), "last_checked": 0.0,
                "last_changed": 0.0, "hash": "", "snapshot": "", "change_count": 0,
                "error_count": 0, "last_error": "", "last_diff": {},
            }
            data["watches"][w["id"]] = w
            self._save(owner, data)
            return self._public(w)

    def list(self, owner) -> List[Dict[str, Any]]:
        data = self._load(owner)
        return sorted((self._public(w) for w in data["watches"].values()),
                      key=lambda w: w.get("created_at", 0))

    def _resolve(self, data, ref: str) -> Dict[str, Any]:
        ref = (ref or "").strip()
        if ref in data["watches"]:
            return data["watches"][ref]
        for w in data["watches"].values():
            if w["url"] == ref or w["label"].lower() == ref.lower():
                return w
        raise KeyError(f"watch not found: {ref!r}")

    def remove(self, owner, ref: str) -> bool:
        with self._lock:
            data = self._load(owner)
            w = self._resolve(data, ref)
            del data["watches"][w["id"]]
            self._save(owner, data)
            return True

    def set_active(self, owner, ref: str, active: bool) -> Dict[str, Any]:
        with self._lock:
            data = self._load(owner)
            w = self._resolve(data, ref)
            w["active"] = bool(active)
            self._save(owner, data)
            return self._public(w)

    # -- checking ------------------------------------------------------------
    def check_one(self, owner, ref: str, fetcher: Optional[Fetcher] = None,
                  now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load(owner)
            w = self._resolve(data, ref)
            ev = self._check(w, fetcher or default_fetcher, now or time.time())
            self._save(owner, data)
        if ev:
            self._file_to_case(owner, w, ev)
        return ev

    def check_due(self, owner, fetcher: Optional[Fetcher] = None, now: Optional[float] = None,
                  max_checks: int = 25) -> Dict[str, Any]:
        """Check every active watch whose interval has elapsed. Returns
        {"checked": n, "changes": [events], "errors": [..]}."""
        now = now or time.time()
        fetcher = fetcher or default_fetcher
        changes, errors, checked = [], [], 0
        with self._lock:
            data = self._load(owner)
            due = [w for w in data["watches"].values()
                   if w.get("active") and now - float(w.get("last_checked") or 0) >= _effective_interval_s(w) - 30]
            due.sort(key=lambda w: w.get("last_checked") or 0)
            for w in due[:max_checks]:
                checked += 1
                ev = self._check(w, fetcher, now)
                if ev:
                    changes.append((w, ev))
                elif w.get("last_error") and w.get("last_checked") == now:
                    errors.append({"id": w["id"], "url": w["url"], "error": w["last_error"]})
            self._save(owner, data)
        for w, ev in changes:
            self._file_to_case(owner, w, ev)
        return {"checked": checked, "changes": [ev for _, ev in changes], "errors": errors}

    def _check(self, w: Dict[str, Any], fetcher: Fetcher, now: float) -> Optional[Dict[str, Any]]:
        w["last_checked"] = now
        try:
            text = html_to_text(fetcher(w["url"]))[:MAX_SNAPSHOT_CHARS]
        except Exception as e:  # network / http / blocked
            w["error_count"] = int(w.get("error_count", 0)) + 1
            w["last_error"] = f"{type(e).__name__}: {e}"[:300]
            # Back off politely on repeated failures (max 8x interval) and
            # pause after 10 consecutive failures so a dead URL stops costing.
            if w["error_count"] >= 10:
                w["active"] = False
            return None
        w["error_count"] = 0
        w["last_error"] = ""
        h = fingerprint(text)
        if not w.get("hash"):
            w["hash"], w["snapshot"] = h, text
            return None  # baseline
        if h == w["hash"]:
            return None
        diff = summarize_diff(w.get("snapshot", ""), text)
        w["hash"], w["snapshot"] = h, text
        if not diff["added"] and not diff["removed"]:
            return None
        kws = [k.lower() for k in w.get("keywords") or []]
        hit_kws = []
        if kws:
            added_low = "\n".join(diff["added"]).lower()
            hit_kws = [k for k in kws if k in added_low]
            if not hit_kws:
                return None  # changed, but nothing the user asked to be alerted about
        w["change_count"] = int(w.get("change_count", 0)) + 1
        w["last_changed"] = now
        w["last_diff"] = {"added": diff["added"][:MAX_DIFF_LINES], "removed": diff["removed"][:MAX_DIFF_LINES]}
        return {
            "watch_id": w["id"], "url": w["url"], "label": w["label"], "case_id": w.get("case_id", ""),
            "added": diff["added"][:MAX_DIFF_LINES], "removed": diff["removed"][:MAX_DIFF_LINES],
            "n_added": len(diff["added"]), "n_removed": len(diff["removed"]),
            "keywords_hit": hit_kws, "ts": now,
        }

    def _file_to_case(self, owner, w, ev) -> None:
        if not w.get("case_id"):
            return
        try:
            from src.casefiles import get_case_store
            store = get_case_store(os.path.dirname(self.root))
            sample = "; ".join(ev["added"][:5])
            text = (f"Watched page changed: {w['label']} (+{ev['n_added']}/-{ev['n_removed']} lines)"
                    + (f". Keywords: {', '.join(ev['keywords_hit'])}" if ev["keywords_hit"] else "")
                    + (f". New: {sample}" if sample else ""))
            store.add_finding(owner, w["case_id"], text, source_url=w["url"],
                              confidence=0.7, kind="change")
        except Exception:
            # The case may have been deleted; the notification still fires.
            pass


def format_events(events: List[Dict[str, Any]]) -> str:
    if not events:
        return ""
    out = []
    for ev in events:
        head = f"🔔 {ev['label']} changed (+{ev['n_added']}/-{ev['n_removed']})"
        if ev.get("keywords_hit"):
            head += f" — matched: {', '.join(ev['keywords_hit'])}"
        out.append(head)
        out.append(f"   {ev['url']}")
        for line in ev["added"][:4]:
            out.append(f"   + {line[:160]}")
    return "\n".join(out)


_stores: Dict[str, WatchStore] = {}
_stores_lock = threading.Lock()


def get_watch_store(data_dir: Optional[str] = None) -> WatchStore:
    if data_dir is None:
        from src.constants import DATA_DIR
        data_dir = DATA_DIR
    with _stores_lock:
        st = _stores.get(data_dir)
        if st is None:
            st = _stores[data_dir] = WatchStore(data_dir)
        return st
