"""
service_health.py — notice when a backing service degrades, before you do.

Odysseus leans on services that can quietly go away: the SearXNG instance
behind web search, ChromaDB behind documents/memory recall, the ntfy server
that carries reminders, the IMAP/SMTP servers behind each email account, the
LLM endpoints the chat / utility / task / research models run on, and its own
data directory. When one of them is down the app keeps "working" but
answers get worse, reminders never arrive, and nothing says why.

This module probes them, keeps a small per-owner state file, and reports
*transitions* only:

* ``down``      — a service failed ``confirm`` checks in a row (one blip is
                  not an outage), reported once per outage, with the error and
                  a concrete fix;
* ``recovered`` — a service we reported down answers again.

``health_brief_lines`` reads the same state (no new probes) for the daily
brief. ``collect_probes`` builds the probe list from settings, enabled
integrations and the owner's own email accounts; everything it touches is
injectable, so tests run without network, SQLAlchemy or ChromaDB.

Probes are reachability checks only: an HTTP probe never reads the body and
does not follow redirects, every URL passes ``check_outbound_url`` first, and
credentials / query strings are stripped from anything shown to the user.
Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

DEFAULT_CONFIRM = 2          # consecutive failures before "down" is reported
HTTP_TIMEOUT = 5.0
TCP_TIMEOUT = 4.0
ALERT_LIMIT = 8
META_KEY = "_meta"           # state-file entry holding {"checked_at": ts}
STALE_AFTER = 2 * 3600       # no check for this long → status "stale"
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Search providers that need an API key, and the settings field holding it
# (mirrors services/search/providers.py:_get_provider_key).
SEARCH_KEY_FIELDS = {
    "langsearch": "langsearch_api_key",
    "brave": "brave_api_key",
    "google_pse": "google_pse_key",
    "tavily": "tavily_api_key",
    "serper": "serper_api_key",
    "scrapebadger": "scrapebadger_api_key",
}


def _clean(text: Any, limit: int = 160) -> str:
    s = " ".join(_CTRL_RE.sub(" ", str(text or "")).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def redact_url(url: str) -> str:
    """Drop userinfo, query and fragment so a URL is safe to show."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return "(invalid URL)"
    if not p.scheme or not p.hostname:
        return _clean(url, 80)
    host = p.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = p.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port else host
    return urlunsplit((p.scheme, netloc, p.path, "", ""))


def _result(key: str, label: str, ok: Optional[bool], detail: str = "",
            fix: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "ok": ok,
            "detail": _clean(detail), "fix": _clean(fix, 200)}


# ── probe primitives ────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None  # surface the 3xx as an HTTPError: reachable, not followed


