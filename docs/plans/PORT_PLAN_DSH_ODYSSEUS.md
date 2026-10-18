# PORT PLAN — DeepSeek Harness chat architecture → Odysseus

Derived from a read-only analysis of:

- **DSH source:** `/Users/luisarmandooriens/Desktop/projects/deepseek-harness` (git `dsh-0.1.0-rc.8`,
  commit `141eb6fef8`). Every DSH claim verified by reading the referenced package source.
  (Note: a stray untracked duplicate of the whole tree sits at `<repo>/deepseek-harness/` —
  same git root, an artifact to clean up; ignore it.)
- **Odysseus source:** `/Users/luisarmandooriens/Desktop/projects/odysseus`. Every Odysseus
  claim verified at the referenced file:line.

Prior docs in this workspace (`GOAL_HARNESS.md`, `UI_SPEC.md`, `UI_DIFF.md`, `UI_UX_PLAN.md`,
`DSH_TECNICAS.md`) are inputs, not repeats.

---

## 0. Executive summary

A large part of this port **is already done**. Prior sessions implemented the goal state
machine, the goal card, input spill (no-truncation of long pastes), detached chat runs that
survive tab-close, model fallback chains, round separators and deliverable cards. Odysseus
already has the *skeleton* of DSH's concurrency model (`src/agent_runs.py` is a clean pub/sub
of exactly DSH's "leave the chat and it keeps running" semantics).

What is genuinely missing (why the experience still feels different):

| # | DSH behavior you want | Odysseus today |
|---|---|---|
| G1 | Agent **asks you mid-task** on a separate level, other chats keep going, resumes with your answer | **Does not exist.** Only a research "clarifying questions" hack (`chat_routes.py:679-698`). Must be built. |
| G2 | **Goal auto-continuation** driven by the harness (no ugly self-ping) | Goal state + driver exist (`src/goal.py`, `src/goal_round_driver.py`) but `goal_round_loop` has **zero callers** — dormant. Continuation today = in-turn self-ping (`agent_loop.py:2197-2246`) + `bg_monitor` for `#!bg` jobs. Must be wired. |
| G3 | **"When something wrong happens it tries something else"** — harness-armed per-tool deadlines, repeated-call reminders, structured errors, LLM request-error retry | No per-tool deadlines, no repeat reminders; failures go back to the model as text. Model-level fallback chain exists. Partial. |
| G4 | **No truncation anywhere** (oversized tool outputs spilled, not cut) | Input spill done; tool outputs hard-truncated at `MAX_OUTPUT_CHARS=10_000` (`agent_tools.py:90`), read_file at 20k, research pages at 15k. DSH **spills** to disk with a locator. Partial. |
| G5 | **Branching to new conversations** (fork a message into a child chat that inherits context and keeps working) | Session fork exists (`manage_session fork`; `branch_id` in DB; fork labels in `sessions.js`), but there is **no subagent/branch execution infrastructure**. Partial. |
| G6 | **Per-message time counting + copying** exactly like DSH | Copy exists (`chatRenderer.js:1264-1275, 1448-1486`); clock timestamps exist; **no live per-message duration ticker** — only a stats-popup `response_time`. Partial. |
| G7 | `present`-style **deliverable cards** when the agent finishes files | `buildDeliverableCard` exists (`chatRenderer.js:233`) but the backend **never emits a `present` event**. Partial. |
| G8 | Switching chats shows **working state** on the other chat and reconnects to the live stream; **new messages queue, they never cancel a live turn** | Detached runs + `/api/chat/resume` exist, but no sidebar "running" indicator and no proactive reconnect. Worse: a new send **cancels** the in-flight run (`agent_runs.start` cancels the previous task) — DSH **queues** the input and lets the turn finish. Partial. |
| G9 | Goal card updates **live** (SSE), not on poll/click | `goalCard.js` renders everything but the SSE wiring is still a comment (`goalCard.js:133-134`); no `goal_update` event server-side. |
| G10 | Hygiene: research `max_time=300` hardcoded (`deep_research.py:179`); goal keying **mismatch** (routes key by owner, loop by session_id) | Small but real. |

The plan closes G1–G10 in 9 phases, in dependency order, each with the DSH reference, the
exact Odysseus files to touch, and acceptance criteria. The **middleman
(`src/llm_middleman.py`) is preserved untouched**, and every new path is designed to run
through it (Phase 7 turns it into a first-class transformation seam — mirroring DSH's hooks
waterfall).

---

## 1. The DSH architecture — the reference model (what we are porting FROM)

All paths relative to `/Users/luisarmandooriens/Desktop/projects/deepseek-harness`.

### 1.1 Repo layout

- pnpm monorepo. `apps/cli` = the only runnable product (`dsh`); `apps/web` = a **thin Vite
  shell** (`src/main.ts` is 10 lines: mount `#root`, run `AppWebEntry` from
  `@deepseek-ai/dsh-client-web`; deliberately non-standalone — only `dsh web` injects
  `window.__DSH_BOOT__`). All real logic lives in `packages/` (~57 libraries).
- `packages/core/` — `agent`, `agent-loop`, `tools`, `session`, `system-prompt`, `scope`.
- `packages/client/` — every chat UI component as a separate package (`ui-conversation`,
  `ui-renderer`, `ui-tool`, `ui-trajectory`, `ui-goal`, `ui-user-questions`, `ui-deliverables`,
  `ui-plan`, `ui-jobs`, `ui-subagent`, `ui-workflow-run`, `ui-sidebar`, `ui-layout`,
  `ui-workspace`, `ui-input-trigger`, `ui-message-feedback`, `ui-attachment`,
  `ui-model-selection`, `ui-permission-presets`, `ui-skill`, `ui-slots`, `ui-primitives`,
  `ui-theme`, `ui-reference`…).
- `packages/goal/`, `packages/jobs/`, `packages/spill/`, `packages/subagent/`,
  `packages/interaction/` (`tool-ask-user`, `user-questions`, `user-approval`),
  `packages/hooks/` (hook-protocol + Claude Code/Codex bridges — the transformation seam),
  `packages/web/` (`tool-web`, `web-search-deepseek`, `web-search-exa`, `web-search-perplexity`,
  `web-fetch-http`), `packages/guard/` (`repeat-tool-reminder`, `timeout-policy`),
  `packages/compaction/`, `packages/feedback/`, `packages/session/` (projection,
  persistence-jsonl/sqlite), `packages/plan/`, `packages/workflow/`, `packages/context/`,
  `packages/llm/llm-retry`.
