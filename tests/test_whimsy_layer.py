"""Pin the wiring of the whimsy layer (static/js/whimsy.js + static/css/whimsy.css).

The layer is purely additive — it observes the DOM the app renders — so what
can silently break is the wiring: the page must load it after the HUD themes,
it must stay gated on html.wh-on (reduced motion / user opt-out), and the
particle canvas must survive voice mode's `content-visibility: hidden` sweep
over every other body child.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "static" / "js" / "whimsy.js"
_CSS = _REPO / "static" / "css" / "whimsy.css"
_INDEX = _REPO / "static" / "index.html"
_SW = _REPO / "static" / "sw.js"


def test_index_loads_whimsy_after_the_hud_themes():
    html = _INDEX.read_text(encoding="utf-8")
    css_at = html.find('href="/static/css/whimsy.css"')
    assert css_at != -1
    # loaded after the last theme sheet so it layers on top
    assert css_at > html.find('href="/static/css/sidebar-a11y.css"')
    assert 'src="/static/js/whimsy.js"' in html


def test_service_worker_precaches_the_layer():
    sw = _SW.read_text(encoding="utf-8")
    assert "'/static/js/whimsy.js'" in sw
    assert "'/static/css/whimsy.css'" in sw


def test_motion_is_gated_on_wh_on_and_reduced_motion():
    css = _CSS.read_text(encoding="utf-8")
    js = _JS.read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in js
    assert "forced-colors: active" in js
    assert "odysseus-whimsy" in js  # user opt-out key
    # every entrance/confirmation animation is scoped to html.wh-on
    for sel in ("wh-enter-user", "wh-enter-ai", "wh-pop", "wh-shake", "wh-land"):
        rules = [m.group(0) for m in re.finditer(r"[^{}]*\." + sel + r"[^{]*\{", css)]
        assert rules, sel
        assert all("html.wh-on" in r for r in rules), (sel, rules)
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_particle_canvas_survives_voice_mode_content_visibility():
    css = _CSS.read_text(encoding="utf-8")
    assert re.search(r"html\.vx-open body > \.wh-fx\s*\{\s*content-visibility:\s*visible !important", css)


def test_overlays_never_live_inside_message_bubbles():
    # Other modules read a bubble's children (footer, last child, raw text);
    # glint/sweep overlays must be appended to <body>, not the message.
    js = _JS.read_text(encoding="utf-8")
    assert "msg.appendChild" not in js
    assert "document.body.appendChild(sw)" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")
def test_module_parses():
    proc = subprocess.run(["node", "--check", str(_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