def _default_http_get(url: str, timeout: float) -> int:
    """GET ``url`` and return the status code (body is not read)."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "Odysseus-HealthCheck/1"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def _default_tcp_connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        pass


def _error_text(e: BaseException) -> str:
    reason = getattr(e, "reason", None)
    if isinstance(reason, BaseException):
        e = reason
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timed out"
    if isinstance(e, ConnectionRefusedError):
        return "connection refused"
    if isinstance(e, socket.gaierror):
        return "host does not resolve"
    return _clean(str(e) or e.__class__.__name__, 100)


def probe_http(key: str, label: str, url: str, *, fix: str = "",
               http_get: Optional[Callable[[str, float], int]] = None,
               url_check: Optional[Callable[[str], Any]] = None,
               timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    """Reachable = any HTTP answer below 500 (401/403/404 still prove it is up)."""
    shown = redact_url(url)
    if url_check is None:
        from src.url_safety import check_outbound_url as url_check
    try:
        safe, why = url_check(url)
    except Exception as e:  # resolver blew up
        safe, why = False, _error_text(e)
    if not safe:
        return _result(key, label, False, f"{shown}: {why}", fix)
    try:
        status = (http_get or _default_http_get)(url, timeout)
    except Exception as e:
        return _result(key, label, False, f"{shown}: {_error_text(e)}", fix)
    if status >= 500:
        return _result(key, label, False, f"{shown}: HTTP {status}", fix)
    return _result(key, label, True, shown)


def probe_tcp(key: str, label: str, host: str, port: int, *, fix: str = "",
              tcp_connect: Optional[Callable[[str, int, float], None]] = None,
              timeout: float = TCP_TIMEOUT) -> Dict[str, Any]:
    where = f"{_clean(host, 80)}:{port}"
    try:
        (tcp_connect or _default_tcp_connect)(host, int(port), timeout)
    except Exception as e:
        return _result(key, label, False, f"{where}: {_error_text(e)}", fix)
    return _result(key, label, True, where)


# ── LLM endpoints ───────────────────────────────────────────────────────────

LLM_PROBE_LIMIT = 6   # when no model role is configured, probe at most this many
ANTHROPIC_VERSION = "2023-06-01"

# (settings key, role label) for the model roles that pick an endpoint.
MODEL_ROLES = (
    ("default_endpoint_id", "Chat"),
    ("utility_endpoint_id", "Utility"),
    ("task_endpoint_id", "Task"),
    ("research_endpoint_id", "Research"),
)
MODEL_FALLBACK_KEYS = (
    ("default_model_fallbacks", "Chat fallback"),
    ("utility_model_fallbacks", "Utility fallback"),
)


def _host_is(host: str, domain: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def model_refs(get: Callable[[str], Any]) -> List[tuple]:
    """``[(role_label, endpoint_id), ...]`` for every model role set in
    settings, fallbacks included. ``get(key)`` resolves a setting (per-user
    where the app supports it)."""
    refs: List[tuple] = []
    for key, role in MODEL_ROLES:
        try:
            eid = str(get(key) or "").strip()
        except Exception:
            eid = ""
        if eid:
            refs.append((role, eid))
    for key, role in MODEL_FALLBACK_KEYS:
        try:
            chain = get(key) or []
        except Exception:
            chain = []
        if not isinstance(chain, list):
            continue
        for entry in chain:
            eid = str((entry or {}).get("endpoint_id") or "").strip() if isinstance(entry, dict) else ""
            if eid:
                refs.append((role, eid))
    return refs


def llm_probe_request(base_url: str, api_key: str = "") -> tuple:
    """``(url, headers)`` for a cheap model-list call on an LLM endpoint.

    Native Ollama (``…/api`` or ollama.com) → ``/api/tags``; Anthropic →
    ``/v1/models`` with ``x-api-key``; everything else is OpenAI-compatible
    → ``{base}/models`` with a Bearer token.
    """
    base = str(base_url or "").strip().rstrip("/")
    try:
        p = urlsplit(base)
    except ValueError:
        return base, {}
    host = p.hostname or ""
    path = (p.path or "").rstrip("/")
    headers: Dict[str, str] = {}
    key = str(api_key or "").strip()
    if _host_is(host, "anthropic.com"):
        root = base if path.endswith("/v1") else base + "/v1"
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        return root + "/models", headers
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if path == "/api" or path.endswith("/api") or (_host_is(host, "ollama.com") and "/v1" not in path):
        root = base if path.endswith("/api") else base + "/api"
        return root + "/tags", headers
    for tail in ("/chat/completions", "/completions", "/models"):
        if path.endswith(tail):
            base = base[: -len(tail)]
            break
    return base + "/models", headers


def _default_http_get_headers(url: str, headers: Dict[str, str], timeout: float) -> int:
    opener = urllib.request.build_opener(_NoRedirect)
    h = {"User-Agent": "Odysseus-HealthCheck/1"}
    h.update(headers or {})
    req = urllib.request.Request(url, method="GET", headers=h)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def probe_llm(key: str, label: str, base_url: str, api_key: str = "", *,
              http_get_headers: Optional[Callable[[str, Dict[str, str], float], int]] = None,
              url_check: Optional[Callable[[str], Any]] = None,
              timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    """Is this LLM endpoint answering, and does it accept our key?

    Unlike ``probe_http``, 401/403 is a failure here: the server is up but
    every model call will be refused. 404 (no model-list route) and 429
    (rate limited) still count as up. The key is sent only to the endpoint it
    is configured for and never appears in the result.
    """
    url, headers = llm_probe_request(base_url, api_key)
    shown = redact_url(url)
    fix = "Check the endpoint is running and its URL in Settings → Add Models."
    if url_check is None:
        from src.url_safety import check_outbound_url as url_check
    try:
        safe, why = url_check(url)
    except Exception as e:
        safe, why = False, _error_text(e)
    if not safe:
        return _result(key, label, False, f"{shown}: {why}", fix)
    try:
        status = (http_get_headers or _default_http_get_headers)(url, headers, timeout)
    except Exception as e:
        return _result(key, label, False, f"{shown}: {_error_text(e)}", fix)
    if status in (401, 403):
        if str(api_key or "").strip():
            return _result(key, label, False, f"{shown}: API key rejected (HTTP {status})",
                           "Update the API key for this endpoint in Settings → Add Models "
                           "(it may be expired, revoked or out of credit).")
        return _result(key, label, False, f"{shown}: needs an API key (HTTP {status})",
                       "Add the provider's API key to this endpoint in Settings → Add Models.")
    if status >= 500:
        return _result(key, label, False, f"{shown}: HTTP {status}", fix)
    return _result(key, label, True, shown)


def llm_probes(endpoints: Iterable[Dict[str, Any]], refs: Iterable[tuple], *,
               http_get_headers=None, url_check=None,
               limit: int = LLM_PROBE_LIMIT) -> List[Dict[str, Any]]:
    """Probe the LLM endpoints your model roles point at.

    ``endpoints`` must already be scoped to what the owner can see. A role
    pointing at an endpoint that was deleted or disabled is reported as its
    own failure (every request in that role will fail). With no role set,
    the first ``limit`` enabled LLM endpoints are probed instead.
    """
    eps = [e for e in endpoints if str(e.get("model_type") or "llm").lower() == "llm"]
    by_id = {str(e.get("id") or ""): e for e in eps}
    refs = list(refs)
    roles: Dict[str, List[str]] = {}
    order: List[str] = []
    out: List[Dict[str, Any]] = []
    broken: Dict[str, Dict[str, Any]] = {}
    for role, eid in refs:
        ep = by_id.get(eid)
        if ep is None or not ep.get("enabled", True):
            why = "no longer exists" if ep is None else f"({_clean(ep.get('name'), 40)}) is disabled"
            b = broken.setdefault(eid, {"roles": [], "why": why})
            if role not in b["roles"]:
                b["roles"].append(role)
            continue
        if eid not in roles:
            roles[eid] = []
            order.append(eid)
        if role not in roles[eid]:
            roles[eid].append(role)
    for eid, b in broken.items():
        names = ", ".join(b["roles"])
        out.append(_result(f"model-ref:{_clean(eid, 60)}", f"{names} model endpoint", False,
                           f"the endpoint selected for {names} {b['why']}",
                           "Pick a working model in Settings → AI Defaults, or re-enable the "
                           "endpoint in Settings → Add Models."))
    if not refs:
        order = [str(e.get("id") or "") for e in eps if e.get("enabled", True)][: max(0, int(limit))]
    for eid in order:
        ep = by_id[eid]
        name = _clean(ep.get("name") or "endpoint", 40)
        role_txt = f" ({', '.join(roles[eid])})" if roles.get(eid) else ""
        out.append(probe_llm(f"llm:{_clean(eid, 60)}", f"Model {name}{role_txt}",
                             str(ep.get("base_url") or ""), str(ep.get("api_key") or ""),
                             http_get_headers=http_get_headers, url_check=url_check))
    return out


# ── what to probe ───────────────────────────────────────────────────────────

def collect_probes(*, settings: Dict[str, Any],
                   integrations: Iterable[Dict[str, Any]] = (),
                   email_accounts: Iterable[Dict[str, Any]] = (),
                   chroma: Optional[Dict[str, Any]] = None,
                   storage_check: Optional[Callable[[], Dict[str, Any]]] = None,
                   default_search_url: str = "http://localhost:8080",
                   model_endpoints: Optional[Iterable[Dict[str, Any]]] = None,
                   model_refs: Iterable[tuple] = (),
                   http_get=None, tcp_connect=None, url_check=None,
                   http_get_headers=None) -> List[Dict[str, Any]]:
    """Run every applicable probe and return their results.

    ``ok`` is True/False, or None when the service is not in use (skipped).
    ``email_accounts`` and ``model_endpoints`` must already be scoped to the
    owner; ``model_endpoints=None`` skips the LLM endpoint probes.
    """
    out: List[Dict[str, Any]] = []
    http = dict(http_get=http_get, url_check=url_check)

    # Storage: the data directory must stay writable (DB + files live there).
    if storage_check is not None:
        try:
            rep = storage_check() or {}
            checks = rep.get("checks") or {}
            bad = [f"{k}: {_clean(v.get('error') or 'failed', 100)}"
                   for k, v in checks.items() if isinstance(v, dict) and not v.get("ok")]
            out.append(_result("storage", "Storage (database + data folder)", not bad,
                               "; ".join(bad) or "ok",
                               "Check free disk space and permissions on the data/ folder."))
        except Exception as e:
            out.append(_result("storage", "Storage (database + data folder)", False,
                               _error_text(e), "Check free disk space and permissions on the data/ folder."))

    # Web search.
    provider = str(settings.get("search_provider") or "searxng").strip().lower()
    if provider == "searxng":
        url = (str(settings.get("search_url") or "").strip() or default_search_url).rstrip("/")
        out.append(probe_http(
            "search", "Web search (SearXNG)", url,
            fix="Start SearXNG (e.g. `docker compose up -d searxng`) or pick another "
                "provider in Settings → Search.", **http))
    elif provider in SEARCH_KEY_FIELDS:
        has_key = bool(str(settings.get(SEARCH_KEY_FIELDS[provider]) or "").strip())
        out.append(_result(
            "search", f"Web search ({provider})", has_key,
            "API key set" if has_key else "no API key configured",
            "Add the API key in Settings → Search, or switch provider."))

    # Vector store (documents + memory recall). Optional dependency.
    if chroma and chroma.get("installed"):
        out.append(probe_tcp(
            "vector", "Vector store (ChromaDB)", str(chroma.get("host") or "localhost"),
            int(chroma.get("port") or 8100),
            fix="Start ChromaDB (e.g. `docker compose up -d chromadb`) or set "
                "CHROMADB_HOST / CHROMADB_PORT.", tcp_connect=tcp_connect))

    # Reminder delivery via ntfy — only when reminders are routed there.
    if str(settings.get("reminder_channel") or "").strip().lower() == "ntfy":
        ntfy = next((i for i in integrations
                     if i.get("preset") == "ntfy" and i.get("enabled", True)
                     and str(i.get("base_url") or "").strip()), None)
        if ntfy is None:
            out.append(_result("ntfy", "Reminders (ntfy)", False,
                               "reminders go to ntfy but no ntfy integration is enabled",
                               "Add/enable the ntfy integration, or change the reminder channel."))
        else:
            base = str(ntfy["base_url"]).strip().rstrip("/")
            out.append(probe_http("ntfy", "Reminders (ntfy)", f"{base}/v1/health",
                                  fix="Check the ntfy server is running and reachable from Odysseus.",
                                  **http))

    # Email: each enabled account's IMAP and SMTP endpoints.
    for acct in email_accounts:
        if not acct.get("enabled", True):
            continue
        aid = str(acct.get("id") or "")
        name = _clean(acct.get("name") or "account", 40)
        for proto, dport in (("imap", 993), ("smtp", 465)):
            host = str(acct.get(f"{proto}_host") or "").strip()
            if not host:
                continue
            port = acct.get(f"{proto}_port") or dport
            out.append(probe_tcp(
                f"email:{aid}:{proto}", f"Email {name} ({proto.upper()})", host, int(port),
                fix=f"Check the {proto.upper()} server/port in Settings → Email, "
                    "or your network/VPN.", tcp_connect=tcp_connect))

    # LLM endpoints behind the model roles (chat, utility, task, research).
    if model_endpoints is not None:
        out.extend(llm_probes(model_endpoints, model_refs,
                              http_get_headers=http_get_headers, url_check=url_check))
    return out


# ── transitions + state ─────────────────────────────────────────────────────

def _state_path(owner: str) -> str:
    from src.constants import DATA_DIR
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(owner or "default"))
    return os.path.join(DATA_DIR, "service_health", f"{safe}.json")


def _load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save_state(path: str, state: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".service-health-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_health(owner: str, results: Iterable[Dict[str, Any]], *,
                  now: Optional[float] = None, confirm: int = DEFAULT_CONFIRM,
                  state_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fold probe results into the owner's state; return new transitions.

    A transition is ``{"kind": "down"|"recovered", "key", "label", "detail",
    "fix", "since", "duration"}``. Skipped services (``ok is None``) and
    services no longer probed are dropped from state silently.
    """
    now = time.time() if now is None else now
    confirm = max(1, int(confirm))
    path = state_path or _state_path(owner)
    state = _load_state(path)
    new_state: Dict[str, Any] = {}
    events: List[Dict[str, Any]] = []
    for r in results:
        if r.get("ok") is None:
            continue
        key = r["key"]
        prev = state.get(key) or {}
        if r["ok"]:
            if prev.get("alerted"):
                since = float(prev.get("since") or now)
                events.append({"kind": "recovered", "key": key, "label": r["label"],
                               "detail": r.get("detail", ""), "fix": "",
                               "since": since, "duration": max(0.0, now - since)})
            new_state[key] = {"ok": True, "since": now if not prev.get("ok", False) else
                              float(prev.get("since") or now),
                              "fails": 0, "alerted": False, "label": r["label"]}
            continue
        was_down = prev and not prev.get("ok", True)
        since = float(prev.get("since") or now) if was_down else now
        fails = (int(prev.get("fails") or 0) if was_down else 0) + 1
        alerted = bool(prev.get("alerted")) if was_down else False
        if not alerted and fails >= confirm:
            alerted = True
            events.append({"kind": "down", "key": key, "label": r["label"],
                           "detail": r.get("detail", ""), "fix": r.get("fix", ""),
                           "since": since, "duration": max(0.0, now - since)})
        new_state[key] = {"ok": False, "since": since, "fails": fails, "alerted": alerted,
                          "label": r["label"], "detail": r.get("detail", ""),
                          "fix": r.get("fix", "")}
    new_state[META_KEY] = {"checked_at": now}
    if new_state != state:
        _save_state(path, new_state)
    return events


