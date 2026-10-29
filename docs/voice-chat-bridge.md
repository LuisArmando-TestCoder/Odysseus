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

- Middleman `guard_rewrite` → voice-memory radar vectors
  (`src/llm_middleman.py:224-280`).
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
