"""Service Health status summary (sidebar dot) + its route and SPA wiring."""
import json
import os
import re

import pytest

from src.service_health import (META_KEY, STALE_AFTER, health_brief_lines, health_status,
                                live_status, update_health)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def down(key="search", label="Web search (SearXNG)"):
    return {"key": key, "label": label, "ok": False, "detail": "http://sx: connection refused",
            "fix": "Start SearXNG."}


def up(key="search", label="Web search (SearXNG)"):
    return {"key": key, "label": label, "ok": True, "detail": "http://sx", "fix": ""}


def test_never_checked_is_unknown(tmp_path):
    s = health_status("u", now=100, state_path=str(tmp_path / "none.json"))
    assert s["status"] == "unknown" and s["services"] == [] and s["checked_at"] is None


def test_all_ok_records_checked_at(tmp_path):
    p = str(tmp_path / "s.json")
    update_health("u", [up(), up("vector", "Vector store")], now=1000, state_path=p)
    s = health_status("u", now=1100, state_path=p)
    assert s["status"] == "ok" and s["total"] == 2 and s["checked_at"] == 1000
    assert s["label"] == "All 2 services OK"
    assert all(r["status"] == "ok" and "detail" not in r for r in s["services"])


def test_first_failure_is_flaky_then_down_sorted_first(tmp_path):
    p = str(tmp_path / "s.json")
    update_health("u", [down(), up("vector", "Vector store")], now=1000, state_path=p)
    s = health_status("u", now=1060, state_path=p)
    assert s["status"] == "flaky" and s["flaky"] == 1 and s["down"] == 0
    update_health("u", [down(), up("vector", "Vector store")], now=2800, state_path=p)
    s = health_status("u", now=1000 + 3 * 3600, state_path=p)
    assert s["status"] == "down" and s["down"] == 1
    first = s["services"][0]
    assert first["key"] == "search" and first["status"] == "down"
    assert first["for"] == "3 hours" and first["fix"] == "Start SearXNG."
    assert "Web search (SearXNG)" in s["label"]


def test_stale_when_checks_stop(tmp_path):
    p = str(tmp_path / "s.json")
    update_health("u", [up()], now=1000, state_path=p)
    s = health_status("u", now=1000 + STALE_AFTER + 60, state_path=p)
    assert s["status"] == "stale" and "paused" in s["label"]
    # a real outage still wins over staleness
    update_health("u", [down()], now=2000, state_path=p)
    update_health("u", [down()], now=3000, state_path=p)
    assert health_status("u", now=3000 + 10 * STALE_AFTER, state_path=p)["status"] == "down"


def test_meta_entry_is_not_a_service(tmp_path):
    p = str(tmp_path / "s.json")
    update_health("u", [down()], now=1, state_path=p)
    update_health("u", [down()], now=2, state_path=p)
    data = json.load(open(p))
    assert data[META_KEY] == {"checked_at": 2}
    assert health_brief_lines("u", now=10, state_path=p)[0] == "Degraded services:"
    assert len(health_brief_lines("u", now=10, state_path=p)) == 2
    assert [r["key"] for r in health_status("u", now=10, state_path=p)["services"]] == ["search"]


def test_corrupt_or_hostile_state_is_cleaned(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        META_KEY: {"checked_at": "nope"},
        "x": {"ok": False, "fails": 5, "since": 1, "label": "Evil\x00<b>label</b>\n" + "y" * 200,
              "detail": "d\x1b[31m", "fix": ""},
        "weird": "not a dict",
    }))
    s = health_status("u", now=100, state_path=str(p))
    assert s["checked_at"] is None and s["status"] == "down"
    r = s["services"][0]
    assert "\x00" not in r["label"] and "\n" not in r["label"] and len(r["label"]) <= 61
    assert "\x1b" not in r["detail"]


def test_owner_scoped_state(tmp_path, monkeypatch):
    import src.service_health as sh
    monkeypatch.setattr(sh, "_state_path", lambda owner: str(tmp_path / f"{owner}.json"))
    update_health("alice", [down()], now=1)
    update_health("alice", [down()], now=2)
    assert health_status("alice", now=3)["status"] == "down"
    assert health_status("bob", now=3)["status"] == "unknown"


def test_live_status_counts_any_failure_as_down_and_skips_unused():
    s = live_status([down(), up("vector", "Vector store"),
                     {"key": "ntfy", "label": "Reminders", "ok": None}], now=50)
    assert s["live"] is True and s["status"] == "down" and s["total"] == 2
    assert s["checked_at"] == 50 and "for" not in s["services"][0]
    assert live_status([up()], now=1)["status"] == "ok"
    assert live_status([], now=1)["label"] == "No services to check"


def test_cooldown():
    pytest.importorskip("fastapi")
    from routes.service_health_routes import _Cooldown
    c = _Cooldown(20)
    assert c.try_acquire("a", now=100) == 0.0
    assert c.try_acquire("a", now=105) == pytest.approx(15)
    assert c.try_acquire("b", now=105) == 0.0
    assert c.try_acquire("a", now=121) == 0.0


# ── wiring (text checks; no fastapi needed) ─────────────────────────────────

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_routes_registered_and_auth_not_exempted():
    src = _read("routes/service_health_routes.py")
    assert '@router.get("/api/health/services")' in src
    assert '@router.post("/api/health/services/check")' in src
    assert "get_current_user" in src
    app = _read("app.py")
    assert "app.include_router(setup_service_health_routes())" in app
    assert '"/api/health/services"' not in app  # exempt list holds only the exact /api/health
    assert "AUTH_EXEMPT_PREFIXES = [\"/static\"]" in app


def test_spa_has_services_entry():
    html = _read("static/index.html")
    for needle in ('id="tool-services-btn"', 'id="svc-health-dot"', 'id="svc-health-panel"',
                   'data-ui-key="tool-services"'):
        assert needle in html
    assert re.search(r'<script type="module" src="/static/js/serviceHealth\.js"></script>', html)
    assert "'tool-services':" in _read("static/app.js")
    js = _read("static/js/serviceHealth.js")
    assert "/api/health/services" in js and "/api/health/services/check" in js
    assert "innerHTML" not in js.replace("never innerHTML", "")
