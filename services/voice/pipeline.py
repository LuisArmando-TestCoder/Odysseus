# services/voice/pipeline.py
"""Multi-agent voice → chat pipeline (fast, duplicate-proof).

    live voice turns
          │
          ▼
    ① GATE      — instant local check: is there NEW, substantive user speech
                  since the last evaluation? (no model call)
          │
          ▼
    ② MEMORY    — snapshot of the hierarchical spine (compression keeps
                  running in the background; we never block on it)
          │
          ▼
    ③ DECIDER   — ONE JEV call (LLM fallback) with typed questions:
                  should_send · ready · already_sent · intent · urgency
          │
          ▼
    ④ FORGER    — one self-contained chat message from the NEW speech,
                  using memory only to resolve references.
          │
          ▼
    duplicate guard (token similarity vs. recently sent) → frontend queue

Duplicate-proofing is deterministic: every user turn carries a sequence
number; once the decider has evaluated speech that the user finished
(ready), it is *consumed* and can never trigger another forge. A per-memory
lock serialises concurrent runs.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from src.decisions import DecisionsUnavailable, jev_available, jev_decide
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async
from services.voice.memory import chat_summary_spine, get_voice_memory

logger = logging.getLogger(__name__)

INTENTS = {
    "task": "Do something: create, edit, send, schedule, run, change a file/email/calendar/code",
    "question": "Answer a factual or analytical question that needs the chat agent",
    "research": "Look something up on the web or do deep research",
    "remember": "Store a fact, preference, or note for later",
    "followup": "Refine, correct, or continue something already sent to the chat",
    "none": ("Small talk, thinking out loud, acknowledgement, commentary about this voice system / the "
             "app / the developer or coding agent, notes-to-self, or reading a text aloud — nothing to send"),
}

# What the decider must NOT treat as a request (shared by JEV and the LLM fallback).
NOT_A_REQUEST = (
    "NOT requests (should_send false, intent none): (1) META-COMMENTARY — the user talking ABOUT this voice "
    "system, the app, its bugs/frame rate/latency, how the assistant behaves, or giving feedback/notes meant "
    "for the developer or 'the coding agent' ('I need the coding agent to take note', 'I'd love that ability', "
    "'I like the state of the system'); (2) READING ALOUD — the user reciting or quoting a passage, headline "
    "or article verbatim (long, literal, no ask of their own) — that is context, not a request, unless they "
    "then ask something about it; (3) thinking out loud / musing without an ask; (4) telling the assistant "
    "what they did NOT ask for. A real request is something the CHAT AGENT can do: answer, look up, research, "
    "create, edit, send, schedule, remember."
)

# Indirect asks: the words alone look like filler, but combined with the
# conversation (pasted context, the last batch, the memory) they are requests.
IMPLICIT_ASK = (
    "INDIRECT / IMPLICIT ASKS count — infer them from recent_context, pasted_context and the memory, not only "
    "from the literal words: (a) the user pasted/typed something (USER_NOTE / pasted_context) in the last few "
    "turns and now says something short that refers to it ('so what do you think', 'what do you make of it', "
    "'go ahead', 'use it', 'search it', 'look at this', 'get back to me') → a request about the pasted content "
    "(intent research/question); (b) the user announced they are about to share something and then shared it → "
    "they want it read/analysed; (c) a wish or complaint whose obvious remedy is something the chat agent can do. "
    "In those cases should_send is high and ready is high."
)

SPEAKER_LEGEND = (
    "USER = the human speaking in the voice call; ASSISTANT = the voice model (GPT-Live); "
    "CHAT_USER = a message already sent into the chat thread; CHAT_AI = the chat agent's answer in the thread; "
    "USER_NOTE = text the user typed/pasted as reference context (not a request by itself)."
)

DECIDER_FALLBACK_PROMPT = """You are the decision maker of a voice→chat pipeline. You receive a hierarchical
memory of a live voice conversation (highest summary layer, newest summary of each level, current raw batch),
the chat thread's own summary, and messages already sent to the chat.
Judge ONLY new_user_speech; the rest is context.
Answer ONLY with JSON:
{"should_send": 0..1, "ready": 0..1, "already_sent": 0..1,
 "intent": "task|question|research|remember|followup|none", "urgency": 0|1|2,
 "commitment": 0..1}
