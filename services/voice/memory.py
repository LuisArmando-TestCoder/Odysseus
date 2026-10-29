# services/voice/memory.py
"""Quasi-infinite, reliable voice memory via continuous hierarchical compression.

Raw voice turns accumulate in a *live* buffer. Every ``batch_size`` turns the
oldest batch is distilled into a level-1 "factual points" summary. Whenever a
level holds ``merge_size`` summaries they are merged losslessly into one
level-(N+1) patch — recursively, forever. This reuses the exact prompts and
merge algorithm of ``src.context_compactor`` so chat and voice memory behave
identically.

What the pipeline agents consume is the **spine**:

  - the highest-layer summary (most compressed overview of everything)
  - the newest summary of EVERY intermediate level (the "middle" of the
    compression — recent detail at each granularity)
  - the lowest layer: the current raw, not-yet-compressed batch

Compression runs in the background (an always-on compressor agent) so the
hot path never waits on it unless it wants a fresh spine.
"""

import asyncio
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_SENT_HISTORY = 30
_MAX_LIVE_TURNS = 400  # safety valve if compression is failing
_MAX_TRANSCRIPT_TURNS = 2000  # full raw voice transcript (user + assistant), never compressed
_MAX_VECTORS = 5000  # conversation-vector cap in persistence (the HUD shows far fewer)


def _memory_dir() -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "voice_memory")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_id(owner: str, voice_id: str) -> str:
    raw = f"{owner or '_'}::{voice_id or 'default'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _voice_settings() -> Dict[str, int]:
    try:
        from src.settings import load_settings
        s = load_settings()
    except Exception:
        s = {}
    def _int(k, d, lo, hi):
        try:
            return max(lo, min(hi, int(s.get(k, d))))
        except (TypeError, ValueError):
            return d
    return {
        "batch_size": _int("voice_batch_size", 20, 2, 64),
        "merge_size": _int("voice_merge_size", 3, 2, 12),
    }


class _VectorPersistenceWorker:
    """Serialized background writer for conversation-vector appends.

    ``add_vector()`` is called from the hot path (middleman guard, chat vector
    detection, error reporting) where a disk write must never block the event
    loop or a request thread. The naive fix — ``run_in_executor`` — is unsafe
    here because ``VoiceMemory`` instances are shared and ``atomic_write_json``
    uses a per-PID temp file: two concurrent saves would race and corrupt the
    file. Instead, ONE daemon thread drains a bounded queue, appending each
    vector to its memory store in arrival order. This is both non-blocking
    (enqueue only) and race-free (all writes serialize through one thread).
    """

    def __init__(self):
        self._q: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="voice-vector-persist", daemon=True)
        self._thread.start()

    # Mutation is queued by (owner, voice_id); the worker mutates the shared
    # VoiceMemory + saves. Queued until drained; the worker coalesces writes so
    # a burst of dots is a single disk write. The enqueue itself is non-blocking
    # (put_nowait), so the hot path never waits on the disk I/O that happens in
    # the worker thread.
    def enqueue_vector(self, owner: str, voice_id: str, vector: Dict[str, Any]):
        try:
            self._q.put_nowait(("vector", owner, voice_id, vector))
        except Exception as e:
            logger.debug(f"voice vector queue full/dropped: {e}")

    def _run(self):
        while True:
            try:
                try:
                    first = self._q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if first is None:
                    continue
                now = time.time()
                items = [(*first, now)]
                # Bundle follow-ups that arrived near-simultaneously.
                try:
                    while len(items) < 120 and time.time() - now < 0.05:
                        item = self._q.get_nowait()
                        if item is None:
                            break
                        items.append((*item, time.time()))
                except queue.Empty:
                    pass
            except Exception as e:
                logger.debug(f"voice vector worker drain error: {e}")
                continue

            # Group by memory key; append all vectors for a key, then save once.
            grouped: Dict[str, Dict[str, Any]] = {}
            for kind, owner, voice_id, payload, _ts in items:
                if kind != "vector":
                    continue
                g = grouped.setdefault((owner, voice_id), {"owner": owner, "voice_id": voice_id, "vectors": []})
                g["vectors"].append(payload)

            for (owner, voice_id), g in grouped.items():
                try:
                    mem = get_voice_memory(owner, voice_id)
                    for vec in g["vectors"]:
                        mem.vectors.append(vec)
                    mem.vectors = mem.vectors[-_MAX_VECTORS:]
                    mem._save()
                except Exception as e:
                    logger.debug(f"voice vector persist failed: {e}")