- `python/` = the Python SDK/runtime; `docs/` has architecture write-ups worth citing
  (`architecture.md`, `agent-lifecycle.md`, `tool-execution-pipeline.md`,
  `capability-seams.md`, `defensive-patterns.md`, `subsystems/jobs.md`, `subsystems/goal.md`).

**Implication for the port:** DSH separates *capability seams* (abstract services) from
*providers* (implementations). Odysseus doesn't need cordis — it needs the same **seams**:
`spill`, `jobs`, `userQuestions`, `goals`, `subagents`, `hooks`. We implement each seam as a
small Python module behind one interface.

### 1.2 Chat core: session event log, projections, streaming

- A chat is a **durable, append-only event log** (`turn/start`, `assistant/chunk`,
  `tool/call`, `tool/result`, `turn/end`, …) — `packages/core/session/src/types.ts`. The
  **model-visible surface is a projection** over `user/message` + `assistant/message` +
  `tool/result` (`surface.ts`); compaction/spill apply as *replace ops* on the projection
  while the human transcript stays intact.
- Streaming: provider chunks → durable `assistant/chunk` events (appended to the log
  **before** the UI renders) → `BlockAssembler` coalesces into one `assistant/message`
  (`packages/core/agent-loop/src/agent.ts:332-420`). The UI replays from the projection store
  over reconnectable downlinks — socket loss never truncates a turn, and aborted streams still
  append `assistant/message {interrupted:true}` (`agent.ts:354-370`).
- Transport: **HTTP unary RPC up, two WebSocket downlinks down** (`/api/events.mux`,
  `/api/events.host`, `packages/client/connection/src/api-path.ts`) with reconnect +
  readiness handshake. Business dispatch over a Typert RPC gateway (`packages/api/gateway`).

**Odysseus equivalent already exists:** `agent_runs.py` replay buffer + live subscribe is the
same replay pattern over SSE. Keep SSE — no WebSocket migration needed.

### 1.3 Agent turn loop & tool deployment

`packages/core/agent-loop/src/agent.ts` + `tool-calls.ts`:

- A turn = a sequence of **steps**: build request → stream → assemble blocks → execute tool
  calls → next step (`agent.ts:332-419`). No global step cap; the loop is bounded by
  max-tokens, aborts, and per-tool timeouts.
- **Parallel tool calls**: one assistant message's tool calls run under a bounded rolling pool
  (`DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10`), each reclassified `parallel` vs `exclusive` before
  start; results + additionalContexts **commit in model order** regardless of completion order
  (`tool-calls.ts:146-160`). Aborted turns fabricate "aborted before dispatch" results for
  skipped calls so replay stays valid (`tool-calls.ts:249-259`).
- **Error handling (the real "tries something else"):**
  - *Model-request errors* → `agent/request-error` waterfall with the provider's `retryPolicy`;
    a handler can answer `{kind:'retry'}` (silent retry) or throw → structured
    `turn/end {kind:'error'}` (`agent.ts:373-390`); durable scheduled retries in
    `packages/llm/llm-retry`.
  - *Tool failures* → **there is NO fixed retry count.** The failure becomes an ordinary
    `tool/result {isError:true}` fed back to the model, and **the model decides** to retry with
    a fix, fall back, or tell the user. The harness' contribution is: (a) `guard/timeout-policy`
    — per-call tool deadlines armed as deployment policy (`TOOL_TIMEOUT`); (b)
    `guard/repeat-tool-reminder` — advisory reminders when identical calls repeat; (c) clean
    structured errors instead of raw text.
- Tools are declared via `defineTool` (JSON schema + `execute(args, exec)` + output schema +
  render). A tool result is a **structured object**, never a bare string.

### 1.4 No-truncation machinery

- **Spill** (`packages/spill/`): `spill-policy` rides the `tools/post-execute` waterfall — a
  tool result above `maxInlineBytes` is **saved whole** to `ctx.spillStore` (session-scoped
  file, `spill-local`) and the model-facing result becomes a **head/tail preview + locator +
  retrieval hint**. A spill failure never turns a successful call into an error.
- **Jobs** (`packages/jobs/`): background job registry — `start/get/list/read/kill/wait`,
  owner isolation by session, `outputLimitBytes`, completion notices; `tool-jobs` =
  `job_output`/`job_list`/`job_kill`.
- **Compaction** (`packages/compaction/`): token-meter policy + LLM summarizer +
  `compaction-tool-result-pruner` (model-free head/middle/tail pruning of old tool results,
  replay-safe).
- Long inputs: `read`/`grep` tools with offset/limit; chunked storage of `assistant/chunk`
  rows in persistence.

### 1.5 Multi-chat concurrency — why one chat never blocks another

- **One `ReactLoopAgent` per session**, each with a private inbox and its own async driver.
  A running turn in chat A never blocks chat B: each agent appends only to its own session log.
- **Input queues, it does not interrupt.** New messages join the inbox as *next-turn* work
  (steer/inject joins *next-step*); a live turn always finishes first; only an explicit
  `cancel()` aborts it (`agent.ts:113-132`). Cold sessions resume once per identity with
  deduped concurrent resumes (`packages/api/remotes/agent-lookup.ts:125-172`).
- Long work runs under `ctx.jobs` or continuable subagents, so the chat turn returns and the
  model gets settlement notices.

**Odysseus gap:** `agent_runs.start` **cancels** the previous run on a new send
(`agent_runs.py:141-155`). DSH queues. See Phase 4 step 0 (turn inbox).

### 1.6 Ask-user mid-task ("asks on a separate level")

`packages/interaction/tool-ask-user` + `user-questions` + `packages/client/ui-user-questions`:

- `ask_user_question` is a **tool**. Its `execute` calls `ctx.userQuestions.ask({questions,
  agent, signal})` and **awaits** a UI provider's promise. The human answer returns as an
  **ordinary tool result** — the turn resumes at exactly the same step boundary. No special
  branch, no new turn.
