"""Goal round driver — auto-continuation loop, mirroring DSH's goal-round-driver.

Re-invokes the agent one round at a time until the goal reaches complete /
blocked / paused, persisting progress in the checkpoint (src/checkpoint.py) and
the versioned goal state (src/goal.py). The harness owns the goal state; the
model only works within a single round and reads state back from the harness.
"""
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from src.goal import get_goal, update_goal, GoalConflict

logger = logging.getLogger(__name__)


def _bump_round(owner: str) -> Optional[Dict[str, Any]]:
    """Increment roundsStarted and persist it atomically.

    The loop owns the counter, not the model. Reads the latest goal, bumps the
    counter, and writes it back — retrying on a concurrent revision bump.
    Goals are owner-keyed (Layer 0 / F0.3); rounds execute in goal.session_id.
    """
    for _ in range(5):
        goal = get_goal(owner)
        if not goal:
            return None
        try:
            # A no-op edit bumps the revision; we then set the counter and
            # persist via a second update so the revision stays consistent.
            goal = update_goal(owner, goal["revision"], "edit")
            goal["roundsStarted"] = goal.get("roundsStarted", 0) + 1
            # Persist the counter directly (atomic write) without another
            # revision bump, so the counter and revision stay in sync.
            from src.goal import _write
            _write(owner, goal)
            return goal
        except GoalConflict:
            continue
    return get_goal(owner)


def seed_queue_from_objective(session_id, items):
    """Seed the item queue for a continuous task."""
    from src.checkpoint import checkpoint_set_queue
    checkpoint_set_queue(items, session_id)


async def run_agent_round(
    goal: Dict[str, Any],
    *,
    endpoint_url: str,
    model: str,
    messages: list,
    headers: Optional[dict] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    context_length: int = 0,
    active_document=None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    relevant_tools: Optional[set] = None,
    fallbacks: Optional[list] = None,
) -> Dict[str, Any]:
    """Run ONE agent round under the goal, wiring to ``stream_agent_loop``.

    The agent works one round; it signals completion/blocking via the
    ``finish_goal`` tool (which transitions the goal state directly). After
    the round, we re-read the goal to detect whether the agent finished it.

    Returns ``{"done": bool, "blocked_reason": str|None}``.
    """
    from src.agent_loop import stream_agent_loop
    from src.goal import get_goal

    session_id = goal.get("session_id")
    async for _chunk in stream_agent_loop(
        endpoint_url,
        model,
        messages,
        headers=headers,
        temperature=temperature,
        max_tokens=max_tokens,
        context_length=context_length,
        active_document=active_document,
        session_id=session_id,
        disabled_tools=disabled_tools,
        owner=owner,
        relevant_tools=relevant_tools,
        fallbacks=fallbacks,
    ):
        # The chunks are consumed; the goal state is the source of truth.
        pass

    # The agent may have called finish_goal, which transitions the state
    # directly. Re-read (owner-keyed, F0.3) to detect the outcome.
    updated = get_goal(goal.get("owner") or owner)
    if not updated:
        return {"done": False, "blocked_reason": None}
    if updated.get("phase") == "complete":
        return {"done": True, "blocked_reason": None}
    if updated.get("phase") == "blocked":
        reason = (updated.get("blockedReason") or {}).get("message")
        return {"done": False, "blocked_reason": reason}
    return {"done": False, "blocked_reason": None}


async def goal_round_loop(
    owner: str,
    run_agent_round: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    blocked_conditions: Optional[list] = None,
) -> None:
    """Auto-continue rounds until complete/blocked/paused.

    ``owner`` is the goal identity (Layer 0 / F0.3); rounds run in the goal's
    ``session_id``. ``run_agent_round(goal)`` must return a dict with optional
    keys:
      - ``done`` (bool): the objective is truly achieved → complete.
      - ``blocked_reason`` (str): a concrete blocking condition. After 3
        consecutive rounds with the same reason, the goal is marked blocked
        (DSH rule — difficulty/uncertainty/remaining work is NOT "blocked").
    """
    same_blocked = 0
    last_blocked_reason = None
    while True:
        goal = get_goal(owner)
        if not goal or goal["phase"] in ("complete", "blocked", "paused"):
            break
        if goal["roundsStarted"] >= goal["maxGoalRounds"]:
            try:
                update_goal(owner, goal["revision"], "blocked",
                            blocked_reason="maxGoalRounds reached")
            except GoalConflict:
                pass
            break

        # 1) run one agent round (the agent appends to checkpoint via
        #    src/checkpoint.py).
        try:
            outcome = await run_agent_round(goal)
        except Exception as e:
            logger.warning(f"goal round failed: {e}")
            outcome = {"blocked_reason": f"round error: {e}"}
        outcome = outcome or {}

        # 2) bump progress (the loop owns the counter, not the model).
        goal = _bump_round(owner)
        if not goal:
            break

        # 2b) advance the continuous-task cursor: mark the injected item done.
        try:
            from src.checkpoint import checkpoint_next, checkpoint_pop_done
            _sid = goal.get("session_id")
            _item = checkpoint_next(_sid)
            if _item is not None:
                checkpoint_pop_done(_item, _sid)
                # if that was the last item, the goal is effectively done
                if checkpoint_next(_sid) is None:
                    outcome["done"] = True
        except Exception:
            pass

        # 3) decide continue vs stop.
        if outcome.get("done"):
            try:
                update_goal(owner, goal["revision"], "complete")
            except GoalConflict:
                pass
            break
        if outcome.get("blocked_reason"):
            reason = outcome["blocked_reason"]
            if reason == last_blocked_reason:
                same_blocked += 1
            else:
                same_blocked = 1
                last_blocked_reason = reason
            if same_blocked >= 3:  # DSH rule: blocked only after 3 consecutive rounds
                try:
                    update_goal(owner, goal["revision"], "blocked",
                                blocked_reason=last_blocked_reason)
                except GoalConflict:
                    pass
                break
        else:
            same_blocked = 0