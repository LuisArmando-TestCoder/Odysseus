# src/cost_ledger.py
"""Per-chat API cost ledger.

Every billable call (chat model, voice pipeline, compression, JEV, GPT-Live)
is recorded here against the chat session it served. The ledger is a SIDECAR
store (``data/cost_ledger/<id>.json``) — it is never written into session
history, so it can never leak into any model's context.

Attribution uses a context variable: callers wrap work in ``cost_scope(...)``
(or call ``set_cost_scope``) and every LLM call made inside — including
background tasks spawned from it — is charged to that chat.

Pricing:
- OpenRouter returns the exact charged cost per call (``usage.cost``). We add
  the configurable credit top-up fee (``openrouter_fee_pct``, default 5.5%).
- GPT-Live (OpenAI direct) reports token usage per response; it is priced with
  editable per-1M-token rates in settings (``realtime_price_*``).
- Anything else is recorded with its token counts and ``usd: None`` (unpriced).
"""

import contextvars
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 200
_UNATTRIBUTED = "_unattributed"

_scope_session: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("cost_session", default=None)
_scope_source: contextvars.ContextVar[str] = contextvars.ContextVar("cost_source", default="chat")
_scope_owner: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("cost_owner", default=None)

_lock = threading.Lock()

DEFAULT_RATES = {
    # USD per 1M tokens — editable in settings; verify against current OpenAI pricing.
    "realtime_price_audio_in": 32.0,
    "realtime_price_audio_out": 64.0,
    "realtime_price_text_in": 4.0,
    "realtime_price_text_out": 16.0,
    "realtime_price_cached_in": 0.40,
}


# ── Scope ────────────────────────────────────────────────────────────────────

def set_cost_scope(session_id: Optional[str], source: Optional[str] = None, owner: Optional[str] = None):
    if session_id is not None:
        _scope_session.set(session_id or None)
    if source:
        _scope_source.set(source)
    if owner is not None:
        _scope_owner.set(owner or None)


