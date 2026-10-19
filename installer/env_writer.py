"""Odysseus installer — safe, idempotent .env reader/writer.

Rules:
  * Preserve existing lines and comments verbatim.
  * Only replace the value of a KEY we are explicitly writing; append a new
    line for any key not already present.
  * Detect leading `export ` and inline comments and preserve the style.
  * Set file permissions to 0o600 (secrets must never be world-readable).
"""

import os
import re
from pathlib import Path
from typing import Dict, Optional

# A .env line is `KEY=value` (optionally `export KEY=value`, optionally with a
# trailing inline comment after an unquoted value).
_LINE_RE = re.compile(
    r'^(?P<export>\s*export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$'
)


def read_env(path: Path) -> Dict[str, str]:
    """Parse a .env file into a dict. Last occurrence wins. Ignores comments/blank lines."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        m = _LINE_RE.match(line)
        if not m:
            continue
        key = m.group("key")
        values[key] = _strip_value(m.group("value"))
    return values


def _strip_value(value: str) -> str:
    value = value.strip()
    # Strip a trailing inline comment only when the value is unquoted
    # (a quoted value may legally contain '#').
    if value and value[0] not in ("'", '"'):
        value = re.sub(r'\s+#.*$', '', value)
    value = value.strip()
    # Strip matching surrounding quotes.
    if len(value) >= 2:
        if value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
    return value


def write_env(path: Path, updates: Dict[str, str], comment_header: Optional[str] = None) -> None:
    """Merge `updates` into the .env file, preserving untouched lines.

    `updates` is a dict {ENV_KEY: value}. Values are written unquoted but are
    escaped so that '#', leading/trailing whitespace and newlines are safe.
    """
    existing = read_env(path)
    original_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    written: Dict[str, bool] = {k: False for k in updates}

    out_lines = []
    for raw in original_lines:
        line = raw.rstrip("\n")
        m = _LINE_RE.match(line)
        if m and m.group("key") in updates:
            key = m.group("key")
            out_lines.append(_format_line(key, updates[key], m.group("export")))
            written[key] = True
        else:
            out_lines.append(line)

    # Append any keys not already present (nice, grouped at the end).
    appended = [k for k in updates if not written[k]]
    if appended and out_lines and out_lines[-1] != "":
        out_lines.append("")
    if appended and comment_header:
        out_lines.append(f"# {comment_header}")
    for k in appended:
        out_lines.append(_format_line(k, updates[k], ""))

    # Ensure trailing newline, then write atomically with 0600 perms.
    text = "\n".join(out_lines)
    if text and not text.endswith("\n"):
        text += "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp then replace, to avoid truncation on failure.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _format_line(key: str, value: str, export: str) -> str:
    prefix = "export " if export and export.strip() else ""
    if value == "":
        return f"{prefix}{key}="
    # Escape newlines and strip surrounding whitespace; protect a '#' so it
    # is not read back as an inline comment.
    safe = value.replace("\n", "\\n").strip()
    if safe and not _value_is_safe_bare(safe):
        safe = f'"{safe.replace(chr(34), chr(92) + chr(34))}"'
    return f"{prefix}{key}={safe}"


def _value_is_safe_bare(value: str) -> bool:
    """Return True if the value can be written unquoted without ambiguity."""
    if value.startswith(("#", " ")) or value.endswith(" "):
        return False
    if '"' in value or "'" in value:
        return False
    if " #" in value:
        return False
    return True