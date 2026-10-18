"""Tests for the DSH-parity pieces added in the UI-parity round:
file-edit guard (read-before-write + diff), hook registration, todo_write."""

import json
import os
import types

import pytest

from src import file_guard as fg
from src import hooks
from src.checkpoint import checkpoint_queue_state
from src.tool_execution import execute_tool_block


def test_mark_read_roundtrip(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    assert not fg.was_read("s1", str(p))
    fg.mark_read("s1", str(p))
    assert fg.was_read("s1", str(p))
    assert not fg.was_read("s2", str(p))


async def test_pre_and_post_hooks_diff_and_unread_warning(tmp_path, monkeypatch):
    p = tmp_path / "notes.md"
    p.write_text("old line\n", encoding="utf-8")
    sid = "diff-session"

    payload = {"tool": "write_file", "content": f"{p}\nnew line\n", "session_id": sid, "result": None}
    await fg.pre_tool_use_hook(payload)  # snapshot

    # simulate the write
    p.write_text("new line\n", encoding="utf-8")

    payload["result"] = {"output": "Wrote 9 bytes", "exit_code": 0}
    out = await fg.post_tool_use_hook(payload)
    res = out["result"]
    assert res["diff"]["added"] == 1 and res["diff"]["removed"] == 1
    assert "[GUARD]" in res["output"]          # never read in this session
    assert res["error_code"] == "unread_edit"


async def test_read_then_write_no_guard(tmp_path):
    p = tmp_path / "ok.txt"
    p.write_text("v1\n", encoding="utf-8")
    sid = "rw-session"
    fg.mark_read(sid, str(p))
    await fg.pre_tool_use_hook({"tool": "write_file", "content": f"{p}\nv2\n", "session_id": sid, "result": None})
    p.write_text("v2\n", encoding="utf-8")
    out = await fg.post_tool_use_hook(
        {"tool": "write_file", "content": f"{p}\nv2\n", "session_id": sid,
         "result": {"output": "Wrote", "exit_code": 0}})
    assert "[GUARD]" not in out["result"]["output"]


def test_run_hooks_registers_builtins_idempotent():
    hooks.register_builtins()
    n1 = len(hooks._REGISTRY.get(hooks.HookPoint.POST_TOOL_USE, ()))
    hooks.register_builtins()
    n2 = len(hooks._REGISTRY.get(hooks.HookPoint.POST_TOOL_USE, ()))
    assert n1 == n2 >= 1


async def test_todo_write_tool_sets_checkpoint_queue(tmp_path, monkeypatch):
    sid = "todo-session"
    content = json.dumps({"items": ["alpha", "beta", "gamma"]})
    block = types.SimpleNamespace(tool_type="todo_write", content=content)
    _desc, result = await execute_tool_block(block, owner=None, session_id=sid)
    assert result.get("exit_code") == 0, result
    state = checkpoint_queue_state(sid)
    assert state["remaining"] == ["alpha", "beta", "gamma"]
    assert state["done"] == []
