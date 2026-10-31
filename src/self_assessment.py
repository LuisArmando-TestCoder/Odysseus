# src/self_assessment.py
"""Self-assessment ledger — what the assistant learned about its own work.

An append-only list of short, independent notes per chat (plus an optional
owner-wide list). Each note records something the assistant got wrong or must
do differently, almost always because the user corrected it:

    kind      correction | lesson | preference | fact
    text      the note, one or two sentences
    evidence  the user's own words that back it (verbatim quote, required for
              notes extracted from speech — a note without evidence is refused)
    relation  "context-only" when the user said two things are related only as
              context, NOT as cause and effect; "" otherwise

The block injected into prompts starts with an epistemic rule: the notes are
INDEPENDENT. Being listed together in this block is not evidence that they are
connected. In particular no cause-and-effect chain may be built from two notes
just because both are here — a causal link exists only when one note states it
explicitly. (The 2026-10-30 session showed why: a damaged gearbox mount and a
sludge-clogged gearbox were both "maintenance" findings in the same context, and
the voice turned that co-occurrence into a vibration→slipping causal chain the
expert had explicitly ruled out.)

Storage: ``data/self_assessment/<owner>/<session>.jsonl`` (``_owner.jsonl`` for
owner-wide notes). Never raises on read; writes are atomic per line.
"""

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KINDS = ("correction", "lesson", "preference", "fact")
_MAX_ENTRIES = 200
_MAX_TEXT = 400
_MAX_EVIDENCE = 400

EPISTEMIC_RULE = (
    "These are independent self-assessment notes about how you have been doing in this conversation. "
    "Apply each note on its own. Being listed together here does NOT mean the notes are related: never "
    "connect two notes into a cause-and-effect chain because they share this list, a topic or a time. "
    "A causal link exists only where a single note states it explicitly. A note marked [context-only] "
    "records that the user ruled the relationship to be context, not cause — never present it as a cause."
)


def _root() -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "self_assessment")
    os.makedirs(d, exist_ok=True)
    return d


def _safe(name: Optional[str], fallback: str) -> str:
    s = "".join(c for c in str(name or "") if c.isalnum() or c in "-_.@")[:120]
    return s or fallback


