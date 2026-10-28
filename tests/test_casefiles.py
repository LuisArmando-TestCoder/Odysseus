"""Case Files: entity normalization/dedupe, findings, links, pivots, search, export."""
import asyncio
import json

import pytest

from src.casefiles import CaseStore, extract_entities, guess_entity_type, normalize_entity
from src.casefile_tools import run_case_file


@pytest.fixture
def store(tmp_path):
    return CaseStore(str(tmp_path))


def test_normalize_and_guess():
    assert normalize_entity("email", " Jo@Ex.COM ") == "jo@ex.com"
    assert normalize_entity("domain", "WWW.Example.org.") == "example.org"
    assert normalize_entity("username", "@JDoe") == "jdoe"
    assert normalize_entity("phone", "+1 (555) 010-9999") == "+15550109999"
    assert normalize_entity("url", "HTTPS://www.Site.com/a/") == "https://site.com/a"
    assert guess_entity_type("jo@ex.com") == "email"
    assert guess_entity_type("https://x.org/p") == "url"
    assert guess_entity_type("8.8.8.8") == "ip"
    assert guess_entity_type("example.co.uk") == "domain"
    assert guess_entity_type("@handle_1") == "username"
    assert guess_entity_type("0x52908400098527886E0F7030069857D2E4169EE7") == "crypto"
    assert guess_entity_type("+506 8888 7777") == "phone"
    assert guess_entity_type("report.pdf") != "domain"


def test_extract_entities_from_free_text():
    text = ("Contact ops@acme-holdings.io or @acme_ops; site https://acme.io/about. "
            "Server 203.0.113.9, phone +1 415 555 0134, also mirrored at acme-backup.net. "
            "Attachment invoice.pdf dated 2024-05-01.")
    pairs = extract_entities(text)
    types = {(t, normalize_entity(t, v)) for t, v in pairs}
    assert ("email", "ops@acme-holdings.io") in types
    assert ("username", "acme_ops") in types
    assert ("url", "https://acme.io/about") in types
    assert ("ip", "203.0.113.9") in types
    assert ("phone", "+14155550134") in types
    assert ("domain", "acme-backup.net") in types
    # Hosts already covered by the email/url are not duplicated as domains,
    # and file names / dates are not selectors.
    assert ("domain", "acme-holdings.io") not in types
    assert ("domain", "acme.io") not in types
    assert not any(v == "invoice.pdf" for _, v in pairs)
    assert not any(t == "phone" and "2024" in v for t, v in pairs)


def test_case_lifecycle_dedupe_and_corroboration(store):
    c = store.create_case("alice", "Acme recon", "Who runs acme.io?", tags=["Corp", "corp"])
    assert c["tags"] == ["corp"]
    e1 = store.add_entity("alice", c["id"], "Ops@Acme.io", source="https://a")
    e2 = store.add_entity("alice", c["id"], "ops@acme.io", source="https://b")
    assert e1["created"] and not e2["created"]
    assert e1["id"] == e2["id"]
    case = store.get_case("alice", c["id"])
    ent = case["entities"][e1["id"]]
    assert ent["sources"] == ["https://a", "https://b"]
    assert ent["confidence"] > 0.5  # corroboration raised it

    f = store.add_finding("alice", c["id"], "Registrant is ops@acme.io; NS at ns1.cloudhost.net",
                          source_url="https://rdap.example/acme.io", confidence=0.9)
    assert {e["value"] for e in f["new_entities"]} == {"ns1.cloudhost.net"}
    dup = store.add_finding("alice", c["id"], "Registrant is ops@acme.io; NS at ns1.cloudhost.net",
                            source_url="https://rdap.example/acme.io")
    assert dup["duplicate"] is True
    assert len(store.get_case("alice", c["id"])["findings"]) == 1


def test_owner_isolation_and_resolution(store):
    a = store.create_case("alice", "Project Nightingale")
    store.create_case("bob", "Project Nightingale")
    assert len(store.list_cases("alice")) == 1
    assert store.resolve_case_id("alice", "nightingale") == a["id"]
    assert store.resolve_case_id("alice", a["id"][5:9]) == a["id"]
    with pytest.raises(KeyError):
        store.get_case("carol", a["id"])
    with pytest.raises(KeyError):
        store._case_path("alice", "../../etc/passwd")


def test_links_aliases_steps_pivots(store):
    c = store.create_case("u", "Handle hunt")
    store.add_entity("u", c["id"], "@shadowfox")
    store.add_alias("u", c["id"], "shadowfox", "shadow_fox")
    again = store.add_entity("u", c["id"], "shadow_fox", "username")
    assert again["created"] is False  # alias resolves to the same entity
    l1 = store.link("u", c["id"], "shadowfox", "fox@mail.example", "uses_email", "profile bio")
    l2 = store.link("u", c["id"], "shadowfox", "fox@mail.example", "uses_email", "second source")
    assert l1["id"] == l2["id"] and len(l2["evidence"]) == 2
    pivots = store.suggest_pivots("u", c["id"])
    assert any("sherlock_osint" in p for p in pivots)
    assert any("breach" in p for p in pivots)
    s = store.add_step("u", c["id"], pivots[0])
    assert pivots[0] not in store.suggest_pivots("u", c["id"])  # queued → not re-suggested
    done = store.complete_step("u", c["id"], s["id"], "3 accounts found")
    assert done["done"] and done["result"] == "3 accounts found"


def test_search_and_export(store):
    c = store.create_case("u", "Doe matter", "Locate John Doe's business interests")
    store.add_finding("u", c["id"], "John Doe is director of Doe Logistics SRL", source_url="https://reg.example/1")
    store.add_entity("u", c["id"], "doe-logistics.cr", "domain")
    hits = store.search("u", "doe-logistics.cr")
    assert any(h["kind"] == "entity" for h in hits)
    assert any(h["kind"] == "finding" for h in store.search("u", "director"))
    md = store.export_markdown("u", c["id"])
    assert md.startswith("# Case: Doe matter")
    assert "doe-logistics.cr" in md and "[source](https://reg.example/1)" in md
    assert "## Next steps" in md
    assert "Doe matter" in store.brief("u")


def test_tool_handler_end_to_end(store):
    out = run_case_file({"op": "open", "title": "Tool case", "objective": "x"}, "u", store=store)
    assert out["exit_code"] == 0
    cid = out["case_id"]
    out = run_case_file({"op": "add_finding", "case": "Tool case", "text": "Seen as @toolguy at https://t.example/toolguy"}, "u", store=store)
    assert out["exit_code"] == 0 and "username:toolguy" in out["output"]
    out = run_case_file({"op": "pivots", "case": cid, "queue": True}, "u", store=store)
    assert out["exit_code"] == 0
    got = run_case_file({"op": "get", "case": cid}, "u", store=store)
    assert "Open steps:" in got["output"]
    assert run_case_file({"op": "get", "case": "nope"}, "u", store=store)["exit_code"] == 1
    assert run_case_file({"op": "bogus", "case": cid}, "u", store=store)["exit_code"] == 1
    assert run_case_file({"op": "get"}, "u", store=store)["exit_code"] == 1


def test_do_case_file_rejects_bad_json():
    from src.casefile_tools import do_case_file
    res = asyncio.run(do_case_file("not json", owner="u"))
    assert res["exit_code"] == 1


def test_case_file_is_valid_json_on_disk(store, tmp_path):
    c = store.create_case("u", "Persist")
    store.add_finding("u", c["id"], "a@b.io")
    path = tmp_path / "cases" / "u" / f"{c['id']}.json"
    data = json.loads(path.read_text())
    assert data["title"] == "Persist" and data["findings"]
