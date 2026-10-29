# src/goal_driver.py
"""Goal round driver — harness-driven auto-continuation (Cluster C / G1–G3).

Mirrors DSH's goal-round-driver semantics with Odysseus' plumbing:

* Rounds run ONLY at agent quiescence (G2 idle gating): no active run, no
  queued user turns, and no user message arrived since the last round.
* Each round is a fresh DETACHED run (agent_runs) in the goal's session — the
  model never pings itself inside one long turn.
* The driver never arms a disarmed goal (human-only re-arm — DSH rule).
* Trigger points: goal creation (route), run termination (on_run_finished),
  and a 5 s daemon (boot resume after restart).

A round can never start another round directly: only the terminal state of the
detached run triggers the next attempt, exactly like DSH's quiescence watch.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DAEMON: Optional[asyncio.Task] = None
_LOCKS: Dict[str, asyncio.Lock] = {}
# Per-owner round bookkeeping: consecutive failed rounds (driver-side blocking).
_ERRORS: Dict[str, int] = {}

_DAEMON_INTERVAL_S = 5.0
_BLOCK_AFTER_CONSECUTIVE_ERRORS = 3


# ── goal scanning ────────────────────────────────────────────────────────────

def _iter_active_goals():
    from src.constants import DATA_DIR
    from src import goal as goal_mod
    d = os.path.join(DATA_DIR, "goals")
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            g = goal_mod._read(name[:-5])
        except Exception:
            continue
        if g and g.get("phase") == "active":
            yield g


def _find_goal_by_session(session_id: str):
    for g in _iter_active_goals():
        if g.get("session_id") == session_id:
            return g
    return None


# ── round construction ───────────────────────────────────────────────────────

def _bump_rounds(owner: str) -> Optional[Dict[str, Any]]:
    """Increment roundsStarted (loop-owned counter) with revision consistency."""
    from src import goal as goal_mod
    for _ in range(5):
        g = goal_mod.get_goal(owner)
        if not g:
            return None
        try:
            g = goal_mod.update_goal(owner, g["revision"], "edit")
        except goal_mod.GoalConflict:
            continue
        g["roundsStarted"] = g.get("roundsStarted", 0) + 1
        goal_mod._write(owner, g)
        return g
    return goal_mod.get_goal(owner)


def _round_messages(goal: Dict[str, Any], owner: str) -> list:
    """Goal frame + checkpoint + synthetic continue message (DSH goal-source)."""
    from src.checkpoint import checkpoint_read
    try:
        cp = checkpoint_read(goal.get("session_id"), owner) or ""
    except Exception:
        cp = ""
    frame = (
        "=== ACTIVE GOAL (round {r}/{m}) ===\n"
        "objective: {obj}\n"
        "This is a harness-driven goal round. Work toward the objective NOW; "
        "inspect the checkpoint and tool results (do not re-derive). Make "
        "concrete, verifiable progress. When the objective is truly achieved, "
        "call finish_goal(status='done'); if something concretely blocks you, "
        "call finish_goal(status='blocked', reason='...'). Do NOT self-ping — "
        "the harness starts the next round."
    ).format(r=goal.get("roundsStarted", 0), m=goal.get("maxGoalRounds", 256),
             obj=goal.get("objective", ""))
    messages = [{"role": "system", "content": frame}]
    if cp and cp.strip():
        messages.append({"role": "system", "content":
                         "=== YOUR OWN PROGRESS SO FAR (from checkpoint) ===\n" + cp})
    messages.append({
        "role": "user",
        "content": "Continue working toward the goal. Use the checkpoint; do not re-derive.",
    })
    return messages


async def _round_stream(sess, messages, owner: str):
    """Detached round generator: runs the agent loop, persists the assistant
    message (bg_monitor pattern), and yields the full SSE protocol."""
    from src.agent_loop import stream_agent_loop
    from core.models import ChatMessage
    from src.cost_ledger import set_cost_scope

    try:
        set_cost_scope(sess.id, "chat", owner)
    except Exception:
        pass

    yield f'data: {json.dumps({"type": "turn_resumed", "data": {"session_id": sess.id, "goal_round": True}})}\n\n'
    yield f'data: {json.dumps({"type": "model_info", "model": sess.model, "suffix": "Goal round"})}\n\n'

    full = ""
    _metrics = {}
    tool_events = []
    round_num = 1
    async for chunk in stream_agent_loop(
        sess.endpoint_url, sess.model, messages,
        headers=getattr(sess, "headers", None),
        temperature=0.3,
        max_tokens=0,  # server decides — a hard cap truncates reasoning models
        context_length=getattr(sess, "context_length", 0) or 0,
        session_id=sess.id,
        owner=owner,
        can_ask_user=True,
    ):
        if chunk.startswith("data: ") and chunk != "data: [DONE]\n\n":
            try:
                d = json.loads(chunk[6:])
            except (ValueError, TypeError):
                pass
            else:
                if isinstance(d, dict):
                    if "delta" in d:
                        if not d.get("thinking"):
                            full += d["delta"]
                    elif d.get("type") == "metrics" and isinstance(d.get("data"), dict):
                        _metrics = dict(d["data"])
                    elif d.get("type") == "agent_step":
                        round_num = d.get("round", round_num)
                    elif d.get("type") == "tool_output":
                        tool_events.append({
                            "round": round_num,
                            "tool": d.get("tool"),
                            "command": d.get("command"),
                            "output": d.get("output"),
                            "exit_code": d.get("exit_code"),
                        })
        yield chunk

    if full.strip():
        try:
            _md = dict(_metrics) if _metrics else {"tool_events": tool_events}
            _md.update({"goal_round": True, "model": sess.model})
            sess.add_message(ChatMessage("assistant", full.strip(), metadata=_md))
        except Exception as e:
            logger.warning("[goal-driver] persisting round output failed: %s", e)
        yield f'data: {json.dumps({"type": "message_saved"})}\n\n'
    yield "data: [DONE]\n\n"


# ── idle-gated start ─────────────────────────────────────────────────────────

def _owner_lock(owner: str) -> asyncio.Lock:
    lock = _LOCKS.get(owner)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[owner] = lock
    return lock


def maybe_start_goal_round(owner: Optional[str] = None,
                           goal: Optional[Dict[str, Any]] = None) -> bool:
    """Public trigger (sync, safe to call from routes/callbacks). Schedules the
    idle-gated round start as a task. Returns True when scheduled."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    loop.create_task(_start_round(owner, goal))
    return True