- **Root-only rule**: questions are valid only for the *live runtime root* agent; a delegated
  child gets `DELEGATED_CALLER` and must surface the open question in its result instead
  (`user-questions/src/index.ts:92-113`). This is why questions never block other branches.
- Question shape: `[{id, question, header?, options?[], multi_select?}]`; an option can be
  marked `(Recommended)`. Answer: `{id, selected[], custom?}`.
- UI: one registered provider renders the question card and resolves the promise.

### 1.7 Goals — versioned state + round driver (no self-ping)

`packages/goal/goal`, `tool-goal`, `goal-round-driver`:

- `GoalSnapshot` = `{id, revision, objective, phase (active|paused|blocked|complete),
  blockedReason?{code,message}, maxGoalRounds}`; `GoalRef {id, revision}` is
  **compare-and-set** (`goal/src/types.ts`). `roundsStarted` is derived from the session log;
  `activation` (`armed|disarmed`) is **process-local, never persisted**.
- `goal-round-driver/src/index.ts`: queues the next round **only at agent quiescence**
  (status idle); one reservation per agent `{goalId, revision, round}`; a pre-step fence
  rejects any goal message that isn't the exact armed reservation for `roundsStarted+1`
  (`validReservation`); human prompts mark the attempt `stale` (`competingQueued`); a round can
  **never trigger another round directly** — only an ADMITTED goal-sourced `user/message`
  advances `roundsStarted`. `blocked` requires `blockedAfterConsecutiveRounds` (default 3,
  `tool-goal/src/index.ts:302`). Disarm on agent error/max-tokens; resume/fork disarms.
- `tool-goal` = `create_goal`/`get_goal`/`update_goal` with authority rules: edit/pause/resume
  require direct-human authority; complete/blocked also allowed from a goal round.

**Odysseus mirrors the data model** (`src/goal.py`, `GOAL_HARNESS.md`) — what's missing is
wiring the driver into the turn lifecycle (G2).

### 1.8 Branching & subagents

`packages/subagent/` — `tool-subagent` README is the contract:

- Providers: `spawn` (fresh child) vs **`fork`** (child seeded with a prefix of the parent's
  log — `parentSession` + `seedLength` live in the `SessionHeader`,
  `packages/core/session/src/types.ts:74-80`).
- Background modes: `one-shot` (returns a `jobId`; collect with `job_output`, kill with
  `job_kill`) vs **`continuable`** (returns a durable `subagentId`; the child owns its turns;
  parent sends more work with `send_message`, lists with `list_agents`, interrupts with
  `interrupt_agent`; a settlement notice arrives when the child ends).
- Sibling delegations in one message run **concurrently** under the rolling pool, committing
  in model order. `maxDepth` caps delegation depth (default 3).
- `subagent_fork` = a fork provider seeded with the *current conversation* — the parent's
  context becomes the child's starting point (the tool I am using right now).

### 1.9 Chat-side UI components (the "perks" you listed)

- **Message chrome** — `ui-conversation/src/client/chat/MessageIconActions.tsx`: **copy**
  (pending/check feedback + cooldown guard, `copyPending`/`copyTimer`), **branch** icon, and
  **clock** row — `formatMessageClock` (clock time), `formatRunDuration` (per-message
  duration), `formatLatencySeconds`, `formatTokensPerSecond` (`message-chrome.ts`). Timing
  data lives in `client/runtime/src/client/sessions/assistant-timing.ts` and
  `ui-trajectory/duration-store.ts`.
- **Streaming render** — `ui-renderer` (app root, assistant markdown), `ui-tool`
  (ToolCallTree), `ui-trajectory` (step timeline/tables).
- **Goal card** — `ui-goal` (GoalBar above the composer); **question card** —
  `ui-user-questions`; **deliverables** — `ui-deliverables` (ProducedFiles turn-tail cards
  with preview + native-open); **plan mode** — `ui-plan` + `exit_plan_mode`; **jobs** —
  `ui-jobs`; **branching** — `ui-subagent`; **workflow** — `ui-workflow-run`; **sidebar** —
  `ui-sidebar` (session tree, search, grouping, state dots).

### 1.10 Web search

`packages/web/tool-web` registers `web_search` + `web_fetch` with bounds (30s cooperative
timeouts, `fetchMaxOutputChars` 200k); providers are swappable. Results return as ordinary
tool results (formatted markdown + sources + truncated flag). **Your scraper stack is superior
— keep it.** Adopt only the pattern you already have: provider abstraction + structured
results (`src/search/universal.py`).

### 1.11 Message-transformation middleman — DSH's equivalent

DSH has **no hidden user↔model rewrite** like Odysseus' `llm_middleman.py`. Its transformation
seams are:

1. **`packages/hooks/`** — a hook protocol with matcher engine, stdin/stdout codec, and hook
   points including `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`; plus bridges that
   run external Claude Code / Codex hook configs on those seams.
2. **In-process waterfalls** — `tools/pre-execute`, `tools/post-execute` (spill-policy rides
   these), `agent/request`, `agent/pre-step` (goal driver, compaction), and the
   `core/system-prompt` assembly registry.
3. **Projection replace-ops** — compaction/edits transform the model-visible surface while the
   append-only log keeps the human transcript.

**Conclusion:** the middleman is an Odysseus differentiator — keep it. Phase 7 mirrors DSH's
*hook-point* structure so the middleman becomes `PostResponse` in an explicit hook chain.

---

## 2. Odysseus today — verified inventory (what NOT to rebuild)

Base: `/Users/luisarmandooriens/Desktop/projects/odysseus`. All refs verified.