@contextmanager
def cost_scope(session_id: Optional[str] = None, source: Optional[str] = None, owner: Optional[str] = None):
    tokens = []
    if session_id is not None:
        tokens.append((_scope_session, _scope_session.set(session_id or None)))
    if source:
        tokens.append((_scope_source, _scope_source.set(source)))
    if owner is not None:
        tokens.append((_scope_owner, _scope_owner.set(owner or None)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            try:
                var.reset(tok)
            except ValueError:
                pass  # reset from a different context (async generator) — harmless


def current_source() -> str:
    return _scope_source.get()


# ── Storage ──────────────────────────────────────────────────────────────────

def _dir() -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "cost_ledger")
    os.makedirs(d, exist_ok=True)
    return d


def _path(session_id: str) -> str:
    safe = hashlib.sha256((session_id or _UNATTRIBUTED).encode()).hexdigest()[:32]
    return os.path.join(_dir(), safe + ".json")


def _load(session_id: str) -> Dict[str, Any]:
    try:
        with open(_path(session_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"session_id": session_id, "owner": None, "total_usd": 0.0, "base_usd": 0.0,
                "fee_usd": 0.0, "calls": 0, "unpriced_calls": 0, "by_source": {}, "entries": []}


def _save(session_id: str, d: Dict[str, Any]):
    try:
        from core.atomic_io import atomic_write_json
        atomic_write_json(_path(session_id), d)
    except Exception as e:
        logger.warning(f"cost ledger save failed: {e}")


def _settings() -> Dict[str, Any]:
    try:
        from src.settings import load_settings
        return load_settings() or {}
    except Exception:
        return {}


def _fee_pct() -> float:
    try:
        return max(0.0, float(_settings().get("openrouter_fee_pct", 5.5)))
    except (TypeError, ValueError):
        return 5.5


def _rate(key: str) -> float:
    try:
        return float(_settings().get(key, DEFAULT_RATES[key]))
    except (TypeError, ValueError):
        return DEFAULT_RATES[key]


# ── Recording ────────────────────────────────────────────────────────────────

def record(
    *,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    base_usd: Optional[float] = None,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
    apply_fee: Optional[bool] = None,
    detail: Optional[Dict[str, Any]] = None,
):
    """Charge one call to a chat. Never raises."""
    try:
        sid = session_id if session_id is not None else _scope_session.get()
        sid = sid or _UNATTRIBUTED
        src = source or _scope_source.get() or "chat"
        own = owner if owner is not None else _scope_owner.get()
        if apply_fee is None:
            apply_fee = provider == "openrouter"
        fee = 0.0
        if base_usd is not None:
            base_usd = max(0.0, float(base_usd))
            if apply_fee:
                fee = base_usd * _fee_pct() / 100.0
        total = None if base_usd is None else base_usd + fee

        with _lock:
            d = _load(sid)
            if own and not d.get("owner"):
                d["owner"] = own
            d["calls"] = int(d.get("calls", 0)) + 1
            if total is None:
                d["unpriced_calls"] = int(d.get("unpriced_calls", 0)) + 1
            else:
                d["base_usd"] = float(d.get("base_usd", 0.0)) + base_usd
                d["fee_usd"] = float(d.get("fee_usd", 0.0)) + fee
                d["total_usd"] = float(d.get("total_usd", 0.0)) + total
            bs = d.setdefault("by_source", {}).setdefault(src, {"usd": 0.0, "calls": 0, "in": 0, "out": 0})
            bs["calls"] += 1
            bs["in"] += int(input_tokens or 0)
            bs["out"] += int(output_tokens or 0)
            if total is not None:
                bs["usd"] += total
            entry = {"ts": time.time(), "source": src, "provider": provider, "model": model,
                     "in": int(input_tokens or 0), "out": int(output_tokens or 0),
                     "base_usd": base_usd, "fee_usd": fee if total is not None else None, "usd": total}
            if detail:
                entry["detail"] = detail
            d.setdefault("entries", []).append(entry)
            d["entries"] = d["entries"][-_MAX_ENTRIES:]
            d["updated_at"] = time.time()
            _save(sid, d)
    except Exception as e:
        logger.warning(f"cost ledger record failed: {e}")


def record_openrouter_usage(model: str, usage: Dict[str, Any], source: Optional[str] = None):
    """Record from an OpenRouter ``usage`` block (has ``cost`` when usage.include=true)."""
    if not isinstance(usage, dict):
        return
    cost = usage.get("cost")
    record(provider="openrouter", model=model or "",
           input_tokens=usage.get("prompt_tokens", 0) or 0,
           output_tokens=usage.get("completion_tokens", 0) or 0,
           base_usd=float(cost) if isinstance(cost, (int, float)) else None,
           source=source)


def price_realtime_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    """Price a GPT-Live ``response.done`` usage block."""
    u = usage or {}
    idt = u.get("input_token_details") or {}
    odt = u.get("output_token_details") or {}
    cached = int(idt.get("cached_tokens") or 0)
    a_in = int(idt.get("audio_tokens") or 0)
    t_in = int(idt.get("text_tokens") or 0)
    a_out = int(odt.get("audio_tokens") or 0)
    t_out = int(odt.get("text_tokens") or 0)
    if not (a_in or t_in or a_out or t_out):
        # No modality split reported — treat everything as text.
        t_in = int(u.get("input_tokens") or 0)
        t_out = int(u.get("output_tokens") or 0)
    # Cached tokens are a subset of input; bill them at the cached rate.
    cached_audio = min(cached, a_in)
    cached_text = min(cached - cached_audio, t_in)
    usd = (
        (a_in - cached_audio) * _rate("realtime_price_audio_in")
        + (t_in - cached_text) * _rate("realtime_price_text_in")
        + (cached_audio + cached_text) * _rate("realtime_price_cached_in")
        + a_out * _rate("realtime_price_audio_out")
        + t_out * _rate("realtime_price_text_out")
    ) / 1_000_000
    return {"usd": usd, "in": a_in + t_in, "out": a_out + t_out,
            "detail": {"audio_in": a_in, "text_in": t_in, "cached_in": cached,
                       "audio_out": a_out, "text_out": t_out}}


# ── Reading ──────────────────────────────────────────────────────────────────

def summary(session_id: str) -> Dict[str, Any]:
    d = _load(session_id)
    return {k: d.get(k) for k in ("session_id", "owner", "total_usd", "base_usd", "fee_usd",
                                   "calls", "unpriced_calls", "by_source", "updated_at")}


def detail(session_id: str, limit: int = 50) -> Dict[str, Any]:
    d = _load(session_id)
    out = summary(session_id)
    out["entries"] = (d.get("entries") or [])[-limit:]
    return out


def totals_for(session_ids) -> Dict[str, Dict[str, Any]]:
    out = {}
    for sid in session_ids:
        if not sid or not os.path.exists(_path(sid)):
            continue
        d = _load(sid)
        out[sid] = {"usd": round(float(d.get("total_usd", 0.0)), 6),
                    "calls": d.get("calls", 0), "unpriced": d.get("unpriced_calls", 0)}
    return out
