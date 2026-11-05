"""Long-horizon research: no wall-clock cap by default, a stall watchdog
instead, and periodic check-ins (2026-11-05).

The trigger was a deep-research run that was cut at exactly 900s after only
3 rounds while it was still making progress. Long investigations (e.g. ones
started from a phone and checked on later) must run until the AI decides it
is done, and the user must be able to keep an eye on them while they run.
"""
import asyncio
import time

import pytest

import src.research_handler as rh
from src.deep_research import DeepResearcher
from src.research_handler import ResearchHandler


class _FakeResearcher:
    def __init__(self):
        self.findings = []
        self.urls_fetched = set()
        self.round_count = 0
        self.evolving_report = ""
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def get_stats(self):
        return {"Rounds": self.round_count, "Queries": 1, "URLs": len(self.urls_fetched)}


def _settings(monkeypatch, **values):
    import src.settings as st
    real = st.get_setting

    def fake(key, default=None):
        if key in values:
            return values[key]
        if key in ("research_checkin_push",):
            return "off"
        return real(key, default)
    monkeypatch.setattr(st, "get_setting", fake)


@pytest.fixture
def handler(monkeypatch, tmp_path):
    monkeypatch.setattr(rh, "RESEARCH_DATA_DIR", tmp_path)
    h = ResearchHandler()
    monkeypatch.setattr(h, "_save_result", lambda *a, **k: None)
    return h


async def _wait_done(h, sid, timeout=10):
    t0 = time.time()
    while h._active_tasks[sid]["status"] == "running":
        assert time.time() - t0 < timeout, "run never finished"
        await asyncio.sleep(0.05)
    return h._active_tasks[sid]


def test_defaults_are_unbounded():
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["research_run_timeout_seconds"] == 0
    assert DEFAULT_SETTINGS["research_max_time_seconds"] == 0
    assert DEFAULT_SETTINGS["research_stall_timeout_seconds"] > 0
    assert DEFAULT_SETTINGS["research_checkin_seconds"] > 0


def test_deep_researcher_zero_max_time_never_expires():
    r = DeepResearcher.__new__(DeepResearcher)
    r.max_time = 0
    r._start_time = time.time() - 10 * 86400
    assert r._time_exceeded() is False
    r.max_time = 60
    assert r._time_exceeded() is True


async def test_progressing_run_is_not_cut(handler, monkeypatch):
    """A run that keeps emitting progress outlives every limit."""
    _settings(monkeypatch, research_run_timeout_seconds=0,
              research_stall_timeout_seconds=1, research_checkin_seconds=0,
              research_max_time_seconds=0)
    # Bypass the [120, ...] floor so the test runs fast.
    monkeypatch.setattr(rh, "_setting_seconds",
                        lambda key, d, lo, hi: {"research_stall_timeout_seconds": 1}.get(key, 0))
    seen = {}

    async def fake_service(query, *a, progress_callback=None, _task_entry=None, max_time=None, **k):
        seen["max_time"] = max_time
        _task_entry["researcher"] = _FakeResearcher()
        for i in range(12):            # ~2.4s total, > stall timeout of 1s
            await asyncio.sleep(0.2)
            progress_callback({"phase": "reading", "round": i})
        return "FULL REPORT"

    monkeypatch.setattr(handler, "call_research_service", fake_service)
    handler.start_research("s1", "q", "http://x", "m")
    entry = await _wait_done(handler, "s1")
    assert entry["status"] == "done"
    assert entry["result"] == "FULL REPORT"
    assert seen["max_time"] == 0


async def test_stalled_run_is_stopped_with_partial_results(handler, monkeypatch):
    monkeypatch.setattr(rh, "_setting_seconds",
                        lambda key, d, lo, hi: {"research_stall_timeout_seconds": 1}.get(key, 0))
    done = []

    async def fake_service(query, *a, _task_entry=None, **k):
        r = _FakeResearcher()
        r.evolving_report = "Partial findings so far."
        r.findings = [{"url": "https://a.example", "title": "A"}]
        _task_entry["researcher"] = r
        await asyncio.sleep(60)        # hung call, no progress
        return "never"

    monkeypatch.setattr(handler, "call_research_service", fake_service)
    handler.start_research("s2", "q", "http://x", "m",
                           on_complete=lambda *a: done.append(a))
    entry = await _wait_done(handler, "s2", timeout=15)
    assert entry["status"] == "done"
    assert "Partial findings so far." in entry["result"]
    assert "stalled" in entry["result"]
    assert "900s time limit" not in entry["result"]
    assert entry["researcher"].cancelled
    assert done, "partial result must still be persisted via on_complete"


async def test_checkins_are_recorded_and_exposed(handler, monkeypatch):
    monkeypatch.setattr(rh, "_setting_seconds",
                        lambda key, d, lo, hi: {"research_checkin_seconds": 1,
                                                "research_stall_timeout_seconds": 0}.get(key, 0))
    # Supervisor polls every 5s; make it fast for the test.
    real_wait = asyncio.wait

    async def fast_wait(fs, timeout=None, **k):
        return await real_wait(fs, timeout=min(timeout or 0.1, 0.1), **k)
    monkeypatch.setattr(rh.asyncio, "wait", fast_wait)
    pushes = []
    monkeypatch.setattr(handler, "_push", lambda e, t, b, n: pushes.append((t, b, n)))

    async def fake_service(query, *a, _task_entry=None, progress_callback=None, **k):
        r = _FakeResearcher()
        r.round_count = 2
        r.urls_fetched = {"https://a", "https://b"}
        r.findings = [{"title": "Samsung bribery ruling", "url": "https://a"}]
        _task_entry["researcher"] = r
        progress_callback({"phase": "analyzing"})
        await asyncio.sleep(2.6)
        return "REPORT"

    monkeypatch.setattr(handler, "call_research_service", fake_service)
    handler.start_research("s3", "q", "http://x", "m")
    await asyncio.sleep(1.5)
    status = handler.get_status("s3")
    assert status["status"] == "running"
    assert "elapsed" in status and "last_progress_age" in status
    assert status["limits"]["wall_clock_seconds"] == 0
    ck = status["last_checkin"]
    assert ck["round"] == 2 and ck["sources"] == 2 and ck["findings"] == 1
    assert "Samsung bribery ruling" in ck["text"]
    entry = await _wait_done(handler, "s3")
    assert entry["result"] == "REPORT"
    assert len(entry["checkins"]) >= 2
    assert any("checkin" in n for _, _, n in pushes)


def test_dead_snapshot_is_stale_without_900s_rule(handler, tmp_path):
    import json
    now = time.time()
    # Fresh checkpoint on a run started 3 hours ago: NOT stale.
    (tmp_path / "live.json").write_text(json.dumps(
        {"status": "running", "started_at": now - 3 * 3600, "checkpoint_at": now - 5}))
    assert handler.get_status("live")["status"] == "running"
    # Checkpoint 10 minutes old: the task is gone.
    (tmp_path / "dead.json").write_text(json.dumps(
        {"status": "running", "started_at": now - 3 * 3600, "checkpoint_at": now - 600}))
    assert handler.get_status("dead")["status"] == "error"