| Capability | Where it lives | Status |
|---|---|---|
| FastAPI app + ~50 routers + SQLite | `app.py:45-81, 520-731`, `core/database.py` | ✅ |
| Chat stream (form POST → SSE) | `routes/chat_routes.py:332-1089` | ✅ |
| **Detached runs** (survive tab close) + replay buffer + subscribe + reconnect + stop + status | `src/agent_runs.py` (whole file), `/api/chat/resume\|stop\|stream_status` `chat_routes.py:1095-1128` | ✅ |
| Double-send guard re-subscribes to in-flight run | `chat_routes.py:474-486` | ✅ |
| Partial response saved on disconnect | `chat_routes.py:955-962, 1054-1070` | ✅ |
| Model fallback chain | `stream_llm_with_fallback` `llm_core.py:1647-1654`; chat & agent paths | ✅ (model-level only) |
| Parallel tool blocks per round (asyncio pool) | `agent_loop.py:2338-2413` | ✅ |
| Tool budget + `budget_exceeded` event | `agent_loop.py:2350-2353` | ✅ |
| Input spill (20k → file + 2k prefix + read hint) | `routes/chat_helpers.py:75-101` | ✅ (G4 input half) |
| Context budget + LLM compactor + trim | `src/context_budget.py`, `src/context_compactor.py` | ✅ |
| Background bash `#!bg` + `bg_monitor` re-invocation | `src/bg_jobs.py`, `src/bg_monitor.py` | ✅ |
| Goal state (revision CAS, phases, activation) + routes + prompt injection + `finish_goal` | `src/goal.py`, `routes/goal_routes.py`, `agent_loop.py:1688-1709` | ✅ (driver unwired → G2) |
| Goal card UI (badge, progress, queue, activity, trail, toast, banner) | `static/index.html:703-721`, `static/js/goalCard.js` | ✅ (SSE unwired → G9) |
| Checkpoint append + injection ("RESUME, don't re-derive") | `src/checkpoint.py`, `agent_loop.py:1660-1686` | ✅ |
| Anti-loop nets (self-ping ×3, loop-breaker, verifier subagent, truncated-markup detection) | `agent_loop.py:1451-1487, 2197-2305, 1330` | ✅ (cruder than DSH dispatch → G3) |
| Copy actions, footer actions (copy/delete/fork/edit), clock timestamps | `chatRenderer.js:1264-1275, 1448-1486, 888-897` | ✅ (no live duration → G6) |
| Round separators | `chatRenderer.js:237-242` | ✅ |
| Deliverable cards | `chatRenderer.js:233-256` | ✅ (no `present` event → G7) |
| Session fork action + `branch_id` in DB + fork labels in sidebar | `src/agent_loop.py:342` (manage_session), `core/database.py`, `sessions.js` `_isFork` | ✅ (no subagent infra → G5) |
| Cross-chat completion toasts + notifications | `chatStream.js:201-267` | ✅ (no running indicator → G8) |
| Voice: GPT-Live WebRTC + neural pipeline + JEV + voice memory | `routes/realtime_routes.py`, `services/voice/` | ✅ (keep; out of scope) |
| Search/scraper stack (SearXNG/Brave/DDG/PSE/Tavily/Serper + scrapebadger + CDP + OSINT + docfind/registros_cr) | `src/search/`, `src/deep_research.py`, `src/osint_*`, `src/scrapebadger.py` | ✅ (superior; keep) |
| **Middleman** (JEV detect → reducer rewrite, fence-safe, heartbeats, re-chunk) | `src/llm_middleman.py` | ✅ (preserve; Phase 7) |

**Dormant / mismatched:**

- `src/goal_round_driver.py` — `goal_round_loop`/`run_agent_round` have **zero callers** (G2).
- Goal keying: routes use `owner`, loop/agent use `session_id` (G10).
- `deep_research.max_time=300` hardcoded (`deep_research.py:179`) (G10).
- Tool-output hard truncation `MAX_OUTPUT_CHARS=10_000` / `MAX_READ_CHARS=20_000`
  (`agent_tools.py:26,90`) (G4).
- `MAX_AGENT_ROUNDS=20` constant (`agent_tools.py:19-60`) — DSH has no global step cap; make
  this a setting (G3).
