"""Pin the pure keyboard helpers in static/js/sidebarA11y.js.

The module drives the sidebar's WAI-ARIA tree, menu, toolbar and splitter
patterns. Its DOM wiring is guarded by `typeof document`, so under node only
the exported pure helpers run — which is exactly what these tests lock in:
arrow/Home/End movement (clamped for the tree, wrapping for menus/toolbar),
type-ahead cycling, keyboard resize (incl. mirrored right-side sidebar), and
folder accessible names.

Same harness as test_esc_menu_stack_js.py: source inlined into a
`node --input-type=module` body; skips when node is missing.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MOD = _REPO / "static" / "js" / "sidebarA11y.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _MOD.read_text(encoding="utf-8") if _MOD.exists() else ""


def _run(body: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=_SRC + "\n" + body, capture_output=True, text=True,
        encoding="utf-8", cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


_needs_node = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


@_needs_node
def test_tree_movement_clamps_at_edges():
    out = _run("""console.log(JSON.stringify([
      moveIndex(5, 0, 'ArrowUp'), moveIndex(5, 4, 'ArrowDown'),
      moveIndex(5, 2, 'ArrowDown'), moveIndex(5, 2, 'Home'), moveIndex(5, 2, 'End'),
      moveIndex(5, -1, 'ArrowDown'), moveIndex(0, 0, 'ArrowDown'), moveIndex(5, 3, 'x')]));""")
    assert out == [0, 4, 3, 0, 4, 0, -1, 3]


@_needs_node
def test_menu_movement_wraps():
    out = _run("""console.log(JSON.stringify([
      moveIndex(3, 2, 'ArrowDown', true), moveIndex(3, 0, 'ArrowUp', true)]));""")
    assert out == [0, 2]


@_needs_node
def test_typeahead_cycles_and_matches_prefix():
    out = _run("""const L = ['Alpha', 'beta', 'Apple', 'Brain', 'Cookbook'];
    console.log(JSON.stringify([
      typeaheadIndex(L, 0, 'a'),      // next A after Alpha → Apple
      typeaheadIndex(L, 2, 'a'),      // wraps back to Alpha
      typeaheadIndex(L, 0, 'aa'),     // repeated char still cycles
      typeaheadIndex(L, 0, 'br'),     // multi-char prefix
      typeaheadIndex(L, 0, 'z'),      // no match
      typeaheadIndex(L, -1, 'c'),
      typeaheadIndex([], 0, 'a')]));""")
    assert out == [2, 0, 2, 3, -1, 4, -1]


@_needs_node
def test_keyboard_resize_steps_clamps_and_mirrors():
    out = _run("""console.log(JSON.stringify([
      resizeTarget(340, 'ArrowRight'), resizeTarget(340, 'ArrowLeft', {shift: true}),
      resizeTarget(205, 'ArrowLeft'), resizeTarget(695, 'ArrowRight'),
      resizeTarget(340, 'ArrowLeft', {mirrored: true}),
      resizeTarget(340, 'Home'), resizeTarget(340, 'End'), resizeTarget(340, 'Enter')]));""")
    assert out == [356, 276, 200, 700, 356, 200, 700, None]


@_needs_node
def test_folder_names_read_naturally():
    out = _run("""const a = parseFolderLabel(' Work ', '(4)');
    const b = parseFolderLabel('Solo', '(1)');
    console.log(JSON.stringify([
      folderAccessibleName(a.name, a.count), folderAccessibleName(b.name, b.count),
      folderAccessibleName('', null)]));""")
    assert out == ["Work, 4 chats", "Solo, 1 chat", "Folder"]


def test_index_wires_the_sidebar_a11y_layer():
    # Runs without node: static wiring that the keyboard layer depends on.
    html = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
    assert '/static/js/sidebarA11y.js' in html
    assert '/static/css/sidebar-a11y.css' in html
    # CSS must load after the theme layers it overrides.
    assert html.index('/static/css/sidebar-a11y.css') > html.index('/static/css/jarvis-mobile.css')
    assert 'id="session-list" role="tree"' in html
    assert 'aria-describedby="sb-tree-help"' in html
    assert 'class="sb-skip-link" href="#message"' in html


def test_hud_layer_loads_between_theme_and_a11y_and_fixes_goal_card():
    html = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
    hud = html.index('/static/css/sidebar-hud.css')
    assert html.index('/static/css/jarvis-mobile.css') < hud < html.index('/static/css/sidebar-a11y.css')
    css = (_REPO / "static" / "css" / "sidebar-hud.css").read_text(encoding="utf-8")
    # The goal card must not shrink inside the scrolling flex column.
    assert ".sidebar-inner > .goal-card" in css and "flex: 0 0 auto" in css
    assert (_REPO / "static" / "fonts" / "ChakraPetch-SemiBold.woff2").exists()
