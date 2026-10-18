"""Tests for src/stream_util.py — heartbeat_wait / heartbeat_stream (F0.1)."""

import asyncio

from src.stream_util import HEARTBEAT, heartbeat_wait, heartbeat_stream


async def _slow_value(delay: float, value):
    await asyncio.sleep(delay)
    return value


async def test_heartbeat_wait_slow_yields_heartbeats_then_value():
    items = []
    async for item in heartbeat_wait(_slow_value(0.25, "done"), interval=0.05):
        items.append(item)
    assert items and items[-1] == "done"
    # At least one heartbeat must have been emitted while waiting.
    assert any(i == HEARTBEAT for i in items[:-1])


async def test_heartbeat_wait_fast_yields_only_value():
    items = []
    async for item in heartbeat_wait(_slow_value(0.0, 42), interval=15.0):
        items.append(item)
    assert items == [42]


async def test_heartbeat_wait_propagates_exceptions():
    async def _boom():
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")

    try:
        async for _ in heartbeat_wait(_boom(), interval=0.02):
            pass
        raised = False
    except RuntimeError:
        raised = True
    assert raised


async def test_heartbeat_stream_passes_items_and_injects_on_stall():
    async def _src():
        yield "a"
        await asyncio.sleep(0.2)
        yield "b"

    out = []
    async for item in heartbeat_stream(_src(), interval=0.05):
        out.append(item)
    assert out[0] == "a" and out[-1] == "b"
    assert HEARTBEAT in out
    # Order: a, heartbeats..., b
    assert out.index("b") > out.index("a")


async def test_heartbeat_stream_fast_no_heartbeats():
    async def _src():
        yield "x"
        yield "y"

    out = [i async for i in heartbeat_stream(_src(), interval=15.0)]
    assert out == ["x", "y"]
