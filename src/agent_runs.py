"""Detached agent-run manager.

Keeps an agent/chat stream running server-side after the SSE client disconnects
(tab close, navigate away, refresh). The streaming generator is drained by a
background asyncio task into a per-session replay buffer; SSE clients SUBSCRIBE
to that buffer (replay everything so far, then live). Closing the SSE only drops
the subscriber — the drain task keeps going.

The wrapped generator already persists the assistant message to the session on
completion, so reopening the session shows the finished result even if nobody
was connected when it finished. Reconnecting mid-run replays the buffer + streams
live (pick up where it is).

Durability scope: in-memory, survives as long as the server process runs (tab
close / navigation / refresh). It does NOT survive a server restart.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Layer 0 / F0.2: out-of-band event injection, owner tracking, run inventory
# and run-termination callbacks. Registered callbacks are contained — a
# throwing or hanging callback can never break `_drain` (it runs as its own
# task) and `publish*` is a no-op when no run exists.
_RunFinishCallback = Callable[[str, str], Optional[Awaitable[None]]]
_FINISH_CALLBACKS: List[_RunFinishCallback] = []


def on_finish(cb: _RunFinishCallback) -> None:
    """Register a callback invoked as `cb(session_id, status)` when a run
    terminates (done/stopped/error), after the replay buffer and subscribers
    are finalized. Sync and async callbacks are both supported; async ones
    are scheduled as contained tasks."""
    if cb not in _FINISH_CALLBACKS:
        _FINISH_CALLBACKS.append(cb)


class _Run:
    __slots__ = ("buffer", "subscribers", "status", "task", "evict_task", "owner")

    def __init__(self, owner: Optional[str] = None) -> None:
        self.buffer: list = []          # ordered SSE event strings (replay log)
        self.subscribers: set = set()   # one asyncio.Queue per connected client
        self.status: str = "running"    # running | done | error | stopped
        self.task: Optional[asyncio.Task] = None
        self.evict_task: Optional[asyncio.Task] = None
        self.owner: Optional[str] = owner


_RUNS: Dict[str, _Run] = {}

# How long a FINISHED run (and its full replay buffer) is retained after the
# last subscriber disconnects, so a reconnect within the window can still
# replay the result. After this, the run is evicted to bound memory — without
# it, every session that ever streamed kept its entire event log forever.
# Tunable via the `agent_run_evict_grace_seconds` setting (Layer 0 / F0.5).
_EVICT_GRACE_S = 180


def _evict_grace() -> float:
    try:
        from src.settings import get_setting
        return max(5.0, float(get_setting("agent_run_evict_grace_seconds", _EVICT_GRACE_S)))
    except Exception:
        return float(_EVICT_GRACE_S)


def _buffer_cap() -> int:
    try:
        from src.settings import get_setting
        return int(get_setting("agent_run_buffer_max_events", 5000) or 5000)
    except Exception:
        return 5000


def _publish(run: _Run, ev: str) -> None:
    """Append one SSE event and fan it out to every live subscriber."""
    run.buffer.append(ev)
    seq = len(run.buffer) - 1
    for q in list(run.subscribers):
        try:
            q.put_nowait((seq, ev))
        except Exception:
            pass
    # S1 — bound the replay buffer for very long runs: keep head + tail and a
    # notice about the dropped middle. Live subscribers already received the
    # dropped events; the truncation only affects NEW subscribers' replay
    # (they get the notice and re-sync from the live tail).
    try:
        cap = max(200, int(_buffer_cap()))
    except Exception:
        cap = 5000
    if len(run.buffer) > cap + 100:
        keep_head = cap // 2
        dropped = len(run.buffer) - cap
        notice = (
            'data: ' + json.dumps({"type": "run_buffer_truncated", "data": {"dropped": dropped}}) + '\n\n'
        )
        run.buffer = run.buffer[:keep_head] + [notice] + run.buffer[-(cap - keep_head):]


def _schedule_evict(session_id: str) -> None:
    """(Re)arm a grace-period eviction for a terminal run with no subscribers.
    Identity-checked so a run that gets replaced/reused is never evicted by a
    stale timer."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    if run.evict_task and not run.evict_task.done():
        run.evict_task.cancel()

    async def _evict(run_ref: _Run) -> None:
        try:
            await asyncio.sleep(_evict_grace())
        except asyncio.CancelledError:
            return
        cur = _RUNS.get(session_id)
        if cur is run_ref and cur.status != "running" and not cur.subscribers:
            _RUNS.pop(session_id, None)

    run.evict_task = asyncio.create_task(_evict(run))


def is_active(session_id: str) -> bool:
    r = _RUNS.get(session_id)
    return bool(r and r.status == "running")


def get_status(session_id: str) -> Optional[str]:
    r = _RUNS.get(session_id)
    return r.status if r else None


# ---------------------------------------------------------------------------
# Layer 0 / F0.2 — out-of-band events, owner inventory, finish callbacks
# ---------------------------------------------------------------------------

def publish(session_id: str, event: str) -> bool:
    """Inject one SSE event string into a session's run (replay buffer +
    live subscribers) from OUTSIDE the run's own generator. No-op (False)
    when no run exists for the session — callers treat this as drop-on-floor
    (consumers that need the data poll their own endpoints on load)."""
    run = _RUNS.get(session_id)
    if run is None:
        return False
    _publish(run, event)
    return True


