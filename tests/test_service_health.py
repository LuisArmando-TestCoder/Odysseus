import asyncio
import socket
import urllib.error

import pytest

from src.service_health import (collect_probes, format_health_alerts, health_brief_lines,
                                probe_http, probe_tcp, redact_url, update_health)

SAFE = lambda url: (True, "ok")  # noqa: E731


def ok_get(url, timeout):
    return 200


def refuse(*a):
    raise ConnectionRefusedError()


def test_redact_url_strips_credentials_and_query():
    assert redact_url("https://u:p@search.local:8443/x?token=1#f") == "https://search.local:8443/x"
    assert redact_url("http://[::1]:8080/") == "http://[::1]:8080/"


def test_probe_http_classifies_statuses_and_errors():
    assert probe_http("s", "S", "http://h/", http_get=ok_get, url_check=SAFE)["ok"] is True
    assert probe_http("s", "S", "http://h/", http_get=lambda u, t: 403, url_check=SAFE)["ok"] is True
    r = probe_http("s", "S", "http://h/", http_get=lambda u, t: 502, url_check=SAFE)
    assert r["ok"] is False and "HTTP 502" in r["detail"]

    def boom(u, t):
        raise urllib.error.URLError(ConnectionRefusedError())
    r = probe_http("s", "S", "http://a:secret@h/?k=v", http_get=boom, url_check=SAFE, fix="do x")
    assert r["ok"] is False and "connection refused" in r["detail"]
    assert "secret" not in r["detail"] and "k=v" not in r["detail"] and r["fix"] == "do x"


def test_probe_http_refuses_unsafe_url_without_fetching():
    called = []
    r = probe_http("s", "S", "http://169.254.169.254/", url_check=lambda u: (False, "link-local blocked"),
                   http_get=lambda u, t: called.append(u) or 200)
    assert r["ok"] is False and "link-local" in r["detail"] and not called


def test_probe_tcp():
    assert probe_tcp("v", "V", "h", 1, tcp_connect=lambda h, p, t: None)["ok"] is True
    r = probe_tcp("v", "V", "h", 8100, tcp_connect=refuse)
    assert r["ok"] is False and r["detail"] == "h:8100: connection refused"

    def to(*a):
        raise socket.timeout()
    assert "timed out" in probe_tcp("v", "V", "h", 1, tcp_connect=to)["detail"]


def test_collect_probes_picks_what_is_in_use():
    seen = []

    def get(url, timeout):
        seen.append(url)
        return 200
    res = collect_probes(
        settings={"search_provider": "searxng", "search_url": "http://sx:8080/",
                  "reminder_channel": "ntfy"},
        integrations=[{"preset": "ntfy", "enabled": True, "base_url": "https://ntfy.local/"}],
        email_accounts=[{"id": "a1", "name": "Work", "imap_host": "imap.x", "imap_port": 993,
                         "smtp_host": "smtp.x", "smtp_port": 465},
                        {"id": "a2", "name": "Off", "enabled": False, "imap_host": "imap.y"}],
        chroma={"installed": True, "host": "c", "port": 8100},
        storage_check=lambda: {"checks": {"database": {"ok": True}, "data_dir": {"ok": True}}},
        http_get=get, tcp_connect=lambda h, p, t: None, url_check=SAFE)
    keys = [r["key"] for r in res]
    assert keys == ["storage", "search", "vector", "ntfy", "email:a1:imap", "email:a1:smtp"]
    assert all(r["ok"] for r in res)
    assert seen == ["http://sx:8080", "https://ntfy.local/v1/health"]


def test_collect_probes_config_problems_and_skips():
    res = collect_probes(settings={"search_provider": "brave", "reminder_channel": "ntfy"},
                         integrations=[{"preset": "ntfy", "enabled": False, "base_url": "http://n"}],
                         chroma={"installed": False},
                         storage_check=lambda: {"checks": {"data_dir": {"ok": False, "error": "disk full"}}},
                         url_check=SAFE)
    by = {r["key"]: r for r in res}
    assert set(by) == {"storage", "search", "ntfy"}
    assert by["search"]["ok"] is False and "no API key" in by["search"]["detail"]
    assert by["ntfy"]["ok"] is False and "no ntfy integration" in by["ntfy"]["detail"]
    assert by["storage"]["ok"] is False and "disk full" in by["storage"]["detail"]
    assert collect_probes(settings={"search_provider": "disabled"}) == []