- should_send: probability the user wants something done, looked up, answered or remembered — implicit and
  indirect requests count ("I want to see…", "bring me…", "what time is it")
- ready: probability the user has finished expressing it (not mid-sentence / still thinking)
- already_sent: probability the same request is literally in already_sent_to_chat or a CHAT_USER turn
  (ASSISTANT acknowledgements like "I'll check" do NOT count)
- commitment: probability the ASSISTANT's recent speech contains a concrete promise to do real work
  (look up, check, search, research, find, verify, open, run, create, send, ask the chat) that should be
  forwarded to the chat agent. Vague filler ("I'm on it", "let me think") or mere acknowledgement is 0.
""" + NOT_A_REQUEST + "\n" + IMPLICIT_ASK

# Forger output contract: a BARE message, not JSON and not a labelled envelope.
# A long chat message full of names, quotes, URLs and newlines routinely broke
# JSON escaping (and a TITLE:/MESSAGE: envelope still invited the model to add
# stray prefixes that the parser had to strip). The message body is now the
# ENTIRE output, verbatim — nothing inside it can break parsing, and there is
# no prefix to malform.
FORGE_DECLINE = "NO_MESSAGE"
FORGE_FORMAT = (
    "OUTPUT FORMAT (plain text, NOT JSON, no code fences, no labels, no prefixes):\n"
    "Respond with ONLY the chat message itself — nothing else. Do NOT add a "
    "\"TITLE:\" or \"MESSAGE:\" prefix, no markdown, no explanation, no preamble.\n"
    f"To send nothing, output exactly one line: {FORGE_DECLINE}"
)

FORGER_META_RULE = (
    "If NEW USER SPEECH is only meta-commentary about this voice system or the app, feedback/notes for the "
    "developer or coding agent, musing, or the user reading a text aloud with no ask of their own, do NOT "
    f"invent a task — output exactly {FORGE_DECLINE}."
)

FORGER_PROMPT = """You are the message forger of a voice→chat pipeline. A separate chat agent will execute
your message but CANNOT hear the voice conversation. Write exactly ONE message, in the user's own first-person
voice, that the chat agent can act on directly.
The request is ONLY what is in NEW USER SPEECH. Use the context and memory solely to resolve references
("it", "that file", "the meeting") and to fill in names, values, dates and constraints the user mentioned.
Speakers: USER = human; ASSISTANT = voice model; CHAT_USER = already sent; CHAT_AI = the chat agent's answer;
USER_NOTE = text the user typed/pasted. When the user's request refers to something they pasted ("search it",
"read this", "the article") include the pasted URL/text from USER_NOTE verbatim in the message — the chat
agent cannot see it otherwise.
BROWSER ACTIONS: when the user wants something opened, played, redirected or run in their browser (a link, a
YouTube video, a page), phrase it as "Find <thing> and open it in my browser (new tab) using run_javascript
with window.open(url, '_blank')" — never ask for a script file or shell command for that; if the URL is
already known from context, include it.
SELF-REFERENCE: when the user asks about THEMSELVES ("audit me", "my own presence", "my name is X"), keep it
first-person — "my own public presence", "my name is X" — never "his"/"her"/"the target". A self-audit is
legitimate; a third-person phrasing makes it look like profiling a stranger and gets refused.
IDENTIFIERS: carry EVERY username, handle, name, email, URL or number the user lists, verbatim, into the
message — the chat agent cannot guess them and will otherwise stop and ask for exactly what you dropped.
Never repeat what CHAT_USER already asked or CHAT_AI already did — if it's a follow-up, phrase it as a change.
Concise, no preamble, no mention of "voice" or "transcript".
""" + FORGE_FORMAT + "\n" + FORGER_META_RULE

# The voice assistant promised the user to do something ("I'll run a quick check
# on Stroessner"). Turn that promise into a real chat request so the promise is
# kept — this is what makes the assistant proactive instead of all talk.
COMMITMENT_PROMPT = """You turn a promise made by the VOICE ASSISTANT into a request for a separate chat
agent that will actually do the work (the chat agent cannot hear the call). Write ONE concise, self-contained
message in the user's first-person voice ("Look up…", "Check…"), resolving names and details from context.
If the promise is vague filler with no concrete subject ("I'll get on it", "let me think"), or it only repeats
something already sent to the chat, output exactly """ + FORGE_DECLINE + ".\n" + FORGE_FORMAT

# Lateral extractor: when the answer to the user's question is ALREADY in the
# chat thread (or voice memory), pull it out and hand it straight to the voice
# assistant — no round-trip to the chat agent. This is the "inference + extract"
# pair the user described: JEV decides it's answerable, this LLM extracts it.
EXTRACTOR_PROMPT = """You extract an answer that is ALREADY present in the provided context, so a voice
assistant can say it aloud without asking the chat agent again.
Given the user's question and the context (recent chat turns + voice memory), find the passage that answers
it. Answer ONLY with JSON:
{"answer": "<the answer, in the user's language, concise and spoken naturally>", "found": true|false}
- found: true only if the context genuinely contains the answer. If it does not, found=false and answer="".
- Do not invent, summarize beyond what is there, or add facts not in the context.
- Keep the answer short enough to speak aloud (a few sentences)."""


async def _extract_answer(owner: str, question: str, context: str) -> Optional[str]:
    """Run the lateral extractor. Returns the answer text, or None if not found."""
    try:
        raw = await _utility(owner, EXTRACTOR_PROMPT,
                             f"Question:\n{question}\n\nContext:\n{context[:12000]}", 400)
    except Exception as e:
        logger.info(f"voice extractor failed: {e}")
        return None
    data = _parse_json(raw)
    if not data.get("found"):
        return None
    answer = (data.get("answer") or "").strip()
    return answer or None


def _ev(**kw) -> Dict[str, Any]:
    kw.setdefault("ts", time.time())
    return kw


def _parse_json(raw: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _looks_like_json(text: str) -> bool:
    """True for a bare/fenced JSON object, e.g. '{"title": "", "message": ""}'."""
    return bool(re.fullmatch(r'\s*(?:```\w*)?\s*\{.*\}\s*(?:```)?\s*', text or "", re.DOTALL))


def _title_from_message(text: str) -> str:
    """Derive a short title from a bare forged message (first few words)."""
    words = re.sub(r"\s+", " ", (text or "")).strip().split()
    if not words:
        return ""
    return " ".join(words[:6])[:60]


def _parse_forged(raw: str) -> Dict[str, Any]:
    """Parse forger output into {"title", "message", "declined", "malformed"}.

    Primary contract is a BARE message: the entire output IS the message,
    verbatim. Never raises, and never silently turns real content into a
    decline:
      - bare text (the expected format) → the whole text IS the message;
      - legacy TITLE:/MESSAGE: envelope → message is everything after
        "MESSAGE:", verbatim (kept for older cached outputs);
      - legacy JSON that parses → its title/message (empty message = decline);
      - only an explicit NO_MESSAGE (or empty reply) declines.
    """
    from src.text_helpers import strip_think
    text = (raw or "").strip()
    try:
        text = strip_think(text, prose=False, prompt_echo=False).strip()
    except Exception:
        text = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", text, flags=re.I).strip()
    # Drop a wrapping code fence if the model added one anyway.
    fence = re.fullmatch(r"```\w*\s*\n?([\s\S]*?)\n?```", text)
    if fence:
        text = fence.group(1).strip()

    out = {"title": "", "message": "", "declined": False, "malformed": False}
    if not text or re.fullmatch(rf"[`*\s]*{FORGE_DECLINE}[`*.\s]*", text, re.I):
        out["declined"] = True
        return out

    # ① Legacy plain-text envelope (older prompt / cached output). Strip the
    #    "MESSAGE:" prefix if present, but the bare message is the norm.
    m = re.search(r"(?im)^\s*\**MESSAGE\**\s*:\s*", text)
    if m:
        body = text[m.end():].strip()
        t = re.search(r"(?im)^\s*\**TITLE\**\s*:\s*(.+)$", text[:m.start()])
        out["title"] = (t.group(1).strip().strip("*`\"'") if t else "")[:60]
        if not body or re.fullmatch(rf"{FORGE_DECLINE}[.\s]*", body, re.I):
            out["declined"] = True
        else:
            out["message"] = body
        return out

    # ② Legacy JSON (older prompt / model ignored the format).
    if "{" in text:
        data = _parse_json(text)
        if data:
            out["title"] = str(data.get("title") or "").strip()[:60]
            out["message"] = str(data.get("message") or "").strip()
            out["declined"] = not out["message"]
            return out
        # Broken JSON: salvage the "message" value instead of dropping it.
        mm = re.search(r'"message"\s*:\s*"([\s\S]*?)"\s*(?:[,}]|$)', text)
        if mm and mm.group(1).strip():
            tt = re.search(r'"title"\s*:\s*"([^"]*)"', text)
            out["title"] = (tt.group(1).strip() if tt else "")[:60]
            out["message"] = mm.group(1).replace('\\"', '"').replace("\\n", "\n").strip()
            out["malformed"] = True
            return out

    # ③ Bare message (the expected format): the whole text IS the message.
    out["message"] = text
    out["title"] = _title_from_message(text)
    return out


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _threshold() -> float:
    try:
        from src.settings import load_settings
        return max(0.05, min(0.99, float(load_settings().get("voice_act_threshold", 0.5))))
    except Exception:
        return 0.5


async def _utility(owner: Optional[str], system: str, user: str, max_tokens: int = 400) -> str:
    url, model, headers = resolve_endpoint("utility", owner=owner or None)
    if not url or not model:
        raise RuntimeError("No utility/default model configured")
    return await llm_call_async(
        url, model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.1, max_tokens=max_tokens, headers=headers, timeout=60,
        # The forger/decider/extractor are small, deterministic transforms that
        # only need reference resolution — not deep chain-of-thought. "low" keeps
        # enough reasoning to resolve "it"/"that file" while skipping the long
        # think that (a) burns the token budget and (b) blows past the timeout.
        # Only sent for o-series/OpenRouter models; ignored elsewhere.
        reasoning_effort="low",
    )


def _fmt(turns: List[Dict[str, Any]]) -> str:
    return "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in turns)


# Explicit "do this in the chat" directives. When the user names the chat as the
# target ("as the chat", "ask the chat", "send to the chat", "the chat should…",
# "chat agent"), they want the CHAT AGENT to do the work — the voice assistant
# must not answer from its own chatter. These bypass the lateral extractor.
_CHAT_DIRECTIVE = re.compile(
    r"\b(?:as|ask|send|tell|have|let|for|to|in|via|through)\s+the\s+chat\b"
    r"|\bchat\s+agent\b"
    r"|\bthe\s+chat\s+(?:should|needs|must|can|will|has|is)\b",
    re.IGNORECASE,
)


def _directed_to_chat(text: str) -> bool:
    return bool(_CHAT_DIRECTIVE.search(text or ""))


def _lock_for(mem) -> asyncio.Lock:
    lock = getattr(mem, "_pipe_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        mem._pipe_lock = lock
    return lock


async def _decide(owner: str, state: Dict[str, Any], use_jev: bool) -> Dict[str, Any]:
    """One decision call. JEV when available; LLM fallback otherwise."""
    if use_jev:
        try:
            ans = await jev_decide(state, {
                "should_send": {
                    "type": "noul",
                    "instructions": (
                        "Does new_user_speech contain anything the user wants the CHAT AGENT to do, look up, answer or "
                        "remember — including IMPLICIT or INDIRECT requests ('I want to see…', 'bring me…', 'what time "
                        "is it', 'it'd be nice to know…', 'have you seen…', wishes, half-finished instructions)? "
                        "Filler, acknowledgements, small talk, and questions about the call itself ('can you hear me', "
                        "'are you there') are false. " + NOT_A_REQUEST + " " + IMPLICIT_ASK
                    ),
                    "criteria": {"true": "An explicit, implicit or indirect request the chat agent can act on",
                                 "false": "Filler / small talk / meta-commentary about the system / notes for the "
                                          "developer / reading a text aloud"},
                },
                "ready": {
                    "type": "noul",
                    "instructions": (
                        "Is the request in new_user_speech clear enough to act on? Spoken requests are full of fillers "
                        "('um', 'uh', 'let's say') and trailing words — that is still ready if the intent is clear."
                    ),
                    "criteria": {"true": "Intent is clear enough to act on", "false": "Genuinely cut off before the request was stated"},
                },
                "already_sent": {
                    "type": "noul",
                    "instructions": (
                        "Is essentially the same request already listed in already_sent_to_chat or as a CHAT_USER turn? "
                        "ASSISTANT acknowledgements ('I'll check', 'on it', 'still waiting') do NOT mean it was sent. "
                        "If already_sent_to_chat is empty and there are no CHAT_USER turns, this is false."
                    ),
                    "criteria": {"true": "The same request is literally in already_sent_to_chat / CHAT_USER",
                                 "false": "Not in that list (acknowledgements don't count)"},
                },
                "intent": {
                    "type": "choice",
                    "instructions": "What kind of action does the user want from the chat agent?",
                    "criteria": INTENTS,
                },
                "answerable": {
                    "type": "noul",
                    "instructions": (
                        "Is the answer to the user's question ALREADY present in the chat thread summary "
                        "(the chat agent's own answers)? If the chat agent already answered this or a closely "
                        "related question, the answer is answerable from context and should NOT be sent to the "
                        "chat again. The voice assistant's own chatter does NOT count — only the chat agent's "
                        "answers. If the answer is not in the chat thread, this is false."
                    ),
                    "criteria": {"true": "The answer is already in the chat thread (the chat agent's answers)",
                                 "false": "The answer is not in the chat thread — the chat agent must look it up"},
                },
                "commitment": {
                    "type": "noul",
                    "instructions": (
                        "Does the ASSISTANT's recent speech contain a concrete promise to do real work for the "
                        "user — look something up, check, search, research, find, verify, open, run, create, "
                        "send, ask the chat, etc. ('I'll check that', 'let me look into it', 'I'll ask the chat', "
                        "'I'll find out')? A concrete promise to act is true. Vague filler ('I'm on it', 'let me "
                        "think', 'one moment'), mere acknowledgement, or the assistant already answering the "
                        "question itself is false."
                    ),
                    "criteria": {"true": "The assistant promised concrete work that should be forwarded to the chat agent",
                                 "false": "No concrete promise / vague filler / mere acknowledgement"},
                },
                "urgency": {
                    "type": "score",
                    "instructions": "How urgent is it for the user?",
                    "criteria": ["Whenever", "Soon", "Right now"],
                },
            })
            return {
                "engine": "jev",
                "should_send": _f(ans["should_send"]["value"]),
                "ready": _f(ans["ready"]["value"], 1.0),
                "already_sent": _f(ans["already_sent"]["value"]),
                "intent": ans["intent"]["value"] or "none",
                "intent_confidence": ans["intent"].get("confidence"),
                "answerable": _f(ans["answerable"]["value"]),
                "commitment": _f(ans["commitment"]["value"]),
                "urgency": ans["urgency"]["value"],
            }
        except DecisionsUnavailable as e:
            logger.info(f"voice decider: JEV unavailable → LLM fallback ({e})")

    data = _parse_json(await _utility(owner, DECIDER_FALLBACK_PROMPT,
                                      json.dumps(state, ensure_ascii=False)[:9000], 100))
    return {
        "engine": "llm",
        "should_send": _f(data.get("should_send")),
        "ready": _f(data.get("ready", 1), 1.0),
        "already_sent": _f(data.get("already_sent")),
        "intent": data.get("intent") or "none",
        "answerable": _f(data.get("answerable")),
        "commitment": _f(data.get("commitment")),
        "urgency": data.get("urgency", 0),
    }


async def run_pipeline(
    owner: str,
    voice_id: str,
    turns: List[Dict[str, Any]],
    chat_session_id: Optional[str] = None,
    force: bool = False,
    settle: bool = False,
    commitment: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    from src.cost_ledger import set_cost_scope
    set_cost_scope(chat_session_id or None, "voice-pipeline", owner)
    mem = get_voice_memory(owner, voice_id)
    mem.add_turns(turns)

    # Trusted-claim extraction from the user's spoken turns. The user is the
    # most trusted source of identity reconnaissance, so a first-person claim
    # spoken in voice ("my name is X") is captured into the trusted-claims
    # store the same way a typed claim is. Deterministic, no LLM call.
    try:
        from services.identity.claim_extractor import extract_claims, apply_claim_operations
        from src.trusted_claims import get_trusted_claims_manager
        _tcm = get_trusted_claims_manager()
        _spoken = " ".join(
            t.get("text", "") for t in turns
            if str(t.get("speaker", "")).lower() in ("user", "you")
        ).strip()
        if _spoken:
            _ops = extract_claims(_spoken)
            if _ops:
                apply_claim_operations(
                    _ops, _tcm,
                    source="voice", session_id=chat_session_id, owner=owner,
                )
    except Exception as _tce:
        logger.debug(f"voice trusted-claim extraction skipped: {_tce}")

    use_jev = jev_available()
    yield _ev(type="hello", engine="jev" if use_jev else "llm", memory=mem.stats())

    # The decision path owns the utility model while it runs; compression is a
    # background nice-to-have that starts only once this run is finished.
    mem.pipeline_active += 1
    try:
        gen = _run_locked(mem, owner, chat_session_id, force, use_jev, settle, commitment)
        async for ev in gen:
            yield ev
    finally:
        mem.pipeline_active = max(0, mem.pipeline_active - 1)
        mem.schedule_compaction()


async def _forge_and_emit(
    mem, owner, chat_session_id, prompt, forger_input, decision, fallback_text, source=None
) -> AsyncIterator[Dict[str, Any]]:
    """Run the forger LLM and emit the forged message.

    The forge is a TRANSFORM, not a decision: the decider already said "send",
    so the forge's only job is to phrase the message. If it declines (empty /
    NO_MESSAGE) or errors, fall back to ``fallback_text`` so the request still
    reaches the chat instead of silently dying here.
    """
    t0 = time.time()
    yield _ev(type="stage", stage="forge", status="running")
    try:
        from src.cost_ledger import cost_scope
        with cost_scope(source="voice-forge"):
            raw = await _utility(owner, prompt, forger_input[:14000], 500)
        forged = _parse_forged(raw)
        message, title = forged["message"], forged["title"]
        if forged["malformed"]:
            logger.warning("voice forger ignored the output format; using text as-is: %r", (raw or "")[:200])
    except Exception as e:
        logger.warning(f"voice forger failed: {e}")
        message, title = "", ""
    if not message:
        message = fallback_text
        title = _title_from_message(message)
        logger.warning("voice forger declined/errored after decider said send; using raw speech as the message")
    dup = mem.is_duplicate(message)
    if dup:
        yield _ev(type="stage", stage="forge", status="idle", ms=int((time.time() - t0) * 1000))
        yield _ev(type="done", send=False, reason="duplicate", duplicate_of=dup[:160])
        return
    mem.record_sent(message, decision)
    yield _ev(type="stage", stage="forge", status="done", ms=int((time.time() - t0) * 1000))
    ev = _ev(type="forged", title=title or (decision.get("intent") or "Request").title(),
             message=message, intent=decision.get("intent"), urgency=decision.get("urgency"))
    if source:
        ev["source"] = source
    yield ev
    yield _ev(type="memory", memory=mem.stats())
    yield _ev(type="done", send=True)


async def _run_locked(mem, owner, chat_session_id, force, use_jev, settle=False, commitment=None) -> AsyncIterator[Dict[str, Any]]:
    async with _lock_for(mem):
        # ① GATE ──────────────────────────────────────────────────────────────
        t0 = time.time()
        yield _ev(type="stage", stage="observe", status="running")
        new_user = mem.new_user_turns()
        new_text = " ".join(t["text"] for t in new_user).strip()
        words = len(new_text.split())
        if force and not new_user:
            # "Send now" with nothing new: forge from the latest user speech.
            new_user = [t for t in mem.recent_turns(8) if t["speaker"] == "user"][-2:]
            new_text = " ".join(t["text"] for t in new_user).strip()
            words = len(new_text.split())
        # Assistant speech not yet checked for a promise (proactivity).
        new_assistant = mem.new_assistant_turns()
        assistant_text = " ".join(t["text"] for t in new_assistant).strip()
        gate = force or words >= 3 or bool(assistant_text)
        yield _ev(type="stage", stage="observe", status="done" if gate else "idle",
                  score=1.0 if gate else 0.0, words=words, ms=int((time.time() - t0) * 1000))
        if not gate:
            yield _ev(type="done", send=False, reason="no-new-speech")
            return

        # ② MEMORY SNAPSHOT (no waiting on compression) ─────────────────────
        t0 = time.time()
        yield _ev(type="stage", stage="compress", status="running")
        spine = mem.spine()
        chat_txt = chat_summary_spine(chat_session_id, owner)
        yield _ev(type="stage", stage="compress", status="done", ms=int((time.time() - t0) * 1000),
                  depth=len(spine["layers"]), live=len(spine["current_batch"]))

        # ③ DECIDER ───────────────────────────────────────────────────────────
        t0 = time.time()
        yield _ev(type="stage", stage="decide", status="running", engine="jev" if use_jev else "llm")
        if force:
            decision = {"engine": "manual", "should_send": 1.0, "ready": 1.0, "already_sent": 0.0,
                        "intent": "task", "urgency": 1, "commitment": 0.0}
        else:
            notes = [t for t in mem.recent_turns(16) if t.get("speaker") == "user_note"][-3:]
            state = {
                "speaker_legend": SPEAKER_LEGEND,
                "new_user_speech": new_text,
                "assistant_speech": assistant_text,
                "pasted_context": [str(t.get("text", ""))[:800] for t in notes],
                "recent_context": mem.recent_turns(8),
                "already_sent_to_chat": spine["recently_sent"],
                "memory_overview": (spine["highest"]["latest"][:1500] if spine["highest"] else ""),
                "chat_thread_summary": chat_txt[:2000],
            }
            try:
                from src.cost_ledger import cost_scope
                with cost_scope(source="voice-decide"):
                    decision = await _decide(owner, state, use_jev)
            except Exception as e:
                logger.warning(f"voice decider failed: {e}")
                yield _ev(type="stage", stage="decide", status="error", error=str(e)[:200])
                yield _ev(type="done", send=False, reason="decider-error")
                return

        thr = _threshold()
        # Spoken requests ramble; "ready" only holds back speech that was
        # genuinely cut off, and a settle re-run (after the user went quiet)
        # always counts as ready.
        ready = settle or decision.get("ready", 1.0) >= 0.35
        # JEV's duplicate score is only trusted when something was actually
        # sent — the deterministic guards (consumption + similarity) do the rest.
        has_sent = bool(spine["recently_sent"]) or any(
            t["speaker"] == "chat_user" for t in mem.recent_turns(8))
        dup = has_sent and decision.get("already_sent", 0) >= 0.8
        send = (
            bool(new_text)
            and decision.get("should_send", 0) >= thr
            and ready
            and not dup
            and decision.get("intent") != "none"
        )
        decision["threshold"] = thr
        decision["send"] = send
        # Finished speech is consumed whether or not it was actionable, so the
        # same words are never re-evaluated (or re-forged) on the next turn.
        if ready or force:
            mem.consume()
        # Assistant speech is consumed for commitment checks regardless.
        mem.consume_commitments()
        ms = int((time.time() - t0) * 1000)
        yield _ev(type="decision", decision=decision, ms=ms)
        yield _ev(type="stage", stage="decide", status="done" if (send or decision.get("commitment", 0) >= 0.7) else "idle", ms=ms, engine=decision.get("engine"))

        # ③b LATERAL EXTRACTOR — the answer is already in the chat/memory, so
        # hand it straight to the voice assistant instead of re-asking the chat.
        # SKIP when the user explicitly directed the request to the chat ("as
        # the chat", "ask the chat") — they want the chat agent to do the work,
        # not the voice to answer from its own chatter.
        if send and decision.get("answerable", 0) >= 0.7 and not _directed_to_chat(new_text):
            context = (chat_txt or "") + "\n\n" + (mem.spine_text(include_live=False) or "")
            answer = await _extract_answer(owner, new_text, context)
            if answer:
                yield _ev(type="answer_from_context", answer=answer, question=new_text[:300])
                yield _ev(type="done", send=False, reason="answered-from-context")
                return

        # ④ FORGER (user request) — the forge is a TRANSFORM, not a decision:
        # the decider already said "send", so it can only phrase the message.
        if send:
            forger_input = (
                f"Intent: {decision.get('intent')} · urgency: {decision.get('urgency')}\n\n"
                f"=== NEW USER SPEECH (the request) ===\n{new_text}\n\n"
                f"=== Recent context ===\n{_fmt(mem.recent_turns(8))}\n\n"
                f"=== Memory ===\n{mem.spine_text(include_live=False)[:4000] or '(none)'}\n\n"
                f"=== Chat thread so far ===\n{chat_txt[:3000] or '(empty)'}\n\n"
                f"=== Already sent to chat ===\n" + ("\n".join(f"- {s}" for s in spine["recently_sent"]) or "(nothing)")
            )
            async for ev in _forge_and_emit(mem, owner, chat_session_id, FORGER_PROMPT,
                                            forger_input, decision, new_text):
                yield ev
            return

        # ⑤ COMMITMENT — the decider saw the ASSISTANT promise concrete work.
        # Forge that promise into a chat request (the assistant is proactive).
        if commitment or decision.get("commitment", 0) >= 0.7:
            promise = commitment or assistant_text
            if promise:
                cinput = (
                    f"=== ASSISTANT PROMISE (turn into the request) ===\n{promise}\n\n"
                    f"=== Recent context ===\n{_fmt(mem.recent_turns(10))}\n\n"
                    f"=== Chat thread so far ===\n{chat_txt[:3000] or '(empty)'}\n\n"
                    f"=== Already sent to chat ===\n" + ("\n".join(f"- {s}" for s in spine["recently_sent"]) or "(nothing)")
                )
                cdecision = {"engine": "commitment", "should_send": 1.0, "ready": 1.0, "already_sent": 0.0,
                             "intent": "research", "urgency": 1, "send": True}
                async for ev in _forge_and_emit(mem, owner, chat_session_id, COMMITMENT_PROMPT,
                                                cinput, cdecision, promise, source="commitment"):
                    yield ev
                return

        # Nothing to send.
        if not ready:
            reason = "not-ready"
        elif dup:
            reason = "duplicate"
        else:
            reason = "below-threshold"
        yield _ev(type="done", send=False, reason=reason)