def _span(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 3600:
        m = max(1, int(round(s / 60)))
        return f"{m} min"
    if s < 86400:
        h = int(round(s / 3600))
        return f"{h} hour{'s' if h != 1 else ''}"
    d = int(round(s / 86400))
    return f"{d} day{'s' if d != 1 else ''}"


def format_health_alerts(events: List[Dict[str, Any]], limit: int = ALERT_LIMIT) -> str:
    down = [e for e in events if e["kind"] == "down"]
    up = [e for e in events if e["kind"] == "recovered"]
    head = []
    if down:
        head.append(f"{len(down)} service{'s' if len(down) != 1 else ''} down")
    if up:
        head.append(f"{len(up)} recovered")
    lines = [f"Service health: {', '.join(head)}."]
    for e in down[:limit]:
        line = f"- DOWN {e['label']}: {e['detail'] or 'unreachable'}"
        if e.get("fix"):
            line += f" Fix: {e['fix']}"
        lines.append(line)
    if len(down) > limit:
        lines.append(f"…and {len(down) - limit} more down")
    for e in up[:limit]:
        lines.append(f"- OK again {e['label']} (was down {_span(e['duration'])})")
    return "\n".join(lines)


def health_brief_lines(owner: str, *, now: Optional[float] = None,
                       confirm: int = DEFAULT_CONFIRM,
                       state_path: Optional[str] = None) -> List[str]:
    """Daily-brief section for services currently down (state only, no probes)."""
    now = time.time() if now is None else now
    state = _load_state(state_path or _state_path(owner))
    down = [(k, v) for k, v in state.items()
            if not k.startswith("_") and not v.get("ok", True) and int(v.get("fails") or 0) >= max(1, int(confirm))]
    if not down:
        return []
    down.sort(key=lambda kv: float(kv[1].get("since") or now))
    lines = ["Degraded services:"]
    for _k, v in down[:ALERT_LIMIT]:
        dur = _span(now - float(v.get("since") or now))
        lines.append(f"- {_clean(v.get('label'), 60)} — down {dur}: "
                     f"{_clean(v.get('detail'), 120) or 'unreachable'}")
    if len(down) > ALERT_LIMIT:
        lines.append(f"…and {len(down) - ALERT_LIMIT} more")
    return lines


# ── status summary (sidebar dot / GET /api/health/services) ────────────────

def _service_rows(items: Iterable[Dict[str, Any]], now: float,
                  confirm: int) -> List[Dict[str, Any]]:
    rows = []
    for v in items:
        ok = v.get("ok")
        if ok is None:
            continue
        fails = int(v.get("fails") or 0)
        if ok:
            status = "ok"
        else:
            status = "down" if fails >= confirm else "flaky"
        since = v.get("since")
        row = {"key": _clean(v.get("key"), 80), "label": _clean(v.get("label"), 60) or "Service",
               "status": status}
        if status != "ok":
            row["detail"] = _clean(v.get("detail"), 160) or "unreachable"
            row["fix"] = _clean(v.get("fix"), 200)
            if since is not None:
                row["since"] = float(since)
                row["for"] = _span(now - float(since))
        rows.append(row)
    order = {"down": 0, "flaky": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["label"].lower()))
    return rows


