"""
tool_execution.py

Tool dispatcher and result formatter for the agent loop.
Routes tool blocks to MCP servers or native implementations.

Extracted from agent_tools.py.
"""

import asyncio
import collections
import json
import logging
import os
import sys
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from src.tool_security import is_public_blocked_tool, owner_is_admin_or_single_user

MAX_OUTPUT_CHARS = 10_000
MAX_READ_CHARS = 20_000

# T1.2 — per-call tool deadlines (port of DSH guard/timeout-policy). Applied
# by the agent loop's parallel pool; a call past its deadline returns a clean
# {"error_code": "timeout"} result instead of hanging the round. bash/python
# keep their own internal subprocess timeout (_tool_timeout_seconds) as the
# first line; these pool deadlines are the second, slightly larger net.
TOOL_TIMEOUTS: Dict[str, float] = {
    "web_search": 30,
    "web_fetch": 45,
    "universal_search": 30,
    "read_file": 15,
    "write_file": 15,
    "bash": 120,
    "python": 120,
    "download_pdf": 60,
    "google_ai_search": 30,
    "ask_user_question": 600,   # human answer wait (Cluster B)
    "subagent": 900,            # foreground subagent await (Cluster D)
    "sinalevi_search": 30,
    "corteidh_search": 30,
    "corteidh_case": 20,
    "legal_fetch_document": 60,
}

# ---------------------------------------------------------------------------
# Path confinement for read_file / write_file
# ---------------------------------------------------------------------------
# read_file + write_file are admin-only tools, but the path the agent
# supplies is model-controlled. Prompt-injection in an admin's chat can
# weaponise "read /etc/shadow" or "write ~/.ssh/authorized_keys" without
# the admin noticing.
#
# Policy:
#   1. Sensitive-subpath deny list — checked FIRST. Blocks .ssh,
#      .gnupg, shell rc files, token/env files even if the root above
#      them is on the allowlist.
#   2. Allowlist — only the directories the agent legitimately needs
#      (project data/, system tmp). $HOME is NOT on the default list.
#   3. Opt-in extra roots — admin can add broader roots via the
#      "tool_path_extra_roots" setting (list of path strings).
# ---------------------------------------------------------------------------

_SENSITIVE_BASENAMES: set[str] = {
    ".ssh", ".gnupg", ".gitconfig",
    ".bashrc", ".bash_profile", ".bash_logout",
    ".zshrc", ".zprofile", ".zshenv",
    ".profile", ".tcshrc", ".cshrc",
    ".env", ".netrc",
}

_SENSITIVE_FILE_PATTERNS: tuple[str, ...] = (
    "authorized_keys", "id_rsa", "id_ed25519", "id_ecdsa",
    "known_hosts",
)


def _is_sensitive_path(resolved: str) -> bool:
    """Return True if *resolved* falls under a sensitive directory or
    matches a sensitive filename — regardless of what root it sits under.
    """
    parts = resolved.split(os.sep)
    filenames: set[str] = {parts[-1]} if parts else set()

    # Check if any path component is a sensitive directory.
    for part in parts:
        if part in _SENSITIVE_BASENAMES:
            return True

    # Check filename against known sensitive files.
    for pat in _SENSITIVE_FILE_PATTERNS:
        if pat in filenames:
            return True

    return False


def _tool_path_roots(owner: Optional[str] = None, session_id: Optional[str] = None) -> list[str]:
    """Return the list of directory roots that read_file / write_file
    may touch. Default: the session's own documents folder + system temp
    dirs. Extra roots are loaded from the ``tool_path_extra_roots`` setting.
    """
    roots: list[str] = []

    # Project data/ — the documented confinement contract (see
    # tests/test_tool_path_confinement.py): "Legitimate paths under project
    # data/ and /tmp — allowed". Covers documents/, uploads/, spills/ and the
    # other agent workspaces; the sensitive-path deny list above still blocks
    # .ssh/.gnupg/rc-files etc. even under data/.
    from src.constants import DATA_DIR, UPLOAD_DIR
    roots.append(DATA_DIR)

    # The session's own documents folder — the agent's primary workspace.
    # Confined per-session so one chat cannot read another chat's files.
    import re as _re
    sid = _re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or owner or "shared"))
    roots.append(os.path.join(DATA_DIR, "documents", sid))
    if owner:
        roots.append(os.path.join(UPLOAD_DIR, str(owner)))

    # /tmp (and its macOS realpath /private/tmp).
    roots.append("/tmp")
    try:
        private_tmp = os.path.realpath("/tmp")
        if private_tmp != "/tmp":
            roots.append(private_tmp)
    except OSError:
        pass

    # $TMPDIR — per-user temp root on macOS (e.g. /var/folders/.../T/).
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir)

    # Opt-in extra roots from settings.
    try:
        from src.settings import get_setting
        extra = get_setting("tool_path_extra_roots")
        if isinstance(extra, list):
            roots.extend(str(r) for r in extra if r)
    except Exception:
        pass

    # Deduplicate; resolve symlinks so containment is unambiguous.
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        try:
            real = os.path.realpath(r)
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        out.append(real)
    return out


def _resolve_tool_path(raw_path: str, owner: Optional[str] = None, session_id: Optional[str] = None) -> str:
    """Resolve and confine a model-supplied path.

    Order of checks:
      1. Non-empty path.
      2. Sensitive-subpath deny list (blocks .ssh, .gnupg, etc.
         even when the root is on the allowlist).
      3. Allowlist containment (must land under one of the roots).

    Returns the realpath on success. Raises ValueError on rejection.
    Symlinks are resolved before comparison.
    """
    if raw_path is None or not str(raw_path).strip():
        raise ValueError("path is required")
    expanded = os.path.expanduser(str(raw_path).strip())
    resolved = os.path.realpath(expanded)

    if _is_sensitive_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside a sensitive directory "
            f"(e.g. .ssh, .gnupg) or matches a sensitive filename"
        )

    for root in _tool_path_roots(owner, session_id):
        if resolved == root:
            return resolved
        try:
            common = os.path.commonpath([resolved, root])
        except ValueError:
            continue
        if common == root:
            return resolved
    raise ValueError(
        f"path '{raw_path}' is outside the allowed roots"
    )

# Bash + python tools used to share a single 60s timeout. That's
# enough for one-shot commands but starves real workloads (pip
# install, ffmpeg conversions, etc.) — and worse, the agent saw the
# 60s timeout and went silent because it had nothing to report.
# The new default is intentionally generous: long enough that real
# work isn't killed mid-flight, but bounded so a runaway process
# (infinite loop, hung connect, etc.) eventually frees the worker.
# The user can cancel sooner via the chat stop button — when the
# SSE stream is torn down, the asyncio task running the subprocess
# gets cancelled and the subprocess is killed by the finally block.
DEFAULT_BASH_TIMEOUT = 60 * 60     # 1 hour
DEFAULT_PYTHON_TIMEOUT = 60 * 60


def _tool_timeout_seconds() -> float:
    """Resolve the configurable per-tool execution timeout.

    Reads `agent_tool_timeout_seconds` (default 90s — "over a minute").
    0 or negative means unlimited (falls back to the generous 1h cap so a
    runaway process still eventually frees the worker). The chat can change
    this via manage_settings ("tool timeout").
    """
    try:
        from src.settings import get_setting
        val = get_setting("agent_tool_timeout_seconds", 90)
        val = float(val)
    except (TypeError, ValueError):
        val = 90.0
    if val <= 0:
        return DEFAULT_BASH_TIMEOUT  # unlimited → generous cap
    return val

# How often to push a progress event while a long-running subprocess
# is still in flight. The frontend cares about "alive" more than
# "every-byte" — 2s is the sweet spot.
PROGRESS_INTERVAL_S = 2.0
# Tail buffer size — we keep the most recent N lines of stdout +
# stderr so the progress event includes a "what's it doing right now"
# snippet without dragging the whole output along.
PROGRESS_TAIL_LINES = 12


def get_mcp_manager():
    from src import agent_tools
    return agent_tools.get_mcp_manager()


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text

logger = logging.getLogger(__name__)


