"""Feed watches: RSS/Atom/JSON parsing, new-item detection, keywords, case filing."""
import json

import pytest

from src.casefiles import CaseStore
from src.casefile_tools import run_watch_target
from src.feeds import parse_feed
from src.watchlist import WatchStore, format_events


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>https://blog.example/{g}</link><guid>{g}</guid>"
        f"<description>&lt;p&gt;{d}&lt;/p&gt;</description></item>" for g, t, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>B</title>{body}</channel></rss>'


ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Rel</title>
<entry><id>tag:x,1</id><title>v1.0</title><link rel="alternate" href="https://rel.example/1"/>
<summary>first</summary><updated>2026-10-01T00:00:00Z</updated></entry>
<entry><id>tag:x,2</id><title type="html">v1.1 &lt;b&gt;security&lt;/b&gt; fix</title>
<link href="https://rel.example/2"/><content>patch</content></entry></feed>"""


class FakeWeb:
    def __init__(self):
        self.pages = {}

    def __call__(self, url):
        return self.pages[url]


@pytest.fixture
def env(tmp_path):
    return WatchStore(str(tmp_path)), CaseStore(str(tmp_path)), FakeWeb()


def test_parse_rss_atom_json():
    items = parse_feed(rss(("a1", "Hello", "World")))
    assert items == [{"id": "a1", "title": "Hello", "link": "https://blog.example/a1",
                      "summary": "World", "published": ""}]
    atom = parse_feed(ATOM)
    assert [i["id"] for i in atom] == ["tag:x,1", "tag:x,2"]
    assert atom[1]["title"] == "v1.1 security fix" and atom[1]["link"] == "https://rel.example/2"
    jf = parse_feed(json.dumps({"version": "https://jsonfeed.org/version/1.1",
                                "items": [{"id": 7, "title": "J", "url": "https://j.example/7"}]}))
    assert jf[0]["id"] == "7" and jf[0]["link"] == "https://j.example/7"
    arr = parse_feed(json.dumps([{"name": "Row", "link": "javascript:alert(1)"}]))
    assert arr[0]["title"] == "Row" and arr[0]["link"] == ""  # non-http links dropped
    assert parse_feed(json.dumps([{"name": "No id"}]))[0]["id"].startswith("h:")  # content hash


def test_not_a_feed_and_doctype_refused():
    assert parse_feed("<html><body>hi</body></html>") is None
    assert parse_feed('{"status": "ok"}') is None
    assert parse_feed("plain text") is None
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    with pytest.raises(ValueError):
        parse_feed(bomb)


def test_feed_watch_reports_only_new_items_and_files_case(env):
    ws, cs, web = env
    case = cs.create_case("u", "Vendor blog")
    url = "https://blog.example/feed.xml"
    web.pages[url] = rss(("a1", "Old post", "x"))
    w = ws.add("u", url, label="Blog", interval_minutes=60, case_id=case["id"])
    assert w["kind"] == "auto"
    t0 = 1_000_000.0
    assert ws.check_due("u", fetcher=web, now=t0)["changes"] == []  # baseline
    listed = ws.list("u")[0]
    assert listed["is_feed"] and listed["item_count"] == 1 and "seen_ids" not in listed
    # Reordering / re-fetching the same items is not a change.
    assert ws.check_due("u", fetcher=web, now=t0 + 3600)["changes"] == []
    web.pages[url] = rss(("a2", "Breach disclosed", "contact sec@vendor.example"), ("a1", "Old post", "x"))
    r = ws.check_due("u", fetcher=web, now=t0 + 7200)
    assert len(r["changes"]) == 1
    ev = r["changes"][0]
    assert ev["feed"] and ev["n_added"] == 1 and ev["added"] == ["Breach disclosed — https://blog.example/a2"]
    assert "1 new item" in format_events([ev])
    got = cs.get_case("u", case["id"])
    f = [x for x in got["findings"] if x["text"].startswith("New in Blog")]
    assert len(f) == 1 and f[0]["source_url"] == "https://blog.example/a2"
    assert any(e["value"] == "sec@vendor.example" for e in got["entities"].values())


def test_feed_keywords_match_titles(env):
    ws, _, web = env
    url = "https://rel.example/atom"
    web.pages[url] = ATOM.replace("<entry><id>tag:x,2</id>", "<x><id>").replace("</content></entry>", "</content></x>")
    ws.add("u", url, keywords=["security"], interval_minutes=15)
    t0 = 2_000_000.0
    ws.check_due("u", fetcher=web, now=t0)
    web.pages[url] = ATOM.replace("v1.1 &lt;b&gt;security&lt;/b&gt; fix", "v1.1 perf")
    assert ws.check_due("u", fetcher=web, now=t0 + 900)["changes"] == []  # new item, no keyword
    web.pages[url] = ATOM.replace("</feed>", '<entry><id>tag:x,3</id><title>v1.2 SECURITY</title>'
                                  '<link href="https://rel.example/3"/></entry></feed>')
    r = ws.check_due("u", fetcher=web, now=t0 + 1800)
    assert len(r["changes"]) == 1 and r["changes"][0]["keywords_hit"] == ["security"]
    assert [i["id"] for i in r["changes"][0]["items"]] == ["tag:x,3"]


def test_kind_page_and_feed_modes(env):
    ws, _, web = env
    url = "https://blog.example/feed.xml"
    web.pages[url] = rss(("a1", "Post", "x"))
    ws.add("u", url, kind="page", interval_minutes=15)
    ws.check_due("u", fetcher=web, now=1.0e6)
    assert not ws.list("u")[0].get("is_feed")
    page = "https://plain.example/"
    web.pages[page] = "<html><body>not a feed</body></html>"
    ws.add("u", page, kind="feed", interval_minutes=15)
    r = ws.check_due("u", fetcher=web, now=1.0e6 + 5)
    assert r["errors"] and "not an RSS" in r["errors"][0]["error"]
    with pytest.raises(ValueError):
        ws.add("u", "https://x.example/", kind="weird")


def test_tool_add_kind_and_list_badge(env):
    ws, _, web = env
    url = "https://blog.example/feed.xml"
    web.pages[url] = rss(("a1", "Post", "x"))
    r = run_watch_target({"op": "add", "url": url, "kind": "feed"}, "u", store=ws)
    assert r.get("success", True) and "Feed mode" in json.dumps(r)
    run_watch_target({"op": "check", "url": url}, "u", store=ws, fetcher=web)
    out = json.dumps(run_watch_target({"op": "list"}, "u", store=ws))
    assert "[feed]" in out
