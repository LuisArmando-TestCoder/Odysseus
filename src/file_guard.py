# src/file_guard.py
"""File-edit guardrails (hook seam) — DSH-style "read before you write".

Rides the H2 hook points (src/hooks.py):

* read-before-write (ENFORCED): a ``write_file`` that would overwrite an
  EXISTING, non-empty file this session has not read is blocked before it
  runs — the model gets a clear error telling it to read the file first.
  Counts as "read": ``read_file`` of that path, a bash/python call that names
  the file, or this session having written the file itself. New files are
  always allowed. Setting ``file_guard_enforce`` = false downgrades the guard
  to the old advisory warning.

* edit diff: the pre-hook snapshots the file (empty for a new file) and the
  post-hook computes a unified diff, attached as ``result["diff"]`` — the
  agent loop emits a ``file_diff`` SSE event, persists it on the tool event,
  and the frontend renders the per-file diff popover.

In-memory per server process (same scope as agent_runs).
"""

import difflib
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EDIT_TOOLS = {"write_file", "edit_document", "update_document"}
_SHELL_TOOLS = {"bash", "python", "run_python", "shell"}

# session_id → set of realpaths read (or written) this process
_READ: Dict[str, set] = {}
# session_id → recent shell commands (a `cat`/`sed -n` of a file counts as a read)
_SHELL: Dict[str, List[str]] = {}
# (session_id, realpath) → previous content snapshot ("" for a new file)
_SNAPSHOTS: Dict[tuple, str] = {}

_MAX_DIFF_LINES = 400
_MAX_SHELL = 200


def _enforce() -> bool:
    try:
        from src.settings import get_setting
        return bool(get_setting("file_guard_enforce", True))
    except Exception:
        return True


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
        rp = os.path.realpath(path)
    except OSError:
        return False
    if rp in _READ.get(session_id, set()):
        return True
    base = os.path.basename(rp)
    return bool(base) and any(base in cmd for cmd in _SHELL.get(session_id, ()))


def _write_target_path(content: str, owner=None, session_id=None) -> Optional[str]:
    """write_file content is '<path>\\n<body>'. Resolve exactly like the tool."""
    if not isinstance(content, str) or "\n" not in content:
        return None
    raw = content.split("\n", 1)[0].strip()
    if not raw:
        return None
    try:
        from src.tool_execution import _resolve_tool_path
        return _resolve_tool_path(raw, owner=owner, session_id=session_id)
    except ValueError:
        # Outside the allowed roots / sensitive: let the tool reject it with its
        # own error — never probe (or reveal) the file from the guard.
        return None
    except Exception:
        return os.path.expanduser(raw)


def _blocked(path: str) -> Dict[str, Any]:
    return {
        "error": (
            f"write_file BLOCKED by the read-before-write guard: {path} already exists and you "
            "have not read it in this session. Read it first (read_file), then write your "
            "change based on its CURRENT content. Nothing was written."
        ),
        "error_code": "unread_edit_blocked",
        "exit_code": 1,
        "guard": {"path": path, "rule": "read-before-write"},
    }


async def pre_tool_use_hook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """PRE_TOOL_USE — record shell reads, enforce read-before-write, snapshot."""
    try:
        tool = payload.get("tool")
        session_id = payload.get("session_id")
        if tool in _SHELL_TOOLS and session_id:
            cmds = _SHELL.setdefault(session_id, [])
            cmds.append(str(payload.get("content") or "")[:2000])
            del cmds[:-_MAX_SHELL]
            return payload
        if tool != "write_file" or not session_id:
            return payload
        path = _write_target_path(payload.get("content") or "", payload.get("owner"), session_id)
        if not path:
            return payload
        rp = os.path.realpath(path)
        if not os.path.exists(path):
            _SNAPSHOTS[(session_id, rp)] = ""          # new file → diff against empty
            return payload
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                current = f.read()
        except OSError:
            return payload
        if current.strip() and not was_read(session_id, path) and _enforce():
            payload["blocked"] = _blocked(path)
            return payload
        _SNAPSHOTS[(session_id, rp)] = current
    except Exception as e:
        logger.debug("[file-guard] pre-hook failed: %s", e)
    return payload


def _diff(path: str, old: str, new: str) -> Optional[Dict[str, Any]]:
    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{os.path.basename(path)}", tofile=f"b/{os.path.basename(path)}",
        lineterm="",
    ))
    if not lines:
        return None
    return {
        "path": path,
        "added": sum(1 for l in lines if l.startswith("+") and not l.startswith("+++")),
        "removed": sum(1 for l in lines if l.startswith("-") and not l.startswith("---")),
        "new_file": old == "",
        "lines": lines[:_MAX_DIFF_LINES],
        "truncated": len(lines) > _MAX_DIFF_LINES,
    }


async def post_tool_use_hook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST_TOOL_USE — diff + (advisory mode only) unread-edit warning."""
    try:
        if payload.get("tool") in EDIT_TOOLS and payload.get("tool") != "write_file":
            # A document this session created/edited is "known" to it.
            _r = payload.get("result")
            if isinstance(_r, dict) and _r.get("path") and payload.get("session_id"):
                mark_read(payload["session_id"], str(_r["path"]))
            return payload
        if payload.get("tool") != "write_file":
            return payload
        result = payload.get("result")
        session_id = payload.get("session_id")
        if not isinstance(result, dict) or not session_id or result.get("exit_code") != 0:
            return payload
        path = _write_target_path(payload.get("content") or "", payload.get("owner"), session_id) \
            or result.get("path")
        if not path:
            return payload
        rp = os.path.realpath(path)
        old = _SNAPSHOTS.pop((session_id, rp), None)
        if old is not None:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    d = _diff(path, old, f.read())
                if d:
                    result["diff"] = d
            except OSError:
                pass
        if not _enforce() and not was_read(session_id, path) and old:
            result["output"] = str(result.get("output") or "") + (
                "\n\n[GUARD] This file was modified without a prior read in this session. "
                "If you have not seen its current content, read_file it first and verify your edit."
            )
            result["error_code"] = result.get("error_code") or "unread_edit"
        mark_read(session_id, path)    # the agent now knows this file's content
    except Exception as e:
        logger.debug("[file-guard] post-hook failed: %s", e)
    return payload
