# src/user_questions.py
"""Ask-user seam (Cluster B / Q1) — mirror of DSH's ctx.userQuestions.

The agent's ``ask_user_question`` tool pauses ONLY its own tool call: the
registry holds one pending question per session, the loop awaits the Future
with SSE heartbeats, and a route resolves it when the human answers. Root-only
rule: ``can_ask_user`` is server-enforced — delegated contexts (bg_monitor,
verifier, subagent children) receive DELEGATED_CALLER and must surface the open
question in their own result instead.

Durability: the pending question is also persisted as a marker file so a page
refresh can re-render the card before the stream replay catches up. The Future
itself is in-memory (same scope as agent_runs).
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)


@dataclass
class AskState:
    session_id: str
    questions: list
    future: asyncio.Future
    created_at: float
    turn_id: Optional[str] = None
    timeout_task: Optional[asyncio.Task] = None


PENDING: Dict[str, AskState] = {}


def _marker_path(session_id: str) -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "agent_state")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "unknown"))
    return os.path.join(d, f"{safe}.question.json")


def _write_marker(session_id: str, questions: list) -> None:
    try:
        atomic_write_json(_marker_path(session_id), {
            "questions": questions,
            "created_at": time.time(),
        })
    except Exception as e:
        logger.debug("[questions] marker write failed: %s", e)


def _remove_marker(session_id: str) -> None:
    try:
        p = _marker_path(session_id)
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def _timeout_seconds() -> float:
    try:
        from src.settings import get_setting
        return max(30.0, float(get_setting("ask_user_timeout_seconds", 600) or 600))
    except Exception:
        return 600.0


def ask(session_id: str, questions: list, *, can_ask_user: bool,
        turn_id: Optional[str] = None) -> Dict[str, Any]:
    """Register a pending question set for the session.

    Returns:
      - {"status": "pending", "questions": [...]}  → registered; the loop must
        await the returned future (via get_future) with heartbeats.
      - {"error": ..., "error_code": "DELEGATED_CALLER"} when this context may
        not interact with a human (root-only rule, server-enforced).
      - {"error": ..., "error_code": "ALREADY_PENDING"} when a question is
        already waiting for this session.
    """
    if not can_ask_user:
        return {
            "error": (
                "ask_user_question is unavailable in this context: only the live "
                "root chat turn may ask the user (DELEGATED_CALLER). Include the "
                "unresolved question in your final result instead."
            ),
            "error_code": "DELEGATED_CALLER",
            "exit_code": 1,
        }
    existing = PENDING.get(session_id)
    if existing is not None and not existing.future.done():
        return {
            "error": "a question is already pending for this session; wait for its answer first",
            "error_code": "ALREADY_PENDING",
            "exit_code": 1,
        }
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    state = AskState(
        session_id=session_id,
        questions=list(questions),
        future=future,
        created_at=time.time(),
        turn_id=turn_id,
    )
    PENDING[session_id] = state
    _write_marker(session_id, questions)
    state.timeout_task = asyncio.create_task(_arm_timeout(session_id, future))
    return {"status": "pending", "questions": questions}


async def _arm_timeout(session_id: str, future: asyncio.Future) -> None:
    try:
        await asyncio.sleep(_timeout_seconds())
        if not future.done():
            future.set_result({"timeout": True, "answers": []})
            _remove_marker(session_id)
            logger.info("[questions] %s timed out unanswered", session_id)
    except asyncio.CancelledError:
        pass


def get_future(session_id: str) -> Optional[asyncio.Future]:
    state = PENDING.get(session_id)
    return state.future if state else None


def answer(session_id: str, answers: list) -> bool:
    """Resolve the pending question with the human's answers. False if none."""
    state = PENDING.pop(session_id, None)
    _remove_marker(session_id)
    if state is None:
        return False
    if state.timeout_task and not state.timeout_task.done():
        state.timeout_task.cancel()
    if not state.future.done():
        state.future.set_result({"timeout": False, "answers": answers})
        return True
    return False


def get_pending(session_id: str) -> Optional[list]:
    """Questions currently waiting for this session (live state first, then
    the persisted marker — used to re-render the card after a page refresh)."""
    state = PENDING.get(session_id)
    if state is not None and not state.future.done():
        return list(state.questions)
    try:
        import json
        p = _marker_path(session_id)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("questions") or None
    except Exception:
        pass
    return None


def cancel_pending(session_id: str) -> None:
    """Clean up any pending question for a session (stop button, run cancel)."""
    state = PENDING.pop(session_id, None)
    _remove_marker(session_id)
    if state is not None:
        if state.timeout_task and not state.timeout_task.done():
            state.timeout_task.cancel()
        if not state.future.done():
            state.future.set_result({"timeout": True, "answers": []})
