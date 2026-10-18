"""Tests for src/user_questions.py — the ask-user registry (Cluster B / Q1)."""

import asyncio
import json
import os

import pytest

from src import constants
from src import user_questions as uq


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    uq.PENDING.clear()
    yield
    for st in list(uq.PENDING.values()):
        if st.timeout_task and not st.timeout_task.done():
            st.timeout_task.cancel()
    uq.PENDING.clear()


async def test_root_only_rule():
    res = uq.ask("s1", [{"id": "q1", "question": "x?"}], can_ask_user=False)
    assert res.get("error_code") == "DELEGATED_CALLER"
    assert uq.get_future("s1") is None


async def test_ask_registers_future_and_marker(tmp_path):
    res = uq.ask("s1", [{"id": "q1", "question": "x?"}], can_ask_user=True)
    assert res["status"] == "pending"
    fut = uq.get_future("s1")
    assert fut is not None and not fut.done()
    # Marker file persisted for page-refresh recovery.
    p = uq._marker_path("s1")
    assert os.path.exists(p)
    with open(p, encoding="utf-8") as f:
        assert json.load(f)["questions"][0]["id"] == "q1"


async def test_already_pending_rejected():
    uq.ask("s1", [{"id": "q1", "question": "x?"}], can_ask_user=True)
    res = uq.ask("s1", [{"id": "q2", "question": "y?"}], can_ask_user=True)
    assert res.get("error_code") == "ALREADY_PENDING"


async def test_answer_resolves_future_and_clears():
    uq.ask("s1", [{"id": "q1", "question": "x?"}], can_ask_user=True)
    fut = uq.get_future("s1")
    assert uq.answer("s1", [{"id": "q1", "selected": ["A"], "custom": ""}]) is True
    payload = fut.result()
    assert payload["timeout"] is False
    assert payload["answers"][0]["selected"] == ["A"]
    assert uq.get_future("s1") is None
    assert uq.get_pending("s1") is None
    assert not os.path.exists(uq._marker_path("s1"))


async def test_answer_unknown_session_false():
    assert uq.answer("nope", [{"id": "q1", "selected": []}]) is False


async def test_timeout_auto_resolves(monkeypatch):
    monkeypatch.setattr(uq, "_timeout_seconds", lambda: 0.05)
    uq.ask("s1", [{"id": "q1", "question": "x?"}], can_ask_user=True)
    fut = uq.get_future("s1")
    await asyncio.sleep(0.15)
    assert fut.done()
    assert fut.result()["timeout"] is True
    assert uq.get_pending("s1") is None


async def test_get_pending_reads_marker_after_restart_simulation():
    uq.ask("s1", [{"id": "q1", "question": "x?"}], can_ask_user=True)
    # Simulate a page refresh / fresh process: pending futures gone, marker stays.
    uq.PENDING.clear()
    qs = uq.get_pending("s1")
    assert qs is not None and qs[0]["id"] == "q1"
    # Cleanup: answering with no live future still clears the marker.
    assert uq.answer("s1", [{"id": "q1", "selected": ["A"]}]) is False
    uq.cancel_pending("s1")
    assert uq.get_pending("s1") is None


async def test_cancel_pending_resolves_with_timeout():
    uq.ask("s1", [{"id": "q1", "question": "x?"}], can_ask_user=True)
    fut = uq.get_future("s1")
    uq.cancel_pending("s1")
    assert fut.done() and fut.result()["timeout"] is True
    assert uq.get_pending("s1") is None
