"""L6 — legal corpus tests, fully offline (fixture HTML + fakes; no network)."""

import json

import pytest

from src import constants
from src.legal import corpus
from src.legal.sinalevi import LawHit, _parse_results, search_sinalevi
from src.legal.corteidh import _parse_cases, corteidh_catalog

SINALEVI_HTML = """
<html><body>
<table>
 <tr><td><a href="/normas/verNorma.aspx?id=12345">Ley Orgánica del Ambiente, N°7554</a>
     <a href="/pdf/7554.pdf">PDF</a></td></tr>
 <tr><td><a href="http://www.pgrweb.go.cr/scij/Busqueda/Normativa/Normas/nrm_texto_completo.aspx?nValor1=1">Ley de Aguas N°276</a></td></tr>
</table>
</body></html>
"""

CORTEIDH_HTML = """
<html><body>
<a href="https://jurisprudencia.corteidh.or.cr/documento.php?id=123">Caso Velásquez Rodríguez (Serie C No. 4)</a>
<a href="https://www.corteidh.or.cr/docs/casos/articulos/seriec_04_esp.pdf">sentencia</a>
</body></html>
"""


@pytest.fixture(autouse=True)
def _tmp_data(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    yield


# ── parsers ──────────────────────────────────────────────────────────────────

def test_sinalevi_parse_extracts_hits_and_pdf():
    hits = _parse_results(SINALEVI_HTML)
    assert len(hits) >= 2
    by_title = {h.title: h for h in hits}
    ley = by_title.get("Ley Orgánica del Ambiente, N°7554")
    assert ley is not None
    assert ley.number  # N°7554 matched
    assert ley.pdf_url and ley.pdf_url.endswith("7554.pdf")
    assert ley.url.startswith("http")


def test_corteidh_parse_extracts_case_and_pdf():
    hits = _parse_cases(CORTEIDH_HTML)
    assert len(hits) >= 1
    case = hits[0]
    assert case.case_id  # "123" or "4" matched
    pdfs = [h for h in hits if h.pdf_urls]
    assert any("seriec_04_esp.pdf" in p for h in pdfs for p in h.pdf_urls)


# ── adapters (fake transport) ────────────────────────────────────────────────

def _noop():
    async def _n():
        return None
    return _n()


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        return self._resp

    async def get(self, *a, **kw):
        return self._resp


async def test_search_sinalevi_returns_hits(monkeypatch):
    import src.legal.sinalevi as mod
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeClient(_FakeResp(SINALEVI_HTML)))
    monkeypatch.setattr(mod, "_polite", _noop)
    hits = await search_sinalevi(theme="ambiente")
    assert any("Ambiente" in h.title for h in hits)


async def test_search_sinalevi_falls_back_when_form_changes(monkeypatch):
    import src.legal.sinalevi as mod
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeClient(_FakeResp("<html>changed</html>")))
    monkeypatch.setattr(mod, "_polite", _noop)
    monkeypatch.setattr(mod, "_parse_results", lambda html: [])
    monkeypatch.setattr(mod, "_fallback_search", _fake_fallback)
    hits = await search_sinalevi(term="aguas")
    assert hits and hits[0].title == "fallback hit"


async def _fake_fallback(query, client):
    return [LawHit(title="fallback hit", number="", url="https://sinalevi.go.cr/x")]


async def test_corteidh_catalog_offline(monkeypatch):
    import src.legal.corteidh as mod
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeClient(_FakeResp(CORTEIDH_HTML)))
    monkeypatch.setattr(mod, "_polite", _noop)
    insts = await corteidh_catalog()
    assert any("Velásquez" in i.name for i in insts)


# ── corpus store ─────────────────────────────────────────────────────────────

def test_record_dedupes_by_unique_key():
    assert corpus.record({"title": "Ley X", "number": "7554", "url": "u1"}, "sinalevi") is True
    assert corpus.record({"title": "Ley X dup", "number": "7554", "url": "u2"}, "sinalevi") is False
    assert len(corpus.index_rows("sinalevi")) == 1


def test_save_and_search_corpus():
    path = corpus.save_document("sinalevi", "7554", "Ley Orgánica del Ambiente",
                                "Artículo 1. La presente ley regula el ambiente.\nArtículo 2. Definiciones.")
    assert path.endswith(".txt")
    hits = corpus.search_corpus("regula el ambiente", source="sinalevi")
    assert hits and hits[0]["file"] == path
    assert "regula el ambiente" in hits[0]["snippet"]
    # Source filter works.
    assert corpus.search_corpus("regula", source="corteidh") == []


def test_fetch_pdf_dedupes_on_disk():
    # Pre-populated extraction (simulating a previous fetch): no network needed.
    path = corpus.save_document("corteidh", "123", "Caso X", "texto" * 60)
    import asyncio
    hit = {"pdf_url": "https://x/pdf.pdf", "title": "Caso X", "case_id": "123", "url": "u"}
    got = asyncio.run(corpus.fetch_pdf(hit, "corteidh"))
    assert got == path


async def test_bulk_inventory_resumable_and_deduped():
    async def _adapter(q):
        return [{"title": f"ley {q}", "number": q, "url": f"u-{q}"}]

    total = await corpus.bulk_inventory("sinalevi", ["100", "200"], adapter=_adapter)
    assert total == 2
    # Second run adds nothing (index dedupe = the checkpoint).
    total2 = await corpus.bulk_inventory("sinalevi", ["100", "200", "300"], adapter=_adapter)
    assert total2 == 1
