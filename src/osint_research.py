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


def run_sherlock(usernames, timeout: int = 30) -> dict:
    """Run Sherlock against one or more usernames and return structured hits.

    Returns ``{"found": [{"username": ..., "sites": ["[+] Site: url", ...]}], ...}``.
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
            found.append({"username": username, "sites": hits})
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
