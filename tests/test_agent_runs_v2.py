"""Tests for src/agent_runs.py v2 — publish / owner / on_finish / list_active (F0.2).

Agent_runs is dependency-light (asyncio/json/logging), so these run without the
app stack. Fixtures scrub _RUNS and _FINISH_CALLBACKS so tests are isolated,
and neutralize the eviction grace timer so terminal runs don't get popped
mid-assertion.
"""

import asyncio

import pytest

import src.agent_runs as ar


@pytest.fixture(autouse=True)
def _clean_runs(monkeypatch):
    # Neutralize the post-finish eviction timer during tests.
    monkeypatch.setattr(ar, "_evict_grace", lambda: 3600.0)
    ar._RUNS.clear()
    ar._FINISH_CALLBACKS.clear()
    yield
    for run in list(ar._RUNS.values()):
        if run.evict_task and not run.evict_task.done():
            run.evict_task.cancel()
        if run.task and not run.task.done():
            run.task.cancel()
    ar._RUNS.clear()
    ar._FINISH_CALLBACKS.clear()


async def _gen(*events):
    for ev in events:
        yield ev
        await asyncio.sleep(0.01)


async def test_start_records_owner_and_list_active():
    ar.start("s1", _gen("data: x\n\n"), owner="alice")
    assert ar.is_active("s1")
    acts = ar.list_active()
    assert {"session_id": "s1", "status": "running", "owner": "alice"} in acts
    await asyncio.sleep(0.05)
    assert ar.get_status("s1") == "done"


async def test_publish_injects_into_live_run_and_noop_when_absent():
    ar.start("s1", _gen("data: first\n\n"))
    await asyncio.sleep(0.02)
    assert ar.publish("s1", 'data: {"type":"goal_update"}\n\n') is True
    assert ar.publish("missing", "data: x\n\n") is False
    await asyncio.sleep(0.05)
    run = ar._RUNS.get("s1")
    joined = "".join(run.buffer)
    assert "first" in joined
    assert "goal_update" in joined


async def test_publish_to_owner_fans_only_to_owner_runs():
    ar.start("a1", _gen("data: a\n\n"), owner="alice")
    ar.start("b1", _gen("data: b\n\n"), owner="bob")
    ar.start("a2", _gen("data: a2\n\n"), owner="alice")
    await asyncio.sleep(0.02)
    ar.publish_to_owner("alice", 'data: {"type":"goal_update"}\n\n')
    ar.publish_to_owner("nobody", "data: z\n\n")
    await asyncio.sleep(0.05)
    joined_a1 = "".join(ar._RUNS["a1"].buffer)
    joined_a2 = "".join(ar._RUNS["a2"].buffer)
    joined_b1 = "".join(ar._RUNS["b1"].buffer)
    assert "goal_update" in joined_a1 and "goal_update" in joined_a2
    assert "goal_update" not in joined_b1
    assert "z" not in joined_b1


async def test_on_finish_callback_fires_with_session_and_status():
    seen = []

    def _cb(session_id, status):
        seen.append((session_id, status))

    ar.on_finish(_cb)
    ar.start("s9", _gen("data: x\n\n"))
    await asyncio.sleep(0.05)
    assert ("s9", "done") in seen


async def test_on_finish_async_callback_runs_and_throws_are_contained():
    seen = []

    async def _ok(session_id, status):
        seen.append(status)

    def _bad(session_id, status):
        raise RuntimeError("callback boom")

    ar.on_finish(_ok)
    ar.on_finish(_bad)
    ar.start("s10", _gen("data: x\n\n"))
    await asyncio.sleep(0.08)
    assert seen == ["done"]  # bad callback didn't break the drain


async def test_publish_into_terminal_run_appends_buffer():
    ar.start("s11", _gen("data: x\n\n"))
    await asyncio.sleep(0.05)
    assert ar.get_status("s11") == "done"
    # Terminal-but-still-grace-retained runs still accept publish (harmless).
    assert ar.publish("s11", "data: late\n\n") is True
