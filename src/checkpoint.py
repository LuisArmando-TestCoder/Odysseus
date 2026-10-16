"""
checkpoint.py

Persistent, append-only execution checkpoint for the agent loop.

The agent forgets its own working state between turns (paths it confirmed,
endpoints it found, what it already extracted/wrote), so each turn re-derives
and re-explores instead of advancing. This module backs `do_checkpoint` — the
tool that turns "state" from volatile model context into durable on-disk lines.

Checkpoint file:  data/agent_state/<session>.md   (append-only)
Primitives:
  - append(text):  append a timestamped line to the checkpoint (never rewrites).
  - read():        return the whole checkpoint so a nudge/`go` becomes *resume*.
"""

import json
import os
import re
from typing import Any, Dict, Optional


def _checkpoint_path(session_id: Optional[str] = None, owner: Optional[str] = None) -> str:
    """Return the on-disk path for a session's append-only execution checkpoint."""
    from src.constants import DATA_DIR
    base = os.path.join(DATA_DIR, "agent_state")
    os.makedirs(base, exist_ok=True)
    if session_id:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id))
        return os.path.join(base, f"{safe}.md")
    if owner:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(owner))
        return os.path.join(base, f"owner_{safe}.md")
    return os.path.join(base, "_default.md")


def checkpoint_read(session_id: Optional[str] = None, owner: Optional[str] = None) -> str:
    """Read the full checkpoint file (empty string if none exists yet)."""
    path = _checkpoint_path(session_id, owner)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    return ""


def checkpoint_append(text: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> None:
    """Append a timestamped entry to the checkpoint. Never rewrites existing lines."""
    path = _checkpoint_path(session_id, owner)
    from datetime import datetime as _dt
    stamp = _dt.utcnow().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## [{stamp}]\n{text}\n")


def do_checkpoint(content: str, session_id: Optional[str] = None,
                  owner: Optional[str] = None) -> Dict[str, Any]:
    """Append-only execution checkpoint tool.

    Content is a JSON object (or JSON string) shaped like:
        {"action": "append" | "read", "text": "..."}

    - `append` appends `text` to `data/agent_state/<session>.md` (never rewrites),
      so the agent accumulates an execution trail instead of re-deriving.
    - `read` returns the whole checkpoint file so the agent can resume instead
      of re-exploring.

    Returns {"output": ..., "exit_code": 0} on success, or {"error": ...,
    "exit_code": 1} on failure.
    """
    action = "append"
    text = ""
    if isinstance(content, str) and content.strip():
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            # Not JSON — treat the whole string as the text to append. Models
            # often pass bare markdown rather than a JSON object.
            parsed = {"text": content}
        if isinstance(parsed, dict):
            action = str(parsed.get("action", "append") or "append").lower()
            text = str(parsed.get("text", ""))
        else:
            text = str(parsed)
    elif isinstance(content, dict):
        action = str(content.get("action", "append") or "append").lower()
        text = str(content.get("text", ""))

    path = _checkpoint_path(session_id, owner)

    try:
        if action == "read":
            data = checkpoint_read(session_id, owner)
            if not data:
                return {"output": "(checkpoint is empty — nothing recorded yet)", "exit_code": 0}
            return {"output": data, "exit_code": 0}

        if action == "append":
            if not text.strip():
                return {"error": "checkpoint append requires non-empty `text`", "exit_code": 1}
            checkpoint_append(text, session_id, owner)
            return {"output": f"checkpoint appended ({len(text)} chars) to {os.path.basename(path)}", "exit_code": 0}

        return {"error": f"checkpoint: unknown action '{action}' (use 'append' or 'read')", "exit_code": 1}
    except OSError as e:
        return {"error": f"checkpoint IO error: {e}", "exit_code": 1}


def _queue_path(session_id=None, owner=None):
    from src.constants import DATA_DIR
    base = os.path.join(DATA_DIR, "agent_state")
    os.makedirs(base, exist_ok=True)
    if session_id:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id))
        return os.path.join(base, f"{safe}.queue.json")
    if owner:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(owner))
        return os.path.join(base, f"owner_{safe}.queue.json")
    return os.path.join(base, "_default.queue.json")


def checkpoint_set_queue(items, session_id=None, owner=None):
    from core.atomic_io import atomic_write_json
    atomic_write_json(_queue_path(session_id, owner),
                      {"remaining": list(items), "done": []})


def checkpoint_queue_state(session_id=None, owner=None):
    p = _queue_path(session_id, owner)
    if not os.path.exists(p):
        return {"remaining": [], "done": []}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def checkpoint_next(session_id=None, owner=None):
    s = checkpoint_queue_state(session_id, owner)
    return s["remaining"][0] if s["remaining"] else None


def checkpoint_pop_done(item, session_id=None, owner=None):
    from core.atomic_io import atomic_write_json
    s = checkpoint_queue_state(session_id, owner)
    if item in s["remaining"]:
        s["remaining"].remove(item)
        s["done"].append(item)
    atomic_write_json(_queue_path(session_id, owner), s)
    return s