"""Watchlist: baseline, change detection, keyword gating, backoff, case filing."""
import pytest

from src.casefiles import CaseStore
from src.casefile_tools import run_watch_target
from src.watchlist import WatchStore, fingerprint, html_to_text, summarize_diff


class FakeWeb:
    def __init__(self):
        self.pages = {}
        self.fail = set()

    def __call__(self, url):
        if url in self.fail:
            raise ConnectionError("boom")
        return self.pages[url]


@pytest.fixture
def env(tmp_path):
    return WatchStore(str(tmp_path)), CaseStore(str(tmp_path)), FakeWeb()


def test_html_to_text_and_fingerprint():
    html = "<html><head><title>x</title><script>var t=1</script></head><body><h1>Hi</h1><p>Line&nbsp;one</p><style>p{}</style></body></html>"
    assert html_to_text(html) == "Hi\nLine one"
    # Volatile clock times / tokens do not change the fingerprint.
    assert fingerprint("Updated 10:42 pm\nPrice 5") == fingerprint("Updated 11:07 pm\nPrice 5")
    assert fingerprint("Price 5") != fingerprint("Price 6")
    d = summarize_diff("a\nb\nc", "a\nc\nd")
    assert d == {"added": ["d"], "removed": ["b"]}


def test_baseline_then_change_files_into_case(env):
    ws, cs, web = env
    case = cs.create_case("u", "Docket watch")
    url = "https://court.example/docket"
    web.pages[url] = "<p>Case 1: Smith</p>"
    w = ws.add("u", url, label="Docket", interval_minutes=60, case_id=case["id"])
    t0 = 1_000_000.0
    r = ws.check_due("u", fetcher=web, now=t0)
    assert r == {"checked": 1, "changes": [], "errors": []}  # baseline
    web.pages[url] = "<p>Case 1: Smith</p><p>Case 2: Doe, contact doe@law.example</p>"
    # Not due yet.
    assert ws.check_due("u", fetcher=web, now=t0 + 600)["checked"] == 0
    r = ws.check_due("u", fetcher=web, now=t0 + 3600)
    assert len(r["changes"]) == 1
    ev = r["changes"][0]
    assert ev["added"] == ["Case 2: Doe, contact doe@law.example"] and ev["watch_id"] == w["id"]
    got = cs.get_case("u", case["id"])
    assert any("Watched page changed" in f["text"] for f in got["findings"])
    assert any(e["value"] == "doe@law.example" for e in got["entities"].values())


def test_keyword_gating(env):
    ws, _, web = env
    url = "https://news.example/"
    web.pages[url] = "headline A"
    ws.add("u", url, keywords=["Acme"], interval_minutes=15)
    ws.check_due("u", fetcher=web, now=1000.0)
    web.pages[url] = "headline A\nheadline B"
    assert ws.check_due("u", fetcher=web, now=1000.0 + 900)["changes"] == []
    web.pages[url] = "headline A\nheadline B\nAcme acquires rival"
    r = ws.check_due("u", fetcher=web, now=1000.0 + 1800)
    assert r["changes"][0]["keywords_hit"] == ["acme"]


def test_errors_back_off_and_pause(env):
    ws, _, web = env
    url = "https://down.example/"
    web.fail.add(url)
    w = ws.add("u", url, interval_minutes=15)
    now = 0.0
    r = ws.check_due("u", fetcher=web, now=now + 1000)
    assert r["errors"] and r["errors"][0]["error"].startswith("ConnectionError")
    # After one failure, the effective interval doubles: 15 min later is not due.
    assert ws.check_due("u", fetcher=web, now=now + 1000 + 900)["checked"] == 0
    assert ws.check_due("u", fetcher=web, now=now + 1000 + 1800)["checked"] == 1
    t = now + 10_000
    for _ in range(12):
        t += 86_400
        ws.check_due("u", fetcher=web, now=t)
    assert [x for x in ws.list("u") if x["id"] == w["id"]][0]["active"] is False


def test_add_validation_dedupe_and_limits(env):
    ws, _, _ = env
    with pytest.raises(ValueError):
        ws.add("u", "file:///etc/passwd")
    a = ws.add("u", "https://x.example/", interval_minutes=1)
    assert a["interval_minutes"] == 15  # clamped to minimum
    b = ws.add("u", "https://x.example/", label="X", interval_minutes=60)
    assert a["id"] == b["id"] and b["label"] == "X" and len(ws.list("u")) == 1
    assert "snapshot" not in b
    assert ws.list("other") == []


def test_tool_handler(env, tmp_path):
    ws, cs, web = env
    c = cs.create_case("u", "Tool watch")
    url = "https://t.example/"
    web.pages[url] = "v1"
    out = run_watch_target({"op": "add", "url": url, "case": "Tool watch", "keywords": ["v2"]}, "u", store=ws)
    assert out["exit_code"] == 0 and c["id"] in out["output"]
    assert "No change" in run_watch_target({"op": "check", "url": url}, "u", store=ws, fetcher=web)["output"]
    web.pages[url] = "v1\nv2 released"
    res = run_watch_target({"op": "check", "url": url}, "u", store=ws, fetcher=web)
    assert res["exit_code"] == 0 and "v2 released" in res["output"]
    assert "paused" in run_watch_target({"op": "pause", "url": url}, "u", store=ws)["output"]
    assert run_watch_target({"op": "remove", "url": url}, "u", store=ws)["exit_code"] == 0
    assert "empty" in run_watch_target({"op": "list"}, "u", store=ws)["output"]


def test_check_watchlist_action(env, monkeypatch):
    import asyncio
    ba = pytest.importorskip("src.builtin_actions")
    import src.watchlist as wl
    ws, _, web = env
    monkeypatch.setattr(wl, "get_watch_store", lambda data_dir=None: ws)
    monkeypatch.setattr(wl, "default_fetcher", web)
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_watchlist("u"))  # nothing watched
    url = "https://a.example/"
    web.pages[url] = "one"
    ws.add("u", url, interval_minutes=15)
    with pytest.raises(ba.TaskNoop):
        asyncio.run(ba.action_check_watchlist("u"))  # baseline, no change
    data = ws._load("u")
    for w in data["watches"].values():
        w["last_checked"] = 0
    ws._save("u", data)
    web.pages[url] = "one\ntwo"
    body, ok = asyncio.run(ba.action_check_watchlist("u"))
    assert ok and "two" in body
    assert "check_watchlist" in ba.BUILTIN_ACTIONS and "check_watchlist" in ba.BUILTIN_ACTION_INFO
