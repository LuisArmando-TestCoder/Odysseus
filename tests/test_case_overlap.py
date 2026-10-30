"""Cross-case entity overlap: "this email also appears in case X"."""
import pytest

from src.casefiles import CaseStore
from src.casefile_tools import run_case_file


@pytest.fixture
def store(tmp_path):
    return CaseStore(str(tmp_path))


def test_overlap_finds_shared_selectors_ranked(store):
    a = store.create_case("u", "Acme recon")
    b = store.create_case("u", "Phishing kit")
    c = store.create_case("u", "Unrelated")
    store.add_finding("u", a["id"], "ops@acme.io registered acme-backup.net")
    store.add_entity("u", a["id"], "@JDoe", "username")
    store.add_finding("u", b["id"], "Kit panel on acme-backup.net mails OPS@acme.io")
    store.add_entity("u", b["id"], "jdoe", "username")
    store.add_entity("u", c["id"], "acme-backup.net")
    store.add_entity("u", c["id"], "nothing.example")

    ov = store.overlaps("u", a["id"])
    assert ov["case_id"] == a["id"]
    assert ov["shared_entities"] == 3
    assert [x["case_id"] for x in ov["cases"]] == [b["id"], c["id"]]
    assert ov["cases"][0]["shared"] == 3 and ov["cases"][1]["shared"] == 1
    ents = {e["value"]: e["id"] for e in store.get_case("u", a["id"])["entities"].values()}
    assert {x["case_id"] for x in ov["entities"][ents["acme-backup.net"]]} == {b["id"], c["id"]}
    # Case-insensitive email + @-stripped username canonicalization meet across cases.
    assert ov["entities"][ents["ops@acme.io"]][0]["case_id"] == b["id"]
    assert ov["entities"][ents["@JDoe"]][0]["case_id"] == b["id"]


def test_overlap_matches_aliases_and_skips_short_keys(store):
    a = store.create_case("u", "A")
    b = store.create_case("u", "B")
    store.add_entity("u", a["id"], "Jane Doe", "person")
    store.add_alias("u", a["id"], "Jane Doe", "J. Doe")
    store.add_entity("u", b["id"], "j. doe", "person")
    store.add_entity("u", a["id"], "US", "location")
    store.add_entity("u", b["id"], "us", "location")
    ov = store.overlaps("u", b["id"])
    assert ov["shared_entities"] == 1 and ov["cases"][0]["values"] == ["j. doe"]


def test_overlap_is_owner_scoped(store):
    a = store.create_case("alice", "Mine")
    store.add_entity("alice", a["id"], "shared@x.io")
    m = store.create_case("mallory", "Theirs")
    store.add_entity("mallory", m["id"], "shared@x.io")
    assert store.overlaps("alice", a["id"])["cases"] == []
    assert store.cases_with_entity("alice", "shared@x.io", exclude_case=a["id"]) == []
    with pytest.raises(KeyError):
        store.overlaps("alice", m["id"])


def test_cases_with_entity_and_graph_annotation(store):
    a = store.create_case("u", "A")
    b = store.create_case("u", "B", )
    store.add_entity("u", b["id"], "evil.example")
    store.update_case("u", b["id"], status="closed")
    store.add_finding("u", a["id"], "evil.example points at 203.0.113.9")
    hits = store.cases_with_entity("u", "EVIL.example", exclude_case=a["id"])
    assert hits == [{"case_id": b["id"], "title": "B", "status": "closed"}]
    assert store.cases_with_entity("u", "evil.example", exclude_case=b["id"])[0]["case_id"] == a["id"]

    plain = store.graph("u", a["id"])
    assert "overlap_cases" not in plain and "also_in" not in plain["nodes"][0]
    g = store.graph("u", a["id"], include_overlap=True)
    by_val = {n["value"]: n for n in g["nodes"]}
    assert by_val["evil.example"]["also_in"][0]["case_id"] == b["id"]
    assert by_val["203.0.113.9"]["also_in"] == []
    assert g["overlap_cases"][0]["status"] == "closed"


def test_tool_overlap_op_and_cross_case_notes(store):
    a = store.create_case("u", "Old lead")
    store.add_entity("u", a["id"], "ghost@mail.example")
    b = store.create_case("u", "New lead")

    r = run_case_file({"op": "add_finding", "case": b["id"], "text": "Contact ghost@mail.example for the dump"}, "u", store=store)
    assert r["exit_code"] == 0 and "Cross-case: ghost@mail.example also appears in Old lead" in r["output"]

    r = run_case_file({"op": "add_entity", "case": b["id"], "value": "fresh.example"}, "u", store=store)
    assert "Cross-case" not in r["output"]

    r = run_case_file({"op": "overlap", "case": b["id"]}, "u", store=store)
    assert "1 selector(s) shared with 1 other case(s)" in r["output"] and a["id"] in r["output"]

    r = run_case_file({"op": "overlap", "case": a["id"]}, "nobody", store=store)
    assert r["exit_code"] == 1


def test_tool_overlap_none(store):
    a = store.create_case("u", "Solo")
    store.add_entity("u", a["id"], "solo.example")
    r = run_case_file({"op": "overlap", "case": "Solo"}, "u", store=store)
    assert r["output"] == "No selectors shared with your other cases."
