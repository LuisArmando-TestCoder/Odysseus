"""
osint_research.py — runnable code behind the bundled OSINT skill
("osint-research-via-cdp-browser-and-sherlock").

Turns the skill's prose procedure into concrete functions:

  - run_sherlock()      enumerate usernames across sites via the Sherlock CLI
  - cdp_targets()       list Chrome remote-debugging (CDP) page targets
  - cdp_extract_text()  pull document.body.innerText from a bot-walled page
  - research_osint()    orchestrate open sources + Sherlock + CDP into findings

Sherlock must be installed separately (``pip install sherlock``); everything
else uses only the Python stdlib + the already-bundled ``websocket-client``.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)


def _sherlock_exe():
    """Locate the Sherlock CLI on PATH or in the venv's bin directory."""
    exe = shutil.which("sherlock")
    if exe:
        return exe
    candidate = os.path.join(os.path.dirname(sys.executable), "sherlock")
    return candidate if os.path.isfile(candidate) else None


def sherlock_available() -> bool:
    return _sherlock_exe() is not None


class _InnerTextExtractor(HTMLParser):
    """Strip markup and collect the visible text of an HTML document.

    Equivalent to ``document.body.innerText`` for verification purposes: only
    human-visible text is kept, and script/style/template/svg/head subtrees are
    skipped so a page that merely renders the username client-side (JS) or in
    hidden markup does not falsely count as "real data".
    """

    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks: list = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def _fetch_inner_text(url: str, timeout: int = 10) -> dict:
    """Fetch a profile URL and return its visible text (innerText equivalent).

    Lightweight stdlib GET — no Chrome/CDP required, which is the right call
    for a self-audit (the bundled SKILL.md explicitly says NOT to drive a
    bot-walled browser for this). Returns:
      {"ok": bool, "inner_text": str, "status": int|None, "error": str|None}
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Odysseus/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            # Only read a bounded prefix — profile pages are small, and a
            # misbehaving endpoint must not let us slurp unbounded data.
            body = resp.read(512 * 1024)
            parser = _InnerTextExtractor()
            parser.feed(body.decode("utf-8", errors="replace"))
            parser.close()
            return {"ok": True, "inner_text": parser.text(), "status": status, "error": None}
    except HTTPError as e:
        return {"ok": False, "inner_text": "", "status": e.code, "error": f"HTTP {e.code}"}
    except (URLError, OSError, ValueError) as e:
        return {"ok": False, "inner_text": "", "status": None, "error": str(e)}


def _parse_sherlock_hit(hit: str) -> tuple[str, str]:
    """Split a Sherlock ``[+] Site: URL`` line into (site_name, url)."""
    stripped = hit.strip()
    if stripped.startswith("[+]"):
        stripped = stripped[3:].strip()
    # Site names never contain ": " but URLs always contain a colon followed
    # by "//", so split once on the LAST ": " separator — the name is left,
    # the URL right.
    if ": " in stripped:
        name, url = stripped.rsplit(": ", 1)
        return name.strip(), url.strip()
    # Fallback: no clear separator — treat the whole thing as the URL.
    return "", stripped


def _verify_sherlock_hit(hit: str, username: str, timeout: int) -> dict:
    """Verify a single Sherlock candidate by inspecting the page's innerText.

    The user asked to stop returning fake profiles "by inspecting the innerText
    of the so said url in the process reiterating until basing on real data".
    A hit is only REAL when the username actually appears in the page's visible
    text. Three outcomes:
      status "verified"   — innerText contains the username (case-insensitive).
      status "unverified" — page fetched but username absent (parked page,
                            a different person, a login wall, or a deleted
                            account). This is the false-positive case.
      status "unchecked"  — fetch failed (network/bot-wall/non-OK); we can't
                            confirm or refute, so keep it flagged as a possible
                            match rather than silently dropping real data.
    """
    site, url = _parse_sherlock_hit(hit)
    result = _fetch_inner_text(url, timeout=timeout)

    if not result["ok"] or result["status"] is None or result["status"] >= 400:
        status = "unchecked"
        matched = None
        observed_keys = 0
    else:
        needle = username.lower()
        haystack = result["inner_text"].lower()
        matched = needle in haystack
        # Strength signal: how many times the username literally appeared in
        # the visible text. Repeated occurrences are a strong "this page is
        # genuinely about this account" indicator.
        observed_keys = haystack.count(needle)
        status = "verified" if matched else "unverified"

    return {
        "site": site or url,
        "url": url,
        "status": status,
        "matched": matched,
        "occurrences": observed_keys,
        "inner_text_error": result["error"] if not result["ok"] else None,
    }


def run_sherlock(usernames, timeout: int = 30, verify: bool = True, verify_timeout: int = 10) -> dict:
    """Run Sherlock and (optionally) verify every hit against the page's innerText.

    Sherlock is notorious for false positives — it flags a site whenever that
    site does NOT show a "not found" page, which includes parked domains,
    login walls, and pages that redirect to a generic home. So by default each
    ``[+]`` hit is re-fetched and only marked ``verified`` when the username
    literally appears in the returned page's visible text.

    ``verify=False`` skips the (slower) fetch pass and returns raw candidates
    only. Returns:
      {"found": [{"username": ..., "sites": [{"site","url","status",
                "matched","occurrences","inner_text_error"}, ...], ...}], ...}
    """
    if isinstance(usernames, str):
        usernames = [usernames]
    exe = _sherlock_exe()
    if not exe:
        return {
            "error": "Sherlock is not installed. Install it once with: pip install sherlock",
            "found": [],
            "usernames": list(usernames),
        }

    found = []
    output = []
    for username in usernames:
        cmd = [exe, username, "--timeout", str(timeout), "--print-found"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=max(60, timeout * 2),
            )
            raw = (proc.stdout or "") + (proc.stderr or "")
            output.append(raw)
            # Sherlock prints positive hits as "[+] Site: URL".
            hits = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("[+]")]
            if verify and hits:
                verified: list = [None] * len(hits)
                # Verify candidates CONCURRENTLY and under an overall wall-clock
                # deadline. A long list of hits serialized each hit's fetch
                # timeout into many minutes of blocking with no SSE traffic —
                # which tripped the frontend's tab-inactive stall recovery
                # ("Streaming was interrupted after the tab went inactive").
                # A bounded worker pool + deadline keeps verification fast and
                # still leaves the stream alive.
                _deadline = max(verify_timeout * 6, 90)
                pool = ThreadPoolExecutor(max_workers=8)
                try:
                    futures = {
                        pool.submit(_verify_sherlock_hit, hit, username, verify_timeout): idx
                        for idx, hit in enumerate(hits)
                    }
                    for fut in as_completed(futures, timeout=_deadline):
                        idx = futures[fut]
                        try:
                            verified[idx] = fut.result()
                        except Exception as e:  # noqa: BLE001
                            site, url = _parse_sherlock_hit(hits[idx])
                            verified[idx] = {
                                "site": site or url,
                                "url": url,
                                "status": "unchecked",
                                "matched": None,
                                "occurrences": 0,
                                "inner_text_error": str(e),
                            }
                except TimeoutError:
                    # Deadline hit before all hits verified — leave the rest
                    # unchecked rather than blocking the stream further.
                    pass
                finally:
                    # Must NOT wait on pending futures (that is the stall we're
                    # avoiding); abandon them and tear the pool down immediately.
                    pool.shutdown(wait=False, cancel_futures=True)
                # Any hit that didn't finish before the deadline stays unchecked.
                for idx, v in enumerate(verified):
                    if v is None:
                        site, url = _parse_sherlock_hit(hits[idx])
                        verified[idx] = {
                            "site": site or url,
                            "url": url,
                            "status": "unchecked",
                            "matched": None,
                            "occurrences": 0,
                            "inner_text_error": "verify deadline exceeded",
                        }
                found.append({"username": username, "sites": verified})
            else:
                found.append({
                    "username": username,
                    "sites": [
                        {"site": _parse_sherlock_hit(h)[0], "url": _parse_sherlock_hit(h)[1]}
                        for h in hits
                    ],
                })
        except subprocess.TimeoutExpired:
            found.append({"username": username, "sites": [], "error": "timed out"})
        except Exception as e:  # noqa: BLE001
            found.append({"username": username, "sites": [], "error": str(e)})

    return {"found": found, "usernames": list(usernames), "output": "\n".join(output)}


def cdp_targets(port: int = 9222) -> list:
    """List Chrome remote-debugging targets (GET /json)."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cdp_extract_text(url: str | None = None, port: int = 9222, wait_ms: int = 1500) -> dict:
    """Extract ``document.body.innerText`` from a Chrome CDP page target.

    Requires Chrome running with ``--remote-debugging-port=<port>``.
    """
    import websocket  # websocket-client (bundled dependency)

    targets = cdp_targets(port)
    page = next((t for t in targets if t.get("type") == "page"), None)
    if not page:
        return {"error": f"No page target found on CDP port {port}. Launch Chrome with --remote-debugging-port={port}."}

    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)
    try:
        if url:
            ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
            time.sleep(wait_ms / 1000.0)
        ws.send(json.dumps({
            "id": 2,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "document && document.body ? document.body.innerText : ''",
                "returnByValue": True,
            },
        }))
        text = ""
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                text = (msg.get("result", {}).get("result", {}).get("value") or "")
                break
        return {"url": url or page.get("url", ""), "text": text}
    finally:
        ws.close()


def research_osint(
    query: str | None = None,
    usernames=None,
    open_urls=None,
    cdp_url: str | None = None,
    cdp_port: int = 9222,
) -> dict:
    """Orchestrate the OSINT procedure into a single findings payload.

    ``open_urls`` are left for the caller to fetch with web_fetch; Sherlock and
    the CDP walled-page extraction are handled here.
    """
    findings: dict = {"query": query, "username_hits": [], "walled_text": None, "open_urls": list(open_urls or [])}

    if usernames:
        findings["username_hits"] = run_sherlock(usernames)

    if cdp_url:
        try:
            findings["walled_text"] = cdp_extract_text(cdp_url, cdp_port)
        except Exception as e:  # noqa: BLE001
            findings["walled_text"] = {"error": str(e)}

    return findings
