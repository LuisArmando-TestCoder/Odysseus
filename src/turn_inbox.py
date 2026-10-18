# src/turn_inbox.py
"""Per-session turn queue — Layer 0 / F0.6 primitives.

Queue-don't-cancel semantics (Cluster C/I1 will wire these into chat_routes):
a NEW user message arriving while a detached run is live must be persisted
and queued instead of cancelling the run. When the current run terminates,
``drain_on_finish`` (registered via ``agent_runs.on_finish`` by Cluster C/I1)
starts the next queued turn.

Each queue entry is a RICH dict (not a bare string) so the queued turn can be
rebuilt with the SAME context fidelity as a live turn: the built system preface
(memory/RAG/web/persona) plus the preset's temperature / max_tokens. The user
message itself is intentionally NOT stored as context — the caller persists it
to session history BEFORE enqueueing (see I1), so the queued turn rebuilds
``preface + live history`` and never duplicates the message.

In-memory only — the same durability scope as ``agent_runs``: a server restart
loses the queue ORDER but never the message (the caller persists the message
to session history BEFORE enqueueing; see I1).
"""

import logging
from collections import deque
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# session_id -> deque of queued entries (oldest first). Each entry is a dict:
#   {"message": str, "preface": list, "temperature": float|None,
#    "max_tokens": int|None}
_PENDING: Dict[str, deque] = {}

# Registered by routes/chat_routes at startup (Cluster C/I1): builds the
# detached stream for a queued entry. Returns (async_generator, owner).
# A duplicate registration is a programming error.
# The second arg is the rich queue entry dict (see enqueue()).
StreamFactory = Callable[[str, dict], Tuple[AsyncGenerator[str, None], Optional[str]]]
_factory: Optional[StreamFactory] = None


def register_stream_factory(factory: StreamFactory) -> None:
    global _factory
    if _factory is not None:
        raise RuntimeError("turn_inbox: stream factory already registered")
    _factory = factory


def enqueue(session_id: str, message: str, *, preface: Optional[list] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """Queue a NEW user message that arrived during a live run.

    ``preface`` is the built system context (memory/RAG/web/persona messages)
    captured when the message was sent, so the queued turn rebuilds the same
    prompt fidelity as a live turn. The message itself is NOT re-stored — the
    caller persists it to session history first; the queued turn rebuilds
    ``preface + live history`` (which already contains the message).

    Returns {"state": "queued", "position": N} (1-based).
    """
    q = _PENDING.setdefault(session_id, deque())
    entry = {
        "message": message,
        "preface": list(preface) if preface else [],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    q.append(entry)
    return {"state": "queued", "position": len(q)}


def is_queued(session_id: str) -> bool:
    return bool(_PENDING.get(session_id))


def queued_count(session_id: str) -> int:
    q = _PENDING.get(session_id)
    return len(q) if q else 0


def pending_items(session_id: str) -> List[str]:
    """The queued message strings (oldest first) — for dedup checks."""
    q = _PENDING.get(session_id)
    return [e.get("message", "") for e in q] if q else []


def entries(session_id: str) -> List[Dict[str, Any]]:
    """Inspectable view of the queue for the frontend (list + cancel)."""
    q = _PENDING.get(session_id)
    if not q:
        return []
    out = []
    for i, e in enumerate(q, start=1):
        out.append({
            "position": i,
            "message": e.get("message", ""),
        })
    return out


def remove(session_id: str, position: int) -> bool:
    """Cancel the queued entry at ``position`` (1-based). Returns True if removed."""
    q = _PENDING.get(session_id)
    if not q or position < 1 or position > len(q):
        return False
    try:
        del q[position - 1]
    except IndexError:
        return False
    if not q:
        _PENDING.pop(session_id, None)
    return True


def clear(session_id: str) -> None:
    _PENDING.pop(session_id, None)


def start_next(session_id: str) -> bool:
    """Start a detached run for the oldest queued entry, if the session is
    idle and a factory is registered. Returns True when a run was started."""
    if _factory is None:
        return False
    q = _PENDING.get(session_id)
    if not q:
        return False
    from src import agent_runs
    if agent_runs.is_active(session_id):
        return False
    entry = q.popleft()
    if not q:
        _PENDING.pop(session_id, None)
    try:
        agen, owner = _factory(session_id, entry)
    except Exception as e:
        # The message is already persisted in history (I1 contract), so it is
        # never lost — a failing factory leaves it for the next send. The
        # follower stream detects this (queue drained + no run) and surfaces
        # a terminal `turn_failed` event to the client.
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