- No `present` SSE event; no `ask_user` SSE event; no `goal_update` SSE event (G7/G1/G9).
- New send **cancels** the live run (G8, queue-don't-cancel).

---

## 3. THE PLAN — 9 phases

Order = dependency order. Each phase is independently shippable. Effort assumes one developer.

---

### Phase 0 — Hygiene & settings alignment (½ day)

**Objective:** remove the small mismatches that would corrupt Phases 3–5.

- [ ] G10a — **Unify goal keying.** `routes/goal_routes.py:28-31` resolves goals by owner while
  `agent_loop.py:1688-1709` and `src/goal_round_driver.py` read by `session_id`. Key by
  `session_id` everywhere (DSH keys goals per session/agent); derive the owner from the session
  for authorization (`_verify_session_owner`).
- [ ] G10b — **Make research tunable.** `deep_research.py:179`: `max_time` (and `max_rounds`,
  `max_empty_rounds` if not already settings) from `src/settings.py`
  (`deep_research_max_time_seconds`, default 300).
- [ ] G3-part — **`MAX_AGENT_ROUNDS` → setting** `agent_max_rounds` (default 20) in
  `src/settings.py`; read it where the constant is used.
- [ ] G9 (server half) — **Emit `goal_update` SSE events.** Add
  `agent_runs.publish(session_id, event)` (next to `_publish` in `agent_runs.py`); call it from
  `src/goal.py` mutations (`update_goal`, `finish_goal`) with
  `data: {"type":"goal_update","data":{...goal view...}}`. No run active → event dropped (card
  polls on load anyway).
- [ ] G9 (client half) — **Wire goalCard to the stream.** In `chat.js`'s SSE event loop, handle
  `goal_update` → `import('./goalCard.js').then(m => m.refreshGoal())`. Remove the stale comment
  at `goalCard.js:133-134`.
- [ ] Add pytest regressions: goal keying (owner A cannot read/patch owner B's goal), research
  settings honored, `agent_max_rounds` honored.

**Acceptance:** creating a goal in chat A while chatting in chat B updates B's card when you
return; `/api/goal` and the agent loop agree on the same goal; `max_time` comes from settings.

---

### Phase 1 — Tool-result contract + harness guardrails (2–3 days)

**Objective:** G3 — the harness contributes what DSH's harness contributes: structured errors,
per-call deadlines, repeat reminders, LLM request-error retry. (DSH does **not** auto-retry
tools — it makes failures clean and lets the model decide; copy that truth, don't invent one.)

**DSH reference:** `guard/timeout-policy`, `guard/repeat-tool-reminder`,
`agent/request-error` waterfall + `llm-retry`, `tools` result shape (`{isError, content}`).

**Backend:**

- [ ] **Structured tool results.** Formalize `ToolResult` dataclass
  `{ok, output, error_code, is_error, spilled}` in `src/tool_execution.py` (outcomes already
  carry `{"error", "exit_code"}`). No behavior change yet.
- [ ] **Per-call tool deadlines** (port of `timeout-policy`): new table
  `TOOL_TIMEOUTS = {web_search: 30, web_fetch: 45, bash: 60 (existing SHELL_TIMEOUT),
  read_file: 15, ...}` in `src/tool_execution.py`; wrap each tool task in `asyncio.wait_for`
  (the parallel pool at `agent_loop.py:2338-2413` is where the tasks exist). On timeout →
  `ToolResult(ok=False, error_code="timeout")` + a `tool_timeout` SSE event, never a hung
  round. Round-level deadline (`agent_loop.py:1912`) stays as backstop.
- [ ] **Repeated-tool reminder** (port of `repeat-tool-reminder`): after the 3rd identical
  tool+args in a turn, inject a one-line system notice: "You have called X with the same args
  N times — the args or the approach are stuck; change one." Gentle precursor to the existing
  loop-breaker (`agent_loop.py:2252-2305`).
- [ ] **LLM request-error retry policy** (port of `agent/request-error`): in `src/llm_core.py`
  give `llm_call_async`/`stream_llm` an explicit `retry_policy {max_retries, retry_on}` (it
  already has `max_retries` — make it per-call-configurable and emit a
  `{"type":"llm_retry","data":{attempt}}` SSE notice so the UI can show it). The fallback
  chain (`stream_llm_with_fallback`) stays as the second layer — that combination **is** DSH's
  "tries something else".
- [ ] *(Optional Odysseus extension, defaults OFF)* per-tool `RetryPolicy` table
  (`src/tool_retry.py`) for the few calls where a silent retry is safe (web_search retry once
  on timeout then switch provider — reusing `universal.py` multi-provider). Keep it OFF by
  default; DSH's model-driven retry remains the primary path.

**Frontend:**

- [ ] Render `tool_timeout` / `llm_retry` events in the agent thread as small status lines
  ("retrying (attempt 2/3)…"). Nothing else changes.

**Acceptance:** kill the network mid-search → the loop surfaces a clean timeout result, the
model switches provider in its next step (visible in chat), and no hung round blocks the turn;
a repeated identical tool call triggers the reminder before the loop-breaker has to fire.

---

### Phase 2 — No-truncation hardening (1–2 days)

**Objective:** G4 — oversized *outputs* are spilled with a locator (DSH `spill`), never cut.

**DSH reference:** `packages/spill/spill-policy` (post-execute transformer: full text → file,
model sees head/tail preview + locator + retrieval hint; spill failure never fails the call).

**Backend:**

- [ ] New `src/spill.py` mirroring `SpillStore.saveText`: writes to
  `data/spills/<owner>/<session>/<ts>_<tool>.txt` (reuse `core/atomic_io`), returns
  `{"locator": path, "bytes": n, "retrieval_hint": "read_file '<path>' or docfind <term>"}`.
- [ ] Replace hard truncation in `src/agent_tools.py:90-93` (`_truncate`, `MAX_OUTPUT_CHARS`)
  and `MAX_READ_CHARS` at `:26`: above threshold → `spill.save` and the tool result becomes
  prefix + locator + hint. Keep the display-only clipping (`agent_loop.py:2477-2498`) — the
  model gets the locator, the bubble stays tidy.
- [ ] Research pages: `deep_research.py:592-595` (`max_content_chars=15_000`) → spill overflow
  into the per-research notes file instead of discarding.
- [ ] Heartbeats: extract the pattern already used by the middleman
  (`llm_middleman.py:486-513`) and the research loop (`chat_routes.py:757-776`) into
  `src/stream_util.py: async def with_heartbeats(awaitable, interval=15)`; use it for every new
  multi-second await (retry backoff, spill write, deadlines, later: question waits).

**Acceptance:** a tool printing 80k chars → the chat bubble is clipped (fine) but the model's
context contains a file path, and "what's in that file around line N?" is answered from disk.
Zero `[Truncated]` text fed to the model.

---

### Phase 3 — Ask-user seam (the centerpiece UX) (2–3 days)

**Objective:** G1 — the agent asks you a question mid-task, on its own level; the answer
resumes the exact turn; other chats keep running.

**DSH reference:** `tool-ask-user` + `userQuestions` service + `ui-user-questions`
(section 1.6). Root-only rule is mandatory.

**Backend:**

- [ ] New `src/user_questions.py` (mirror of the DSH service):
  - `pending: dict[session_id, AskState]`, `AskState = {questions, future: asyncio.Future,
    created_at, turn_id}`.
  - `async def ask(session_id, questions, can_ask_user) -> Answer` — if `not can_ask_user`,
    return `{"error_code": "DELEGATED_CALLER", ...}` as the tool result (server-enforced
    root-only). Otherwise register the future; the loop awaits it with `with_heartbeats` and
    `ASK_TIMEOUT` (10 min) → auto-resolve `{"timeout": true}`.
  - `def answer(session_id, answers) -> bool` — resolves the future (route calls it).
  - Persist a pending marker (`data/agent_state/<session>.question.json`) so a page refresh
    re-renders the card even before the stream replay.
- [ ] Tool `ask_user_question` — schema in `src/tool_schemas.py` (same shape as DSH:
  `questions[] {id, question, header?, options[]?, multi_select?}`), implementation
  `do_ask_user_question` in `src/tool_implementations.py` that emits
  `data: {"type":"ask_user","data":{questions, session_id}}` and awaits the registry.
- [ ] Loop integration (`src/agent_loop.py`, tool pool `:2338-2413`): the ask-user task must
  not block the generator — emit the event, then `async for hb in with_heartbeats(future, 15):
  yield hb`, then feed the resolved answer as that call's tool result (same slot → ordering
  preserved).
- [ ] `can_ask_user` flag: `stream_agent_loop(..., can_ask_user=False)` default;
  `chat_routes.py` passes `True` only for the root user turn. `bg_monitor`, verifier, and
  (Phase 5) subagent runs pass `False` → `DELEGATED_CALLER`, surface the question in the
  result (DSH contract).
- [ ] Routes: `GET /api/question/{session_id}` (pending question for reconnect),
  `POST /api/question/{session_id}/answer` `{answers:[{id, selected[], custom?}]}` — both
  `_verify_session_owner`. The answer is appended to history as a visible user message
  (`metadata: {answer_to_question: true}`) so the transcript stays honest.
- [ ] Middleman safety: `ask_user` is a non-delta SSE event → `guard_stream` passes it verbatim
  (verified: only `delta` chunks are buffered, `llm_middleman.py:560-569`). Add a test.

**Frontend:**

- [ ] `chat.js`: on `ask_user` → render question card(s) above the composer (new
  `static/js/questionCard.js`, styles in `messages-hyper.css`): header, question, options with
  "(Recommended)" tag, multi-select checkboxes, custom text field, Send button.
- [ ] On send → `POST /api/question/{sid}/answer`; card collapses to "✓ answered"; the live
  stream continues (heartbeats).
- [ ] On session load → `GET /api/question/{sid}` restores an unanswered card.
- [ ] While pending, the current chat shows a subtle "waiting for your answer…" banner; other
  chats are untouched by design (per-session registry).

**Acceptance:** "set up the project, but ask me before choosing the stack" → mid-run a question
card appears; you answer 3 minutes later from another chat; the run resumes from the exact tool
call; during the wait the other chats and goals ran uninterrupted.

---

### Phase 4 — Turn inbox + goal round driver wiring (2–4 days)

**Objective:** G2 + G8 — goals continue by **harness-driven rounds** (DSH
`goal-round-driver`), and **new messages queue instead of cancelling** a live turn (DSH inbox).

**DSH reference:** `goal-round-driver/src/index.ts` (idle-gated continuation,
queue/claim/admit + `validReservation` fences, `competingQueued`, disarm semantics);
inbox semantics from `agent.ts:113-132`.

**Step 0 — Turn inbox (queue, don't cancel):**

- [ ] In `agent_runs.py` / `chat_routes.py`, change the double-send path so that **a new user
  message during a live run is appended to the session history and queued** as *next-turn*
  work instead of cancelling the current run. Concretely: keep a per-session
  `pending_turns: list[message]`; when the current run finishes, the finish hook (in
  `_drain`) drains the queue and starts the next detached run automatically.
- [ ] Keep the existing double-send guard (re-subscribe for the *same* message) — it is a
  different case (duplicate), not a new turn.
- [ ] `POST /api/chat/stop` remains the only way a turn is cancelled.

**Step 1 — Driver trigger points:**

- [ ] Start the driver when: (a) `create_goal` (routes) → phase active & armed & no active run;
  (b) a run finishes while a goal is `active` + `armed` (hook the `_drain` finish path from
  Step 0); (c) app startup — scan `data/goals/*.json`, resume active goals (boot resume).

**Step 2 — Idle gating (the anti-self-ping):**

- [ ] Before each round: `agent_runs.is_active(session)` must be False, goal `phase==active`,
  `activation=="armed"`, no queued user turn, and no user message arrived since the last round
  (record `len(sess.history)` at round start; compare — Odysseus' `competingQueued` fence).
  Any failure → skip; re-check on a 5s per-goal timer (cheap poll; mirrors DSH's quiescence
  watch).

**Step 3 — Round execution:**

- [ ] `goal_round_driver.run_agent_round` (exists, `goal_round_driver.py:47-103`): build round
  messages = goal frame (`=== ACTIVE GOAL (round r/max) ===`, already injected at
  `agent_loop.py:1688-1709`) + checkpoint context + synthetic user message
  `"Continue working toward the goal. Use the checkpoint; do not re-derive."`; call
  `stream_agent_loop(..., can_ask_user=True)` wrapped in `agent_runs.start(...)` so the round
  is a **detached run** (visible from any chat, survives refresh).
- [ ] Round events: `{"type":"goal_round","data":{"round":r}}` at start; `goal_update` at end
  (Phase 0 helper).

**Step 4 — Authority & disarm:**

- [ ] The driver reads `activation` every round; `pause`/`resume` disarm, and **only a human
  re-arms** (DSH rule — already the semantics in `src/goal.py`; the driver must simply never
  set it).
- [ ] Blocked rule stays as implemented (`goal_round_driver.py:119-181`, 3 consecutive rounds).
- [ ] De-emphasize in-turn self-ping: keep it as *single-turn* recovery only (empty/malformed
  rounds); add one system-prompt line: "do not self-ping for continuation — the harness starts
  the next round."

**Frontend:**

- [ ] `goalCard.js`: `goal_round` events → `setGoalActivity('working')`; `goal_update` →
  refresh (Phase 0 wiring).
- [ ] Sidebar: "working" dot per running session (batch endpoint `GET /api/chat/active` listing
  `{session_id, status}` from `agent_runs` + queued-turn count) — closes G8; selecting a
  running chat auto-calls `/api/chat/resume/{id}`.

**Acceptance:** (1) create a 20-step goal, close the tab, return in 30 minutes → rounds kept
advancing, checkpoint trail grew, zero `[self-ping]` deltas; (2) send a message while a turn is
running → the turn finishes, then your message gets its own turn — nothing is cancelled.

---

### Phase 5 — Branching & subagents (2–4 days)

**Objective:** G5 — "branching to new conversations": fork a message/chat into a child
conversation that inherits context and keeps working while the parent continues.

**DSH reference:** `subagent-fork-in-process` (child seeded with parent log prefix;
`parentSession` + `seedLength` in SessionHeader), `tool-subagent` (spawn/fork,
run_in_background, continuable), `tool-subagent-control` (`send_message`, `interrupt_agent`,
`list_agents`), depth cap 3.

**Backend (reuse `agent_runs` — it is already per-session):**

- [ ] Complete the fork action: `POST /api/session/{id}/fork` — child session with parent
  history copied as seed (fork-in-process equivalent), `branch_id` set (DB column exists),
  name `Fork: <title>`.
- [ ] New `src/subagents.py`: registry `{subagent_id → {session_id, parent_id, depth}}`.
  `subagent` tool (`tool_schemas.py` + `do_subagent` in `tool_implementations.py`):
  - `spawn`: new session + task prompt; detached `agent_runs.start(child_session,
    stream_agent_loop(...can_ask_user=False))`; **await** with `with_heartbeats` +
    `subagent_timeout` (default 15 min); return the child's final answer as the tool result.
  - `fork`: same, but the child's seed context = the current session's history (the "branch to
    a new conversation" you described).
  - `continuable`: start detached, return immediately with `{subagent_id}`; the child owns its
    turns; the parent continues.
- [ ] Companion tools: `send_message` (append user message to child session; start a detached
  run if idle), `list_agents` (registry → `{id, label, session_id, status}`),
  `interrupt_agent` (`agent_runs.stop(child_session)`), `job_output`/`job_kill` aliases for
  one-shot runs (reuse `/api/chat/stream_status` + stop). Depth: refuse delegation below
  depth 3 (registry tracks it).
- [ ] Ownership: child sessions inherit `owner`; all routes already verify ownership.
- [ ] Root-only ask flows automatically: child runs get `can_ask_user=False` (Phase 3).

**Frontend:**

- [ ] Branch button on messages: footer actions already have `fork`
  (`chatRenderer.js:1448-1486`) — wire it to `POST /api/session/{id}/fork` and open the child.
- [ ] Sidebar: group children under their parent via `branch_id` (sessions.js already renders
  fork labels); working dots via `/api/chat/active` (Phase 4).
- [ ] Opening a child chat while the parent streams already works (independent runs).

**Acceptance:** in a big research chat, branch from a message "pursue this lead separately" →
a child chat appears under the parent, runs in parallel, and the parent turn continues
uninterrupted; the child reports back into the parent transcript when done.

---

### Phase 6 — Chat UX parity (timers, copy, present, indicators) (1–2 days)

**Objective:** G6, G7, G8 polish — the small "perks" that make DSH's chat feel better.

**DSH reference:** `MessageIconActions.tsx` + `message-chrome.ts` (`formatRunDuration`,
`formatMessageClock`, `formatTokensPerSecond`); `ui-deliverables`; sidebar state dots.

- [ ] **Live per-message duration.** `chat.js` streaming path: `streamStart = performance.now()`
  per bubble; 1s ticker shows `12.4s` while streaming; on `metrics`/`[DONE]` freeze to
  `response_time` and render `3.2s · 14:05:11` (duration + clock) in the footer — the DSH
  clock row.
- [ ] **Copy parity.** Add the DSH feedback loop to the existing copy button: icon → ✓ for
  1.5s + cooldown guard against double-copy (port of `copyPending`/`copyTimer`,
  `MessageIconActions.tsx:53-63`).
- [ ] **`present` events.** When the agent writes a deliverable (`do_write_file` and friends,
  `src/tool_implementations.py`), emit at turn end
  `data: {"type":"present","data":{"files":[{name, path, description?}]}}`; `chat.js` inserts
  `buildDeliverableCard(f)` per file (renderer exists, `chatRenderer.js:233`).
- [ ] **Trajectory polish.** Tool blocks as DSH-like hierarchy: round separator → collapsible
  tool card (summary + output) → final answer. Apply `UI_SPEC.md` Part 3 CSS if not already
  present.

**Acceptance:** a long agent turn shows a live ticking duration that freezes at completion;
copy shows ✓ feedback; finished files appear as deliverable cards with Previsualizar/Abrir;
the sidebar pulses on a working chat and reconnects when you click it.

---

### Phase 7 — Middleman preservation & transform seam (½–1 day)

**Objective:** guarantee every new path still flows through your transformation layer, and make
the seam explicit — mirroring DSH's hook-point structure.

**DSH reference:** `packages/hooks/` (hook points `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `Stop`) + in-process waterfalls (`tools/pre-execute`, `tools/post-execute`,
`agent/request`, `agent/pre-step`).

- [ ] **Invariant + tests.** Phases 1–6 call `stream_llm*`/`llm_call*` from `src/llm_core`
  (they do — the guard lives inside those wrappers, `llm_core.py:1583-1654`). Regression
  suite: with `llm_middleman_enabled=true` and a mocked JEV that flags — (a) a fenced tool
  call survives reduction uncorrupted; (b) `ask_user`/`present`/`goal_update` non-delta events
  pass verbatim; (c) reducer failure returns the original (fail-open); (d) heartbeats during
  the guard window.
- [ ] **Hook-point seam (small, structural).** Define in `src/llm_core.py` a
  `HookPoint` enum + ordered `transform_chain(stages)` helper (`async gen → async gen`) with
  stages: `user_prompt_submit` (before request), `pre_tool_use` / `post_tool_use` (around tool
  execution), `post_response` (the middleman's current position). No behavior change — the
  middleman becomes the registered `post_response` stage. This mirrors DSH's hook protocol and
  makes "I want to transform messages in any way" a one-line insertion.
- [ ] Document (README note): **any new chat feature must emit through `llm_core` wrappers**,
  never raw httpx, or it bypasses the guard.

**Acceptance:** guard regression suite passes; adding a new transform is a one-line chain
insertion with a test fixture.

---

### Phase 8 — Performance & durability hardening (1–2 days, ongoing)

**Objective:** speed + reliability under the new workload (rounds, questions, branches).

- [ ] Research tunables verified end-to-end (Phase 0); re-check `max_empty_rounds` against
  `universal.py` fallback behavior (no blind retries — `DSH_TECNICAS.md` rule 4).
- [ ] Heartbeat audit: grep every `await` in `stream_agent_loop` / `agent_runs._drain` for
  silent gaps >15s (proxy kill). Fix with `with_heartbeats` (Phase 2).
- [ ] Frontend: throttle streaming markdown re-renders (pending from `DSH_TECNICAS.md` §6),
  keep `content-visibility`, verify sticky-scroll in `_scrollTranscript`.
- [ ] `agent_runs` tuning: cap the replay buffer for very long runs (currently an unbounded
  list); make the 180s eviction grace a setting.
- [ ] Restart durability (documented scope): goal JSON + checkpoint + DB survive restart; the
  in-flight generator does not (`agent_runs.py:14-15`). Same as DSH desktop (process-local).
  Accept and document; Phase 4 boot-resume already covers the durable part.
- [ ] Security pass: `ask_user`/`answer`/`fork`/`subagent` routes all `_verify_session_owner`;
  question timeouts free the Future; no cross-session question reads.

**Acceptance:** 3 concurrent sessions (one goal-driving, one waiting on a question, one running
a 20-tool turn) + a background research all progress simultaneously with the UI responsive, and
a 60s silent await does not drop any stream.

---

### Phase 9 — Test & release gates (continuous, per phase)

Odysseus has a strong `tests/` convention (183 files). Each phase lands with at least one
pytest + one JS-level test:

- Phase 0: goal keying / settings / `agent_max_rounds`.
- Phase 1: deadline fires; reminder after 3 repeats; LLM retry event emitted.
- Phase 2: spill writes file + locator; model never sees `[Truncated]`.
- Phase 3: root-only `DELEGATED_CALLER`; answer resumes the run; timeout auto-resolves;
  guard passes `ask_user` verbatim.
- Phase 4: driver starts on create; idle-gating skips busy sessions; blocked after 3;
  disarm on pause; boot resume; queue-don't-cancel (message during run → second turn).
- Phase 5: fork seeds history; continuable child keeps running; depth cap; parent unaffected.
- Phase 6: duration freezes at `response_time`; `present` renders cards; copy feedback.
- Phase 7: guard regression suite (fences, heartbeats, fail-open).
- Phase 8: concurrency soak test (3 sessions + research) in CI-friendly form.

---

## 4. Cross-cutting decisions & risks

1. **Transport: keep SSE.** DSH uses WS downlinks, but Odysseus' SSE + `agent_runs` replay
   already provides replay-then-live semantics. Moving to WS buys nothing. Decide: do not move.
2. **In-memory runs.** Like DSH desktop, runs are process-local. Accepted; documented.
   Boot-resume of goals (Phase 4) covers the durable part.
3. **Goal keying.** One key everywhere: `session_id`. Owner only for authorization (Phase 0).
4. **Root-only questions.** Server-enforced (`can_ask_user` flag), never prompt-enforced. A
   subagent that needs an answer surfaces the question in its result (DSH `DELEGATED_CALLER`).
5. **Middleman buffering vs live streaming.** The guard buffers whole streams; accepted UX
   (heartbeats hide it). New non-text events pass through unbuffered. Do not change guard
   semantics in this port.
6. **Round/user-message race.** A goal round yields if the user sends a message (idle-gating +
   history-length fence + turn inbox, Phase 4) — DSH's `competingQueued`.
7. **Queue vs cancel** is a behavior change users will notice immediately (Phase 4 Step 0) —
   ship it with the Stop button prominent and a "queued" indicator, or users will think sends
   are lost.
8. **Effort realism.** Sized for one developer; total ≈ 3–4 focused weeks, excluding polish.

## 5. Suggested execution order

```
Phase 0 (½d) ──► Phase 1 (2-3d) ──► Phase 2 (1-2d) ──► Phase 3 (2-3d)
                                                          │
                                                          ▼
Phase 4 (2-4d) ◄── (turn inbox + goal driver reuse the detached-run + heartbeat plumbing)
                                                          │
                                                          ▼
Phase 5 (2-4d) ──► Phase 6 (1-2d) ──► Phase 7 (½-1d) ──► Phase 8 (1-2d + ongoing)
```

Phases 0–2 are the plumbing every later phase reuses (`with_heartbeats`, deadlines,
structured results, spill, `agent_runs.publish`). Phase 3 (ask-user) is the highest
user-visible impact; Phase 4 (turn inbox + goal driver) is the highest capability impact.
Phase 5 builds on 3+4 (children are detached runs that can't ask). Phase 6 is polish, 7 safety,
8 soak.

## 6. What NOT to do

- Do **not** rebuild: detached runs, input spill, goal state/card, deliverable card renderer,
  round separators, fallback chains, search/scraper stack, voice pipeline, middleman.
- Do **not** adopt WebSocket or cordis — the value is in the *semantics* (replay, queueing,
  idle-gating, root-only questions, spill, deadlines, hook points), not the plugin framework.
- Do **not** invent harness-level auto-retry for tools as a blanket policy — DSH deliberately
  leaves retry decisions to the model; the harness arms deadlines, reminders and clean errors
  (Phase 1). Optional per-tool retry only where provably safe, defaults OFF.
- Do **not** let the model drive long-goal continuation — the driver owns rounds (Phase 4).
- Do **not** touch the middleman's behavior — only its seam (Phase 7).
- Do **not** alter the advanced voice mode or its context: WebRTC GPT-Live, the neural
  pipeline, `services/voice/*`, `services/stt|tts`, and the two-way voice↔chat bridge
  (`routes/chat_helpers.py:542-573` chat←voice; `routes/realtime_routes.py:135-190`
  voice←chat) are frozen — every new turn type must preserve them (matrix in
  `BACKEND_BUILD_PLAN_ODYSSEUS.md` Cluster V).
- New capability (independent of the DSH port): legal corpus adapters for SINALEVI + Corte IDH
  as **query-first tools with an opt-in bulk crawler** — never a default mass scraper
  (`BACKEND_BUILD_PLAN_ODYSSEUS.md` Cluster L).

---

*Sources: DSH checkout rc.8 — `packages/core/agent-loop`, `packages/core/session`,
`packages/core/tools`, `packages/goal/*`, `packages/interaction/*`, `packages/spill`,
`packages/jobs`, `packages/hooks`, `packages/subagent/tool-subagent`, `packages/guard`,
`packages/compaction`, `packages/web`, `packages/client/ui-*`, `packages/client/connection`,
`packages/api/remotes`, `apps/web`, `docs/*`; Odysseus — `app.py`,
`routes/chat_routes.py`, `routes/chat_helpers.py`, `src/agent_runs.py`, `src/agent_loop.py`,
`src/agent_tools.py`, `src/tool_execution.py`, `src/goal.py`, `src/goal_round_driver.py`,
`src/llm_middleman.py`, `src/deep_research.py`, `src/settings.py`, `static/js/*`; plus the
workspace docs `GOAL_HARNESS.md`, `UI_SPEC.md`, `UI_DIFF.md`, `UI_UX_PLAN.md`,
`DSH_TECNICAS.md`.*