def summarize(rows: List[Dict[str, Any]], *, checked_at: Optional[float],
              now: float, live: bool = False) -> Dict[str, Any]:
    """Overall verdict for a list of service rows.

    ``status`` is ``down`` (a confirmed outage), ``flaky`` (one failed check —
    may be a blip), ``ok``, ``stale`` (checks have not run for STALE_AFTER —
    the Service Health task may be paused), or ``unknown`` (never checked).
    """
    down = sum(1 for r in rows if r["status"] == "down")
    flaky = sum(1 for r in rows if r["status"] == "flaky")
    if checked_at is None and not rows:
        status, label = "unknown", "Service health: not checked yet"
    elif down:
        status = "down"
        names = ", ".join(r["label"] for r in rows if r["status"] == "down")
        label = f"{down} service{'s' if down != 1 else ''} down: {names}"
    elif flaky:
        status = "flaky"
        label = f"{flaky} service{'s' if flaky != 1 else ''} failed the last check"
    elif not live and checked_at is not None and now - checked_at > STALE_AFTER:
        status = "stale"
        label = (f"Service health last checked {_span(now - checked_at)} ago — "
                 "is the Service Health task paused?")
    else:
        status = "ok"
        label = (f"All {len(rows)} services OK" if rows else "No services to check")
    return {"status": status, "label": label, "down": down, "flaky": flaky,
            "total": len(rows), "checked_at": checked_at, "live": live,
            "services": rows}


