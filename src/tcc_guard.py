# src/tcc_guard.py
"""macOS TCC privacy-denial helper.

On macOS, "Desktop & Documents Folders" (and Downloads/Library) are protected
by the OS's TCC layer: processes WITHOUT Full Disk Access get
"Operation not permitted" from the kernel even when the file exists and the
process runs as the same user (stat works, open does not). Agents flail for
rounds against this — retrying the same paths, inventing workarounds. This
module turns that failure mode into ONE actionable tool result:

  * is_tcc_path() — is a path inside a TCC-guarded folder?
  * is_tcc_denial() — does tool output carry the kernel's denial signature?
  * tcc_error() — the actionable explanation (error_code "tcc_denied").

The agent loop (src/agent_loop.py) additionally injects a stop-notice after
repeated tcc_denied results in one turn.
"""

import os
from typing import Optional

TCC_FOLDERS = ("~/Documents", "~/Desktop", "~/Downloads", "~/Library")

_TCC_HINT = (
    "macOS privacy protection (TCC) is blocking this process from reading/writing "
    "{folder} — the OPERATING SYSTEM denies it ('Operation not permitted'), not "
    "Odysseus. To access the file: (1) System Settings → Privacy & Security → "
    "Full Disk Access → enable the app that runs Odysseus (for a native run, "
    "your terminal), then retry; or (2) move/copy the file into the project data "
    "folder or /tmp; or (3) paste the content or attach the file in the chat. "
    "Do NOT keep retrying direct paths under {folder} — every retry fails the "
    "same way until permission is granted."
)


def _expanded(path: str) -> str:
    try:
        return os.path.realpath(os.path.expanduser(str(path or "")))
    except (OSError, TypeError):
        return os.path.expanduser(str(path or ""))


def is_tcc_path(path: str) -> Optional[str]:
    """Return the protected folder name ('~/Documents', …) when `path` sits
    under a TCC-guarded macOS location, else None."""
    if not path:
        return None
    home = os.path.realpath(os.path.expanduser("~"))
    expanded = _expanded(path)
    for folder in TCC_FOLDERS:
        base = os.path.realpath(os.path.expanduser(folder))
        if expanded == base or expanded.startswith(base + os.sep):
            return folder
    # Home itself is not protected, but a few subfolders beyond the list are;
    # catch the two that matter for the common flail patterns.
    return None


def is_tcc_denial(text: str) -> bool:
    """True when tool output carries the kernel/TCC denial signature."""
    t = (text or "")
    low = t.lower()
    return (
        "operation not permitted" in low
        or "errno=1," in low
        or "oserror: [errno 1]" in low
    )


def folder_from_text(text: str) -> Optional[str]:
    """Best-effort: which protected folder does a command/error mention?"""
    t = (text or "").lower()
    for folder in TCC_FOLDERS:
        if folder.lower() in t:
            return folder
    return None


def tcc_error(path: str = "", text: str = "") -> str:
    folder = is_tcc_path(path) or folder_from_text(text) or "a protected folder"
    return _TCC_HINT.format(folder=folder)
