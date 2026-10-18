# src/turn_inbox.py
"""Per-session turn queue — Layer 0 / F0.6 primitives.

Queue-don't-cancel semantics (Cluster C/I1 will wire these into chat_routes):
a NEW user message arriving while a detached run is live must be persisted
and queued instead of cancelling the run. When the current run terminates,
``drain_on_finish`` (registered via ``agent_runs.on_finish`` by Cluster C/I1)
starts the next queued turn.

In-memory only — the same durability scope as ``agent_runs``: a server restart
loses the queue ORDER but never the message (the caller persists the message
to session history BEFORE enqueueing; see I1).
"""

import logging
from collections import deque
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# session_id -> deque of queued user messages (oldest first)
_PENDING: Dict[str, deque] = {}

# Registered by routes/chat_routes at startup (Cluster C/I1): builds the
# detached stream for a queued message. Returns (async_generator, owner).
# A duplicate registration is a programming error.
StreamFactory = Callable[[str, str], Tuple[AsyncGenerator[str, None], Optional[str]]]
_factory: Optional[StreamFactory] = None


def register_stream_factory(factory: StreamFactory) -> None:
    global _factory
    if _factory is not None:
        raise RuntimeError("turn_inbox: stream factory already registered")
    _factory = factory


def enqueue(session_id: str, message: str) -> Dict[str, Any]:
    """Queue a NEW user message that arrived during a live run.

    Returns {"state": "queued", "position": N} (1-based). The caller must
    have persisted the message to session history first.
    """
    q = _PENDING.setdefault(session_id, deque())
    q.append(message)
    return {"state": "queued", "position": len(q)}


def is_queued(session_id: str) -> bool:
    return bool(_PENDING.get(session_id))


def queued_count(session_id: str) -> int:
    q = _PENDING.get(session_id)
    return len(q) if q else 0


def pending_items(session_id: str) -> List[str]:
    q = _PENDING.get(session_id)
    return list(q) if q else []


def clear(session_id: str) -> None:
    _PENDING.pop(session_id, None)


def start_next(session_id: str) -> bool:
    """Start a detached run for the oldest queued message, if the session is
    idle and a factory is registered. Returns True when a run was started."""
    if _factory is None:
        return False
    q = _PENDING.get(session_id)
    if not q:
        return False
    from src import agent_runs
    if agent_runs.is_active(session_id):
        return False
    message = q.popleft()
    if not q:
        _PENDING.pop(session_id, None)
    try:
        agen, owner = _factory(session_id, message)
    except Exception as e:
        # The message is already persisted in history (I1 contract), so it is
        # never lost — a failing factory just leaves it for the next send.
        logger.error("[turn-inbox] stream factory failed for %s: %s", session_id, e)
        return False
    agent_runs.start(session_id, agen, owner=owner)
    return True


def drain_on_finish(session_id: str, status: str) -> None:
    """agent_runs.on_finish callback (registered by Cluster C/I1): when a run
    terminates, start the next queued turn. Contained — never raises."""
    try:
        if status not in ("done", "stopped", "error"):
            return
        start_next(session_id)
    except Exception as e:
        logger.error("[turn-inbox] drain failed for %s: %s", session_id, e)
