"""Tests for src/spill.py — SpillStore + spill_or_truncate policy (T1.3/T1.6)."""

import os

import pytest

from src import constants
from src import spill


@pytest.fixture(autouse=True)
def _tmp_data(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    yield


def test_save_text_writes_owner_scoped_file(tmp_path):
    store = spill.SpillStore()
    ref = store.save_text(owner="alice", session_id="s1", tool="bash", text="big output")
    assert ref is not None
    assert ref.bytes == len("big output")
    assert os.path.exists(ref.locator)
    # Owner-scoped directory naming.
    assert os.sep + "alice" + os.sep + "s1" + os.sep in ref.locator
    with open(ref.locator, encoding="utf-8") as f:
        assert f.read() == "big output"


def test_spill_or_truncate_short_text_passthrough():
    out = spill.spill_or_truncate("short", tool="bash")
    assert out == "short"


def test_spill_or_truncate_large_text_spills_with_locator():
    big = "z" * 15_000
    out = spill.spill_or_truncate(big, tool="bash", session_id="s1", owner="alice", limit=10_000)
    assert len(out) < 15_000
    assert "FULL OUTPUT SPILLED TO:" in out
    locator = out.split("FULL OUTPUT SPILLED TO: ")[1].split(" — ")[0]
    assert os.path.exists(locator)
    with open(locator, encoding="utf-8") as f:
        assert f.read() == big  # complete, verbatim


def test_spill_or_truncate_degrades_to_truncation_on_failure(monkeypatch):
    class _Broken:
        def save_text(self, **kw):
            return None
    monkeypatch.setattr(spill, "get_spill_store", lambda: _Broken())
    big = "y" * 15_000
    out = spill.spill_or_truncate(big, tool="bash", limit=10_000)
    assert "truncated" in out
    assert len(out) < 15_000
