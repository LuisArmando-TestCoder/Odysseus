# Harness port — architecture summary

Odysseus' DeepSeek-Harness (DSH) port. Backend and frontend implemented
cluster-by-cluster; the full plan lives in the repo history and the workspace
docs (`BACKEND_BUILD_PLAN_ODYSSEUS.md`). This page is the working map: where
each capability lives, the SSE event vocabulary, the new routes, and the tests.

## Component map

| Capability | Backend | Frontend |
|---|---|---|
| Detached runs (tab-close survives) + replay + reconnect | `src/agent_runs.py` (+ `/api/chat/resume`, `/api/chat/stream_status`, `/api/chat/active`) | `chat.js`, `sessions.js` (working dots) |
| Turn inbox — new messages queue, never cancel | `src/turn_inbox.py`, `routes/chat_routes.py` (factory + `turn_queued`) | `chat.js` (`turn_queued` note) |
| Goals — versioned, owner-keyed, auto-continuing rounds | `src/goal.py`, `src/goal_driver.py`, `src/goal_round_driver.py`, `routes/goal_routes.py` | `goalCard.js` (live via `goal_update`/`goal_round`) |
| Ask-user mid-task (root-only) | `src/user_questions.py`, `ask_user_question` tool, `routes/question_routes.py` | `questionCard.js` |
| Subagents & branching | `src/subagents.py`, `POST /api/session/{sid}/fork`, 6 agent tools | `chat.js` (clickable `subagent_started`), `sessions.js` fork rows |
| Spill / no-truncation | `src/spill.py` (`data/spills/`), wired into bash/python/read_file | — (locator in tool output) |
| Tool guardrails | `TOOL_TIMEOUTS` + pool deadlines, repeat-tool reminder, `llm_retry` retry policy (T1.x, `src/tool_result.py`) | `chat.js` (`tool_timeout`, `llm_retry` notes) |
| Hook seam (middleman = `post_response`) | `src/hooks.py` | — |
| Deliverables | `present` event at turn end (agent_loop deliverable registry) | `chatRenderer.js` `buildDeliverableCard` |
| Legal corpus | `src/legal/` (sinalevi, corteidh, corpus) | — (agent tools) |
| Voice (preserved) | `services/voice/*`, `routes/realtime_routes.py` — untouched | `voice.js`, `voice_vectors.js` — untouched |

## SSE event vocabulary (backend → frontend)

| Event | Emitted by | Frontend handling |
|---|---|---|
| `goal_update` | goal mutations (`publish_to_owner`) | `goalCard.js` refresh + activity dot |
| `goal_round` | goal round start | `goalCard.js` activity `working` |
| `ask_user` | ask-user tool await | `questionCard.js` render |
| `turn_queued` | queued message behind a live run | status note |
| `tool_timeout` | pool deadline exceeded | status note |
| `llm_retry` | same-model retry (T1.5) | status note |
| `present` | turn end, files produced | deliverable cards |
| `subagent_started` / `subagent_done` | child run start / continuable settlement | clickable chip / note |
| `run_buffer_truncated` | replay-buffer cap (S1) | debug-only |

## Routes added/changed by the port

- `POST /api/goal` — create goal (owner-keyed, stamps session). `POST /api/goal/{action}` unchanged surface.
- `POST /api/chat_stream` — queue-don't-cancel branch.
- `GET /api/chat/active` — run inventory (+ queued counts).
- `GET /api/chat/stream_status/{sid}` — now includes `queued`.
- `GET /api/question/{sid}` / `POST /api/question/{sid}/answer`.
- `POST /api/session/{sid}/fork` — branch chat into a child session.

## Harness settings (Settings UI, defaults in `src/settings.py`)

`agent_max_rounds` (20) · `ask_user_timeout_seconds` (600) ·
`subagent_timeout_seconds` (900) · `agent_run_evict_grace_seconds` (180) ·
`agent_run_buffer_max_events` (5000) · plus the existing
`agent_stream_timeout_seconds`, `research_max_time_seconds`, etc.

## Turn-type context matrix (voice bridge)

See `docs/voice-chat-bridge.md` — goal rounds and queued turns share the chat's
voice stratum; subagent children, verifier/bg-monitor turns and incognito do
not. Voice modules are out of port scope (verified untouched).

## Test map

`tests/test_stream_util.py` · `test_agent_runs_v2.py` · `test_goal_l0.py` ·
`test_turn_inbox.py` · `test_tool_result.py` · `test_spill.py` ·
`test_llm_fallback_retry.py` · `test_user_questions.py` · `test_goal_driver.py` ·
`test_subagents.py` · `test_hooks.py` · `test_soak_concurrency.py` ·
`test_voice_chat_bridge.py` · `test_legal.py` · `test_port_frontend_js.py`.
Full suite: 1476 passed / 1 skipped / 0 failed.
