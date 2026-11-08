"""Pin the long-chat performance work (static/js/chatPerf.js + friends).

Each of these regressions made the chat slower with every message:
  * a `:has()` rule whose right-hand compound has no class/id/tag made Chrome
    restyle the WHOLE page on every DOM change (all :has() rules share one
    invalidation set and the `body:has(...)` rules make <body> an anchor);
  * finished entrance animations (fill: both) kept every bubble animated and on
    its own compositor layer;
  * canvas background loops called getComputedStyle every frame;
  * scroll / mutation handlers walked every message.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_STATIC = _REPO / "static"
_PERF = _STATIC / "js" / "chatPerf.js"
_PERF_CSS = _STATIC / "css" / "chat-perf.css"


def _css_files():
    yield _STATIC / "style.css"
    yield from sorted((_STATIC / "css").glob("*.css"))


def _split_top(sel):
    out, depth, cur = [], 0, ""
    for ch in sel:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


def _last_compound(sel):
    depth = 0
    for i in range(len(sel) - 1, -1, -1):
        ch = sel[i]
        if ch in ")]":
            depth += 1
        elif ch in "([":
            depth -= 1
        elif depth == 0 and ch in " >+~":
            return sel[i + 1:], sel[:i + 1].rstrip()
    return sel, ""


def _strip_not(compound):
    out, i = "", 0
    while i < len(compound):
        if compound.startswith(":not(", i):
            depth, i = 1, i + 5
            while i < len(compound) and depth:
                depth += {"(": 1, ")": -1}.get(compound[i], 0)
                i += 1
            continue
        out += compound[i]
        i += 1
    return out


def test_no_has_rule_restyles_the_whole_page():
    """`A:has(x) > :not(.y)` — a featureless subject after a :has() anchor
    poisons Chrome's shared :has invalidation set (whole-page restyles)."""
    bad = []
    for f in _css_files():
        css = re.sub(r"/\*.*?\*/", "", f.read_text(encoding="utf-8"), flags=re.S)
        for m in re.finditer(r"([^{}@;]+)\{", css):
            for sel in _split_top(m.group(1).strip()):
                if ":has(" not in sel:
                    continue
                last, rest = _last_compound(sel)
                if ":has(" in last or not rest or ":has(" not in rest:
                    continue
                if rest.endswith(("~", "+")):
                    continue  # sibling invalidation only: bounded
                core = re.sub(r"::?[\w-]+(\([^)]*\))?", "", _strip_not(last)).lstrip("*")
                if not re.search(r"[.#\[]|^[a-zA-Z]", core):
                    bad.append(f"{f.name}: {sel[:120]}")
    assert not bad, "featureless subject after :has():\n" + "\n".join(bad)


def test_settled_bubbles_drop_their_entrance_animation():
    css = _PERF_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.chat-history > \.msg\.msg-settled\s*\{\s*animation-name:\s*none", css)
    js = _PERF.read_text(encoding="utf-8")
    assert "animationend" in js and "msg-settled" in js


def test_index_and_service_worker_wire_the_layer():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'href="/static/css/chat-perf.css"' in html
    assert html.find('href="/static/css/chat-perf.css"') > html.find('href="/static/css/messages-hyper.css"')
    sw = (_STATIC / "sw.js").read_text(encoding="utf-8")
    assert "'/static/js/chatPerf.js'" in sw and "'/static/css/chat-perf.css'" in sw


def test_history_batch_goes_through_progressive_hydration():
    r = (_STATIC / "js" / "chatRenderer.js").read_text(encoding="utf-8")
    assert "import chatPerf from './chatPerf.js'" in r
    assert "chatPerf.hydrate(box, messages, build, post)" in r
    js = _PERF.read_text(encoding="utf-8")
    for name in ("export function hydrate", "export function flush", "export function hydrating", "export function cancel"):
        assert name in js
    # find-in-page and printing see every message
    assert "e.key === 'f'" in js and "beforeprint" in js


def test_index_math_flushes_hydration_first():
    chat = (_STATIC / "js" / "chat.js").read_text(encoding="utf-8")
    for fn in ("editUserMessage", "resendUserMessage", "regenerateFrom", "forkFrom", "deleteMessage"):
        body = chat[chat.index(f"function {fn}("):]
        body = body[: body.index("allMsgs.indexOf")]
        assert "odysseusChatPerf.flush()" in body, fn


def test_background_loops_do_not_force_style_every_frame():
    theme = (_STATIC / "js" / "theme.js").read_text(encoding="utf-8")
    loops = theme[theme.index("function spawnPulse") - 4000:]
    assert "getComputedStyle(document.documentElement)" not in loops
    assert "function _rootVar(" in theme


def test_scroll_handlers_do_not_walk_every_message():
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "document.querySelectorAll('.memory-used-pill').forEach" not in app
    assert "_deEmojiDirty" in app  # only changed bodies are de-emojified
    ui = (_STATIC / "js" / "ui.js").read_text(encoding="utf-8")
    assert "popupsMaybeOpen('chat-dropdowns')" in ui


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_chat_perf_module_parses():
    out = subprocess.run(["node", "--input-type=module", "--check"], input=_PERF.read_text(encoding="utf-8"),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
