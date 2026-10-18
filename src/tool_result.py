# src/tool_result.py
"""Normalized tool-result contract (Layer 0 / T1.1).

``execute_tool_block`` keeps returning ``(description, result_dict)`` for
backward compatibility; ``ToolResult.coerce`` is the single normalization
point that turns any result-dict shape into the structured contract the
harness layers (deadlines T1.2, spill T1.6, ask-user Cluster B, subagents
Cluster D) rely on:

    ok            True when the call succeeded (no "error", exit_code 0)
    output        model-facing text (output|results|stdout|response|content)
    error_code    stable machine-routable code ('timeout', 'not_found', ...)
    is_error      True for failures
    spilled       SpillRef dict when T1.6 spilled oversized output
    call_id       native function-call id when known
    tool          producing tool name
    raw           the original result dict (never mutated)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Tool errors whose exit codes map to stable codes (timeout is special-cased).
_EXIT_CODE_MAP = {
    124: "timeout",
    2: "not_found",
    13: "permission",
}


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: str
    error_code: Optional[str] = None
    is_error: bool = False
    spilled: Optional[Dict[str, Any]] = None
    call_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def coerce(tool: str, result: Any, call_id: Optional[str] = None) -> "ToolResult":
        """Normalize any execute_tool_block result dict into a ToolResult."""
        if not isinstance(result, dict):
            return ToolResult(
                tool=tool, ok=False, output=str(result or ""),
                error_code="unknown", is_error=True, call_id=call_id,
            )
        error = result.get("error")
        error_code = result.get("error_code")
        exit_code = result.get("exit_code", 0)
        if error_code is None and exit_code:
            try:
                error_code = _EXIT_CODE_MAP.get(int(exit_code))
            except (TypeError, ValueError):
                error_code = None
        if error_code is None and error:
            error_code = "tool_error"
        output = ""
        for key in ("output", "results", "stdout", "response", "content"):
            if result.get(key):
                output = str(result[key])
                break
        if not output and error:
            output = str(error)
        ok = not error and error_code is None and exit_code in (0, None)
        return ToolResult(
            tool=tool,
            ok=ok,
            output=output,
            error_code=error_code,
            is_error=not ok,
            spilled=result.get("spilled") if isinstance(result.get("spilled"), dict) else None,
            call_id=call_id,
            raw=result,
        )

    def model_text(self) -> str:
        """Render the result for the next LLM round (spill-aware)."""
        if self.spilled:
            loc = self.spilled.get("locator")
            hint = self.spilled.get("retrieval_hint") or f"read_file '{loc}'"
            prefix = self.output[:2000]
            return (
                f"{prefix}\n\n[FULL OUTPUT SPILLED TO: {loc} — {len(self.output)} chars]\n"
                f"(Use read_file with offset/limit or docfind to inspect it. {hint})"
            )
        return self.output
