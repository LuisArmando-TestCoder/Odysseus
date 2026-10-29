# src/autopilot.py
"""Autopilot — Odysseus keeps working on its own until the plan is done.

When a turn finishes (agent_runs on_finish) in a chat whose `todo_write` plan
still has open steps, the next turn starts automatically as a detached run:
same chat history + system preface, plus a short "continue with step X" frame.
It repeats until every step is done, the model asks the user something, the
per-chat cap is hit, or the user presses Stop.

* Stop (POST /api/chat/stop) DISARMS the chat — nothing restarts until the
  user sends a new message (which re-arms it and resets the counter). Stop
  also pauses an active goal that lives in this chat.
* A user message always wins: rounds only start when the chat is idle and no
  user turn is queued.
* Settings: ``autopilot_enabled`` (default True), ``autopilot_max_turns``
  (default 25 per user message), ``autopilot_delay_seconds`` (default 3).
* Long objectives that need more than a chat's worth of turns still use goals
  (src/goal_driver.py); autopilot never runs in a chat that has an active goal.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DISARMED: set = set()                    # chats the user stopped
_COUNT: Dict[str, int] = {}               # auto turns since the last user message
_PREFACE: Dict[str, List[dict]] = {}      # last built system preface per chat
_OWNER: Dict[str, Optional[str]] = {}
_PENDING: set = set()                     # chats with a scheduled start
_STATE: Dict[str, Dict[str, Any]] = {}    # last decision per chat (dashboard)


def _setting(key: str, default):
    try:
        from src.settings import get_setting
        v = get_setting(key, default)
        return default if v is None else v
    except Exception:
        return default


def enabled() -> bool:
    v = _setting("autopilot_enabled", True)
    return v not in (False, "false", "0", 0)


# ── hooks called by the chat routes ─────────────────────────────────────────

def on_user_message(session_id: str, preface: Optional[List[dict]] = None,
                    owner: Optional[str] = None) -> None:
    """A real user turn: re-arm, reset the counter, remember the preface."""
    if not session_id:
        return
    _DISARMED.discard(session_id)
    _COUNT[session_id] = 0
    if preface is not None:
        _PREFACE[session_id] = list(preface)
    if owner is not None:
        _OWNER[session_id] = owner
    _STATE[session_id] = {"state": "armed", "at": time.time()}


def disarm(session_id: str, reason: str = "stopped by user") -> None:
    """Stop button: no auto-continuation until the next user message, and an
    active goal in this chat is paused."""
    if not session_id:
        return
    _DISARMED.add(session_id)
    _PENDING.discard(session_id)
    _STATE[session_id] = {"state": "stopped", "reason": reason, "at": time.time()}
    try:
        from src import goal as goal_mod
        owner = goal_mod.resolve_owner(session_id, _OWNER.get(session_id))
        g = goal_mod.get_goal(owner) if owner else None
        if g and g.get("phase") == "active" and g.get("session_id") == session_id:
            goal_mod.update_goal(owner, g["revision"], "pause")
            from src.goal_driver import _publish_goal_update
            _publish_goal_update(owner)
    except Exception as e:
        logger.debug("[autopilot] goal pause on stop failed: %s", e)


def status(session_id: str) -> Dict[str, Any]:
    st = dict(_STATE.get(session_id) or {})
    st.update({
        "enabled": enabled(),
        "armed": session_id not in _DISARMED,
        "auto_turns": _COUNT.get(session_id, 0),
        "max_turns": int(_setting("autopilot_max_turns", 25) or 25),
    })
    return st


def all_status() -> Dict[str, Dict[str, Any]]:
    return {sid: status(sid) for sid in set(_STATE) | set(_COUNT)}


# ── the decision ────────────────────────────────────────────────────────────

_ASKS_USER_RE = re.compile(
    r"(\?\s*$|\b(would you like|do you want|should i|shall i|let me know|which (one|option)|"
    r"please (confirm|provide|choose|share)|can you (provide|share|confirm))\b)",
    re.I,
)


def _last_assistant_text(sess) -> str:
    try:
        msgs = sess.get_context_messages()
    except Exception:
        return ""
    for m in reversed(msgs or []):
        if m.get("role") == "assistant":
            c = m.get("content")
            return c if isinstance(c, str) else ""
        if m.get("role") == "user":
            return ""
    return ""


def _session(session_id: str):
    try:
        from src.ai_interaction import get_session_manager
        sm = get_session_manager()
        return sm.get_session(session_id) if sm else None
    except Exception:
        return None


def on_run_finished(session_id: str, status_: str) -> None:
    """agent_runs.on_finish callback."""
    try:
        _maybe_schedule(session_id, status_)
    except Exception as e:
        logger.warning("[autopilot] decision failed for %s: %s", session_id, e)


def _maybe_schedule(session_id: str, status_: str) -> None:
    if status_ != "done" or not enabled() or session_id in _DISARMED:
        return
    from src.checkpoint import checkpoint_queue_state
    q = checkpoint_queue_state(session_id)
    remaining = q.get("remaining") or []
    if not remaining:
        if _STATE.get(session_id, {}).get("state") == "running":
            _STATE[session_id] = {"state": "finished", "at": time.time()}
        return
    # A goal in this chat drives itself.
    try:
        from src.goal import get_goal_for_session
        g = get_goal_for_session(session_id, _OWNER.get(session_id))
        if g and g.get("phase") == "active":
            return
    except Exception:
        pass
    cap = int(_setting("autopilot_max_turns", 25) or 25)
    n = _COUNT.get(session_id, 0)
    if n >= cap:
        _STATE[session_id] = {"state": "capped", "at": time.time(),
                              "reason": f"reached {cap} automatic turns"}
        _note(session_id, f"Autopilot paused after {cap} automatic turns — send a message to continue.")
        return
    sess = _session(session_id)
    if not sess:
        return
    last = _last_assistant_text(sess).strip()
    if last and _ASKS_USER_RE.search(last[-400:]):
        _STATE[session_id] = {"state": "waiting", "at": time.time(),
                              "reason": "the agent asked you something"}
        return
    if session_id in _PENDING:
        return
    _PENDING.add(session_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _PENDING.discard(session_id)
        return
    loop.create_task(_start_later(session_id))


def _note(session_id: str, text: str) -> None:
    try:
        from src import agent_runs
        agent_runs.publish(session_id, f'data: {json.dumps({"type": "autopilot", "data": {"note": text}})}\n\n')
    except Exception:
        pass


async def _start_later(session_id: str) -> None:
    try:
        await asyncio.sleep(float(_setting("autopilot_delay_seconds", 3) or 3))
        if session_id in _DISARMED or not enabled():
            return
        from src import agent_runs, turn_inbox
        if agent_runs.is_active(session_id) or turn_inbox.is_queued(session_id):
            return
        sess = _session(session_id)
        if not sess:
            return
        from src.checkpoint import checkpoint_queue_state
        q = checkpoint_queue_state(session_id)
        remaining = q.get("remaining") or []
        if not remaining:
            return
        _COUNT[session_id] = _COUNT.get(session_id, 0) + 1
        n = _COUNT[session_id]
        cur = q.get("current") or remaining[0]
        done = q.get("done") or []
        frame = (
            "=== AUTOPILOT (automatic turn {n}) ===\n"
            "You are continuing your own plan without waiting for the user. "
            "Done: {d}/{t}. Next step: {cur}\n"
            "Remaining: {rem}\n"
            "Do the next step now with tools. Keep narration to one line. After "
            "finishing a step, re-send todo_write with the full list and updated "
            "statuses. When every step is done, give the final concise answer. "
            "If you truly need the user's input or a decision, ask one clear "
            "question and stop."
        ).format(n=n, d=len(done), t=len(done) + len(remaining), cur=cur,
                 rem="; ".join(remaining[:12]))
        owner = _OWNER.get(session_id) or getattr(sess, "owner", None)
        messages = list(_PREFACE.get(session_id) or []) + list(sess.get_context_messages())
        messages.append({"role": "system", "content": frame})
        _STATE[session_id] = {"state": "running", "at": time.time(), "turn": n, "step": cur}
        agent_runs.start(session_id, _stream(sess, messages, owner, n, cur),
                         owner=owner, kind="autopilot", label=f"Autopilot · {cur}"[:120])
        logger.info("[autopilot] turn %s started in %s (step: %s)", n, session_id, cur)
    except Exception as e:
        logger.warning("[autopilot] start failed for %s: %s", session_id, e)
    finally:
        _PENDING.discard(session_id)


async def _stream(sess, messages, owner, n: int, step: str):
    """Detached autopilot turn: runs the agent loop, persists the assistant
    message with the same metadata a normal turn gets (thread, round texts,
    edits), and yields the full SSE protocol."""
    from src.agent_loop import stream_agent_loop
    from core.models import ChatMessage
    try:
        from src.cost_ledger import set_cost_scope
        set_cost_scope(sess.id, "autopilot", owner)
    except Exception:
        pass
    yield f'data: {json.dumps({"type": "turn_resumed", "data": {"session_id": sess.id, "autopilot": True}})}\n\n'
    yield f'data: {json.dumps({"type": "autopilot", "data": {"turn": n, "step": step}})}\n\n'
    yield f'data: {json.dumps({"type": "model_info", "model": sess.model, "suffix": "Autopilot"})}\n\n'
    full = ""
    meta: Dict[str, Any] = {}
    try:
        async for chunk in stream_agent_loop(
            sess.endpoint_url, sess.model, messages,
            headers=getattr(sess, "headers", None),
            temperature=0.3, max_tokens=0,
            context_length=getattr(sess, "context_length", 0) or 0,
            session_id=sess.id, owner=owner, can_ask_user=True,
        ):
            if chunk.startswith("data: ") and chunk != "data: [DONE]\n\n":
                try:
                    d = json.loads(chunk[6:])
                except (ValueError, TypeError):
                    d = None
                if isinstance(d, dict):
                    if "delta" in d and not d.get("thinking"):
                        full += d["delta"]
                    elif d.get("type") == "metrics" and isinstance(d.get("data"), dict):
                        meta = dict(d["data"])
            yield chunk
    finally:
        if full.strip():
            meta.update({"autopilot": True, "autopilot_turn": n, "model": sess.model})
            try:
                sess.add_message(ChatMessage("assistant", full.strip(), metadata=meta))
            except Exception as e:
                logger.warning("[autopilot] persisting turn failed: %s", e)
    yield f'data: {json.dumps({"type": "message_saved"})}\n\n'
    yield "data: [DONE]\n\n"