def health_status(owner: str, *, now: Optional[float] = None,
                  confirm: int = DEFAULT_CONFIRM,
                  state_path: Optional[str] = None) -> Dict[str, Any]:
    """Summary of the saved state (no probes) for the sidebar status dot."""
    now = time.time() if now is None else now
    confirm = max(1, int(confirm))
    state = _load_state(state_path or _state_path(owner))
    meta = state.get(META_KEY) or {}
    try:
        checked_at = float(meta["checked_at"]) if "checked_at" in meta else None
    except (TypeError, ValueError):
        checked_at = None
    items = [dict(v, key=k) for k, v in state.items() if not k.startswith("_")]
    return summarize(_service_rows(items, now, confirm), checked_at=checked_at, now=now)


def live_status(results: Iterable[Dict[str, Any]], *,
                now: Optional[float] = None) -> Dict[str, Any]:
    """Summary of fresh probe results ("Check now"). Not saved to state, so
    the scheduled task still owns the down/recovered notifications; a failure
    seen here counts as ``down`` because the user is looking at it now."""
    now = time.time() if now is None else now
    items = [dict(r, fails=0 if r.get("ok") else 1, since=None) for r in results]
    return summarize(_service_rows(items, now, 1), checked_at=now, now=now, live=True)


# ── live wiring (lazy imports keep the module importable without deps) ──────

