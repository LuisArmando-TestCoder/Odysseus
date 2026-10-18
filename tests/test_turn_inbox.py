"""Tests for src/turn_inbox.py — queue primitives (F0.6)."""

import asyncio

import pytest

import src.agent_runs as ar
import src.turn_inbox as ti


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ar, "_evict_grace", lambda: 3600.0)
    ti._PENDING.clear()
    ti._factory = None
    ar._RUNS.clear()
    ar._FINISH_CALLBACKS.clear()
    yield
    for run in list(ar._RUNS.values()):
        if run.evict_task and not run.evict_task.done():
            run.evict_task.cancel()
        if run.task and not run.task.done():
            run.task.cancel()
    ar._RUNS.clear()
    ti._PENDING.clear()
    ti._factory = None


async def _gen():
    yield "data: hi\n\n"


def _factory(session_id, message):
    return (_gen(), "alice")


def test_enqueue_positions_and_clear():
    assert ti.enqueue("s1", "m1") == {"state": "queued", "position": 1}
    assert ti.enqueue("s1", "m2") == {"state": "queued", "position": 2}
    assert ti.is_queued("s1")
    assert ti.queued_count("s1") == 2
    assert ti.pending_items("s1") == ["m1", "m2"]
    ti.clear("s1")
    assert not ti.is_queued("s1") and ti.queued_count("s1") == 0


def test_start_next_without_factory_keeps_queue():
    ti.enqueue("s1", "m1")
    assert ti.start_next("s1") is False
    assert ti.queued_count("s1") == 1  # message stays queued, never lost


def test_duplicate_factory_registration_raises():
    ti.register_stream_factory(_factory)
    with pytest.raises(RuntimeError):
        ti.register_stream_factory(_factory)


async def test_start_next_starts_detached_run_and_drains_queue():
    ti.register_stream_factory(_factory)
    ti.enqueue("s1", "m1")
    ti.enqueue("s1", "m2")

    assert ti.start_next("s1") is True
    assert ar.is_active("s1")
    assert ar._RUNS["s1"].owner == "alice"
    assert ti.queued_count("s1") == 1  # m2 still queued

    await asyncio.sleep(0.06)
    assert ar.get_status("s1") == "done"

    # Second drain starts the next queued message.
    assert ti.start_next("s1") is True
    assert ti.queued_count("s1") == 0


async def test_start_next_skips_while_run_active():
    ti.register_stream_factory(_factory)
    ar.start("s1", _gen(), owner="alice")
    ti.enqueue("s1", "m1")
    assert ti.start_next("s1") is False   # run active → don't start
    assert ti.queued_count("s1") == 1
    await asyncio.sleep(0.05)


def test_drain_on_finish_contained():
    # No factory, no queue, unknown session — must never raise.
    ti.drain_on_finish("nope", "done")
    ti.enqueue("s1", "m1")
    ti.drain_on_finish("s1", "done")  # no factory → False path, contained
    assert ti.queued_count("s1") == 1


async def test_drain_on_finish_starts_next_turn():
    ti.register_stream_factory(_factory)
    ar.start("s1", _gen(), owner="alice")
    ti.enqueue("s1", "m-queued")
    await asyncio.sleep(0.05)  # let the first run finish (no on_finish wiring here)
    # Simulate the Cluster C wiring: drain runs on finish.
    ti.drain_on_finish("s1", "done")
    assert not ti.is_queued("s1")
    assert ar.is_active("s1")  # the queued message's run started
    await asyncio.sleep(0.05)
    assert ar.get_status("s1") == "done"