async def _start_round(owner: Optional[str], goal: Optional[Dict[str, Any]]):
    from src import goal as goal_mod
    if goal is None and owner:
        goal = goal_mod.get_goal(owner)
    if not goal or not owner:
        return
    owner = goal.get("owner") or owner

    async with _owner_lock(owner):
        # Re-read fresh state under the lock (another task may have started).
        goal = goal_mod.get_goal(owner)
        if not goal or goal.get("phase") != "active":
            return
        if goal.get("activation") != "armed":
            return  # human-only re-arm (DSH rule)

        session_id = goal.get("session_id")
        if not session_id:
            return

        # G2 idle-gating fences.
        from src import agent_runs, turn_inbox
        if agent_runs.is_active(session_id):
            return
        if turn_inbox.is_queued(session_id):
            return  # a user message is waiting — the user turn runs first

        # Round cap → blocked with the DSH reason.
        if goal.get("roundsStarted", 0) >= goal.get("maxGoalRounds", 256):
            try:
                goal_mod.update_goal(owner, goal["revision"], "blocked",
                                     blocked_reason="maxGoalRounds reached")
                _publish_goal_update(owner)
            except Exception as e:
                logger.warning("[goal-driver] cap-block failed: %s", e)
            return

        try:
            from src.ai_interaction import get_session_manager
            sm = get_session_manager()
            sess = sm.get_session(session_id) if sm else None
        except Exception as e:
            logger.warning("[goal-driver] session resolution failed: %s", e)
            return
        if not sess:
            logger.warning("[goal-driver] goal session %s missing — skipping round", session_id)
            return

        # Bump the round counter (the loop owns it, not the model).
        goal = _bump_rounds(owner)
        if not goal or goal.get("phase") != "active":
            return

        messages = _round_messages(goal, owner)
        try:
            from src import agent_runs
            agent_runs.start(session_id, _round_stream(sess, messages, owner), owner=owner,
                             kind="goal", label=f"Goal round {goal.get('roundsStarted', 0)}")
        except Exception as e:
            logger.error("[goal-driver] round start failed: %s", e)
            return
        # Announce the round into every live run of this owner (the goal card
        # lives globally; the events fan out to whichever chat is open).
        from src import agent_runs as _ar
        _ar.publish_to_owner(owner, f'data: {json.dumps({"type": "goal_round", "data": {"round": goal.get("roundsStarted", 0), "max": goal.get("maxGoalRounds", 256)}})}\n\n')
        logger.info("[goal-driver] round %s started for owner %s in session %s",
                    goal.get("roundsStarted"), owner, session_id)


def _publish_goal_update(owner: str) -> None:
    from src import goal as goal_mod, agent_runs
    g = goal_mod.get_goal(owner)
    if g is None:
        return
    agent_runs.publish_to_owner(owner, f'data: {json.dumps({"type": "goal_update", "data": g})}\n\n')


def on_run_finished(session_id: str, status: str) -> None:
    """agent_runs.on_finish callback (G1): publish the goal update, count
    consecutive failures (driver-side blocked rule), and schedule the next
    round at quiescence."""
    goal = _find_goal_by_session(session_id)
    if goal is None:
        return
    owner = goal.get("owner")
    if not owner:
        return
    _publish_goal_update(owner)
    if status == "error":
        _ERRORS[owner] = _ERRORS.get(owner, 0) + 1
        if _ERRORS[owner] >= _BLOCK_AFTER_CONSECUTIVE_ERRORS:
            try:
                from src import goal as goal_mod
                g = goal_mod.get_goal(owner)
                if g and g.get("phase") == "active":
                    goal_mod.update_goal(owner, g["revision"], "blocked",
                                         blocked_reason="3 consecutive goal rounds failed")
                    _publish_goal_update(owner)
                    _ERRORS[owner] = 0
                    return
            except Exception as e:
                logger.warning("[goal-driver] error-block failed: %s", e)
    else:
        _ERRORS.pop(owner, None)
    # Next round at quiescence (the idle fences re-check everything).
    if status in ("done", "stopped", "error"):
        maybe_start_goal_round(owner=owner)


# ── daemon (boot resume) ─────────────────────────────────────────────────────

async def _daemon_loop() -> None:
    while True:
        try:
            for g in _iter_active_goals():
                if g.get("activation") == "armed":
                    maybe_start_goal_round(owner=g.get("owner"), goal=g)
        except Exception as e:
            logger.debug("[goal-driver] daemon tick failed: %s", e)
        await asyncio.sleep(_DAEMON_INTERVAL_S)


def start_daemon() -> asyncio.Task:
    global _DAEMON
    if _DAEMON is None or _DAEMON.done():
        _DAEMON = asyncio.create_task(_daemon_loop())
    return _DAEMON
