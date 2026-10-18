# src/stream_util.py
"""Shared SSE streaming helpers (Layer 0 / F0.1).

Every long await inside the chat/agent/research paths must keep the SSE
connection alive, or proxies and browsers drop the stream while the server
is silently working (the same failure mode the middleman's
``_guard_both_with_heartbeats`` and the research progress loop already
guard against). These two helpers centralize that pattern so new wait sites
(ask-user, subagent await, retry backoff, spill writes, goal-round setup)
don't re-implement it.

Two shapes, because there are two wait shapes in the codebase:

* ``heartbeat_wait(awaitable)`` — for awaiting ONE slow thing (a Future, a
  coroutine): yields ``: keep-alive`` comments while waiting and then yields
  the awaited RESULT as its final yielded value. Callers treat the last
  yielded item as the result, exactly like the middleman's
  ``_guard_both_with_heartbeats``.

* ``heartbeat_stream(agen)`` — for draining a long async generator (tool
  execution, streaming generation): passes items through verbatim and injects
  ``: keep-alive`` comments whenever the source stalls longer than the
  interval.

``: keep-alive`` is a comment line, ignored by every SSE parser (frontends
only act on ``data:`` / ``event:`` lines) but enough to keep the HTTP
connection alive through proxies and idle-tab throttling.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Awaitable

logger = logging.getLogger(__name__)

HEARTBEAT = ": keep-alive\n\n"
DEFAULT_INTERVAL = 15.0


async def heartbeat_wait(
    awaitable: Awaitable[Any],
    *,
    interval: float = DEFAULT_INTERVAL,
) -> AsyncGenerator[Any, None]:
    """Await ``awaitable``, yielding ``: keep-alive`` comments while it runs.

    The FINAL yielded item is the awaited result; every earlier item is a
    heartbeat comment. Usage::

        async for item in heartbeat_wait(future):
            if item == HEARTBEAT:
                yield item          # forward keep-alive to the SSE client
            else:
                result = item       # last item — the awaited value
    """
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if task in done:
                yield task.result()
                return
            yield HEARTBEAT
    finally:
        if not task.done():
            task.cancel()


_END = object()


async def heartbeat_stream(
    agen: AsyncGenerator[Any, None],
    *,
    interval: float = DEFAULT_INTERVAL,
) -> AsyncGenerator[Any, None]:
    """Pass through items of ``agen``, injecting keep-alives when it stalls.

    Uses a background pump task + queue so a heartbeat timeout NEVER cancels
    the wrapped generator (cancelling a pending ``__anext__`` would kill the
    producer mid-await — a real hazard with wait_for directly on the source).
    Yields the wrapped generator's items verbatim; between items that take
    longer than ``interval``, yields ``HEARTBEAT`` comments. Exceptions from
    the source propagate to the caller.
    """
    q: asyncio.Queue = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for item in agen:
                await q.put(item)
            await q.put(_END)
        except BaseException as exc:  # noqa: BLE001 — propagate the original error
            await q.put(exc)

    task = asyncio.ensure_future(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield HEARTBEAT
                continue
            if item is _END:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if not task.done():
            task.cancel()
