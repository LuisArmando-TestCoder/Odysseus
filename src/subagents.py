# src/subagents.py
"""Branching & subagents (Cluster D / B2–B4) — mirror of DSH's tool-subagent.

A subagent is a DETACHED agent run in its own session (reusing agent_runs),
started from a parent chat. Modes:

* spawn        — fresh child session with just the task prompt.
* fork         — child session seeded with a copy of the parent's history
                 (DSH subagent-fork-in-process: the child inherits completed
                 parent turns).
* one-shot     — the parent turn AWAITS the child's terminal state (with SSE
                 heartbeats) and receives its final text as the tool result.
* continuable  — returns a durable subagent_id immediately; the child owns its
                 turns; send_message/interrupt_agent/list_agents steer it; a
                 subagent_done notice is fanned into the parent owner's live
                 runs when the child settles (B4).

Children NEVER ask the user (can_ask_user=False → DELEGATED_CALLER, Cluster B
root-only rule). Delegation depth is capped at 3.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_DEPTH = 3

# subagent_id → entry
SUBS: Dict[str, Dict[str, Any]] = {}
# child session_id → subagent_id (fast reverse lookup)
_BY_SESSION: Dict[str, str] = {}
# child session_id → asyncio.Future resolved when its detached run terminates
_WAIT_FUTURES: Dict[str, asyncio.Future] = {}
_HOOK_REGISTERED = False


def _ensure_finish_hook():
    global _HOOK_REGISTERED
    if _HOOK_REGISTERED:
        return
    from src import agent_runs
    agent_runs.on_finish(_on_run_finished)
    _HOOK_REGISTERED = True


def _on_run_finished(session_id: str, status: str) -> None:
    """Resolve waiter futures + fan subagent_done into the owner's live runs."""
    fut = _WAIT_FUTURES.pop(session_id, None)
    if fut is not None and not fut.done():
        fut.set_result(status)
    sub_id = _BY_SESSION.get(session_id)
    if not sub_id:
        return
    entry = SUBS.get(sub_id)
    if not entry:
        return
    entry["status"] = status
    if entry.get("mode") == "continuable":
        try:
            from src import agent_runs
            agent_runs.publish_to_owner(
                entry.get("owner") or "",
                'data: ' + json.dumps({
                    "type": "subagent_done",
                    "data": {"subagent_id": sub_id, "status": status,
                             "summary": entry.get("task", "")[:200]},
                }) + '\n\n',
            )
        except Exception as e:
            logger.debug("[subagents] settlement publish failed: %s", e)


def _timeout_seconds() -> float:
    try:
        from src.settings import get_setting
        return max(60.0, float(get_setting("subagent_timeout_seconds", 900) or 900))
    except Exception:
        return 900.0


def _session_manager():
    try:
        from src.ai_interaction import get_session_manager
        sm = get_session_manager()
        if sm:
            return sm
    except Exception:
        pass
    return None


