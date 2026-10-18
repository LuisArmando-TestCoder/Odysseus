"""Tests for src/tool_result.py — the normalized tool-result contract (T1.1)."""

from src.tool_result import ToolResult


def test_coerce_success_output():
    r = ToolResult.coerce("bash", {"output": "hello", "exit_code": 0})
    assert r.ok and not r.is_error
    assert r.tool == "bash" and r.output == "hello"
    assert r.error_code is None


def test_coerce_error_with_exit_code():
    r = ToolResult.coerce("web_search", {"error": "nope", "exit_code": 1})
    assert not r.ok and r.is_error
    assert r.error_code == "tool_error"


def test_coerce_timeout_exit_code_maps():
    r = ToolResult.coerce("bash", {"error": "timed out", "exit_code": 124})
    assert r.error_code == "timeout"


def test_coerce_explicit_error_code_wins():
    r = ToolResult.coerce("x", {"error": "deadline", "error_code": "timeout", "exit_code": 124})
    assert r.error_code == "timeout"


def test_coerce_picks_first_content_key():
    r = ToolResult.coerce("t", {"output": "", "results": "from-results", "stdout": "from-stdout"})
    assert r.output == "from-results"


def test_coerce_non_dict_is_error():
    r = ToolResult.coerce("t", None)
    assert not r.ok and r.error_code == "unknown"


def test_coerce_error_output_falls_back_to_error_text():
    r = ToolResult.coerce("t", {"error": "boom happened", "exit_code": 1})
    assert r.output == "boom happened"


def test_model_text_plain():
    r = ToolResult.coerce("t", {"output": "abc", "exit_code": 0})
    assert r.model_text() == "abc"


def test_model_text_spilled_shows_locator():
    r = ToolResult(
        tool="bash", ok=True, output="x" * 3000,
        spilled={"locator": "/data/spills/o/s/1_bash.txt", "bytes": 3000,
                 "retrieval_hint": "read_file '/data/spills/o/s/1_bash.txt'"},
    )
    text = r.model_text()
    assert "FULL OUTPUT SPILLED TO: /data/spills/o/s/1_bash.txt" in text
    assert len(text) < 3000  # model sees prefix + locator, not the whole thing