async def _run_subprocess_streaming(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Tuple[str, str, Optional[int], bool]:
    """Run a subprocess to completion, streaming progress.

    Reads stdout + stderr line-by-line into ring buffers so a
    periodic progress callback can emit a "tail" of recent output
    without waiting for the full result. Returns
    (full_stdout, full_stderr, return_code, timed_out).

    `timed_out=True` means the process was killed because it ran
    past `timeout` seconds. Whatever output we'd buffered up to
    that point is still returned.
    """
    started = time.time()
    stdout_full: list[str] = []
    stderr_full: list[str] = []
    tail = collections.deque(maxlen=PROGRESS_TAIL_LINES)

    async def _reader(stream, full_buf, label: str):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\n")
            full_buf.append(decoded)
            if label == "err":
                tail.append(f"! {decoded}")
            else:
                tail.append(decoded)

    async def _progress_emitter():
        # Skip the first push — many commands finish well under
        # PROGRESS_INTERVAL_S and a 0-second "progress" event would
        # just add UI churn.
        await asyncio.sleep(PROGRESS_INTERVAL_S)
        while True:
            if progress_cb:
                try:
                    await progress_cb({
                        "elapsed_s": round(time.time() - started, 1),
                        "tail": "\n".join(list(tail)),
                    })
                except Exception:
                    # Progress is best-effort — never let a UI hiccup
                    # break the underlying subprocess.
                    pass
            await asyncio.sleep(PROGRESS_INTERVAL_S)

    rd_out = asyncio.create_task(_reader(proc.stdout, stdout_full, "out"))
    rd_err = asyncio.create_task(_reader(proc.stderr, stderr_full, "err"))
    prog_task = asyncio.create_task(_progress_emitter()) if progress_cb else None

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
    except asyncio.CancelledError:
        # User hit stop / SSE stream torn down. Kill the child so it
        # doesn't keep running orphaned. Re-raise so the agent loop's
        # cancellation propagates as the user expects.
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
        # Best-effort: stop the readers + emitter before re-raising.
        for t in (rd_out, rd_err):
            t.cancel()
        if prog_task is not None:
            prog_task.cancel()
        raise
    finally:
        if prog_task is not None and not prog_task.done():
            prog_task.cancel()
            try:
                await prog_task
            except (asyncio.CancelledError, Exception):
                pass
        # Wait for readers to finish draining the pipes.
        for t in (rd_out, rd_err):
            try:
                await asyncio.wait_for(t, timeout=1)
            except Exception:
                pass

    return (
        "\n".join(stdout_full),
        "\n".join(stderr_full),
        proc.returncode,
        timed_out,
    )

_ADMIN_TOOLS = {
    "app_api",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_settings",
    "download_model",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "cancel_download",
}


def _owner_is_admin(owner: Optional[str]) -> bool:
    """Mirror route-level admin behavior for agent tool execution."""
    return owner_is_admin_or_single_user(owner)

# ---------------------------------------------------------------------------
# MCP-backed tool helpers
# ---------------------------------------------------------------------------

# Map legacy tool names -> (MCP server_id, MCP tool_name)
_MCP_TOOL_MAP = {
    "bash":           ("bash",       "bash"),
    "python":         ("python",     "python"),
    "read_file":      ("filesystem", "read_file"),
    "write_file":     ("filesystem", "write_file"),
    "web_search":     ("web_search", "web_search"),
    "web_fetch":      ("web_fetch",  "web_fetch"),
    "generate_image": ("image_gen",  "generate_image"),
}


def _parse_generate_image(content: str) -> Dict:
    lines = content.strip().split("\n")
    args = {"prompt": lines[0].strip() if lines else ""}
    for i, key in enumerate(["model", "size", "quality"], 1):
        if len(lines) > i and lines[i].strip():
            args[key] = lines[i].strip()
    return args


def _parse_manage_memory(content: str) -> Dict:
    lines = content.strip().split("\n")
    action = lines[0].strip().lower() if lines else ""
    args = {"action": action}
    if action == "add":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
        if len(lines) > 2 and lines[2].strip():
            args["category"] = lines[2].strip().lower()
    elif action == "edit":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
        args["text"] = lines[2].strip() if len(lines) > 2 else ""
    elif action == "delete":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "search":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "list":
        if len(lines) > 1 and lines[1].strip():
            args["category"] = lines[1].strip().lower()
    return args


def _parse_write_file(content: str) -> Dict:
    lines = content.split("\n", 1)
    return {"path": lines[0].strip(), "content": lines[1] if len(lines) > 1 else ""}


_MCP_ARG_PARSERS: Dict[str, callable] = {
    "bash":           lambda c: {"command": c},
    "python":         lambda c: {"code": c},
    "web_search":     lambda c: {"query": c.split("\n")[0].strip()},
    "web_fetch":      lambda c: {"url": c.split("\n")[0].strip()},
    "read_file":      lambda c: {"path": c.split("\n")[0].strip()},
    "write_file":     _parse_write_file,
    "generate_image": _parse_generate_image,
    "manage_memory":  _parse_manage_memory,
}


def _build_mcp_args(tool: str, content: str) -> Dict:
    """Convert fenced-block text content to structured MCP arguments."""
    parser = _MCP_ARG_PARSERS.get(tool)
    return parser(content) if parser else {}


async def _call_mcp_tool(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict:
    """Route a legacy tool call through the MCP manager, with direct fallbacks."""
    mcp = get_mcp_manager()
    if not mcp:
        return await _direct_fallback(tool, content, progress_cb=progress_cb, owner=owner, session_id=session_id) or {"error": f"MCP manager not available for tool '{tool}'", "exit_code": 1}

    server_id, tool_name = _MCP_TOOL_MAP[tool]
    qualified = f"mcp__{server_id}__{tool_name}"
    args = _build_mcp_args(tool, content)
    result = await mcp.call_tool(qualified, args)

    # If MCP server not connected, try direct fallback
    if isinstance(result, dict) and result.get("exit_code") == 1 and "not connected" in result.get("error", ""):
        fallback = await _direct_fallback(tool, content, progress_cb=progress_cb, owner=owner, session_id=session_id)
        if fallback:
            return fallback

    return result


_BG_MARKERS = {"#!bg", "#bg", "# bg", "#background", "# background", "@background", "# @background"}


def _split_bg_marker(content: str):
    """If the bash content's first non-empty line is a background marker
    (e.g. `#!bg`), return (True, command_without_marker); else (False, content)."""
    lines = content.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().lower() in _BG_MARKERS:
        del lines[i]
        return True, "\n".join(lines).strip()
    return False, content


async def _direct_fallback(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict]:
    """In-process execution path for the eight tools that used to live as
    stdio MCP servers under mcp_servers/. Those servers were deleted in
    favor of native execution; this function is now the canonical path,
    not a fallback. The name is kept for backwards compat with callers.

    `progress_cb` is called periodically while bash/python subprocesses
    are still running, with `{elapsed_s, tail}` payloads. Other tools
    ignore it.
    """
    import json as _json

    # Inherit env + force a sane terminal so subprocesses that touch
    # terminfo (anything calling `clear`, `tput`, `os.system("clear")`,
    # or scripts that probe $TERM) don't spam "TERM environment variable
    # not set" errors. The agent's bash/python tool calls run with PIPE
    # stdin/stdout (no real TTY), so curses/termios still won't work —
    # but at least non-interactive code with incidental TERM lookups
    # stops failing. COLUMNS/LINES give terminal-width-aware tools (less,
    # rich, etc.) reasonable defaults instead of 0×0.
    _subproc_env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLUMNS": "120",
        "LINES": "40",
    }

    try:
        if tool == "bash":
            proc = await asyncio.create_subprocess_shell(
                content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
            )
            _bash_timeout = _tool_timeout_seconds()
            stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
                proc,
                timeout=_bash_timeout,
                progress_cb=progress_cb,
            )
            if timed_out:
                from src.spill import spill_or_truncate
                return {"error": f"bash: timed out after {_bash_timeout:g}s — process killed", "exit_code": 124,
                        "stdout": spill_or_truncate(stdout, tool="bash", session_id=session_id, owner=owner),
                        "stderr": spill_or_truncate(stderr, tool="bash", session_id=session_id, owner=owner)}
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
            from src.spill import spill_or_truncate
            output = spill_or_truncate(output, tool="bash", session_id=session_id, owner=owner)
            # TCC guard — macOS "Operation not permitted" on protected folders
            # must come back as ONE actionable error, not rounds of flailing.
            from src.tcc_guard import is_tcc_denial, tcc_error
            if is_tcc_denial(output):
                return {
                    "output": output or "(no output)",
                    "error": "bash: macOS privacy protection (TCC) blocked this command\n\n" + tcc_error(text=content),
                    "error_code": "tcc_denied",
                    "exit_code": rc or 1,
                }
            return {"output": output or "(no output)", "exit_code": rc or 0}

        if tool == "python":
            # Run user code in a subprocess so an infinite loop or crash
            # can't take the whole server down. -I = isolated mode (skip
            # user site, no PYTHONPATH inheritance) for hygiene.
            proc = await asyncio.create_subprocess_exec(
                # Use the running interpreter — there is no `python3.exe` on
                # Windows, which made the agent's `python` tool fail there.
                (sys.executable or "python"), "-I", "-c", content,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subproc_env,
            )
            _py_timeout = _tool_timeout_seconds()
            stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
                proc,
                timeout=_py_timeout,
                progress_cb=progress_cb,
            )
            if timed_out:
                from src.spill import spill_or_truncate
                return {"error": f"python: timed out after {_py_timeout:g}s — process killed", "exit_code": 124,
                        "stdout": spill_or_truncate(stdout, tool="python", session_id=session_id, owner=owner),
                        "stderr": spill_or_truncate(stderr, tool="python", session_id=session_id, owner=owner)}
            output = stdout.rstrip()
            err = stderr.rstrip()
            if err:
                output = (output + "\nSTDERR: " + err).strip() if output else "STDERR: " + err
            from src.spill import spill_or_truncate
            output = spill_or_truncate(output, tool="python", session_id=session_id, owner=owner)
            # TCC guard — same one-shot actionable error as bash.
            from src.tcc_guard import is_tcc_denial, tcc_error
            if is_tcc_denial(output):
                return {
                    "output": output or "(no output)",
                    "error": "python: macOS privacy protection (TCC) blocked this code\n\n" + tcc_error(text=content),
                    "error_code": "tcc_denied",
                    "exit_code": rc or 1,
                }
            return {"output": output or "(no output)", "exit_code": rc or 0}

        if tool == "read_file":
            raw_path = content.split("\n", 1)[0].strip()
            try:
                path = _resolve_tool_path(raw_path, owner=owner, session_id=session_id)
            except ValueError as e:
                # TCC guard — a path under ~/Documents|Desktop|Downloads|Library
                # would ALSO be OS-blocked; say so once, with the fix.
                from src.tcc_guard import is_tcc_path, tcc_error
                if is_tcc_path(raw_path):
                    return {"error": f"read_file: {e}\n\n" + tcc_error(path=raw_path),
                            "error_code": "tcc_denied", "exit_code": 1}
                return {"error": f"read_file: {e}", "exit_code": 1}
            try:
                # Run blocking read in a thread to keep the loop responsive.
                # T1.6: read the WHOLE file; oversized content is spilled to
                # disk with a locator instead of being silently discarded —
                # the model can read any offset afterwards.
                def _read():
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        return f.read()
                data = await asyncio.to_thread(_read)
            except FileNotFoundError:
                return {"error": f"read_file: {path}: not found", "exit_code": 1}
            except PermissionError:
                from src.tcc_guard import is_tcc_path, tcc_error
                if is_tcc_path(path):
                    return {"error": f"read_file: {path}: permission denied\n\n" + tcc_error(path=path),
                            "error_code": "tcc_denied", "exit_code": 1}
                return {"error": f"read_file: {path}: permission denied", "exit_code": 1}
            except OSError as e:
                return {"error": f"read_file: {path}: {e}", "exit_code": 1}
            from src.spill import spill_or_truncate
            data = spill_or_truncate(data, tool="read_file", session_id=session_id,
                                     owner=owner, limit=MAX_READ_CHARS)
            # File-guard: remember this read so write_file can warn on unread
            # edits (DSH read-before-write guard, src/file_guard.py).
            try:
                from src.file_guard import mark_read
                mark_read(session_id, path)
            except Exception:
                pass
            return {"output": data, "exit_code": 0}

        if tool == "write_file":
            lines = content.split("\n", 1)
            raw_path = lines[0].strip()
            body = lines[1] if len(lines) > 1 else ""
            try:
                path = _resolve_tool_path(raw_path, owner=owner, session_id=session_id)
            except ValueError as e:
                from src.tcc_guard import is_tcc_path, tcc_error
                if is_tcc_path(raw_path):
                    return {"error": f"write_file: {e}\n\n" + tcc_error(path=raw_path),
                            "error_code": "tcc_denied", "exit_code": 1}
                return {"error": f"write_file: {e}", "exit_code": 1}
            try:
                def _write():
                    d = os.path.dirname(path)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(body)
                    return len(body)
                size = await asyncio.to_thread(_write)
            except PermissionError:
                from src.tcc_guard import is_tcc_path, tcc_error
                if is_tcc_path(path):
                    return {"error": f"write_file: {path}: permission denied\n\n" + tcc_error(path=path),
                            "error_code": "tcc_denied", "exit_code": 1}
                return {"error": f"write_file: {path}: permission denied", "exit_code": 1}
            except OSError as e:
                return {"error": f"write_file: {path}: {e}", "exit_code": 1}
            return {"output": f"Wrote {size} bytes to {path}", "exit_code": 0, "path": path}

        if tool == "web_search":
            from src.search import comprehensive_web_search
            raw = content.strip()
            query = raw
            time_filter = None
            max_pages = 5
            # Allow JSON-shaped args: {"query": "...", "time_filter": "day", "max_pages": 7}
            if raw.startswith("{"):
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict) and "query" in parsed:
                        query = str(parsed.get("query", "")).strip()
                        tf = parsed.get("time_filter") or parsed.get("freshness")
                        if isinstance(tf, str) and tf.lower() in ("day", "week", "month", "year"):
                            time_filter = tf.lower()
                        mp = parsed.get("max_pages")
                        if isinstance(mp, int) and 1 <= mp <= 10:
                            max_pages = mp
                except _json.JSONDecodeError:
                    pass
            if not query:
                query = raw.split("\n")[0].strip()
            # Auto-detect freshness from query phrasing when not explicit
            if time_filter is None:
                q_lc = query.lower()
                if any(kw in q_lc for kw in ("today", "latest", "breaking", "this morning", "right now", "currently")):
                    time_filter = "day"
                elif any(kw in q_lc for kw in ("this week", "past week", "recent news", "last few days")):
                    time_filter = "week"
                elif any(kw in q_lc for kw in ("this month", "past month")):
                    time_filter = "month"
                elif " news" in q_lc or q_lc.startswith("news ") or q_lc.endswith(" news"):
                    time_filter = "week"
            loop = asyncio.get_running_loop()
            text, sources = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: comprehensive_web_search(
                        query,
                        max_pages=max_pages,
                        time_filter=time_filter,
                        return_sources=True,
                    ),
                ),
                timeout=30,
            )
            output = text[:MAX_OUTPUT_CHARS] if len(text) > MAX_OUTPUT_CHARS else text
            if sources:
                output += "\n\n<!-- SOURCES:" + _json.dumps(sources) + " -->"
            return {"output": output, "exit_code": 0}

        if tool == "youtube_transcript":
            from src.youtube_handler import fetch_youtube_transcript
            raw = content.strip()
            url = raw
            target_language = "en"
            if raw.startswith("{"):
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        url = str(parsed.get("url") or "").strip()
                        lang = parsed.get("language")
                        if isinstance(lang, str) and lang.strip():
                            target_language = lang.strip()
                except _json.JSONDecodeError:
                    url = ""
            else:
                # Non-JSON form may carry "@@LANG@@<code>" on a second line.
                lines = raw.split("\n")
                url = lines[0].strip()
                if len(lines) > 1 and lines[1].strip().startswith("@@LANG@@"):
                    target_language = lines[1].strip()[len("@@LANG@@"):] or "en"
            if not url:
                url = raw.split("\n")[0].strip()
            if not url:
                return {"error": "youtube_transcript: provide a YouTube URL", "exit_code": 1}
            loop = asyncio.get_running_loop()
            try:
                data = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: fetch_youtube_transcript(url, max_chars=8000, target_language=target_language),
                    ),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                return {"error": f"youtube_transcript: timed out fetching {url}", "exit_code": 1}
            except Exception as e:
                return {"error": f"youtube_transcript: {e}", "exit_code": 1}
            if not data.get("success"):
                return {"error": f"youtube_transcript: {data.get('error', 'unknown error')}", "exit_code": 1}
            segs = data.get("segments", [])
            lines = []
            lang_label = data.get("language") or "unknown"
            if data.get("translated_from"):
                lang_label = f"{lang_label} (translated from {data['translated_from']})"
            src_kind = "auto-generated ASR" if data.get("is_generated") else "manual captions"
            lines.append(f"YouTube transcript (video {data.get('video_id')}, language {lang_label}, {src_kind}):")
            lines.append("")
            # Timestamped segments when short, plain text otherwise.
            if segs and len(segs) <= 300:
                for s in segs:
                    lines.append(f"[{s['timestamp']}] {s['text']}")
            else:
                lines.append(data.get("transcript", ""))
            output = "\n".join(lines)
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n\n[...truncated]"
            return {"output": output, "exit_code": 0}

        if tool == "reddit_search":
            from src.reddit_scraper import reddit_search
            raw = content.strip()
            query = ""
            subreddit = ""
            limit = 5
            if raw.startswith("{"):
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        query = str(parsed.get("query") or "").strip()
                        subreddit = str(parsed.get("subreddit") or "").strip()
                        lp = parsed.get("limit")
                        if isinstance(lp, int):
                            limit = lp
                except _json.JSONDecodeError:
                    query = raw
            else:
                # Plain form: first line = query, optional second line = subreddit.
                lines_raw = [ln.strip() for ln in raw.split("\n") if ln.strip()]
                query = lines_raw[0] if lines_raw else ""
                subreddit = lines_raw[1] if len(lines_raw) > 1 else ""
            if not query and not subreddit:
                return {"error": "reddit_search: provide a query and/or subreddit", "exit_code": 1}
            loop = asyncio.get_running_loop()
            try:
                data = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: reddit_search(query=query, subreddit=subreddit, limit=limit),
                    ),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                return {"error": "reddit_search: timed out", "exit_code": 1}
            except Exception as e:
                return {"error": f"reddit_search: {e}", "exit_code": 1}
            if not data.get("success"):
                return {"error": f"reddit_search: {data.get('error', 'no results')}", "exit_code": 1}
            posts = data.get("posts", [])
            lines = [f"Reddit results ({len(posts)} posts):", ""]
            for i, p in enumerate(posts, 1):
                lines.append(f"{i}. r/{p.get('subreddit', '?')} · u/{p.get('author', '?')} · score {p.get('score', '?')}")
                lines.append(f"   {p.get('title', '')}")
                if p.get("url"):
                    lines.append(f"   {p.get('url')}")
                st = (p.get("selftext") or "").strip()
                if st:
                    lines.append(f"   {st[:300]}")
            output = "\n".join(lines)
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n\n[...truncated]"
            return {"output": output, "exit_code": 0}

        if tool == "universal_search":
            from services.search.universal import universal_search
            raw = content.strip()
            query = raw
            time_filter = None
            max_pages = 4
            if raw.startswith("{"):
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        query = str(parsed.get("query") or raw).strip()
                        mp = parsed.get("max_pages")
                        if isinstance(mp, int) and 1 <= mp <= 10:
                            max_pages = mp
                        tf = parsed.get("time_filter") or parsed.get("freshness")
                        if isinstance(tf, str) and tf.lower() in ("day", "week", "month", "year"):
                            time_filter = tf.lower()
                except _json.JSONDecodeError:
                    query = raw
            if not query:
                return {"error": "universal_search: a query/claim is required", "exit_code": 1}
            loop = asyncio.get_running_loop()
            try:
                payload = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: universal_search(query, max_pages=max_pages, time_filter=time_filter),
                    ),
                    timeout=60,
                )
            except Exception as e:
                return {"error": f"universal_search: {e}", "exit_code": 1}
            # Format a compact, deterministic response for the model.
            decon = payload.get("deconstruction", {})
            queries = payload.get("queries", {})
            matrix = payload.get("validation", {})
            synth = payload.get("synthesis", {})
            lines = [
                "UNIVERSAL SEARCH RESULT",
                "=" * 40,
                "Phase 1 — Deconstruction:",
                f"  Anchors: {', '.join(decon.get('static_anchors', [])) or '(none)'}",
                f"  Claims:  {', '.join(decon.get('variable_claims', [])) or '(none)'}",
                f"  Constraints: {', '.join(decon.get('structural_constraints', [])) or '(none)'}",
                f"  Information gap: {decon.get('information_gap')}",
                "",
                "Phase 2 — 3-Tier queries:",
                f"  [proximity] {queries.get('tier1_proximity', '')}",
                f"  [broad]     {queries.get('tier2_broad', '')}",
                f"  [opposing]  {queries.get('tier3_opposing', '')}",
                "",
                "Phase 3 — Validation matrix:",
                f"  Source consensus:   {matrix.get('source_consensus', {}).get('score')} ({matrix.get('source_consensus', {}).get('detail', '')})",
                f"  Temporal recency:   {matrix.get('temporal_recency', {}).get('score')} ({matrix.get('temporal_recency', {}).get('detail', '')})",
                f"  Semantic distance:  {matrix.get('semantic_distance', {}).get('score')} ({matrix.get('semantic_distance', {}).get('detail', '')})",
                f"  Domains: {matrix.get('distinct_domains')}  Snippets: {matrix.get('total_snippets')}  Combined: {matrix.get('combined_confidence')}",
                "",
                "Phase 4 — Synthesis:",
                f"  grounded: {synth.get('grounded')}",
                f"  answer: {synth.get('answer', '')}",
            ]
            if synth.get("supporting_sources"):
                lines.append("  supporting sources:")
                for s in synth["supporting_sources"]:
                    lines.append(f"    - {s.get('title', '')} ({s.get('domain', '')}) {s.get('url', '')}")
            output = "\n".join(lines)
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n\n[...truncated]"
            return {"output": output, "exit_code": 0}

        if tool == "web_fetch":
            # Lightweight single-URL fetch. Wraps the SSRF-safe fetcher used
            # by deep research, so private/loopback/metadata addresses are
            # already blocked there.
            from src.search.content import fetch_webpage_content
            raw = content.strip()
            url = ""
            # Accept either a JSON arg ({"url": "..."}) or a plain URL/domain.
            if raw.startswith("{"):
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        url = str(parsed.get("url") or "").strip()
                except _json.JSONDecodeError:
                    url = ""
            if not url:
                # Non-JSON (or JSON without a usable url): take the first line
                # only, so a URL followed by commentary still parses.
                url = raw.split("\n")[0].strip()
            # Reject anything that isn't a single bare URL/domain token.
            if not url or url.startswith("{") or any(c in url for c in (" ", "\t", "\n")):
                return {"error": "web_fetch: provide a single URL or domain, e.g. example.com", "exit_code": 1}
            low = url.lower()
            if "://" in low and not low.startswith(("http://", "https://")):
                return {"error": f"web_fetch: unsupported URL scheme (only http/https): {url[:80]}", "exit_code": 1}
            # Accept bare domains like "example.com" by defaulting to https.
            if not low.startswith(("http://", "https://")):
                url = "https://" + url
            loop = asyncio.get_running_loop()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: fetch_webpage_content(url, timeout=10)),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                return {"error": f"web_fetch: timed out fetching {url}", "exit_code": 1}
            except Exception as e:
                # Direct URL fetches can hit bot protection / auth walls
                # (e.g. eBay 403). Treat that as a tool failure the model can
                # reason around, not an uncaught chat-stream 500.
                return {"error": f"web_fetch: {url}: {e}", "exit_code": 1}
            err = result.get("error")
            text = (result.get("content") or "").strip()
            title = result.get("title") or ""

            if not text:
                if err:
                    return {"error": f"web_fetch: {url}: {err}", "exit_code": 1}
                # No extractable text: non-HTML body, or a pure client-rendered
                # shell. The agent can fall back to the builtin_browser tool.
                return {"error": f"web_fetch: {url}: no readable text content (not HTML, or the page needs JS/login)", "exit_code": 1}

            header = (f"# {title}\n" if title else "") + f"Source: {url}\n\n"
            output = header + text
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n\n[...truncated]"
            return {"output": output, "exit_code": 0}

        # manage_memory / generate_image still live as MCP servers
        # (mcp_servers/{memory,image_gen}_server.py); the MCP path above
        # handles them.
    except Exception as e:
        return {"error": f"{tool}: {e}", "exit_code": 1}

    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def execute_tool_block(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    can_ask_user: bool = False,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    `progress_cb` is forwarded to long-running subprocess tools
    (bash, python) so the agent loop can emit `tool_progress` SSE
    events while the command is in flight. Ignored by other tools.

    `can_ask_user` (Q3) gates the ask_user_question tool: only the live root
    chat turn passes True; delegated contexts receive DELEGATED_CALLER.
    """
    from src.tool_implementations import (
        do_create_document, do_update_document, do_edit_document,
        do_suggest_document, do_search_chats, do_manage_tasks,
        do_manage_skills, do_api_call, do_manage_endpoints,
        do_manage_mcp, do_manage_webhooks, do_manage_tokens,
        do_manage_documents, do_manage_settings, do_manage_notes,
        do_manage_calendar,
        do_download_model, do_serve_model, do_list_served_models, do_stop_served_model,
        do_list_downloads, do_cancel_download, do_search_hf_models, do_list_cached_models,
        do_list_serve_presets, do_serve_preset, do_adopt_served_model,
        do_list_cookbook_servers,
        do_edit_image, do_trigger_research, do_manage_research, do_resolve_contact,
        do_manage_contact,
        do_vault_search, do_vault_get, do_vault_unlock,
        do_app_api, do_sherlock_osint, do_osint_framework,
        do_document_append,
    )
    from src.checkpoint import do_checkpoint
    from src.goal import (
        finish_goal as do_finish_goal,
        create_goal as do_create_goal,
        get_goal as do_get_goal,
        update_goal as do_update_goal,
        get_goal_for_session,
        resolve_owner,
    )
    from src.docfind import do_docfind
    from src.registros_cr import do_registros_cr

    tool = block.tool_type
    content = block.content

    # Misformatted tool call detection: model put JSON inside ```python``` (or
    # similar) without naming the tool. Common with MiniMax-style outputs.
    # Return a helpful error so the model retries with the correct format.
    if tool in ("python", "json", "xml") and content.strip().startswith("{") and content.strip().endswith("}"):
        try:
            import json as _json
            parsed = _json.loads(content.strip())
            if isinstance(parsed, dict):
                desc = f"{tool}: misformatted tool call"
                result = {
                    "error": (
                        f"You wrote a JSON object inside a ```{tool}``` block, but that's not a tool call.\n"
                        "To call a tool, use the tool name as the fence tag, e.g.\n"
                        "```resolve_contact\n"
                        "{\"name\": \"...\"}\n"
                        "```\n"
                        "or\n"
                        "```send_email\n"
                        "{\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}\n"
                        "```"
                    ),
                    "exit_code": 1,
                }
                return desc, result
        except (ValueError, TypeError):
            pass

    # Reject tools that the user has disabled for this request
    if disabled_tools and tool in disabled_tools:
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' is disabled by user.", "exit_code": 1}
        logger.info(f"Tool blocked by user: {tool}")
        return desc, result

    if tool in _ADMIN_TOOLS and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' requires an admin user.", "exit_code": 1}
        logger.warning("Admin tool blocked for non-admin owner=%r tool=%s", owner, tool)
        return desc, result

    if is_public_blocked_tool(tool) and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": (
                f"Tool '{tool}' is restricted to admin users on this deployment. "
                "Ask an admin to perform this action or grant the needed permission."
            ),
            "exit_code": 1,
        }
        logger.warning("Public tool policy blocked owner=%r tool=%s", owner, tool)
        return desc, result

    # Background execution: a `bash` block whose first line is the `#!bg`
    # marker runs DETACHED — returns a job id immediately so the chat stream
    # isn't held open for a multi-minute install/ffmpeg/download. The always-on
    # monitor re-invokes the agent with the full output when the job finishes.
    if tool == "bash" and session_id:
        _is_bg, _bg_cmd = _split_bg_marker(content)
        if _is_bg and _bg_cmd:
            from src import bg_jobs
            rec = bg_jobs.launch(_bg_cmd, session_id=session_id)
            short = _bg_cmd.strip().split(chr(10))[0][:80]
            desc = f"bash (background): {short}"
            result = {
                "output": (
                    f"Started background job `{rec['id']}`. It is running detached — "
                    f"do NOT wait for it or poll it. You will be automatically re-invoked "
                    f"with its full output when it finishes. Continue with other work, or "
                    f"end your turn now and resume when the result arrives."
                ),
                "exit_code": 0,
                "bg_job_id": rec["id"],
            }
            logger.info(f"Tool executed: {desc} -> bg job {rec['id']}")
            return desc, result

    # Route MCP-extracted tools through the MCP manager. Forward
    # the progress callback so long-running subprocess tools
    # (bash, python) can stream `tool_progress` events to the UI.
    if tool in _MCP_TOOL_MAP:
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _call_mcp_tool(tool, content, progress_cb=progress_cb, owner=owner, session_id=session_id)
    elif tool == "create_document":
        title = content.split("\n")[0].strip()[:60]
        desc = f"create_document: {title}"
        result = await do_create_document(content, session_id=session_id, owner=owner)
        # Live artifacts: substantial reports are published as live pages
        # automatically (setting live_artifacts_auto, default on).
        try:
            from src.settings import get_setting as _gs_live
            if (isinstance(result, dict) and result.get("doc_id")
                    and str(_gs_live("live_artifacts_auto", True)).lower() not in ("false", "0")
                    and (result.get("language") or "markdown") in ("markdown", "html", "text", "plain")
                    and len(result.get("content") or "") >= 1200):
                from src import hub as _hub_live
                _it = _hub_live.publish_live(owner, result["doc_id"])
                result["live_url"] = f"/live/{_it['token']}"
        except Exception as _le:
            logger.debug(f"auto live publish skipped: {_le}")
    elif tool == "update_document":
        desc = f"update_document: {content.split(chr(10))[0][:60]}"
        result = await do_update_document(content, owner=owner)
    elif tool == "edit_document":
        result = await do_edit_document(content, owner=owner)
        desc = f"edit_document: {result.get('title', '')}"
    elif tool == "suggest_document":
        result = await do_suggest_document(content, owner=owner)
        desc = f"suggest_document: {result.get('count', 0)} suggestions"
    elif tool in ("search_chats", "session_read", "share_finding", "publish_live"):
        # Knowledge hub (src/hub.py) — cross-session context, always scoped to
        # the caller's own chats. Turn off with setting cross_session_context=false.
        from src import hub as _hub
        try:
            _a = json.loads(content) if content.strip().startswith("{") else None
        except (json.JSONDecodeError, TypeError):
            _a = None
        _allowed = _hub.peers(owner, session_id) if tool != "publish_live" else None
        if _allowed is not None and not _allowed:
            desc = f"{tool}: outside a project"
            result = {"error": "Cross-chat context only works between chats in the same project (sidebar "
                               "folder), and this chat isn't in a project with other chats. Ask the user to "
                               "move the related chats into one project folder.", "exit_code": 1}
        elif tool == "search_chats":
            q = (_a or {}).get("query") if _a else content.strip()
            desc = f"search_chats: {str(q)[:60]}"
            r = _hub.search(owner, q or "", 25, allowed=_allowed)
            lines = []
            for s_ in r["sessions"][:10]:
                lines.append(f"- [{s_['name']}](#{s_['session_id']}) — {s_['hits']} hit(s) · id {s_['session_id'][:8]}")
            lines.append("")
            for h in r["results"][:20]:
                lines.append(f"[{h['session_name']} · {h['role']} · {str(h['timestamp'])[:16]}] {h['snippet']}")
            result = {"output": "\n".join(lines) if r["results"] else "No matches.", "exit_code": 0,
                      "hits": len(r["results"])}
        elif tool == "session_read":
            a = _a or {"session_id": content.strip().split()[0] if content.strip() else ""}
            desc = f"session_read: {str(a.get('session_id'))[:12]}"
            r = _hub.recap(owner, str(a.get("session_id") or ""), query=a.get("query"), allowed=_allowed)
            result = ({"output": f"Chat “{r['name']}” ({r['messages']} messages):\n{r['recap']}", "exit_code": 0}
                      if r else {"error": "Chat not found in this project (use search_chats for ids).", "exit_code": 1})
        elif tool == "share_finding":
            a = _a or {"text": content}
            desc = f"share_finding: {str(a.get('title') or a.get('text'))[:60]}"
            to = a.get("to_session")
            if to:
                _r = _hub.recap(owner, to, max_chars=10, allowed=_allowed)
                to = _r["session_id"] if _r else None
            try:
                it = _hub.share(owner, session_id, a.get("text") or "", to_session=to,
                                kind=a.get("kind") or "finding", title=a.get("title"))
                result = {"output": f"Shared ({it['kind']}){' with chat ' + to[:8] if to else ' on the board'} — the other chats in this project will see it.",
                          "exit_code": 0}
            except ValueError as e:
                result = {"error": str(e), "exit_code": 1}
        else:  # publish_live
            a = _a or {}
            doc_id = a.get("doc_id")
            if not doc_id:
                try:
                    from src.tool_implementations import _active_document_id as _adi
                    doc_id = _adi
                except Exception:
                    doc_id = None
            if not doc_id:
                with _hub._db() as _c:
                    _row = _c.execute("SELECT id FROM documents WHERE session_id=? ORDER BY updated_at DESC LIMIT 1",
                                      (session_id,)).fetchone()
                doc_id = _row["id"] if _row else None
            desc = f"publish_live: {str(doc_id)[:8]}"
            if not doc_id:
                result = {"error": "No document to publish — create one first.", "exit_code": 1}
            else:
                it = _hub.publish_live(owner, doc_id, allow_mcp=bool(a.get("allow_mcp")))
                result = {"output": f"Live page: /live/{it['token']} (updates automatically as the document changes)",
                          "exit_code": 0, "live_url": f"/live/{it['token']}"}
    elif tool in ("chat_with_model", "create_session", "list_sessions",
                  "send_to_session", "pipeline",
                  "manage_session", "manage_memory", "list_models",
                  "ui_control", "ask_teacher"):
        from src.ai_interaction import dispatch_ai_tool
        desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    elif tool == "run_javascript":
        desc = "run_javascript: executing in browser"
        from src.browser_eval import do_run_javascript
        result = await do_run_javascript(
            content, session_id=session_id, owner=owner, progress_cb=progress_cb
        )
    elif tool == "google_ai_search":
        desc = "google_ai_search: asking Google AI Mode"
        from src.tool_implementations import do_google_ai_search
        result = await do_google_ai_search(
            content, session_id=session_id, owner=owner, progress_cb=progress_cb
        )
    elif tool == "download_pdf":
        desc = "download_pdf: downloading PDF"
        from src.tool_implementations import do_download_pdf
        result = await do_download_pdf(content, session_id=session_id, owner=owner)
    elif tool == "scrapebadger":
        desc = "scrapebadger: scraping via ScrapeBadger"
        from src.tool_implementations import do_scrapebadger
        result = await do_scrapebadger(content, session_id=session_id, owner=owner)
    elif tool == "manage_tasks":
        desc = "manage_tasks"
        result = await do_manage_tasks(content, owner=owner)
    elif tool == "manage_skills":
        desc = "manage_skills"
        result = await do_manage_skills(content, owner=owner)
    elif tool == "api_call":
        first_line = content.split("\n")[0].strip()[:60]
        desc = f"api_call: {first_line}"
        result = await do_api_call(content)
    elif tool == "manage_endpoints":
        desc = "manage_endpoints"
        result = await do_manage_endpoints(content, owner=owner)
    elif tool == "manage_mcp":
        desc = "manage_mcp"
        result = await do_manage_mcp(content, owner=owner)
    elif tool == "manage_webhooks":
        desc = "manage_webhooks"
        result = await do_manage_webhooks(content, owner=owner)
    elif tool == "manage_tokens":
        desc = "manage_tokens"
        result = await do_manage_tokens(content, owner=owner)
    elif tool == "manage_documents":
        desc = "manage_documents"
        result = await do_manage_documents(content, owner=owner)
    elif tool == "manage_settings":
        desc = "manage_settings"
        result = await do_manage_settings(content, owner=owner)
    elif tool == "manage_notes":
        desc = "manage_notes"
        result = await do_manage_notes(content, owner=owner)
    elif tool == "manage_calendar":
        desc = "manage_calendar"
        result = await do_manage_calendar(content, owner=owner)
    elif tool == "download_model":
        desc = "download_model"
        result = await do_download_model(content, owner=owner)
    elif tool == "serve_model":
        desc = "serve_model"
        result = await do_serve_model(content, owner=owner)
    elif tool == "list_served_models":
        desc = "list_served_models"
        result = await do_list_served_models(content, owner=owner)
    elif tool == "stop_served_model":
        desc = "stop_served_model"
        result = await do_stop_served_model(content, owner=owner)
    elif tool == "list_downloads":
        desc = "list_downloads"
        result = await do_list_downloads(content, owner=owner)
    elif tool == "cancel_download":
        desc = "cancel_download"
        result = await do_cancel_download(content, owner=owner)
    elif tool == "search_hf_models":
        desc = "search_hf_models"
        result = await do_search_hf_models(content, owner=owner)
    elif tool == "list_cached_models":
        desc = "list_cached_models"
        result = await do_list_cached_models(content, owner=owner)
    elif tool == "app_api":
        desc = "app_api"
        result = await do_app_api(content, owner=owner)
    elif tool == "list_serve_presets":
        desc = "list_serve_presets"
        result = await do_list_serve_presets(content, owner=owner)
    elif tool == "serve_preset":
        desc = "serve_preset"
        result = await do_serve_preset(content, owner=owner)
    elif tool == "adopt_served_model":
        desc = "adopt_served_model"
        result = await do_adopt_served_model(content, owner=owner)
    elif tool == "list_cookbook_servers":
        desc = "list_cookbook_servers"
        result = await do_list_cookbook_servers(content, owner=owner)
    elif tool == "edit_image":
        desc = "edit_image"
        result = await do_edit_image(content, owner=owner)
    elif tool == "universal_search":
        desc = f"universal_search: {content.split(chr(10))[0][:60]}"
        result = await _direct_fallback("universal_search", content)
    elif tool == "youtube_transcript":
        desc = f"youtube_transcript: {content.split(chr(10))[0][:60]}"
        result = await _direct_fallback("youtube_transcript", content)
    elif tool == "reddit_search":
        desc = f"reddit_search: {content.split(chr(10))[0][:60]}"
        result = await _direct_fallback("reddit_search", content)
    elif tool == "trigger_research":
        desc = "trigger_research"
        result = await do_trigger_research(content, owner=owner)
    elif tool == "manage_research":
        desc = "manage_research"
        result = await do_manage_research(content, owner=owner)
    elif tool == "resolve_contact":
        desc = "resolve_contact"
        result = await do_resolve_contact(content, owner=owner)
    elif tool == "manage_contact":
        desc = "manage_contact"
        result = await do_manage_contact(content, owner=owner)
    elif tool == "vault_search":
        desc = "vault_search"
        result = await do_vault_search(content, owner=owner)
    elif tool == "vault_get":
        desc = "vault_get"
        result = await do_vault_get(content, owner=owner)
    elif tool == "vault_unlock":
        desc = "vault_unlock"
        result = await do_vault_unlock(content, owner=owner)
    elif tool == "sherlock_osint":
        desc = "sherlock_osint"
        result = await do_sherlock_osint(content, owner=owner)
    elif tool == "osint_framework":
        desc = "osint_framework"
        result = await do_osint_framework(content, owner=owner)
    elif tool == "case_file":
        from src.casefile_tools import do_case_file
        desc = f"case_file: {content.strip()[:60]}"
        result = await do_case_file(content, owner=owner)
    elif tool == "watch_target":
        from src.casefile_tools import do_watch_target
        desc = f"watch_target: {content.strip()[:60]}"
        result = await do_watch_target(content, owner=owner)
    elif tool == "checkpoint":
        desc = f"checkpoint: {content.split(chr(10))[0][:60]}"
        result = do_checkpoint(content, session_id=session_id, owner=owner)
    elif tool == "finish_goal":
        desc = f"finish_goal: {content.split(chr(10))[0][:60]}"
        # The agent signals done/blocked. Content is JSON {"status": "done"|"blocked", "reason": "..."}
        # or a bare "done"/"blocked" string. The harness owns the transition.
        # Layer 0 / F0.3: goals are owner-keyed; fall back to session id only
        # when no owner is known (then it degrades to a clean "no active goal").
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        _status = _args.get("status") or content.strip().lower()
        _reason = _args.get("reason")
        result = do_finish_goal(owner or session_id or "", _status, reason=_reason)
    elif tool == "create_goal":
        desc = f"create_goal: {content.split(chr(10))[0][:80]}"
        # Layer 0 / F0.4: the agent can START a goal (previously it could only
        # finish one). Content: {"objective": "...", "max_goal_rounds": N?}.
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        _objective = (_args.get("objective") or content.strip() or "").strip()
        if not _objective:
            result = {"error": "create_goal requires an objective", "exit_code": 1}
        else:
            _goal_owner = resolve_owner(session_id, owner)
            if not _goal_owner:
                result = {"error": "create_goal: cannot resolve the goal owner for this session", "exit_code": 1}
            else:
                try:
                    _existing = do_get_goal(_goal_owner)
                except Exception:
                    _existing = None
                if _existing and _existing.get("phase") in ("active", "paused"):
                    result = {"error": "an active goal already exists for this owner", "exit_code": 1}
                else:
                    try:
                        _maxr = int(_args.get("max_goal_rounds") or 0) or None
                    except (TypeError, ValueError):
                        _maxr = None
                    _created = do_create_goal(
                        _goal_owner, _objective, max_goal_rounds=_maxr,
                        session_id=session_id,
                    )
                    result = {"output": f"goal created: {_created['objective'][:120]}",
                              "exit_code": 0, "goal": _created}
    elif tool == "get_goal":
        desc = "get_goal"
        # Session-scoped view: the agent sees the goal of ITS chat (owner-keyed).
        try:
            _goal = get_goal_for_session(session_id, owner)
        except Exception:
            _goal = None
        if not _goal:
            result = {"error": "no active goal for this session", "exit_code": 1}
        else:
            result = {"output": json.dumps(_goal, ensure_ascii=False), "exit_code": 0, "goal": _goal}
    elif tool == "ask_user_question":
        desc = "ask_user_question"
        # Q2/Q3 — the tool REGISTERS the question and returns {"ask_user": ...};
        # the agent loop emits the ask_user SSE event, awaits the human answer
        # with heartbeats, and feeds it back as this call's tool result.
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        _qs = _args.get("questions") or []
        if not isinstance(_qs, list) or not _qs:
            result = {"error": "ask_user_question requires a non-empty questions array", "exit_code": 1}
        else:
            from src.user_questions import ask as _uq_ask
            _reg = _uq_ask(session_id or "", _qs, can_ask_user=can_ask_user)
            if _reg.get("status") == "pending":
                result = {"ask_user": _qs, "session_id": session_id or ""}
            else:
                result = _reg  # DELEGATED_CALLER / ALREADY_PENDING errors
    elif tool == "subagent":
        desc = f"subagent: {content.split(chr(10))[0][:80]}"
        # B2/B3 — start a child agent run (own session, detached). The loop
        # awaits foreground runs with heartbeats; background runs return the
        # subagent_id handle immediately.
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src import subagents as _subs
        result = await _subs.run_subagent(
            parent_session_id=session_id or "",
            owner=owner or "",
            mode=_args.get("mode") or "spawn",
            task=_args.get("task") or content.strip(),
            run_in_background=bool(_args.get("run_in_background")),
            disabled_tools=set(disabled_tools or []),
        )
    elif tool == "send_message":
        desc = f"send_message: {content.split(chr(10))[0][:60]}"
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src import subagents as _subs
        result = _subs.append_message_and_resume(
            _args.get("subagent_id") or "", _args.get("message") or "", owner or "",
        )
    elif tool == "list_agents":
        desc = "list_agents"
        from src import subagents as _subs
        result = {"output": json.dumps(_subs.list_subs(owner or None), ensure_ascii=False),
                  "exit_code": 0}
    elif tool == "interrupt_agent":
        desc = f"interrupt_agent: {content.split(chr(10))[0][:60]}"
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src import subagents as _subs
        result = _subs.interrupt(_args.get("subagent_id") or "", owner or "")
    elif tool == "job_output":
        desc = f"job_output: {content.split(chr(10))[0][:60]}"
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src import subagents as _subs
        result = _subs.job_status(_args.get("job_id") or "", owner or "")
    elif tool == "job_kill":
        desc = f"job_kill: {content.split(chr(10))[0][:60]}"
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src import subagents as _subs
        result = _subs.interrupt(_args.get("job_id") or "", owner or "")
    elif tool == "todo_write":
        desc = f"todo_write: {content.split(chr(10))[0][:60]}"
        # DSH todo parity: maintain the expandable task list shown with the
        # ongoing goal (the goal panel renders /api/goal/queue live).
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        _items = _args.get("items")
        if not isinstance(_items, list) or not _items:
            if not content.strip().startswith("{"):
                _items = [l.strip() for l in content.strip().splitlines() if l.strip()]
        if isinstance(_items, list) and _items:
            # Items: "text" | {"text"/"content"/"title", "status": pending|in_progress|done}.
            # A "[x] " / "- [x] " prefix also marks done.
            import re as _re_td
            _rem, _done, _cur = [], [], None
            for _it in _items:
                if isinstance(_it, dict):
                    _txt = str(_it.get("text") or _it.get("content") or _it.get("title") or "").strip()
                    _stt = str(_it.get("status") or "pending").lower()
                else:
                    _txt = str(_it).strip(); _stt = "pending"
                    _m = _re_td.match(r"^(?:[-*]\s*)?\[( |x|X|~|>)\]\s*", _txt)
                    if _m:
                        _stt = {"x": "done", "X": "done", "~": "in_progress", ">": "in_progress"}.get(_m.group(1), "pending")
                        _txt = _txt[_m.end():].strip()
                if not _txt:
                    continue
                if _stt in ("done", "completed", "complete"):
                    _done.append(_txt)
                else:
                    _rem.append(_txt)
                    if _stt in ("in_progress", "active", "doing") and _cur is None:
                        _cur = _txt
            for _d in (_args.get("done") or []):
                if str(_d) not in _done:
                    _done.append(str(_d))
            from src.checkpoint import checkpoint_set_queue
            checkpoint_set_queue(_rem, session_id, done=_done, current=_cur or (_rem[0] if _rem else None))
            result = {"output": f"todo list: {len(_done)} done, {len(_rem)} remaining"
                                + (f" — now: {_cur or _rem[0]}" if _rem else " — all done"),
                      "exit_code": 0, "items": _rem, "done": _done}
        else:
            result = {"error": "todo_write requires {\"items\": [\"step\", {\"text\": \"step\", \"status\": \"done\"}, ...]}", "exit_code": 1}
    elif tool == "sinalevi_search":
        desc = f"sinalevi_search: {content.split(chr(10))[0][:60]}"
        # L4 — targeted Costa Rican legislation query; results always cite the
        # source so the agent can never fabricate a norm.
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src.legal.sinalevi import search_sinalevi
        _hits = await search_sinalevi(
            term=_args.get("term"), theme=_args.get("theme"), number=_args.get("number"),
        )
        result = {"output": json.dumps([h.as_dict() for h in (_hits or [])[:10]], ensure_ascii=False),
                  "exit_code": 0, "hits": [h.as_dict() for h in (_hits or [])[:10]]}
    elif tool == "corteidh_search":
        desc = f"corteidh_search: {content.split(chr(10))[0][:60]}"
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src.legal.corteidh import corteidh_search
        _hits = await corteidh_search(term=_args.get("term") or "")
        result = {"output": json.dumps([h.as_dict() for h in (_hits or [])[:10]], ensure_ascii=False),
                  "exit_code": 0, "hits": [h.as_dict() for h in (_hits or [])[:10]]}
    elif tool == "corteidh_case":
        desc = f"corteidh_case: {content.split(chr(10))[0][:60]}"
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        from src.legal.corteidh import corteidh_case
        _case = await corteidh_case(_args.get("case_id") or "")
        if not _case:
            result = {"error": "corteidh_case: case not found", "exit_code": 1}
        else:
            result = {"output": json.dumps(_case.as_dict(), ensure_ascii=False),
                      "exit_code": 0, "case": _case.as_dict()}
    elif tool == "legal_fetch_document":
        desc = f"legal_fetch_document: {content.split(chr(10))[0][:60]}"
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        _source = _args.get("source") or ""
        _url = _args.get("pdf_url") or ""
        if _source not in ("sinalevi", "corteidh") or not _url:
            result = {"error": "legal_fetch_document requires source ('sinalevi'|'corteidh') and pdf_url", "exit_code": 1}
        else:
            from src.legal.corpus import fetch_pdf
            _path = await fetch_pdf(
                {"pdf_url": _url, "title": _args.get("title") or _url.rsplit("/", 1)[-1],
                 "number": "", "case_id": "", "url": _url},
                _source,
            )
            if not _path:
                result = {"error": f"legal_fetch_document: could not fetch {_url}", "exit_code": 1}
            else:
                result = {"output": f"document extracted to {_path} — grep it with docfind/read_file",
                          "exit_code": 0, "path": _path}
    elif tool == "update_goal":
        desc = f"update_goal: {content.split(chr(10))[0][:60]}"
        # Model authority (DSH rule): complete/blocked only. edit/pause/resume
        # remain human-only via the API (routes/goal_routes.py).
        try:
            _args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _args = {}
        _action = _args.get("action") or content.strip().lower()
        _goal_owner = resolve_owner(session_id, owner)
        if _action not in ("complete", "blocked"):
            result = {"error": "update_goal from the agent only accepts action 'complete' or 'blocked'", "exit_code": 1}
        elif not _goal_owner:
            result = {"error": "update_goal: cannot resolve the goal owner for this session", "exit_code": 1}
        else:
            try:
                _goal = do_get_goal(_goal_owner)
                if not _goal:
                    raise KeyError("no goal")
                if _action == "complete":
                    _goal = do_update_goal(_goal_owner, _goal["revision"], "complete")
                else:
                    _goal = do_update_goal(
                        _goal_owner, _goal["revision"], "blocked",
                        blocked_reason=_args.get("blocked_reason") or _args.get("reason") or "blocked",
                    )
                result = {"output": f"goal {_action}", "exit_code": 0, "goal": _goal}
            except KeyError:
                result = {"error": "no active goal for this owner", "exit_code": 1}
            except Exception as _ge:
                result = {"error": f"update_goal failed: {_ge}", "exit_code": 1}
    elif tool == "document_append":
        desc = f"document_append: {content.split(chr(10))[0][:60]}"
        result = await do_document_append(content, session_id=session_id, owner=owner)
    elif tool == "docfind":
        desc = f"docfind: {content.split(chr(10))[0][:60]}"
        result = await do_docfind(content, owner=owner, session_id=session_id)
    elif tool == "registros_cr":
        desc = f"registros_cr: {content.split(chr(10))[0][:60]}"
        result = await do_registros_cr(content, owner=owner, session_id=session_id)
    elif tool.startswith("mcp__"):
        # MCP tool dispatch
        mcp = get_mcp_manager()
        if mcp:
            try:
                args = json.loads(content) if content.strip().startswith("{") else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            desc = f"mcp: {tool}"
            result = await mcp.call_tool(tool, args)
        else:
            desc = f"mcp: {tool}"
            result = {"error": "MCP manager not available", "exit_code": 1}
    else:
        desc = f"unknown: {tool}"
        result = {"error": f"Unknown tool type: {tool}", "exit_code": 1}

    logger.info(f"Tool executed: {desc} -> exit_code={result.get('exit_code', 'n/a')}")
    return desc, result


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

# Keys handled by the dedicated branches below — never echo them as raw JSON.
_FORMATTER_HANDLED_KEYS = {
    "stdout", "stderr", "exit_code", "content", "size",
    "response", "results", "session_id", "name", "model", "session_name",
    "success", "path", "action", "title", "doc_id", "version", "applied",
    "error", "output",
}


def format_tool_result(description: str, result: Dict) -> str:
    """Format a tool result into text for feeding back to the LLM."""
    parts = [f"### {description}"]

    if "stdout" in result:
        if result["stdout"]:
            parts.append(f"**stdout:**\n```\n{result['stdout']}\n```")
        if result["stderr"]:
            parts.append(f"**stderr:**\n```\n{result['stderr']}\n```")
        parts.append(f"**exit_code:** {result.get('exit_code', 'unknown')}")
    elif "output" in result:
        # bash / python canonical result shape: {"output": ..., "exit_code": ...}
        parts.append(f"```\n{result['output']}\n```")
        if result.get("exit_code") not in (0, None):
            parts.append(f"**exit_code:** {result['exit_code']}")
    elif "content" in result:
        parts.append(f"**content ({result.get('size', '?')} chars):**\n```\n{result['content']}\n```")
    elif "response" in result:
        model = result.get("model", result.get("session_name", ""))
        if model:
            parts.append(f"**{model} responded:**\n{result['response']}")
        else:
            parts.append(result["response"])
    elif "results" in result:
        parts.append(result["results"])
    elif "session_id" in result and "name" in result:
        parts.append(f"Session created: **{result['name']}** (id: `{result['session_id']}`, model: {result.get('model', 'unknown')})")
    elif "success" in result:
        if result["success"]:
            parts.append(f"File written: {result['path']} ({result['size']} bytes)")
        else:
            parts.append(f"Error: {result.get('error', 'unknown')}")
    elif "action" in result:
        action = result["action"]
        if action == "create":
            parts.append(f"Document created: \"{result.get('title', '')}\" (id: {result['doc_id']}, v{result['version']})")
        elif action == "update":
            parts.append(f"Document updated: \"{result.get('title', '')}\" (v{result['version']})")
        elif action == "edit":
            parts.append(f'Document edited: "{result.get("title", "")}" (v{result.get("version", "?")}, {result.get("applied", 0)} edit(s) applied)')
        elif action == "append":
            parts.append(f"Appended {result.get('chars_appended', 0)} chars to \"{result.get('title', '')}\" (id: {result['doc_id']}, v{result.get('version', '?')})")
    elif "error" in result:
        parts.append(f"**Error:** {result['error']}")

    # Surface any additional structured payload (events, tasks, notes, calendars,
    # documents, attachments, etc.) that the dedicated branches above don't show.
    # Without this, tools that return {"response": "...", "events": [...]} would
    # silently drop the events list and the model would only see the summary line.
    extra = {k: v for k, v in result.items() if k not in _FORMATTER_HANDLED_KEYS}
    if extra:
        try:
            extra_json = json.dumps(extra, indent=2, default=str, ensure_ascii=False)
            # Cap to avoid blowing the context window on huge payloads.
            if len(extra_json) > 8000:
                extra_json = extra_json[:8000] + f"\n... (truncated, {len(extra_json)} chars total)"
            parts.append(f"**data:**\n```json\n{extra_json}\n```")
        except (TypeError, ValueError):
            pass

    return "\n".join(parts)
