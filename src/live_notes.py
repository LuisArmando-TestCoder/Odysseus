# src/live_notes.py
"""Live notes — new information for a chat turn that is ALREADY running.

A voice request can start a long agent run (research, 15+ minutes). If the
user then says something that changes it ("I found it: it was The Banker,
FT Group"), queuing that until the run finishes means the run keeps searching
blind and ends contradicting the user. A live note is handed to the running
turn instead: the agent loop drains the notes for its session between tool
rounds and adds them to the conversation, so the very next round can use them.

In-memory, per session, owner-tagged, capped. A note never starts a turn on its
own; with no run in progress it simply waits for the next turn of that chat.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional

_MAX_PER_SESSION = 10
_MAX_CHARS = 4000
_TTL_S = 3600

_lock = threading.Lock()
_notes: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_PER_SESSION))


def add(session_id: str, text: str, owner: Optional[str] = None, source: str = "voice") -> Optional[dict]:
    text = (text or "").strip()[:_MAX_CHARS]
    if not session_id or not text:
        return None
    note = {"text": text, "owner": owner, "source": source, "ts": time.time()}
    with _lock:
        _notes[session_id].append(note)
    return note


def pending(session_id: str) -> int:
    with _lock:
        return len(_notes.get(session_id) or ())


def drain(session_id: str) -> List[dict]:
    """Take every fresh note for this session (oldest first)."""
    if not session_id:
        return []
    now = time.time()
    with _lock:
        q = _notes.pop(session_id, None)
    return [n for n in (q or ()) if now - n["ts"] <= _TTL_S]


def as_message(notes: List[dict]) -> Optional[dict]:
    """The notes as one user-role message for the running conversation."""
    if not notes:
        return None
    body = "\n".join(f"- {n['text']}" for n in notes)
    return {
        "role": "user",
        "content": (
            "[New information from me while you were working — use it now. It may answer, narrow or change the "
            "task. Facts I state here outrank search results that do not mention them; verify them if you can, "
            "but do not report them as unfound. If this is a separate request, finish the current task first and "
            "then handle it in the same answer.]\n" + body
        ),
    }


def clear(session_id: str) -> None:
    with _lock:
        _notes.pop(session_id, None)
