"""Case Files graph view: co-occurrence + typed links, caps, hubs, owner scoping."""
import pytest

from src.casefiles import CaseStore


@pytest.fixture
def store(tmp_path):
    return CaseStore(str(tmp_path))


def _ids(g):
    return {n["value"]: n["id"] for n in g["nodes"]}


def test_empty_case_graph(store):
    c = store.create_case("u", "Empty")
    g = store.graph("u", c["id"])
    assert g["nodes"] == [] and g["edges"] == [] and g["hubs"] == [] and g["truncated"] == 0


def test_co_mentions_become_weighted_edges(store):
    c = store.create_case("u", "Acme")
    store.add_finding("u", c["id"], "ops@acme.io runs acme-backup.net", source_url="https://a")
    store.add_finding("u", c["id"], "ops@acme.io is also on acme-backup.net status page", source_url="https://b")
    store.add_entity("u", c["id"], "lonely.example")
    g = store.graph("u", c["id"])
    ids = _ids(g)
    co = [e for e in g["edges"] if {e["a"], e["b"]} == {ids["ops@acme.io"], ids["acme-backup.net"]}]
    assert len(co) == 1 and co[0]["kind"] == "co" and co[0]["weight"] == 2
    assert ids["lonely.example"] in g["isolated"]
    assert g["hubs"][0]["degree"] >= 1
    # Best-connected entities come first.
    assert g["nodes"][-1]["value"] == "lonely.example"


def test_explicit_link_overrides_cooccurrence_and_keeps_direction(store):
    c = store.create_case("u", "Acme")
    store.add_finding("u", c["id"], "jane@acme.io registered acme-backup.net")
    store.link("u", c["id"], "jane@acme.io", "acme-backup.net", "owns", "whois")
    store.link("u", c["id"], "jane@acme.io", "acme-backup.net", "admin_of")
    g = store.graph("u", c["id"])
    ids = _ids(g)
    edges = [e for e in g["edges"] if {e["a"], e["b"]} == {ids["jane@acme.io"], ids["acme-backup.net"]}]
    assert len(edges) == 1
    e = edges[0]
    assert e["kind"] == "link" and e["a"] == ids["jane@acme.io"]
    assert e["relation"] == "owns, admin_of" and e["weight"] >= 1 and e["evidence"] == 1
    assert g["edges"][0]["kind"] == "link"  # typed relations sort first


def test_max_nodes_caps_and_reports_truncation(store):
    c = store.create_case("u", "Big")
    for i in range(12):
        store.add_entity("u", c["id"], f"host{i}.example")
    store.add_finding("u", c["id"], "host0.example and host1.example share an IP")
    g = store.graph("u", c["id"], max_nodes=5)
    assert len(g["nodes"]) == 5 and g["truncated"] == 7
    kept = {n["id"] for n in g["nodes"]}
    assert all(e["a"] in kept and e["b"] in kept for e in g["edges"])
    assert {"host0.example", "host1.example"} <= {n["value"] for n in g["nodes"]}


def test_finding_fanout_is_capped(store):
    c = store.create_case("u", "Paste")
    text = " ".join(f"n{i}.example" for i in range(40))
    store.add_finding("u", c["id"], text)
    g = store.graph("u", c["id"], max_nodes=300)
    assert len(g["edges"]) <= 25 * 24 // 2


def test_graph_is_owner_scoped(store):
    c = store.create_case("alice", "Private")
    store.add_finding("alice", c["id"], "a@b.io and b.io")
    with pytest.raises(KeyError):
        store.graph("mallory", c["id"])