def _path(owner: Optional[str], session_id: Optional[str]) -> str:
    d = os.path.join(_root(), _safe(owner, "_anon"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, (_safe(session_id, "") or "_owner") + ".jsonl")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _read(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict) and e.get("text"):
                    out.append(e)
    except FileNotFoundError:
        pass
    except Exception as e:  # never break a prompt build
        logger.debug(f"self-assessment read failed: {e}")
    return out


def entries(owner: Optional[str], session_id: Optional[str] = None,
            include_owner: bool = True) -> List[Dict[str, Any]]:
    """Notes for this chat (oldest first), plus owner-wide notes when asked."""
    items: List[Dict[str, Any]] = []
    if include_owner:
        items.extend(dict(e, scope="owner") for e in _read(_path(owner, None)))
    if session_id:
        items.extend(dict(e, scope="session") for e in _read(_path(owner, session_id)))
    removed = {e.get("removes") for e in items if e.get("removes")}
    return [e for e in items if not e.get("removes") and e.get("id") not in removed][-_MAX_ENTRIES:]


def append(owner: Optional[str], session_id: Optional[str], text: str, *,
           kind: str = "lesson", evidence: str = "", source: str = "chat",
           relation: str = "", scope: str = "session") -> Optional[Dict[str, Any]]:
    """Append one note. Returns the stored entry, the existing one when the
    same note is already there (no duplicates), or None when refused."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:_MAX_TEXT]
    if len(text) < 8:
        return None
    kind = kind if kind in KINDS else "lesson"
    relation = "context-only" if str(relation or "").strip().lower() in ("context-only", "context", "contextual") else ""
    sid = None if scope == "owner" else session_id
    key = _norm(text)
    for e in entries(owner, sid, include_owner=(sid is None)):
        if _norm(e.get("text", "")) == key:
            return e
    entry = {
        "id": hashlib.sha1(f"{owner}|{sid}|{key}|{time.time()}".encode()).hexdigest()[:12],
        "ts": time.time(),
        "kind": kind,
        "text": text,
        "evidence": re.sub(r"\s+", " ", str(evidence or "")).strip()[:_MAX_EVIDENCE],
        "source": str(source or "chat")[:24],
        "relation": relation,
    }
    try:
        with open(_path(owner, sid), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"self-assessment append failed: {e}")
        return None
    return entry


def remove(owner: Optional[str], session_id: Optional[str], entry_id: str) -> bool:
    """Tombstone a note (the file stays append-only)."""
    for scope_sid in ([session_id] if session_id else []) + [None]:
        if any(e.get("id") == entry_id for e in _read(_path(owner, scope_sid))):
            with open(_path(owner, scope_sid), "a", encoding="utf-8") as f:
                f.write(json.dumps({"removes": entry_id, "ts": time.time(), "text": "-"}) + "\n")
            return True
    return False


def prompt_block(owner: Optional[str], session_id: Optional[str] = None, max_chars: int = 3000) -> str:
    """The notes as a prompt block, newest kept when over budget, each note on
    its own numbered line. "" when there are none."""
    items = entries(owner, session_id)
    if not items:
        return ""
    lines: List[str] = []
    used = len(EPISTEMIC_RULE) + 60
    for e in reversed(items):                      # newest first while budgeting
        tag = e.get("kind", "lesson")
        if e.get("relation") == "context-only":
            tag += "] [context-only"
        line = f"[{tag}] {e['text']}"
        if e.get("evidence"):
            line += f' — the user said: "{e["evidence"]}"'
        if used + len(line) + 6 > max_chars:
            break
        used += len(line) + 6
        lines.append(line)
    lines.reverse()
    body = "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1))
    return "=== SELF-ASSESSMENT NOTES (independent; do not chain) ===\n" + EPISTEMIC_RULE + "\n" + body


EXTRACT_PROMPT = """The user just corrected or taught the assistant something. Turn it into ONE short
self-assessment note the assistant must apply from now on, written as an instruction to itself
(e.g. "Treat the damaged mount as context about overall maintenance, not as a cause of the internal failure.").
Answer ONLY with JSON:
{"note": "<the note, one or two sentences, same language as the user>",
 "kind": "correction|lesson|preference|fact",
 "evidence": "<the user's own words that support the note, copied EXACTLY from user_speech>",
 "relation": "context-only|"}
- relation is "context-only" only when the user said two things are related as context and NOT as cause/effect.
- Do not add facts, causes or conclusions the user did not state. If user_speech contains no correction or
  lesson, answer {"note": ""}."""


def _evidence_ok(evidence: str, speech: str) -> bool:
    """The quoted evidence must really be in what the user said (normalized);
    a paraphrase is accepted only if most of its words appear there."""
    ev, sp = _norm(evidence), _norm(speech)
    if not ev or not sp:
        return False
    if ev in sp:
        return True
    words = [w for w in ev.split() if len(w) > 2]
    return bool(words) and sum(w in sp.split() for w in words) / len(words) >= 0.8


async def extract_and_append(owner: str, session_id: Optional[str], user_speech: str,
                             context: str = "", llm=None, source: str = "voice") -> Optional[Dict[str, Any]]:
    """Ask a small model to turn a user correction into a note, then keep it
    only if its evidence is a real quote of the user. ``llm(system, user)`` is
    an async callable returning text (the voice pipeline's utility model)."""
    if not user_speech or llm is None:
        return None
    try:
        raw = await llm(EXTRACT_PROMPT, json.dumps(
            {"user_speech": user_speech[:3000], "recent_context": (context or "")[:3000]},
            ensure_ascii=False))
        m = re.search(r"\{.*\}", raw or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.debug(f"self-assessment extraction failed: {e}")
        return None
    note = str(data.get("note") or "").strip()
    evidence = str(data.get("evidence") or "").strip()
    if not note or not _evidence_ok(evidence, user_speech):
        return None
    return append(owner, session_id, note, kind=str(data.get("kind") or "correction"),
                  evidence=evidence, source=source, relation=str(data.get("relation") or ""))