_vector_worker: Optional[_VectorPersistenceWorker] = None


def _get_vector_worker() -> _VectorPersistenceWorker:
    global _vector_worker
    if _vector_worker is None:
        _vector_worker = _VectorPersistenceWorker()
    return _vector_worker


class VoiceMemory:
    """Per-(owner, voice conversation) hierarchical memory."""

    def __init__(self, owner: str, voice_id: str):
        self.owner = owner or ""
        self.voice_id = voice_id or "default"
        self.path = os.path.join(_memory_dir(), _safe_id(self.owner, self.voice_id) + ".json")
        self.live: List[Dict[str, Any]] = []          # raw, uncompressed turns
        self.levels: Dict[int, List[str]] = {}        # level -> summaries (chronological)
        self.sent: List[Dict[str, Any]] = []          # messages forged into the chat
        self.transcript: List[Dict[str, Any]] = []    # full raw voice turns (user + assistant), never compressed
        self.tasks: List[Dict[str, Any]] = []         # tasks dispatched to the chat (in-progress + queued), for restore
        self.vectors: List[Dict[str, Any]] = []       # conversation vectors (radar dots) for density viz
        self.turns_total = 0
        self.compactions = 0
        self.seq = 0            # monotonically increasing turn sequence number
        self.forge_seq = 0      # user turns with seq <= forge_seq were already evaluated/acted on
        self.commit_seq = 0     # assistant turns with seq <= commit_seq were already checked for promises
        self.created_at = time.time()
        self.updated_at = time.time()
        self._lock = asyncio.Lock()
        self._compact_task: Optional[asyncio.Task] = None
        # >0 while a pipeline decision/forge runs. Compression never competes
        # with it for the utility model — it is a background nice-to-have.
        self.pipeline_active = 0
        # A forged message the guard was unsure about, waiting for the user's
        # yes/no ({"message", "title", "ts"}). In-memory only: it expires fast.
        self.pending_confirm: Optional[Dict[str, Any]] = None
        self._load()

    # ── Persistence ──

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self.live = d.get("live") or []
        self.levels = {int(k): v for k, v in (d.get("levels") or {}).items() if isinstance(v, list)}
        self.sent = d.get("sent") or []
        self.transcript = d.get("transcript") or []
        self.tasks = d.get("tasks") or []
        self.vectors = d.get("vectors") or []
        self.turns_total = int(d.get("turns_total") or 0)
        self.compactions = int(d.get("compactions") or 0)
        self.seq = int(d.get("seq") or 0)
        self.forge_seq = int(d.get("forge_seq") or 0)
        self.commit_seq = int(d.get("commit_seq") or 0)
        self.created_at = float(d.get("created_at") or time.time())
        self.updated_at = float(d.get("updated_at") or time.time())

    def _save(self):
        try:
            from core.atomic_io import atomic_write_json
            atomic_write_json(self.path, {
                "owner": self.owner,
                "voice_id": self.voice_id,
                "live": self.live,
                "levels": {str(k): v for k, v in self.levels.items()},
                "sent": self.sent[-_MAX_SENT_HISTORY:],
                "transcript": self.transcript,
                "tasks": self.tasks[-_MAX_SENT_HISTORY:],
                "vectors": self.vectors[-_MAX_VECTORS:],
                "turns_total": self.turns_total,
                "compactions": self.compactions,
                "seq": self.seq,
                "forge_seq": self.forge_seq,
                "commit_seq": self.commit_seq,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            })
        except Exception as e:
            logger.warning(f"voice memory save failed: {e}")

    def reset(self):
        self.live, self.levels, self.sent, self.transcript, self.tasks, self.vectors = [], {}, [], [], [], []
        self.turns_total = self.compactions = 0
        self.seq = self.forge_seq = self.commit_seq = 0
        self.updated_at = time.time()
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

    # ── Ingest ──

    def add_turns(self, turns: List[Dict[str, Any]]) -> int:
        added = 0
        for t in turns or []:
            text = (t.get("text") or "").strip()
            if not text:
                continue
            raw_sp = str(t.get("speaker", "")).lower()
            if raw_sp in ("you", "user"):
                speaker = "user"
            elif raw_sp in ("chat_user", "chat-user"):
                speaker = "chat_user"      # message that went into the chat thread
            elif raw_sp in ("chat_ai", "chat-ai", "chat_assistant"):
                speaker = "chat_ai"        # the chat agent's answer
            elif raw_sp in ("user_note", "note", "context"):
                # Text the user typed/pasted into the HUD as reference context
                # (an article, a quote). Kept in memory but never evaluated as
                # new speech, so it can't trigger a forge on its own.
                speaker = "user_note"
            else:
                speaker = "assistant"      # the voice model (GPT-Live)
            # A relay is the voice model reading the CHAT AGENT's output aloud
            # ("I'll write it up as a document"). It is not the assistant's own
            # promise, so it must never be forged into a new chat request.
            relay = bool(t.get("relay")) and speaker == "assistant"
            # Merge streaming fragments from the same speaker into one turn.
            if (self.live and self.live[-1]["speaker"] == speaker and not t.get("final_break")
                    and bool(self.live[-1].get("relay")) == relay):
                last = self.live[-1]
                if time.time() - last.get("ts", 0) < 12:
                    sep = "" if (text[:1] in ",.!?;:" or last["text"].endswith((" ", "-"))) else " "
                    last["text"] = (last["text"] + sep + text).strip()
                    last["ts"] = time.time()
                    self.seq += 1
                    last["seq"] = self.seq   # grown turn counts as new speech
                    # Keep the permanent transcript in lock-step with the merged turn.
                    if speaker in ("user", "assistant") and self.transcript and self.transcript[-1]["speaker"] == speaker:
                        self.transcript[-1]["text"] = last["text"]
                        self.transcript[-1]["ts"] = last["ts"]
                    continue
            self.seq += 1
            entry = {"speaker": speaker, "text": text, "ts": time.time(), "seq": self.seq}
            if relay:
                entry["relay"] = True
            self.live.append(entry)
            self.turns_total += 1
            added += 1
            # The permanent transcript is the full conversation the chat must be
            # able to recall verbatim: user + assistant speech AND user_note (text
            # the user typed/pasted as context — a fact, a preference, a link).
            # user_note is NOT "spoken", but it IS real user-provided context the
            # chat must see — omitting it is exactly why the chat answered "I have
            # nothing saved" after the user typed "me gusta la cantante pomme".
            # chat_user / chat_ai already live in the chat thread's own history,
            # so they are intentionally kept OUT of the transcript.
            if speaker in ("user", "assistant", "user_note"):
                self.transcript.append({"speaker": speaker, "text": text, "ts": time.time()})
        if len(self.live) > _MAX_LIVE_TURNS:
            self.live = self.live[-_MAX_LIVE_TURNS:]
        if len(self.transcript) > _MAX_TRANSCRIPT_TURNS:
            self.transcript = self.transcript[-_MAX_TRANSCRIPT_TURNS:]
        self.updated_at = time.time()
        self._save()
        return added

    # ── Consumption + duplicate guard (deterministic, model-independent) ──

    def new_user_turns(self) -> List[Dict[str, Any]]:
        """User speech not yet evaluated by the decider."""
        return [t for t in self.live if t["speaker"] == "user" and t.get("seq", 0) > self.forge_seq]

    def new_assistant_turns(self) -> List[Dict[str, Any]]:
        """Assistant speech not yet checked for a concrete promise."""
        return [t for t in self.live if t["speaker"] == "assistant" and not t.get("relay")
                and t.get("seq", 0) > self.commit_seq]

    def consume_commitments(self):
        """Mark assistant speech as checked for promises."""
        self.commit_seq = self.seq
        self._save()

    def recent_turns(self, n: int = 8) -> List[Dict[str, Any]]:
        return [{"speaker": t["speaker"], "text": t["text"]} for t in self.live[-n:]]

    def transcript_text(self, max_turns: int = 60) -> str:
        """The full voice transcript (user + assistant), newest last, as text.
        This is the conversation the pipeline agents need to resolve references
        ("the arpeggio", "that file") and to judge faithfulness — the raw live
        buffer is trimmed/compressed, but the transcript is never compressed.
        Each turn carries its timestamp so agents can reason about ordering and
        recency."""
        turns = self.transcript[-max_turns:]
        lines = []
        for t in turns:
            ts = t.get("ts")
            stamp = ""
            if ts:
                try:
                    stamp = time.strftime("[%H:%M:%S] ", time.localtime(float(ts)))
                except (TypeError, ValueError, OSError):
                    stamp = ""
            lines.append(f"{stamp}{t['speaker'].upper()}: {t['text']}")
        return "\n".join(lines)

    def consume(self):
        """Mark everything heard so far as evaluated — the same speech can
        never be forged into the chat twice."""
        self.forge_seq = self.seq
        self._save()

    @staticmethod
    def _tokens(text: str) -> set:
        stop = {"the", "a", "an", "to", "of", "and", "or", "please", "me", "my", "i", "for",
                "in", "on", "it", "that", "this", "is", "be", "can", "you", "with", "at"}
        return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in stop and len(w) > 1}

    def is_duplicate(self, message: str, threshold: float = 0.6, window_s: float = 300) -> Optional[str]:
        """Return the earlier message this one duplicates (token Jaccard), if any."""
        new = self._tokens(message)
        if not new:
            return None
        now = time.time()
        for s in reversed(self.sent):
            if now - s.get("ts", 0) > window_s:
                break
            old = self._tokens(s.get("message", ""))
            if not old or len(new & old) / len(new | old) < threshold:
                continue
            # Similar wording but NEW specifics (handles, names, URLs, numbers
            # the user just gave) is a refinement, not a repeat — e.g. the same
            # audit request now with "LuisArmando-TestCoder, ArmandoOriens".
            # Only identifier-like tokens (digits, or URL fragments) count as a
            # genuine refinement. Plain descriptive words ("conversación",
            # "incluyendo", "temas") are NOT a refinement — they are the same
            # request rephrased, and must still be caught as a duplicate.
            # Otherwise the observer re-sends near-identical messages over and
            # over because it can't see what the guard already let through.
            novel = {w for w in new - old if any(c.isdigit() for c in w) or "://" in w}
            if len(novel) >= 2:
                continue
            return s.get("message")
        return None

    def record_sent(self, message: str, decision: Dict[str, Any]):
        self.sent.append({"message": message, "ts": time.time(),
                          "intent": (decision or {}).get("intent")})
        self.sent = self.sent[-_MAX_SENT_HISTORY:]
        self._save()

    # ── Task tracking (in-progress + queued tasks survive a restart) ──

    def record_task(self, task_id: str, title: str, message: str, status: str = "queued"):
        """Record a task dispatched to the chat. Status: queued | running |
        interrupted | done. Kept so a server restart can restore what was in
        flight and summarize it."""
        self.tasks = [t for t in self.tasks if t.get("id") != task_id]
        self.tasks.append({
            "id": task_id, "title": title, "message": message,
            "status": status, "ts": time.time(),
        })
        self.tasks = self.tasks[-_MAX_SENT_HISTORY:]
        self._save()

    def update_task(self, task_id: str, status: str):
        for t in self.tasks:
            if t.get("id") == task_id:
                t["status"] = status
                t["ts"] = time.time()
                self._save()
                return

    def add_vector(self, vtype: str, label: str, ts: float, x: float, y: float,
                   color: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Append one conversation vector (a radar dot) for this chat. Pure,
        cheap persistence — the frontend batches these and never blocks on it."""
        import uuid
        vec = {
            "id": uuid.uuid4().hex[:12],
            "type": str(vtype)[:32],
            "ts": float(ts) or time.time(),
            "label": str(label)[:240],
            "x": float(x), "y": float(y),
            "color": str(color)[:32],
            "extra": extra or {},
        }
        self.vectors.append(vec)
        self.vectors = self.vectors[-_MAX_VECTORS:]
        self._save()
        return vec

    def tasks_summary(self) -> str:
        """A short summary of in-progress / queued tasks, for re-seeding the
        voice assistant after a restart so it can report what was running."""
        active = [t for t in self.tasks if t.get("status") in ("queued", "running")]
        if not active:
            return ""
        lines = []
        for t in active[-6:]:
            lines.append(f"- [{t.get('status')}] {t.get('title')}: {t.get('message', '')[:200]}")
        return "Tasks that were in progress when the session was interrupted:\n" + "\n".join(lines)

    # ── Compression agent ──

    async def _summarize(self, prompt: str, text: str) -> str:
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        url, model, headers = resolve_endpoint("utility", owner=self.owner or None)
        if not url or not model:
            raise RuntimeError("No utility model configured for voice compression")
        return await llm_call_async(
            url, model,
            [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
            temperature=0.2, max_tokens=900, headers=headers, timeout=45,
        )

    async def compact(self) -> bool:
        """Run compression until the live buffer is below one batch and every
        level is below the merge size. Safe to call repeatedly."""
        from src.context_compactor import FACTUAL_POINTS_PROMPT, PATCH_PROMPT, _merge_levels
        from src.cost_ledger import set_cost_scope
        set_cost_scope(None, "voice-compress")
        cfg = _voice_settings()
        changed = False
        async with self._lock:
            # Keep a small tail of raw turns live so the "lowest layer" is
            # never empty right after a compaction.
            while len(self.live) >= cfg["batch_size"] + 2:
                if self.pipeline_active:
                    break  # a decision is running — resume when idle
                chunk = self.live[:cfg["batch_size"]]
                text = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in chunk)
                try:
                    batch = await self._summarize(FACTUAL_POINTS_PROMPT, text)
                except Exception as e:
                    logger.warning(f"voice L1 compaction failed: {e}")
                    break
                if not (batch or "").strip():
                    break
                self.levels.setdefault(1, []).append(batch.strip())
                self.live = self.live[cfg["batch_size"]:]
                self.compactions += 1
                changed = True

            if not self.pipeline_active and any(len(v) >= cfg["merge_size"] for v in self.levels.values()):
                async def _merger(texts):
                    return await self._summarize(PATCH_PROMPT, "\n\n---\n\n".join(texts))
                before = sum(len(v) for v in self.levels.values())
                self.levels = await _merge_levels(self.levels, cfg["merge_size"], _merger)
                if sum(len(v) for v in self.levels.values()) != before:
                    self.compactions += 1
                    changed = True
            if changed:
                self.updated_at = time.time()
                self._save()
        return changed

    def needs_compaction(self) -> bool:
        cfg = _voice_settings()
        return (len(self.live) >= cfg["batch_size"] + 2
                or any(len(v) >= cfg["merge_size"] for v in self.levels.values()))

    def schedule_compaction(self, delay: float = 2.5):
        """Kick the background compressor: non-blocking, coalesced, and only
        while no pipeline decision is running (it waits for idle)."""
        if self._compact_task and not self._compact_task.done():
            return
        if not self.needs_compaction():
            return

        async def _idle_compact():
            await asyncio.sleep(delay)
            for _ in range(40):                 # wait up to ~60s for an idle window
                if not self.pipeline_active:
                    break
                await asyncio.sleep(1.5)
            if self.pipeline_active:
                return
            await self.compact()

        try:
            self._compact_task = asyncio.get_running_loop().create_task(_idle_compact())
        except RuntimeError:
            pass

    async def await_compaction(self, timeout: float = 6.0):
        t = self._compact_task
        if t and not t.done():
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=timeout)
            except Exception:
                pass

    # ── Spine ──

    def spine(self) -> Dict[str, Any]:
        """Highest layer + newest summary of every level + current raw batch."""
        levels_sorted = sorted(self.levels.keys(), reverse=True)
        layers = []
        for lvl in levels_sorted:
            items = self.levels[lvl]
            if not items:
                continue
            layers.append({"level": lvl, "count": len(items), "latest": items[-1]})
        highest = layers[0] if layers else None
        return {
            "highest": highest,
            "layers": layers,                       # newest of each level, high → low
            "current_batch": [{"speaker": t["speaker"], "text": t["text"]} for t in self.live],
            "recently_sent": [s["message"] for s in self.sent[-6:]],
        }

    def spine_text(self, include_live: bool = True) -> str:
        sp = self.spine()
        parts = []
        for layer in sp["layers"]:
            tag = "HIGHEST " if sp["highest"] and layer["level"] == sp["highest"]["level"] else ""
            parts.append(f"[{tag}Memory L{layer['level']} — newest of {layer['count']}]\n{layer['latest']}")
        if include_live and sp["current_batch"]:
            parts.append("[Current batch — raw, uncompressed]\n" + "\n".join(
                f"{t['speaker'].upper()}: {t['text']}" for t in sp["current_batch"]))
        return "\n\n".join(parts)

    def stats(self) -> Dict[str, Any]:
        cfg = _voice_settings()
        return {
            "voice_id": self.voice_id,
            "turns_total": self.turns_total,
            "live_turns": len(self.live),
            "batch_size": cfg["batch_size"],
            "merge_size": cfg["merge_size"],
            "levels": {str(k): len(v) for k, v in sorted(self.levels.items())},
            "depth": max(self.levels.keys()) if self.levels else 0,
            "compactions": self.compactions,
            "sent": len(self.sent),
            "compressing": bool(self._compact_task and not self._compact_task.done()),
            "layers": [
                {"level": l["level"], "count": l["count"], "preview": l["latest"][:220]}
                for l in self.spine()["layers"]
            ],
        }


_registry: Dict[str, VoiceMemory] = {}


def get_voice_memory(owner: str, voice_id: str) -> VoiceMemory:
    key = _safe_id(owner, voice_id)
    mem = _registry.get(key)
    if mem is None:
        mem = VoiceMemory(owner, voice_id)
        _registry[key] = mem
        if len(_registry) > 256:
            _registry.pop(next(iter(_registry)), None)
    return mem


def chat_summary_spine(session_id: Optional[str], owner: Optional[str] = None) -> str:
    """Newest summary at every level from the *chat* session's own compacted
    history, so the decision maker also knows what the chat already covered."""
    if not session_id:
        return ""
    try:
        from core import models as _core_models
        from src.context_compactor import _summary_level, _summary_body
        mgr = getattr(_core_models, "_session_manager", None)
        sess = mgr.get_session(session_id) if mgr and hasattr(mgr, "get_session") else None
        sess_owner = getattr(sess, "owner", None)
        if owner and sess_owner and sess_owner != owner:
            return ""  # never leak another user's chat into the pipeline
        hist = getattr(sess, "history", None) or []
    except Exception:
        return ""
    by_level: Dict[int, str] = {}
    recent: List[str] = []
    for m in hist:
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "") or ""
        if not isinstance(content, str):
            continue
        if role == "system":
            lvl = _summary_level(content)
            if lvl:
                by_level[lvl] = _summary_body(content)
        elif role in ("user", "assistant"):
            recent.append(f"{role.upper()}: {content[:400]}")
    parts = [f"[Chat summary L{l}]\n{b}" for l, b in sorted(by_level.items(), reverse=True)]
    if recent:
        parts.append("[Chat — last turns]\n" + "\n".join(recent[-6:]))
    return "\n\n".join(parts)


def _chat_history(session_id: Optional[str], owner: Optional[str]) -> Optional[list]:
    """The chat session's message history, or None when unknown / not the
    caller's. Owner-scoped: another user's chat is never returned."""
    if not session_id:
        return None
    try:
        from core import models as _core_models
        mgr = getattr(_core_models, "_session_manager", None)
        sess = mgr.get_session(session_id) if mgr and hasattr(mgr, "get_session") else None
        if sess is None:
            return None
        sess_owner = getattr(sess, "owner", None)
        if owner and sess_owner and sess_owner != owner:
            return None
        return list(getattr(sess, "history", None) or [])
    except Exception:
        return None


_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def _clean_turn(text: str) -> str:
    """Make a chat message cheap to carry in voice context: long code / tool
    blocks become a short marker, whitespace collapses."""
    def _fence(m):
        body = m.group(1)
        return m.group(0) if len(body) <= 300 else f"[code/tool block, {len(body)} chars]"
    text = _FENCE_RE.sub(_fence, text or "")
    text = re.sub(r"<[^>]{1,200}>", " ", text)          # stray html tags
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def chat_context_digest(session_id: Optional[str], owner: Optional[str] = None,
                        budget: int = 12000) -> str:
    """Everything the voice assistant needs to know about THIS chat, without
    having to ask: the chat's goal, its compacted summaries, an index of every
    request the user made in the chat (oldest → newest), and the most recent
    exchanges in as much detail as the budget allows (newest kept first).

    Owner-scoped and session-scoped like ``chat_summary_spine``; returns "" for
    an unknown/foreign/empty chat. Never raises.
    """
    try:
        hist = _chat_history(session_id, owner)
        if not hist:
            return ""
        try:
            from src.context_compactor import _summary_level, _summary_body
        except Exception:                                   # keep working if the compactor can't load
            _rx = re.compile(r"^\[(?:Conversation summary|Summary L(\d+))\]", re.IGNORECASE)

            def _summary_level(c):
                m = _rx.match(c or "")
                return 0 if not m else int(m.group(1) or 1)

            def _summary_body(c):
                m = _rx.match(c or "")
                return c[m.end():].strip() if m else (c or "").strip()
        summaries: Dict[int, str] = {}
        turns: List[tuple] = []
        for m in hist:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "") or ""
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "system":
                lvl = _summary_level(content)
                if lvl:
                    summaries[lvl] = _summary_body(content)
            elif role in ("user", "assistant"):
                turns.append((role, _clean_turn(content)))
        if not turns and not summaries:
            return ""

        sections: List[str] = []
        # 1. Goal driving this chat (if any).
        try:
            from src.goal import get_goal_for_session
            g = get_goal_for_session(session_id, owner)
            if g and (g.get("status") or "active") not in ("done", "cleared"):
                line = f"[Chat goal — {g.get('status', 'active')}] {_clip(str(g.get('objective') or g.get('goal') or ''), 400)}"
                if g.get("case_id"):
                    line += f" (drives case {g['case_id']})"
                sections.append(line)
        except Exception:
            pass
        # 2. Compacted summaries, highest level (broadest) first.
        if summaries:
            sm = "\n".join(f"L{l}: {b}" for l, b in sorted(summaries.items(), reverse=True))
            sections.append("[Chat summary — older part of the conversation]\n" + _clip(sm, max(1500, budget // 4)))
        # 3. Index of every user request, so no topic is invisible.
        asks = [t for r, t in turns if r == "user"]
        if asks:
            idx_budget = max(1200, budget // 4)
            rows = [f"{i + 1}. {_clip(a.replace(chr(10), ' '), 180)}" for i, a in enumerate(asks)]
            kept: List[str] = []
            used = 0
            for row in reversed(rows):                      # newest first
                if used + len(row) + 1 > idx_budget:
                    break
                kept.insert(0, row)
                used += len(row) + 1
            omitted = len(rows) - len(kept)
            if omitted > 0:
                head = rows[: min(3, omitted)]
                rest = omitted - len(head)
                kept = head + ([f"… {rest} more earlier requests …"] if rest else []) + kept
            sections.append(f"[Everything the user asked in this chat ({len(asks)} requests, oldest → newest)]\n" + "\n".join(kept))
        # 4. Recent exchanges, as much as fits, newest kept first.
        used = sum(len(s) + 2 for s in sections)
        remaining = max(1500, budget - used - 80)
        recent: List[str] = []
        for role, text in reversed(turns):
            cap = 1800 if role == "assistant" else 700
            who = "Chat agent" if role == "assistant" else "User"
            line = f"{who}: {_clip(text, cap)}"
            if len(line) + 1 > remaining:
                if not recent:                               # always keep the newest turn
                    recent.append(_clip(line, remaining))
                break
            recent.insert(0, line)
            remaining -= len(line) + 1
        if recent:
            sections.append(f"[Most recent exchanges — last {len(recent)} of {len(turns)} messages]\n" + "\n".join(recent))
        return _clip("\n\n".join(sections), budget)
    except Exception:
        return ""


def session_documents_spine(session_id: Optional[str], owner: Optional[str] = None,
                            max_docs: int = 6, max_chars: int = 4000) -> str:
    """Compact record of the documents belonging to a chat session, so the
    voice assistant can contextually reference the files the user is working
    on in THIS chat. Returns a text block (title + a head of content per doc),
    or "" when there are none / the session is unknown / ownership mismatches.

    Scoped to the session AND the owner: a document from another user's chat
    must never leak into the voice pipeline.
    """
    if not session_id:
        return ""
    try:
        from core.database import SessionLocal, Document
        db = SessionLocal()
        try:
            q = db.query(Document).filter(
                Document.session_id == session_id,
                Document.is_active == True,  # noqa: E712
                Document.archived == False,  # noqa: E712
            )
            if owner:
                q = q.filter((Document.owner == owner) | (Document.owner.is_(None)))
            docs = q.order_by(Document.updated_at.desc()).limit(max_docs).all()
        finally:
            db.close()
    except Exception:
        return ""
    if not docs:
        return ""
    parts = []
    for d in docs:
        title = (d.title or "Untitled").strip()
        lang = (d.language or "").strip()
        body = (d.current_content or "").strip()
        head = body[:600] + (" …" if len(body) > 600 else "")
        parts.append(f"[Document: {title}" + (f" ({lang})" if lang else "") + f"]\n{head}")
    return "\n\n".join(parts)
