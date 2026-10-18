"""Port frontend contract tests (static file pins).

The port's backend emits a fixed SSE event vocabulary and route surface; these
tests pin the frontend consumers so a rename on either side breaks loudly.
Static-content assertions (no node execution needed) + one node smoke import of
questionCard.js to prove the module parses and exports its API.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "static" / "js"
_CSS = _REPO / "static" / "css"
_HAS_NODE = shutil.which("node") is not None


def _read(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


def _css(name: str) -> str:
    return (_CSS / name).read_text(encoding="utf-8")


# ── SSE event consumers in chat.js ───────────────────────────────────────────

@pytest.mark.parametrize("event", [
    "ask_user", "subagent_started", "subagent_done", "turn_queued",
    "tool_timeout", "llm_retry", "present", "goal_update", "goal_round",
    "run_buffer_truncated",
])
def test_chat_js_handles_port_events(event):
    chat = _read("chat.js")
    assert f"json.type === '{event}'" in chat, f"chat.js missing handler for {event}"


def test_chat_js_imports_question_card_module():
    assert "import('./questionCard.js')" in _read("chat.js")


def test_chat_js_imports_goal_card_on_goal_events():
    chat = _read("chat.js")
    assert "import('./goalCard.js')" in chat


def test_chat_js_present_uses_build_deliverable_card():
    chat = _read("chat.js")
    assert "buildDeliverableCard" in chat


def test_chat_js_duration_ticker_frozen_by_response_time():
    chat = _read("chat.js")
    assert "setMsgDuration" in chat
    assert "metrics.response_time" in chat
    assert "_durationTicker" in chat


# ── questionCard.js module ───────────────────────────────────────────────────

def test_question_card_exports_and_answer_route():
    qc = _read("questionCard.js")
    for name in ("renderQuestions", "clearQuestions", "restorePendingQuestions"):
        assert f"export function {name}" in qc or f"export async function {name}" in qc
    assert "/api/question/" in qc and "/answer" in qc
    assert 'method: \'POST\'' in qc


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_question_card_module_parses_under_node():
    """The module must parse + export its API (imported, not executed DOM code)."""
    code = _read("questionCard.js")
    # DOM calls live behind the exported functions; import must succeed.
    harness = (
        code
        + "\nif (typeof renderQuestions !== 'function' || typeof restorePendingQuestions !== 'function') { console.error('missing exports'); process.exit(1); }\n"
        + "console.log('QUESTIONCARD_OK');\n"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=30,
    )
    assert "QUESTIONCARD_OK" in out.stdout, out.stderr or out.stdout


# ── sessions.js working dots ─────────────────────────────────────────────────

def test_sessions_polls_chat_active_and_paints_run_dots():
    sess = _read("sessions.js")
    assert "/api/chat/active" in sess
    assert "run-dot" in sess
    assert "_refreshRunDots" in sess


def test_sessions_restores_pending_question_on_switch():
    assert "import('./questionCard.js')" in _read("sessions.js")


# ── chatRenderer.js duration + copy feedback ─────────────────────────────────

def test_renderer_exports_set_msg_duration():
    cr = _read("chatRenderer.js")
    assert "export function setMsgDuration" in cr
    assert "msg-duration" in cr


def test_renderer_copy_cooldown_guard():
    cr = _read("chatRenderer.js")
    assert "btn.dataset.busy" in cr


def test_renderer_exports_build_deliverable_card():
    assert "export function buildDeliverableCard" in _read("chatRenderer.js")


# ── CSS pins ─────────────────────────────────────────────────────────────────

def test_port_css_classes_present():
    messages = _css("messages-hyper.css")
    sidebar = _css("sidebar-hyper.css")
    for cls in ("question-card", "question-send-btn", "msg-duration"):
        assert f".{cls}" in messages
    assert ".run-dot" in sidebar and "runDotPulse" in sidebar