def _load_owner_email_accounts(owner: str) -> List[Dict[str, Any]]:
    from core.database import SessionLocal, EmailAccount
    from src.auth_helpers import owner_filter
    try:
        from core.auth import AuthManager
        allow_null = not AuthManager().is_configured
    except Exception:
        allow_null = False
    db = SessionLocal()
    try:
        q = db.query(EmailAccount)
        if owner:
            q = owner_filter(q, EmailAccount, owner, include_shared=allow_null)
        return [{"id": a.id, "name": a.name, "enabled": bool(a.enabled),
                 "imap_host": a.imap_host, "imap_port": a.imap_port,
                 "smtp_host": getattr(a, "smtp_host", ""),
                 "smtp_port": getattr(a, "smtp_port", None)} for a in q.all()]
    finally:
        db.close()


def _load_owner_model_endpoints(owner: str) -> List[Dict[str, Any]]:
    from core.database import SessionLocal, ModelEndpoint
    from src.auth_helpers import owner_filter
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint)
        if owner:
            q = owner_filter(q, ModelEndpoint, owner)  # own + shared endpoints
        return [{"id": e.id, "name": e.name, "base_url": e.base_url,
                 "api_key": e.api_key or "", "enabled": bool(e.is_enabled),
                 "model_type": e.model_type or "llm"} for e in q.all()]
    finally:
        db.close()


