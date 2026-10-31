# Voice ↔ chat context bridge (V1 — contract)

The advanced voice mode must remain exactly as it exists. This document is the
frozen contract of the two-way context bridge between voice and chat; any PR
touching the functions below must update this doc.

## Direction 1 — chat ← voice (voice memory injected into chat context)

- **Site:** `routes/chat_helpers.py` → `voice_memory_stratum(user, session_id)`
  (called from `build_chat_context`).
- **Key:** voice memory is keyed `chat-{session_id}` — the chat sees ONLY the
  memory of its matching voice conversation.
- **Content:** compressed spine (`spine_text(include_live=True)`) + full live
  transcript (`transcript_text(max_turns=200)`, newest last), joined.
- **Trust:** injected as untrusted, read-only DATA — never instructions.
- **Failure:** any memory-layer failure degrades to "" (chat never breaks).

## Direction 2 — voice ← chat (chat context injected into WebRTC instructions)

- **Site:** `routes/realtime_routes.py` `POST /api/realtime/session`
  (`:135-190`).
- **Composition:** persona + user instructions + `VOICE_CONTRACT` + per-chat
  trusted claims + `chat_context_digest(sid, owner, budget)`
  (`=== CURRENT CHAT THREAD ===`) + `session_documents_spine(sid, owner)[:4000]`
  (`=== DOCUMENTS IN THIS CHAT ===`).
- **Full chat context (V2, 2026-10-29):** `chat_context_digest` replaces the
  old 6-turn / 4000-char spine so the voice knows the WHOLE chat without asking:
  the chat's active goal (and the case it drives), compacted summaries, an
  index of every user request (oldest → newest; the middle is elided only when
  over budget, first and latest always kept), and the latest exchanges in
  detail (newest kept first; long code/tool blocks compacted). Budget:
  `VOICE_CHAT_CONTEXT_CHARS` = 12000, overridable with the
  `voice_chat_context_chars` setting (clamped 2000–40000). Owner- and
  session-scoped; never raises (degrades to "").
- **Live updates during a session:** chat-agent output streams in as silent
  context (`_streamContext`), finished answers are spoken or briefed
  (`_ingestChat`), and messages the user TYPES in the chat are briefed as
  silent context the moment they settle (voice-sent requests are skipped).
  A chat switch restarts the live session, which rebuilds the instructions for
  the new chat.
- **Rule:** the voice session receives everything accessible in the session
  that MATCHES the chat (`chat_session_id`), never another chat's context.

## Auxiliary flows (also frozen)

- Neural pipeline (push-to-talk) writes its own memory via
  `services/voice/memory.py`.
- Voice events/status/memory endpoints: `routes/realtime_routes.py:280-501`.

## Turn-type matrix (V2)

| Turn type | Voice stratum injected? |
|---|---|
| chat turn (root) | ✅ (today's behavior, frozen) |
| goal round (`src/goal_driver.py`) | ✅ — runs in `goal.session_id` → same `chat-{sid}` memory |
| ask-user answer turn | ✅ — same session |
| queued turn (turn inbox) | ✅ — same session, same `build_chat_context` path |
| subagent child (`src/subagents.py`) | ❌ — new session → its own (empty) memory; never inherits the parent's transcript |
| verifier / bg_monitor turns | ❌ — bypass chat context build |
| incognito / `no_memory` | ❌ — guarded in `build_chat_context` |

Out of scope of the DSH port (V4): `/api/realtime/*`, `services/voice/*`,
`services/stt`, `services/tts` — verified untouched (empty git diff).

## V3 — minimal flow (2026-10-31)

From the 2026-10-30 peritaje session (Prado A340F). Tests:
`tests/test_voice_minimal_flow.py`.

- **One hop.** A request the decider wants sent is sent. No "Send this to the
  chat? Say yes" round-trip (`needs_clarification` is retired; directed 0.3–0.7
  sends) and no "guard-paused" state. The voice contract forbids asking
  permission to consult the chat; a permission question that slips through is
  flagged (`repeated_question`, `permission: true`) and GPT-Live is corrected.
- **Faithful relay.** The forger is a minimal transform. A forge that adds a
  deliverable the user never asked for (document, report, table, file, draft…)
  or that the guard finds unfaithful/incoherent is replaced by the user's OWN
  words (`verbatim` event). A doubted or deliverable-inventing assistant
  promise (commitment) is dropped. Only true duplicates and speech clearly not
  meant for the chat (directed < 0.3) stop.
- **New speech only.** A user turn that grew by merging a later fragment only
  contributes its never-evaluated part (`consumed` offset per live turn).
- **About the conversation.** `about_conversation` (decider) → answered from the
  WHOLE chat digest (`chat_context_digest`), never forwarded.
- **Self-assessment notes** (`src/self_assessment.py`). When the user corrects
  or teaches (`teaches`), a small model turns it into one note; it is kept only
  if its evidence is a real quote of the user. Notes are append-only, per chat
  (+ owner-wide), injected into the voice instructions and the chat context
  under a rule: notes are independent; sharing the block, a topic or a time is
  never a causal link; `[context-only]` notes may never be presented as causes.
  API: `GET/POST /api/realtime/lessons`, `DELETE /api/realtime/lessons/{id}`.
- **Questions ledger.** Every question ALFRED asks is logged in the voice
  memory; a repeat is flagged with the user's words since the first ask, and
  the session instructions list the questions already asked.
- **Delta-only streaming** (`static/js/voice-delta.js`). Narration, live
  context, finished-answer ingest and typed-message briefs all go through one
  sentence ledger (`speak` / `know`), so each batch adds only sentences GPT-Live
  never got — re-rendered bubbles, new rounds that restate the last one and
  repeated paths no longer restart from the beginning. The reconnect seed no
  longer replays chat turns (the server instructions already hold the thread).
- **Chat side.** The voice stratum tells the chat that voice relays are
  condensed and nothing was cut (it used to answer "su mensaje se recortó").
- **Middleman removed.** `src/llm_middleman.py` (a reducer LLM that rewrote
  answers into "obedient" ones) is gone; `llm_call*` and `stream_llm` return
  the model's answer unmodified.
