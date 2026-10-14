# src/decisions.py
"""JEV Decisions client (OpenRouter Decisions API).

JEV (``~typesafe/jev-latest``) does not generate text. It answers narrow, typed
questions about a *state* (string / object / array) and returns calibrated
probabilities:

  - ``noul``   — yes/no probability in [0, 1]
  - ``choice`` — pick one of the criteria keys (+ full distribution)
  - ``score``  — position on an ordered rubric (+ full distribution)

Our code owns the workflow and acts on the answers. The voice pipeline uses it
as the *decision maker* — whether a live conversation needs something to be
sent to the chat, what kind of action it is, and how urgent.

The OpenRouter key is shared with STT via ``resolve_openrouter_key``.
"""

import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_JEV_MODEL = "~typesafe/jev-latest"

# Keep JEV calls short — it sits on the hot path between the user speaking and
# the message appearing in the chat queue.
_TIMEOUT = httpx.Timeout(connect=4.0, read=20.0, write=10.0, pool=5.0)


class DecisionsUnavailable(RuntimeError):
    """Raised when JEV can't be reached / isn't configured. Callers fall back."""


def jev_settings() -> Dict[str, Any]:
    try:
        from src.settings import load_settings
        s = load_settings()
    except Exception:
        s = {}
    return {
        "enabled": bool(s.get("jev_enabled", True)),
        "model": (s.get("jev_model") or DEFAULT_JEV_MODEL).strip() or DEFAULT_JEV_MODEL,
    }


def jev_available() -> bool:
    if not jev_settings()["enabled"]:
        return False
    try:
        from src.endpoint_resolver import resolve_openrouter_key
        return bool(resolve_openrouter_key())
    except Exception:
        return False


def _normalize_answer(raw: Any) -> Dict[str, Any]:
    """Flatten an answer into {type, value, confidence, probabilities}."""
    if not isinstance(raw, dict):
        return {"type": "unknown", "value": raw, "confidence": None, "probabilities": None}
    t = raw.get("type")
    out: Dict[str, Any] = {"type": t, "probabilities": raw.get("probabilities")}
    if t == "noul":
        p = raw.get("noul")
        try:
            p = float(p)
        except (TypeError, ValueError):
            p = None
        out["value"] = p
        out["confidence"] = None if p is None else max(p, 1.0 - p)
    elif t == "choice":
        out["value"] = raw.get("choice")
        probs = raw.get("probabilities") or {}
        try:
            out["confidence"] = float(probs.get(out["value"])) if isinstance(probs, dict) else None
        except (TypeError, ValueError):
            out["confidence"] = None
    elif t == "score":
        out["value"] = raw.get("score")
        out["confidence"] = None
        probs = raw.get("probabilities")
        if isinstance(probs, (list, tuple)) and probs:
            try:
                out["confidence"] = float(max(probs))
            except (TypeError, ValueError):
                pass
        elif isinstance(probs, dict) and probs:
            try:
                out["confidence"] = float(max(probs.values()))
            except (TypeError, ValueError):
                pass
    else:
        out["value"] = raw
        out["confidence"] = None
    return out


def _jev_payload(state: Any, questions: Dict[str, Dict[str, Any]],
                 model: Optional[str]) -> Dict[str, Any]:
    """Build the JEV request payload + headers (shared by sync/async clients)."""
    cfg = jev_settings()
    if not cfg["enabled"]:
        raise DecisionsUnavailable("JEV disabled in settings")
    from src.endpoint_resolver import resolve_openrouter_key
    key = resolve_openrouter_key()
    if not key:
        raise DecisionsUnavailable("No OpenRouter API key configured")
    payload = {
        "model": model or cfg["model"],
        "state": state,
        "questions": questions,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/pewdiepie-archdaemon/odysseus",
        "X-Title": "Odysseus",
    }
    return payload, headers


def _jev_parse(data: Any, model: str) -> Dict[str, Dict[str, Any]]:
    """Parse a JEV response body into normalized answers (shared by sync/async)."""
    # Accept either {answers: {...}} or {decision: {answers: {...}}}.
    answers = data.get("answers")
    if answers is None and isinstance(data.get("decision"), dict):
        answers = data["decision"].get("answers")
    if not isinstance(answers, dict):
        raise DecisionsUnavailable(f"JEV unexpected schema: {str(data)[:300]}")
    try:
        from src.cost_ledger import record
        u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = u.get("cost", data.get("cost"))
        record(provider="openrouter", model=model, source="jev",
               input_tokens=u.get("prompt_tokens", 0) or 0,
               output_tokens=u.get("completion_tokens", 0) or 0,
               base_usd=float(cost) if isinstance(cost, (int, float)) else None)
    except Exception:
        pass
    return {name: _normalize_answer(ans) for name, ans in answers.items()}


async def jev_decide(state: Any, questions: Dict[str, Dict[str, Any]],
                     model: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Ask JEV typed questions about ``state``.

    Returns ``{question_name: {type, value, confidence, probabilities}}``.
    Raises ``DecisionsUnavailable`` when disabled / no key / upstream error so
    the caller can degrade gracefully.
    """
    payload, headers = _jev_payload(state, questions, model)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(DECISIONS_URL, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise DecisionsUnavailable(f"JEV unreachable: {e}")
    if not r.is_success:
        raise DecisionsUnavailable(f"JEV HTTP {r.status_code}: {r.text[:300]}")
    try:
        data = r.json()
    except json.JSONDecodeError:
        raise DecisionsUnavailable("JEV returned non-JSON")
    return _jev_parse(data, payload["model"])


def jev_decide_sync(state: Any, questions: Dict[str, Dict[str, Any]],
                    model: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Synchronous twin of :func:`jev_decide` for the sync ``llm_call`` path.

    Uses a blocking ``httpx.Client`` so it can run inside FastAPI's threadpool
    without an event loop. Same semantics and error contract as the async form.
    """
    payload, headers = _jev_payload(state, questions, model)
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(DECISIONS_URL, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise DecisionsUnavailable(f"JEV unreachable: {e}")
    if not r.is_success:
        raise DecisionsUnavailable(f"JEV HTTP {r.status_code}: {r.text[:300]}")
    try:
        data = r.json()
    except json.JSONDecodeError:
        raise DecisionsUnavailable("JEV returned non-JSON")
    return _jev_parse(data, payload["model"])