def down(key="search", label="Web search (SearXNG)"):
    return {"key": key, "label": label, "ok": False, "detail": "http://sx: connection refused",
            "fix": "Start SearXNG."}


def up(key="search", label="Web search (SearXNG)"):
    return {"key": key, "label": label, "ok": True, "detail": "http://sx", "fix": ""}


def test_transitions_debounce_report_once_and_recover(tmp_path):
    p = str(tmp_path / "s.json")
    assert update_health("u", [down()], now=1000, state_path=p) == []      # first blip: quiet
    ev = update_health("u", [down()], now=2800, state_path=p)
    assert [e["kind"] for e in ev] == ["down"] and ev[0]["since"] == 1000
    assert update_health("u", [down()], now=4600, state_path=p) == []      # still down: once
    assert health_brief_lines("u", now=1000 + 3 * 3600, state_path=p) == [
        "Degraded services:", "- Web search (SearXNG) — down 3 hours: http://sx: connection refused"]
    ev = update_health("u", [up()], now=1000 + 7200, state_path=p)
    assert [e["kind"] for e in ev] == ["recovered"] and ev[0]["duration"] == 7200
    assert update_health("u", [up()], now=9000, state_path=p) == []
    assert health_brief_lines("u", state_path=p) == []


def test_blip_then_recovery_is_silent_and_skips_drop_state(tmp_path):
    p = str(tmp_path / "s.json")
    assert update_health("u", [down()], now=1, state_path=p) == []
    assert update_health("u", [up()], now=2, state_path=p) == []
    update_health("u", [down()], now=3, state_path=p)
    update_health("u", [down()], now=4, state_path=p)
    # Service no longer in use (skipped) → forgotten, no "recovered" noise.
    assert update_health("u", [{"key": "search", "label": "x", "ok": None}], now=5, state_path=p) == []
    assert health_brief_lines("u", state_path=p) == []


def test_state_is_per_owner_and_tolerates_corruption(tmp_path):
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    update_health("a", [down()], now=1, state_path=a)
    assert update_health("b", [down()], now=2, state_path=b) == []
    (tmp_path / "c.json").write_text("[broken")
    assert update_health("c", [down()], now=1, confirm=1, state_path=str(tmp_path / "c.json"))[0]["kind"] == "down"


def test_format_alerts():
    body = format_health_alerts([
        {"kind": "down", "label": "Vector store (ChromaDB)", "detail": "c:8100: connection refused",
         "fix": "Start ChromaDB.", "duration": 1800},
        {"kind": "recovered", "label": "Reminders (ntfy)", "detail": "", "fix": "", "duration": 5400},
    ])
    assert body.splitlines() == [
        "Service health: 1 service down, 1 recovered.",
        "- DOWN Vector store (ChromaDB): c:8100: connection refused Fix: Start ChromaDB.",
        "- OK again Reminders (ntfy) (was down 2 hours)",
    ]


def test_check_services_action_and_registration(monkeypatch, tmp_path):
    ba = pytest.importorskip("src.builtin_actions")
    ts = pytest.importorskip("src.task_scheduler")
    import src.service_health as sh
    monkeypatch.setattr(sh, "run_live_probes", lambda owner: [down()])
    monkeypatch.setattr(sh, "_state_path", lambda owner: str(tmp_path / f"{owner}.json"))
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_services("u"))
    body, ok = asyncio.run(ba.action_check_services("u"))
    assert ok and "DOWN Web search (SearXNG)" in body
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_services("u"))
    assert "check_services" in ba.BUILTIN_ACTIONS and "check_services" in ba.BUILTIN_ACTION_INFO
    assert ts.HOUSEKEEPING_DEFAULTS["check_services"]["name"] == "Service Health"
