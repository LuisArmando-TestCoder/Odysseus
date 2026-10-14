"""Verify the JEV wait-for-load decision actually triggers a headless render
and re-parse (the half-baked La Nación case), without real browsers/LLM/JEV."""

import pytest

pytest.importorskip("bs4")

from src.search import content as content


class _FakeResponse:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}
    content = b""

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


_NAV_SHELL = """
<html><head><title>JavaScript Article</title></head>
<body><nav>Home About Contact</nav><div id="app"></div>
<script src="/main.abc123.js"></script></body></html>
"""

_RENDERED = """
<html><head><title>JavaScript Article</title></head>
<body><nav>Home About Contact</nav>
<main><article><p>The rendered collaborative article body text appears after JS runs.</p>
<p>It contains several paragraphs of real reporting that the nav shell omits.</p></article></main>
</body></html>
"""


def _patch(monkeypatch, tmp_path, *, wait_prob, rendered):
    monkeypatch.setattr(content, "CONTENT_CACHE_DIR", tmp_path)
    content.content_cache_index.clear()
    monkeypatch.setattr(content, "_get_public_url", lambda url, headers, timeout: _FakeResponse(_NAV_SHELL))
    monkeypatch.setattr(content, "_jev_need_js_render", lambda url, html: wait_prob)
    monkeypatch.setattr(content, "_render_page_js", lambda url, timeout: rendered)
    # No LLM endpoint and no network: make extraction purely heuristic so the
    # test is deterministic and offline.
    monkeypatch.setattr(content, "_extract_article_body_llm", lambda url, html, title: None)


def test_js_render_triggers_and_body_recovered(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, wait_prob=0.9, rendered=_RENDERED)
    result = content.fetch_webpage_content("https://example.com/js-article")

    assert result["success"] is True
    assert result["js_rendered"] is True
    # The rendered body (not the nav shell) must be what got extracted.
    assert "rendered collaborative article body" in result["content"]
    assert "several paragraphs of real reporting" in result["content"]
    assert "Rendered via headless browser" in result["js_message"]


def test_low_wait_prob_skips_render(monkeypatch, tmp_path):
    calls = []

    def _render(url, timeout):
        calls.append(url)
        return _RENDERED

    _patch(monkeypatch, tmp_path, wait_prob=0.1, rendered=_RENDERED)
    monkeypatch.setattr(content, "_render_page_js", _render)

    content.fetch_webpage_content("https://example.com/js-article")

    # Below threshold: plain fetch suffices, so no headless render is attempted.
    assert calls == []