# src/file_guard.py
"""File-edit guardrails (hook seam) — DSH-style "read before you write".

Two advisory guards ride the H2 hook points (src/hooks.py):

* read-before-write: ``write_file`` targeting a path this session never READ
  gets a warning appended to its tool result — the model should verify the
  current content before editing (DSH's guard that avoids changing a file
  that was never detected as read first).

* edit diff: for ``write_file``, the pre-hook snapshots the file's current
  content and the post-hook computes a unified diff, attached to the result
  as ``result["diff"]`` — the agent loop emits a `file_diff` SSE event and
  the frontend renders the pick-diff popover.

In-memory per server process (same scope as agent_runs); advisory only —
never blocks a write.
"""

import difflib
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

EDIT_TOOLS = {"write_file", "edit_document", "update_document"}

# session_id → set of realpaths read this process
_READ: Dict[str, set] = {}
# (session_id, realpath) → previous content snapshot
_SNAPSHOTS: Dict[tuple, str] = {}

_MAX_DIFF_LINES = 400


def mark_read(session_id: Optional[str], path: Optional[str]) -> None:
    if not session_id or not path:
        return
    try:
        rp = os.path.realpath(path)
    except OSError:
        return
    _READ.setdefault(session_id, set()).add(rp)


def was_read(session_id: Optional[str], path: Optional[str]) -> bool:
    if not session_id or not path:
        return False
    try:
        return os.path.realpath(path) in _READ.get(session_id, set())
    except OSError:
        return False


def _write_target_path(content: str) -> Optional[str]:
    """write_file content is '<path>\\n<body>'."""
    if not isinstance(content, str) or "\n" not in content:
        return None
    raw = content.split("\n", 1)[0].strip()
    if not raw:
        return None
    return os.path.expanduser(raw)


async def pre_tool_use_hook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """PRE_TOOL_USE — snapshot the current file for write_file (diff source)."""
    try:
        if payload.get("tool") != "write_file":
            return payload
        path = _write_target_path(payload.get("content") or "")
        if not path or not os.path.exists(path):
            return payload
        session_id = payload.get("session_id")
        if not session_id:
            return payload
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                _SNAPSHOTS[(session_id, os.path.realpath(path))] = f.read()
        except OSError:
            pass
    except Exception as e:
        logger.debug("[file-guard] pre-hook failed: %s", e)
    return payload


async def post_tool_use_hook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST_TOOL_USE — diff + read-before-write warning."""
    try:
        tool = payload.get("tool")
        if tool not in EDIT_TOOLS:
            return payload
        result = payload.get("result")
        if not isinstance(result, dict):
            return payload
        session_id = payload.get("session_id")

        if tool == "write_file":
            path = _write_target_path(payload.get("content") or "")
            if path and session_id and result.get("exit_code") == 0:
                rp = os.path.realpath(path)
                old = _SNAPSHOTS.pop((session_id, rp), None)
                if old is not None:
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            new = f.read()
                        if old != new:
                            diff_lines = list(difflib.unified_diff(
                                old.splitlines(), new.splitlines(),
                                fromfile=f"a/{os.path.basename(path)}",
                                tofile=f"b/{os.path.basename(path)}",
                                lineterm="",
                            ))
                            if diff_lines:
                                added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
                                removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
                                result["diff"] = {
                                    "path": path,
                                    "added": added,
                                    "removed": removed,
                                    "lines": diff_lines[:_MAX_DIFF_LINES],
                                }
                    except OSError:
                        pass
                # Read-before-write advisory.
                if not was_read(session_id, path):
                    _warn = (
                        "\n\n[GUARD] This file was modified without a prior read in this "
                        "session. If you have not seen its current content, read_file it "
                        "first and verify your edit."
                    )
                    result["output"] = str(result.get("output") or "") + _warn
                    result["error_code"] = result.get("error_code") or "unread_edit"
    except Exception as e:
        logger.debug("[file-guard] post-hook failed: %s", e)
    return payload
