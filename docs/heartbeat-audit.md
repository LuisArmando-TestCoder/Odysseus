# Heartbeat audit (S2)

Every multi-second await on a streaming path must yield `: keep-alive` SSE
comments, or proxies and idle-tab browsers kill the connection while the
server silently works. This file is the audit table for that rule; the CI
enforcement is `tests/test_stream_util.py` (helper behavior) plus
`tests/test_soak_concurrency.py` (no silent stalls under concurrency).

## Helper (single source of truth)

- `src/stream_util.py`
  - `heartbeat_wait(awaitable, interval=15)` — awaiting ONE slow thing.
  - `heartbeat_stream(agen, interval=15)` — draining a long generator
    (background-pump design; a heartbeat timeout NEVER cancels the producer).

## Await sites and their coverage

| Site | Wait shape | Coverage |
|---|---|---|
| `src/llm_middleman.py` `_guard_both_with_heartbeats` | JEV detect + reducer | own heartbeat loop (pre-existing) |
| `routes/chat_routes.py` research progress loop (`:757-776`) | 1 s poll | own `: heartbeat N` comments (pre-existing) |
| `src/agent_loop.py` ask-user await (Q3) | human answer Future | `heartbeat_wait` (15 s) ✅ |
| `src/agent_loop.py` subagent foreground await (B2) | child run Future | `heartbeat_wait` (15 s) ✅ |
| `src/goal_driver.py` `_round_stream` | whole agent turn | wrapped by `agent_runs` — the loop yields chunks/`tool_progress`; no extra idle gap ✅ |
| `src/subagents.py` `_subagent_stream` | whole child turn | same as above ✅ |
| `src/turn_inbox.py` factory turn (`routes/chat_routes._queued_turn_stream`) | whole queued turn | same as above ✅ |
| `src/user_questions.py` `_arm_timeout` | timeout sleep | background task — not on the SSE path ✅ |
| T1.5 `llm_retry` backoff (future) | retry sleep | future work — must use `heartbeat_wait` when added |

## Rule for new code

Any `await` inside a streaming generator that can exceed ~15 s without
yielding a chunk MUST be wrapped in `heartbeat_wait`/`heartbeat_stream`.
Reviewed paths: agent loop tool pool, goal driver, subagents, turn inbox,
research loop, middleman guard.