def publish_to_owner(owner: str, event: str) -> None:
    """Fan one event into every live run whose start() declared ``owner``."""
    if not owner:
        return
    for run in list(_RUNS.values()):
        if run.owner == owner:
            _publish(run, event)


def list_active() -> List[dict]:
    """Cheap inventory of all runs (sidebar dots, /api/chat/active)."""
    return [
        {"session_id": sid, "status": run.status, "owner": run.owner}
        for sid, run in _RUNS.items()
    ]


def _dispatch_finish_callbacks(session_id: str, status: str) -> None:
    """Fire on_finish callbacks as contained tasks (never raise/block here)."""
    for cb in list(_FINISH_CALLBACKS):
        try:
            res = cb(session_id, status)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                asyncio.ensure_future(res)
        except Exception:
            logger.error("[agent-run] finish callback failed", exc_info=True)


async def _drain(session_id: str, agen: AsyncGenerator[str, None],
                 prev_task: Optional[asyncio.Task] = None) -> None:
    """Pull every event from the wrapped generator into the run buffer, fanning
    each out to live subscribers. Runs to completion regardless of subscribers."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    # If this run replaced an in-flight one (rapid double-send), wait for that
    # one to fully finish first. Its CancelledError handler calls aclose(), which
    # persists its partial response — letting it complete before we start writing
    # keeps the two runs' session saves sequential instead of interleaved.
    if prev_task is not None and not prev_task.done():
        try:
            await asyncio.wait({prev_task})
        except asyncio.CancelledError:
            raise            # our own cancellation — propagate
        except Exception:
            pass
    try:
        async for ev in agen:
            _publish(run, ev)
        if run.status == "running":
            run.status = "done"
    except asyncio.CancelledError:
        run.status = "stopped"
        # Let the wrapped generator's own CancelledError handler run (it saves
        # the partial response to the session).
        try:
            await agen.aclose()
        except Exception:
            pass
    except Exception as e:
        logger.error("[agent-run] %s failed: %s", session_id, e, exc_info=True)
        run.status = "error"
        _publish(
            run,
            "event: error\n"
            f"data: {json.dumps({'error': 'Agent run failed before completion.', 'status': 500})}\n\n",
        )
        _publish(run, "data: [DONE]\n\n")
    finally:
        # Wake every subscriber with the end sentinel so their SSE closes.
        for q in list(run.subscribers):
            try:
                q.put_nowait((None, None))
            except Exception:
                pass
        # Run is terminal — arm the grace timer so it (and its buffer) is
        # eventually freed even if nobody ever reconnects. subscribe() cancels
        # this on connect and re-arms on disconnect.
        _schedule_evict(session_id)
        # Layer 0 / F0.2: notify run-finish listeners (turn inbox, goal
        # driver). Contained: callbacks run as their own tasks.
        _dispatch_finish_callbacks(session_id, run.status)


def start(session_id: str, agen: AsyncGenerator[str, None],
          *, owner: Optional[str] = None) -> _Run:
    """Start a detached run draining `agen` for a session. If a run is already in
    flight for this session (e.g. a rapid double-send), it's cancelled first.
    `owner` is stored for publish_to_owner()/list_active() routing."""
    prev = _RUNS.get(session_id)
    prev_task: Optional[asyncio.Task] = None
    if prev:
        if prev.task and not prev.task.done():
            prev.task.cancel()
            prev_task = prev.task   # new run awaits this before it starts writing
        if prev.evict_task and not prev.evict_task.done():
            prev.evict_task.cancel()
    run = _Run(owner=owner)
    _RUNS[session_id] = run
    run.task = asyncio.create_task(_drain(session_id, agen, prev_task))
    return run


async def subscribe(session_id: str) -> AsyncGenerator[str, None]:
    """Replay the run's buffer from the start, then stream live until it ends.
    Safe to call repeatedly (reconnect) and from multiple clients at once."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    q: asyncio.Queue = asyncio.Queue()
    run.subscribers.add(q)            # register BEFORE replaying so nothing is missed
    # A live subscriber is connected — don't let a pending grace timer evict
    # the run out from under it mid-replay.
    if run.evict_task and not run.evict_task.done():
        run.evict_task.cancel()
    try:
        next_seq = 0
        while next_seq < len(run.buffer):
            yield run.buffer[next_seq]
            next_seq += 1
        if run.status != "running":
            return
        while True:
            seq, ev = await q.get()
            if seq is None:            # end sentinel
                while next_seq < len(run.buffer):   # flush any tail the sentinel raced
                    yield run.buffer[next_seq]
                    next_seq += 1
                break
            if seq >= next_seq:        # skip events already replayed from the buffer
                yield ev
                next_seq = seq + 1
    finally:
        run.subscribers.discard(q)
        # Last subscriber gone on a finished run — (re)arm eviction so the
        # buffer doesn't linger indefinitely.
        if not run.subscribers and run.status != "running":
            _schedule_evict(session_id)


def stop(session_id: str) -> bool:
    """Cancel an in-flight run (the wrapped generator saves its partial)."""
    run = _RUNS.get(session_id)
    if run and run.task and not run.task.done():
        run.task.cancel()
        return True
    return False