def fork_child_session(source_session_id: str, owner: str, *,
                       keep_count: int = 0, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """B1/B2 fork mode: a NEW child session seeded with the parent's history."""
    sm = _session_manager()
    if not sm:
        return None
    source = sm.get_session(source_session_id)
    if not source:
        return None
    new_sid = uuid.uuid4().hex[:12]
    sm.create_session(
        session_id=new_sid,
        name=name or f"Fork: {source.name}",
        endpoint_url=getattr(source, "endpoint_url", ""),
        model=getattr(source, "model", ""),
        rag=False,
        owner=owner,
    )
    from core.models import ChatMessage
    child = sm.get_session(new_sid)
    history = source.get_context_messages()
    if keep_count and keep_count > 0:
        history = history[:keep_count]
    for msg in history:
        child.add_message(ChatMessage(msg["role"], msg["content"]))
    try:
        from src.event_bus import fire_event
        fire_event("session_created", owner)
    except Exception:
        pass
    return {"session_id": new_sid, "name": name or f"Fork: {source.name}", "messages_copied": len(history)}


def spawn_child_session(owner: str, task: str) -> Optional[Dict[str, Any]]:
    sm = _session_manager()
    if not sm:
        return None
    new_sid = uuid.uuid4().hex[:12]
    sm.create_session(
        session_id=new_sid,
        name=f"Agent: {task.strip()[:40]}",
        endpoint_url="",
        model="",
        rag=False,
        owner=owner,
    )
    return {"session_id": new_sid, "name": f"Agent: {task.strip()[:40]}", "messages_copied": 0}


async def _subagent_stream(sess, messages, owner: str,
                           disabled_tools: Optional[set] = None):
    """Detached child-turn generator (can_ask_user=False — root-only rule).
    S3: children inherit the parent's disabled-tools set — they never gain
    privileges the parent chat didn't have."""
    from src.agent_loop import stream_agent_loop
    from core.models import ChatMessage
    try:
        from src.cost_ledger import set_cost_scope
        set_cost_scope(sess.id, "chat", owner)
    except Exception:
        pass

    yield f'data: {json.dumps({"type": "model_info", "model": sess.model or "inherited", "suffix": "Subagent"})}\n\n'

    full = ""
    async for chunk in stream_agent_loop(
        sess.endpoint_url, sess.model, messages,
        headers=getattr(sess, "headers", None),
        temperature=0.3,
        max_tokens=0,
        context_length=getattr(sess, "context_length", 0) or 0,
        session_id=sess.id,
        owner=owner,
        can_ask_user=False,
        disabled_tools=disabled_tools or None,
    ):
        if chunk.startswith("data: ") and chunk != "data: [DONE]\n\n":
            try:
                d = json.loads(chunk[6:])
                if isinstance(d, dict) and "delta" in d and not d.get("thinking"):
                    full += d["delta"]
            except (ValueError, TypeError):
                pass
        yield chunk

    if full.strip():
        try:
            sess.add_message(ChatMessage("assistant", full.strip(),
                                         metadata={"subagent": True, "model": sess.model}))
        except Exception as e:
            logger.warning("[subagents] persisting child output failed: %s", e)
        yield f'data: {json.dumps({"type": "message_saved"})}\n\n'
    yield "data: [DONE]\n\n"


def _child_messages(child: Dict[str, Any], task: str) -> list:
    sm = _session_manager()
    msgs = []
    if sm:
        try:
            sess = sm.get_session(child["session_id"])
            msgs = sess.get_context_messages()
        except Exception:
            msgs = []
    msgs.append({"role": "user", "content": task})
    return msgs


def _depth_of(parent_session_id: str) -> int:
    sub_id = _BY_SESSION.get(parent_session_id)
    if sub_id and sub_id in SUBS:
        return int(SUBS[sub_id].get("depth", 0))
    return 0


async def run_subagent(*, parent_session_id: str, owner: str, mode: str,
                       task: str, run_in_background: bool = False,
                       disabled_tools: Optional[set] = None) -> Dict[str, Any]:
    """Start a subagent run. Returns a tool-result dict:

    - background/continuable → {"subagent_started": ..., "run_in_background": true}
    - foreground one-shot      → {"subagent_started": ..., "run_in_background": false}
    In BOTH cases the agent loop awaits the child (foreground) or reports the
    handle (background); the dict carries child_session_id + subagent_id.
    """
    _ensure_finish_hook()
    if mode not in ("spawn", "fork"):
        return {"error": f"subagent: unknown mode {mode!r} (use 'spawn' or 'fork')", "exit_code": 1}
    task = (task or "").strip()
    if not task:
        return {"error": "subagent requires a task prompt", "exit_code": 1}
    depth = _depth_of(parent_session_id) + 1
    if depth > MAX_DEPTH:
        return {"error": f"subagent: max delegation depth ({MAX_DEPTH}) reached", "error_code": "MAX_DEPTH", "exit_code": 1}

    if mode == "fork":
        child = fork_child_session(parent_session_id, owner)
        if not child:
            return {"error": "subagent fork: parent session not found", "exit_code": 1}
    else:
        child = spawn_child_session(owner, task)
        if not child:
            return {"error": "subagent spawn: could not create child session", "exit_code": 1}

    sm = _session_manager()
    if not sm:
        return {"error": "subagent: session manager unavailable", "exit_code": 1}
    # Child inherits the parent session's model/endpoint when the child has none.
    try:
        child_sess = sm.get_session(child["session_id"])
        parent_sess = sm.get_session(parent_session_id)
        if parent_sess and not child_sess.model:
            child_sess.endpoint_url = getattr(parent_sess, "endpoint_url", "")
            child_sess.model = getattr(parent_sess, "model", "")
            child_sess.headers = dict(getattr(parent_sess, "headers", {}) or {})
    except Exception:
        child_sess = sm.get_session(child["session_id"])

    sub_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    SUBS[sub_id] = {
        "id": sub_id,
        "session_id": child["session_id"],
        "parent_id": parent_session_id,
        "depth": depth,
        "mode": "continuable" if run_in_background else "one-shot",
        "status": "running",
        "task": task,
        "owner": owner,
        "disabled_tools": set(disabled_tools or []),
    }
    _BY_SESSION[child["session_id"]] = sub_id
    _WAIT_FUTURES[child["session_id"]] = loop.create_future()

    from src import agent_runs
    agent_runs.start(
        child["session_id"],
        _subagent_stream(child_sess, _child_messages(child, task), owner,
                         disabled_tools=disabled_tools),
        owner=owner,
    )
    return {
        "subagent_started": True,
        "subagent_id": sub_id,
        "child_session_id": child["session_id"],
        "kind": "continuable" if run_in_background else "foreground",
        "run_in_background": run_in_background,
        "output": (f"started subagent {sub_id} (session {child['session_id']}, depth {depth})"),
    }


def wait_run_future(child_session_id: str) -> Optional[asyncio.Future]:
    return _WAIT_FUTURES.get(child_session_id)


def collect_output(child_session_id: str, status_payload: Any = None) -> str:
    """One-shot result text: the child's final assistant message."""
    sm = _session_manager()
    try:
        if sm:
            sess = sm.get_session(child_session_id)
            for msg in reversed(getattr(sess, "history", []) or []):
                if getattr(msg, "role", None) == "assistant" and getattr(msg, "content", ""):
                    return str(msg.content)
    except Exception:
        pass
    if isinstance(status_payload, dict) and status_payload.get("timeout"):
        return "subagent timed out before completing"
    if isinstance(status_payload, str) and status_payload != "done":
        return f"subagent ended with status {status_payload!r} (no final output)"
    return "(subagent produced no output)"


def list_subs(owner: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for sub_id, entry in SUBS.items():
        if owner and entry.get("owner") != owner:
            continue
        out.append({
            "id": sub_id,
            "session_id": entry.get("session_id"),
            "parent_id": entry.get("parent_id"),
            "depth": entry.get("depth"),
            "status": entry.get("status"),
            "mode": entry.get("mode"),
            "task": (entry.get("task") or "")[:80],
        })
    return out


def get_sub(subagent_id: str) -> Optional[Dict[str, Any]]:
    return SUBS.get(subagent_id)


def append_message_and_resume(subagent_id: str, message: str, owner: str) -> Dict[str, Any]:
    """send_message: append a user message to the child; start a detached run
    if the child is idle."""
    entry = SUBS.get(subagent_id)
    if not entry:
        return {"error": f"no such subagent {subagent_id!r} (use list_agents)", "exit_code": 1}
    if entry.get("owner") != owner:
        return {"error": "not your subagent", "exit_code": 1}
    sm = _session_manager()
    if not sm:
        return {"error": "session manager unavailable", "exit_code": 1}
    try:
        sess = sm.get_session(entry["session_id"])
        from core.models import ChatMessage
        sess.add_message(ChatMessage("user", message))
    except Exception as e:
        return {"error": f"send_message failed: {e}", "exit_code": 1}
    from src import agent_runs
    if agent_runs.is_active(entry["session_id"]):
        from src import turn_inbox
        turn_inbox.enqueue(entry["session_id"], message)
        return {"output": f"queued for subagent {subagent_id} (busy)", "exit_code": 0}
    loop = asyncio.get_running_loop()
    _WAIT_FUTURES[entry["session_id"]] = loop.create_future()
    entry["status"] = "running"
    agent_runs.start(
        entry["session_id"],
        _subagent_stream(sess, sess.get_context_messages(), owner,
                         disabled_tools=entry.get("disabled_tools")),
        owner=owner,
    )
    return {"output": f"sent to subagent {subagent_id}", "exit_code": 0}


def interrupt(subagent_id: str, owner: str) -> Dict[str, Any]:
    entry = SUBS.get(subagent_id)
    if not entry:
        return {"error": f"no such subagent {subagent_id!r}", "exit_code": 1}
    if entry.get("owner") != owner:
        return {"error": "not your subagent", "exit_code": 1}
    from src import agent_runs
    stopped = agent_runs.stop(entry["session_id"])
    return {"output": f"interrupt sent (stopped={stopped})", "exit_code": 0, "stopped": stopped}


def job_status(subagent_id: str, owner: str) -> Dict[str, Any]:
    entry = SUBS.get(subagent_id)
    if not entry:
        return {"error": f"no such subagent {subagent_id!r}", "exit_code": 1}
    if entry.get("owner") != owner:
        return {"error": "not your subagent", "exit_code": 1}
    from src import agent_runs
    return {
        "output": json.dumps({
            "id": subagent_id,
            "status": agent_runs.get_status(entry["session_id"]) or entry.get("status"),
            "session_id": entry["session_id"],
            "task": (entry.get("task") or "")[:120],
            "result": collect_output(entry["session_id"]),
        }, ensure_ascii=False),
        "exit_code": 0,
    }
