"""Tests for src/subagents.py — fork/spawn, registry, depth cap, steering (B1–B4)."""

import asyncio
import json
from dataclasses import dataclass

import pytest

import src.agent_runs as agent_runs
import src.subagents as subs
from src import constants


@dataclass
class _Msg:
    role: str
    content: str


class _FakeSess:
    def __init__(self, sid, owner="alice", name=None):
        self.id = sid
        self.name = name or f"chat-{sid}"
        self.owner = owner
        self.endpoint_url = "http://e"
        self.model = "m"
        self.headers = {}
        self.context_length = 0
        self.history = []

    def get_context_messages(self):
        return [{"role": m.role, "content": m.content} for m in self.history]

    def add_message(self, msg):
        self.history.append(msg)


class _FakeSM:
    def __init__(self):
        self.sessions = {}

    def create_session(self, **kw):
        s = _FakeSess(kw["session_id"], owner=kw.get("owner", "alice"), name=kw.get("name"))
        s.endpoint_url = kw.get("endpoint_url") or ""
        s.model = kw.get("model") or ""
        self.sessions[kw["session_id"]] = s
        return s

    def get_session(self, sid):
        return self.sessions.get(sid)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_runs, "_evict_grace", lambda: 3600.0)
    fake_sm = _FakeSM()
    monkeypatch.setattr(subs, "_session_manager", lambda: fake_sm)
    subs.SUBS.clear()
    subs._BY_SESSION.clear()
    subs._WAIT_FUTURES.clear()
    subs._HOOK_REGISTERED = False
    agent_runs._RUNS.clear()
    agent_runs._FINISH_CALLBACKS.clear()

    async def _stub_loop(*a, **kw):
        yield 'data: {"delta": "child result"}\n\n'
        yield "data: [DONE]\n\n"

    import src.agent_loop as al
    monkeypatch.setattr(al, "stream_agent_loop", _stub_loop)
    yield
    for run in list(agent_runs._RUNS.values()):
        if run.evict_task and not run.evict_task.done():
            run.evict_task.cancel()
        if run.task and not run.task.done():
            run.task.cancel()
    agent_runs._RUNS.clear()
    agent_runs._FINISH_CALLBACKS.clear()
    subs.SUBS.clear()
    subs._BY_SESSION.clear()
    subs._WAIT_FUTURES.clear()
    subs._HOOK_REGISTERED = False


def _make_parent():
    sm = subs._session_manager()
    s = sm.create_session(session_id="parent", owner="alice", name="Parent")
    s.history = [_Msg("user", "discuss A"), _Msg("assistant", "about A")]
    return s


def test_fork_child_session_copies_history():
    _make_parent()
    child = subs.fork_child_session("parent", "alice")
    assert child is not None
    sess = subs._session_manager().get_session(child["session_id"])
    assert sess.name.startswith("Fork: ")
    assert [m.content for m in sess.history] == ["discuss A", "about A"]


def test_spawn_child_session_empty():
    child = subs.spawn_child_session("alice", "do a thing")
    assert child is not None
    sess = subs._session_manager().get_session(child["session_id"])
    assert sess.name.startswith("Agent: ")


async def test_depth_cap_rejects():
    _make_parent()
    subs.SUBS["deep"] = {"depth": 3, "owner": "alice", "session_id": "parent", "status": "running", "task": "x"}
    subs._BY_SESSION["parent"] = "deep"
    res = await subs.run_subagent(
        parent_session_id="parent", owner="alice", mode="spawn",
        task="sub", run_in_background=False,
    )
    assert res.get("error_code") == "MAX_DEPTH"


async def test_spawn_background_registers_and_settles(monkeypatch):
    _make_parent()
    # A live run for the owner so the settlement notice has somewhere to land.
    async def _parent_gen():
        yield "data: x\n\n"
    agent_runs.start("parent", _parent_gen(), owner="alice")
    await asyncio.sleep(0.02)

    res = await subs.run_subagent(
        parent_session_id="parent", owner="alice", mode="spawn",
        task="do the research", run_in_background=True,
    )
    assert res.get("subagent_started") is True
    assert res["run_in_background"] is True
    sub_id = res["subagent_id"]
    child_sid = res["child_session_id"]
    assert agent_runs.is_active(child_sid)
    assert subs.SUBS[sub_id]["mode"] == "continuable"

    # Let the child finish → B4 settlement notice fanned into the owner's runs.
    await asyncio.sleep(0.1)
    joined = "".join(agent_runs._RUNS["parent"].buffer)
    assert '"subagent_done"' in joined
    assert sub_id in joined


async def test_spawn_foreground_returns_child_output():
    _make_parent()
    res = await subs.run_subagent(
        parent_session_id="parent", owner="alice", mode="spawn",
        task="do the research", run_in_background=False,
    )
    assert res["run_in_background"] is False
    fut = subs.wait_run_future(res["child_session_id"])
    assert fut is not None
    # Simulate the agent loop's heartbeat wait.
    status = None
    async def _wait():
        nonlocal status
        status = await fut
    await asyncio.wait_for(_wait(), timeout=2.0)
    assert status == "done"
    out = subs.collect_output(res["child_session_id"], status)
    assert out == "child result"


async def test_append_message_and_resume():
    _make_parent()
    res = await subs.run_subagent(
        parent_session_id="parent", owner="alice", mode="spawn",
        task="first", run_in_background=True,
    )
    sub_id = res["subagent_id"]
    await asyncio.sleep(0.08)  # let the first run finish
    r = subs.append_message_and_resume(sub_id, "next task", "alice")
    assert r["exit_code"] == 0
    sess = subs._session_manager().get_session(res["child_session_id"])
    assert sess.history[-1].content == "next task"
    assert agent_runs.is_active(res["child_session_id"])
    await asyncio.sleep(0.05)


async def test_append_message_owner_guard():
    _make_parent()
    res = await subs.run_subagent(
        parent_session_id="parent", owner="alice", mode="spawn",
        task="first", run_in_background=True,
    )
    r = subs.append_message_and_resume(res["subagent_id"], "hi", "mallory")
    assert r["exit_code"] == 1
    assert "not your subagent" in r["error"]


def test_list_subs_filters_by_owner():
    _make_parent()
    subs.SUBS["a1"] = {"id": "a1", "session_id": "s1", "parent_id": "p",
                       "depth": 1, "status": "running", "mode": "continuable",
                       "task": "t1", "owner": "alice"}
    subs.SUBS["b1"] = {"id": "b1", "session_id": "s2", "parent_id": "p",
                       "depth": 1, "status": "running", "mode": "continuable",
                       "task": "t2", "owner": "bob"}
    assert [e["id"] for e in subs.list_subs("alice")] == ["a1"]
    assert len(subs.list_subs(None)) == 2


def test_interrupt_and_job_status():
    _make_parent()
    subs.SUBS["a1"] = {"id": "a1", "session_id": "s9", "parent_id": "p",
                       "depth": 1, "status": "running", "mode": "continuable",
                       "task": "t1", "owner": "alice"}
    r = subs.interrupt("a1", "alice")
    assert r["exit_code"] == 0
    r = subs.interrupt("nope", "alice")
    assert r["exit_code"] == 1
    st = subs.job_status("a1", "alice")
    assert st["exit_code"] == 0
    assert "a1" in st["output"]
    assert subs.job_status("a1", "mallory")["exit_code"] == 1
