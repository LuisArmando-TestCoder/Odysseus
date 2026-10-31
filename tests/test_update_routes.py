"""routes/update_routes.py — status payload, loopback-only idle probe, admin gating."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routes.update_routes as ur  # noqa: E402


class _Research:
    def __init__(self, statuses):
        self._active_tasks = {str(i): {"status": s} for i, s in enumerate(statuses)}


def _client(monkeypatch, admin=True, research=None, host="127.0.0.1"):
    monkeypatch.setenv("AUTH_ENABLED", "false" if admin else "true")
    app = FastAPI()
    app.include_router(ur.setup_update_routes(research))
    return TestClient(app, client=(host, 50000))


def test_status_shape_for_admin(monkeypatch):
    c = _client(monkeypatch, admin=True)
    d = c.get("/api/update/status").json()
    for key in ("head", "boot_sha", "restart_needed", "supervised", "is_admin", "version", "mode", "history"):
        assert key in d
    assert d["is_admin"] is True
    assert d["restart_needed"] is False      # same process, same code


def test_status_hides_admin_fields(monkeypatch):
    c = _client(monkeypatch, admin=False)
    d = c.get("/api/update/status").json()
    assert d["is_admin"] is False
    assert "history" not in d and "error" not in d and "backup_branch" not in d


def test_restart_needed_when_code_changed_on_disk(monkeypatch):
    c = _client(monkeypatch, admin=True)
    real_status = ur.updater.status
    monkeypatch.setattr(ur.updater, "status", lambda: {**real_status(), "head": "b" * 40})
    monkeypatch.setattr(ur, "_BOOT_SHA", "a" * 40)
    assert c.get("/api/update/status").json()["restart_needed"] is True
    monkeypatch.setattr(ur, "_BOOT_SHA", "b" * 40)
    assert c.get("/api/update/status").json()["restart_needed"] is False


def test_idle_is_loopback_only(monkeypatch):
    c = _client(monkeypatch, host="192.168.1.20")
    assert c.get("/api/update/idle").status_code == 404


def test_idle_counts_running_research(monkeypatch):
    c = _client(monkeypatch, research=_Research(["running", "done"]))
    d = c.get("/api/update/idle").json()
    assert d["research"] == 1 and d["idle"] is False
    c2 = _client(monkeypatch, research=_Research(["done"]))
    assert c2.get("/api/update/idle").json()["idle"] is True


def test_admin_actions_require_admin(monkeypatch):
    c = _client(monkeypatch, admin=False)
    for path in ("/api/update/check", "/api/update/apply", "/api/update/restart", "/api/update/rollback"):
        assert c.post(path).status_code == 403, path


def test_restart_without_supervisor_explains(monkeypatch):
    c = _client(monkeypatch, admin=True)
    monkeypatch.setattr(ur.updater, "supervisor_alive", lambda: False)
    r = c.post("/api/update/restart")
    assert r.status_code == 409 and "shortcut" in r.json()["detail"]


def test_idle_probe_is_exempt_from_auth_middleware():
    src = (ur.Path(ur.__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert '"/api/update/idle",' in src
    assert "setup_update_routes(research_handler)" in src


def test_shutdown_needs_loopback_and_token(monkeypatch):
    raised = []
    monkeypatch.setattr(ur.signal, "raise_signal", lambda sig: raised.append(sig))
    monkeypatch.setenv("ODYSSEUS_SHUTDOWN_TOKEN", "tok123")
    c = _client(monkeypatch, admin=True)
    assert c.post("/api/update/shutdown").status_code == 404
    assert c.post("/api/update/shutdown", headers={"X-Odysseus-Shutdown": "nope"}).status_code == 404
    far = _client(monkeypatch, admin=True, host="10.0.0.5")
    assert far.post("/api/update/shutdown", headers={"X-Odysseus-Shutdown": "tok123"}).status_code == 404
    r = c.post("/api/update/shutdown", headers={"X-Odysseus-Shutdown": "tok123"})
    assert r.status_code == 200 and r.json()["stopping"] is True
    monkeypatch.delenv("ODYSSEUS_SHUTDOWN_TOKEN")
    assert c.post("/api/update/shutdown", headers={"X-Odysseus-Shutdown": ""}).status_code == 404
