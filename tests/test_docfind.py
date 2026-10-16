"""tests/test_docfind.py — docfind tool: fetch-primary-doc + grep (DSH technique)."""
import asyncio
import json

from src.docfind import do_docfind, _grep, _json_text


def test_grep_finds_exact_term_with_context():
    text = "line one\nMaynor de Jesús Ledezma Rojas\nline three\n"
    hits = _grep(text, ["Ledezma"], 1)
    assert any("Ledezma Rojas" in h for h in hits)


def test_grep_unique_key_no_false_positive():
    # cédula exacta no presente → cero matches (no homónimos).
    text = "John Smith, US politician\nJane Smith, CEO\n"
    assert _grep(text, ["6-0260-0212"], 0) == []


def test_json_text_pretty():
    data = json.dumps({"slug": "el-fuego-del-amor-consciente", "title": "Rebeca y la defensa"}).encode()
    txt = _json_text(data)
    assert "Rebeca" in txt


def test_do_docfind_local_file_append_and_find(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("factura 12345\ncliente Acme\n", encoding="utf-8")
    res = asyncio.run(do_docfind(json.dumps({"file": str(f), "terms": ["12345"]})))
    assert res["exit_code"] == 0, res
    assert "12345" in res["output"]


def test_do_docfind_no_match_exit_1(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("nada relevante", encoding="utf-8")
    res = asyncio.run(do_docfind(json.dumps({"file": str(f), "terms": ["999-999-999"]})))
    assert res["exit_code"] == 1