def _owner_setting_getter(owner: str, settings: Dict[str, Any]) -> Callable[[str], Any]:
    try:
        from src.settings import get_user_setting
    except Exception:
        return settings.get

    def get(key: str) -> Any:
        try:
            return get_user_setting(key, owner, settings.get(key))
        except Exception:
            return settings.get(key)
    return get


def run_live_probes(owner: str) -> List[Dict[str, Any]]:
    """Probe this install's services for ``owner`` (blocking; run in a thread)."""
    from src.settings import load_settings
    settings = load_settings()
    try:
        from src.integrations import load_integrations
        integrations = load_integrations()
    except Exception:
        integrations = []
    try:
        accounts = _load_owner_email_accounts(owner)
    except Exception:
        accounts = []
    try:
        import importlib.util
        installed = importlib.util.find_spec("chromadb") is not None
    except (ImportError, ValueError):
        installed = False
    chroma = {"installed": installed,
              "host": os.getenv("CHROMADB_HOST", "localhost"),
              "port": int(os.getenv("CHROMADB_PORT", "8100") or 8100)}
    try:
        from src.constants import SEARXNG_INSTANCE
    except Exception:
        SEARXNG_INSTANCE = "http://localhost:8080"
    try:
        from src.readiness import check_readiness
    except Exception:
        check_readiness = None
    try:
        endpoints = _load_owner_model_endpoints(owner)
    except Exception:
        endpoints = None  # DB unavailable — storage probe reports it
    refs = model_refs(_owner_setting_getter(owner, settings)) if endpoints is not None else []
    return collect_probes(settings=settings, integrations=integrations,
                          email_accounts=accounts, chroma=chroma,
                          storage_check=check_readiness,
                          default_search_url=SEARXNG_INSTANCE,
                          model_endpoints=endpoints, model_refs=refs)
