"""New cross-case links surfaced in the daily brief."""
import pytest

import src.casefiles as cf
from src.casefiles import CaseStore, format_recent_overlaps


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(cf, "_now", c)
    return c


@pytest.fixture
def store(tmp_path):
    return CaseStore(str(tmp_path))


def test_link_forms_when_second_case_records_selector(store, clock):
    a = store.create_case("u", "Acme recon")
    b = store.create_case("u", "Phishing kit")
    store.add_entity("u", a["id"], "ops@acme.io")
    clock.t += 5 * 86400
    store.add_entity("u", b["id"], "OPS@acme.io")

    hits = store.recent_overlaps("u", since=clock.t - 86400)
    assert len(hits) == 1
    h = hits[0]
    assert h["type"] == "email" and h["formed_at"] == clock.t
    assert [c["case_id"] for c in h["cases"]] == [a["id"], b["id"]]
    # Before the second case recorded it, there was no link yet.
    assert store.recent_overlaps("u", since=clock.t + 1) == []


def test_old_links_and_single_case_selectors_are_quiet(store, clock):
    a = store.create_case("u", "A")
    b = store.create_case("u", "B")
    store.add_entity("u", a["id"], "shared.example")
    store.add_entity("u", b["id"], "shared.example")
    clock.t += 3 * 86400
    store.add_entity("u", a["id"], "lonely.example")
    # Re-mentioning an old shared selector does not make the link "new".
    store.add_entity("u", b["id"], "shared.example")
    assert store.recent_overlaps("u", since=clock.t - 86400) == []


def test_closed_only_links_skipped_and_owner_scoped(store, clock):
    a = store.create_case("u", "A")
    b = store.create_case("u", "B")
    store.add_entity("u", a["id"], "x@corp.io")
    store.add_entity("u", b["id"], "x@corp.io")
    store.update_case("u", a["id"], status="closed")
    store.update_case("u", b["id"], status="closed")
    assert store.recent_overlaps("u", since=0) == []

    other = store.create_case("v", "Someone else")
    store.add_entity("v", other["id"], "y@corp.io")
    c = store.create_case("u", "C")
    store.add_entity("u", c["id"], "y@corp.io")
    assert store.recent_overlaps("u", since=0) == []


def test_alias_match_reported_once_newest_first(store, clock):
    a = store.create_case("u", "A")
    b = store.create_case("u", "B")
    store.add_entity("u", a["id"], "Jane Doe", "person")
    store.add_alias("u", a["id"], "Jane Doe", "J. Doe")
    clock.t += 10
    store.add_entity("u", b["id"], "J. Doe", "person")
    clock.t += 10
    store.add_entity("u", a["id"], "evil.example")
    store.add_entity("u", b["id"], "evil.example")

    hits = store.recent_overlaps("u", since=0)
    assert [h["value"] for h in hits] == ["evil.example", "J. Doe"]


def test_format_cleans_and_caps():
    assert format_recent_overlaps([]) == ""
    text = format_recent_overlaps([{
        "value": "bad\nvalue\x00", "type": "other", "formed_at": 1,
        "cases": [{"case_id": f"c{i}", "title": "T" * 100 if i == 0 else f"Case {i}",
                   "status": "closed" if i == 1 else "open"} for i in range(6)],
    }])
    lines = text.splitlines()
    assert lines[0] == "New cross-case links (24h):"
    assert "bad value" in lines[1] and "\x00" not in lines[1]
    assert "Case 1 (closed)" in lines[1] and lines[1].endswith("+2 more")
    assert "T" * 60 not in lines[1]


def test_daily_brief_includes_new_links(store, clock, monkeypatch):
    ba = pytest.importorskip("src.builtin_actions")
    monkeypatch.setattr(cf, "get_case_store", lambda data_dir=None: store)
    monkeypatch.setattr(ba.time, "time", lambda: clock.t)
    a = store.create_case("u", "A")
    b = store.create_case("u", "B")
    store.add_entity("u", a["id"], "kit.example")
    store.add_entity("u", b["id"], "kit.example")
    text = "\n".join(ba._intel_brief_lines("u")).splitlines()
    assert "New cross-case links (24h):" in text
    assert any("kit.example" in ln and "A ↔ B" in ln for ln in text)
