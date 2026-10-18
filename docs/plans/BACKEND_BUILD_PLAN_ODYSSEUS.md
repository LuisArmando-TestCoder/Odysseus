# BACKEND BUILD PLAN — Odysseus ⇐ DeepSeek Harness (component level)

Everything that is **missing in the backend**, organized as **functional clusters**, each cluster
ordered so that **foundations come before the things that depend on them**. This is the backend
half of `PORT_PLAN_DSH_ODYSSEUS.md`; every component below is a concrete, buildable unit with
files, interface, and acceptance criteria. Frontend appears only where a connection touches it
(events/routes it consumes).

Notation: `F0.1`, `T1.3`, `Q2`, `G4`… = component IDs. Each lists **needed-by** so the dependency
graph is explicit. "Connections" between components are collected in §9 (SSE event catalog),
§10 (route catalog) and §11 (wiring map).

---

## 0. Ground-truth corrections (verified after the previous plan)

1. **Goal keying is the reverse of what the first plan assumed.** `routes/goal_routes.py:1-6`
   deliberately keys goals by **owner** ("the goal card lives in the global sidebar, not
   per-session"). But `src/goal.py` stores/reads by whatever id it is given, and the two
   writers disagree:
   - the `finish_goal` tool executes as `do_finish_goal(session_id, …)` (`tool_execution.py:1179-1189`)
     → writes `data/goals/<session_id>.json`;
   - the goal card reads `get_goal(owner)` → `data/goals/<owner>.json`.
   **Two different files.** Canonical rule for the port: **identity = owner; the goal object
   carries `session_id` (the chat where rounds run).**
2. **Goals cannot be created at all.** There is no `POST /api/goal` (create) route
   (`goal_routes.py` only has `GET ""`, `GET /queue`, `GET /checkpoint`, `POST /{action}`), and
   no `create_goal`/`get_goal`/`update_goal` agent tools — grep finds **zero callers** of
   `create_goal` outside `src/goal.py`. Only `finish_goal` is exposed (`tool_schemas.py:174`,
   `tool_execution.py:1179`). The agent can *end* a goal it can never *start*. Creation is
   therefore **Layer 0**, not a later phase.

---

## 1. Layer 0 — Foundations (nothing above can exist without these)

### F0.1 — `src/stream_util.py`: `with_heartbeats()` · 2 h
**Why first:** every new long wait (ask-user, subagent await, retry backoff, spill write,
goal-round setup) must yield `: keep-alive` SSE comments or proxies drop the stream.
**Files:** new `src/stream_util.py`; pattern already proven in `llm_middleman.py:486-513`
(`_guard_both_with_heartbeats`) and `chat_routes.py:757-776` (research loop).
**Spec:**
```python
async def with_heartbeats(agen, *, interval: float = 15.0):
    """Async-generator wrapper: yields `: keep-alive` comments while `agen` runs,
    then yields its final value exactly once."""
```
**Acceptance:** unit test — a 40 s awaitable yields ≥ 2 heartbeats then the value; a fast
awaitable yields only the value.

### F0.2 — `src/agent_runs.py` v2: `publish()` + owner + finish callbacks · 3 h
**Why first:** out-of-band events (`goal_update`, `turn_queued`, `subagent_done`, `present`)
must be injectable into a running stream from modules that are not the generator; the goal
driver and inbox need to react to run termination; the sidebar needs a run inventory.
**Files:** extend `src/agent_runs.py` (keep `_publish`/`_drain` semantics untouched).
**Spec:**
```python
def start(session_id, agen, *, owner: str | None = None) -> _Run   # store owner on _Run
def publish(session_id: str, event: str) -> bool                   # no-op if no run
def publish_to_owner(owner: str, event: str) -> None               # fan to all live runs of owner
def list_active() -> list[dict]                                    # {session_id, status, owner}
def on_finish(cb: Callable[[str, str], Awaitable[None]]) -> None   # cb(session_id, status); called in _drain finally
```
`on_finish` callbacks are contained (exceptions logged, not raised into `_drain`).
**Acceptance:** publish into a live run appears in `subscribe()` output and in the replay
buffer; `list_active()` reflects start/stop; a throwing finish-callback does not break the run.

### F0.3 — Goal keying unification (correction #1) · 3 h
**Files:** `src/goal.py`, `routes/goal_routes.py`, `src/tool_execution.py` (`finish_goal`
branch `:1179-1189`), `src/agent_loop.py` (goal injection `:1688-1709`).
**Spec:**
- `src/goal.py`: document + enforce **identity = owner**; the stored object keeps `session_id`
  (already a field of `create_goal`) as the round-execution chat. Add
  `get_goal_by_session(session_id)` helper (resolves owner from the session, then reads the
  goal, returning it only if `goal.session_id == session_id` — the agent-injection lookups use
  this).
- `tool_execution.finish_goal`: pass **owner** (derive from `stream_agent_loop(owner=…)`, which
  already receives `_user` at `chat_routes.py:987`) instead of `session_id`.
- `routes/goal_routes.py`: keep owner keying; add `_verify_session_owner`-style authorization
  unchanged.
- Migrate any existing `data/goals/*.json` files on startup (rename session-keyed file to
  owner-keyed if the session's owner is resolvable).
**Acceptance:** `finish_goal` from a chat and the goal card read the **same** file; injection
in `agent_loop` shows the goal only in its own session.

### F0.4 — Goal creation path (correction #2) · 3 h
**Files:** `routes/goal_routes.py`, `src/tool_schemas.py`, `src/tool_implementations.py`,
`src/tool_execution.py`.
**Spec:**
- Route `POST /api/goal` body `{objective, session_id?, max_goal_rounds?}`: resolve owner from
  auth; if `session_id` given, `_verify_session_owner`; default `session_id` = the caller's
  current/default session; call `create_goal(owner, objective, max_goal_rounds)` and stamp
  `session_id` on the stored object; return the goal view. One active goal per owner
  (409 if one exists in `active|paused`).
- Agent tools (DSH `tool-goal` equivalent): `create_goal {objective, max_goal_rounds?}`,
  `get_goal {}`, `update_goal {action: complete|blocked, blocked_reason?}` — schemas in
  `tool_schemas.py`, `do_*` in `tool_implementations.py`, dispatch entries in
  `tool_execution.py` next to `finish_goal`. All keyed by owner, derived from the session.
**Acceptance:** creating a goal from the API and from the agent produce the same goal; the card
shows it; `create_goal` from a session stamps that session id.

### F0.5 — Settings extraction · 2 h
**Files:** `src/settings.py`, `src/deep_research.py:179`, `src/agent_tools.py:19-60`,
`src/agent_loop.py:1912`, `src/agent_runs.py:42`.
**Spec — new keys (all with current values as defaults):**
```
deep_research_max_time_seconds   = 300      (was hardcoded param)
deep_research_max_rounds         = 8
deep_research_max_empty_rounds   = 2
agent_max_rounds                 = 20       (was MAX_AGENT_ROUNDS constant)
agent_stream_timeout_seconds     = 300      (already a setting; keep)
ask_user_timeout_seconds         = 600      (used by Cluster B)
subagent_timeout_seconds         = 900      (used by Cluster D)
agent_run_evict_grace_seconds    = 180      (agent_runs.py:42)
agent_run_buffer_max_events      = 5000     (Cluster F)
```
**Acceptance:** each consumer reads `get_setting(...)`; changing a value in Settings changes
behavior without code edits.

### F0.6 — `src/turn_inbox.py`: queue primitives · 3 h
**Why in Layer 0:** the goal driver's idle-gating (G2) must know about queued turns, and
Cluster C's "queue, don't cancel" is a prerequisite for G8 and for subagent parents.
**Files:** new `src/turn_inbox.py`; hook into `src/agent_runs.py` via `on_finish` (F0.2).
**Spec:**
```python
def enqueue(session_id: str, message: str) -> dict      # {state: 'queued'|'running', position}
def is_queued(session_id: str) -> bool
def queued_count(session_id: str) -> int
def clear(session_id: str) -> None                       # stop button also clears the queue
def start_next(session_id: str) -> bool                  # pops + starts a detached run for the
                                                         # queued message (calls the same
                                                         # stream builder as chat_stream — injected
                                                         # via a registered factory to avoid a
                                                         # routes→inbox→routes import cycle)
register_stream_factory(factory)                         # chat_routes registers its builder here
```
In-memory only (same scope as `agent_runs`); the queued message is **persisted to history
first** with `metadata {"queued": true}` — so a restart loses the *run*, never the *message*.
**Acceptance:** enqueue during an active run → finish → `start_next` launches the new run
exactly once; double registration of the factory is an error.

---

## 2. Cluster A — Tool execution contract, deadlines, spill (G3 + G4)

> Depends on: F0.1, F0.5. Everything above in the stack calls tools, so this cluster hardens
> the tool layer **before** ask-user/subagent/goal rounds are built on it.

### T1.1 — `ToolResult` contract · 4 h
**Files:** `src/tool_execution.py` (execute_tool_block `:895-1293`), `src/agent_loop.py`
(pool `:2338-2413`).
**Spec:**
```python
@dataclass
class ToolResult:
    ok: bool
    output: str                    # model-facing text
    error_code: str | None         # 'timeout'|'not_found'|'disabled'|'DENIED'|...
    is_error: bool
    spilled: SpillRef | None       # set by T1.6 when output was spilled
    call_id: str | None
```
Every `execute_tool_block` return coerced to `ToolResult`; the pool collects `ToolResult`s and
builds the model-facing result messages from them (replacing the current dict splicing at
`agent_loop.py:1154-1227`).
**Acceptance:** no raw dicts reach `_append_tool_results`; a failed call has a stable
`error_code`.

### T1.2 — Per-call tool deadlines (port of DSH `guard/timeout-policy`) · 3 h
**Files:** `src/tool_execution.py`, `src/agent_loop.py` pool.
**Spec:** `TOOL_TIMEOUTS = {web_search: 30, web_fetch: 45, bash: SHELL_TIMEOUT,
read_file: 15, ask_user_question: 600 (later), subagent: 900 (later), ...}`; wrap each pool
task in `asyncio.wait_for`; on `TimeoutError` → `ToolResult(ok=False, error_code="timeout",
output="tool exceeded its deadline")` + emit `{"type":"tool_timeout","data":{"tool":name}}`
through the shared progress queue. Round-level deadline (`agent_loop.py:1912`) stays as
backstop.
**Acceptance:** a hung `bash` is cut at 60 s, the round continues, the model sees a clean
`timeout` result.

### T1.3 — `src/spill.py`: SpillStore · 2 h
**Files:** new `src/spill.py` (uses `core/atomic_io`).
**Spec:**
```python
class SpillStore:
    def save_text(self, *, owner: str, session_id: str, tool: str, text: str) -> SpillRef
    # SpillRef = {locator: data/spills/<owner>/<session>/<ts>_<tool>.txt, bytes: int,
    #             retrieval_hint: "read_file '<locator>' (use offset/limit) or docfind <term>"}
```
**Acceptance:** two concurrent saves land in distinct files; owner A cannot see owner B's
directory naming.

### T1.4 — Repeated-tool reminder (port of DSH `guard/repeat-tool-reminder`) · 2 h
**Files:** `src/agent_loop.py` (tool-result collection).
**Spec:** per-turn dict `(tool, normalized_args) → count`; on the 3rd identical call, inject
into the next round's messages: *"You have called `<tool>` with the same arguments 3 times.
Either the arguments are wrong or the approach is stuck — change one."* Gentle precursor to
the existing loop-breaker (`:2252-2305`), which stays.
**Acceptance:** three identical `web_search` calls in one turn → notice appears in round 4.

### T1.5 — LLM request-error retry events (port of `agent/request-error` waterfall) · 3 h
**Files:** `src/llm_core.py` (`llm_call_async` `:1604-1621`, `stream_llm*` `:1630-1654`).
**Spec:** make retries configurable per call (`retry_policy {max_retries, retry_on, backoff_s}`,
defaults = current `MAX_RETRIES`); on each retry, `stream_llm*` yields
`data: {"type":"llm_retry","data":{"attempt":N,"model":...}}` (same channel as the existing
`{"type":"fallback"}` event). `stream_llm_with_fallback` keeps its model-switch layer — retry
then fallback = DSH's exact two-layer behavior.
**Acceptance:** a flaky endpoint shows `llm_retry` chunks then a fallback event; the UI renders
both as status lines (frontend consumer only).

### T1.6 — Tool-output spill policy (port of DSH `spill-policy`) · 4 h
**Files:** `src/agent_tools.py:90-93` (`_truncate`, `MAX_OUTPUT_CHARS`), `:26`
(`MAX_READ_CHARS`), `src/agent_loop.py` display clipping `:2477-2498` (unchanged).
**Spec:** above threshold → `SpillStore.save_text(...)`; the model-facing `ToolResult.output`
becomes: first 2 000 chars + `[FULL OUTPUT SPILLED TO: <locator> — read it with read_file
(offset/limit) or grep it with docfind]`. Spill failure → fall back to the old truncation
(never make a successful call an error — DSH rule). Display clipping stays (bubbles stay
tidy; the model gets the locator).
**Acceptance:** an 80k-char bash output produces a spill file; the model's next round can read
arbitrary offsets; zero `[Truncated]` text reaches the model.

---

## 3. Cluster B — Ask-user (G1)

> Depends on: F0.1 (heartbeats), F0.2 (events), F0.5 (timeout), T1.1 (result contract).
> Provides: the seam the goal rounds (Cluster C) and root-turns use; subagents (Cluster D)
> consume its `DELEGATED_CALLER` contract.

### Q1 — `src/user_questions.py`: the registry · 4 h
**Files:** new `src/user_questions.py` (mirror of DSH `user-questions/src/index.ts`).
**Spec:**
```python
class AskState:  questions: list[dict]; future: asyncio.Future; created_at: float; turn_id: str
PENDING: dict[str, AskState]                       # keyed by session_id; ONE per session

async def ask(session_id: str, questions: list[dict], *, can_ask_user: bool) -> dict
    # can_ask_user=False  → {"error_code": "DELEGATED_CALLER", "message": "human interaction is
    #   unavailable in this context; include the open question in your final result"}
    # 409-style error if one already pending → {"error_code": "ALREADY_PENDING"}
    # else: register AskState, persist marker data/agent_state/<session>.question.json, return
    #   {"status": "pending"}

def answer(session_id: str, answers: list[dict]) -> bool      # resolves the future; False if none
def cancel_timeout(session_id: str) -> None                   # resolves {"timeout": true}
def get_pending(session_id: str) -> dict | None               # for reconnect (reads marker too)
```
Timeout task armed with `ask_user_timeout_seconds` (F0.5); cleanup removes marker file.
**Acceptance:** second concurrent ask in the same session → `ALREADY_PENDING`; timeout
auto-resolves; marker file survives a page refresh.

### Q2 — `ask_user_question` tool · 3 h
**Files:** `src/tool_schemas.py`, `src/tool_implementations.py`, `src/tool_execution.py`.
**Spec:** schema identical to DSH (`questions[] {id, question, header?, options[]?, multi_select?}`,
options may carry `(Recommended)` in the label); `do_ask_user_question` calls `Q1.ask(...)`
with the turn's `can_ask_user` flag and returns the `ToolResult` (answer or error_code).
**Acceptance:** tool appears in RAG tool index; disabled in `disabled_tools` for incognito.

### Q3 — Loop integration + `can_ask_user` flag · 4 h
**Files:** `src/agent_loop.py` (signature, pool `:2338-2413`, round assembly), `routes/chat_routes.py:974-989`, `src/bg_monitor.py`, verifier call `agent_loop.py:1330`, (later) `src/goal_round_driver.py`, (later) `src/subagents.py`.
**Spec:**
- `stream_agent_loop(..., can_ask_user: bool = False)`; `chat_routes.py` passes `True` only for
  the root user turn; `bg_monitor`, verifier, goal rounds, subagents pass `False` explicitly
  (audited per call site).
- The ask tool's pool task: **emit** `data: {"type":"ask_user","data":{"questions":[...],
  "session_id":...}}` through the shared progress queue, then
  `async for hb in with_heartbeats(future, 15): yield hb` and resolve the answer as that
  call's `ToolResult` — same slot, so ordering is preserved and the generator never blocks
  silently.
- On `DELEGATED_CALLER`, the error string is the tool result (model surfaces the question in
  its final answer — DSH contract).
**Acceptance:** root turn asks and resumes from the exact call; a `#!bg`-driven
`bg_monitor` turn gets `DELEGATED_CALLER` instead of hanging.

### Q4 — Question API · 3 h
**Files:** `routes/chat_routes.py` (or new `routes/question_routes.py` + registration in
`app.py` next to goal routes).
**Spec:**
- `GET /api/question/{session_id}` → `{questions}|{questions:null}` (from Q1.get_pending);
  `_verify_session_owner`.
- `POST /api/question/{session_id}/answer` body `{answers:[{id, selected[], custom?}]}` →
  `Q1.answer`; on success, append to session history a user-visible message
  (`role:"user"`, `metadata:{"answer_to_question": true, "question_ids":[...]}`) so the
  transcript stays honest; return `{"resolved": true}`.
**Acceptance:** answering from a second device resolves the awaiting tool call within the
heartbeat interval; the answer appears in history.

---

## 4. Cluster C — Turn inbox + goal round driver (G2 + G8)

> Depends on: F0.2/F0.3/F0.4/F0.5/F0.6, T1.x (rounds run tools), Q3 (rounds may ask the user).
> This cluster is the harness-driven continuation that replaces self-pinging.

### I1 — Queue-don't-cancel in `chat_stream` · 4 h
**Files:** `routes/chat_routes.py:474-486` (double-send guard), `:1088-1089` (start), F0.6.
**Spec:** keep the duplicate guard. New branch: if `agent_runs.is_active(session)` and the
message is **new** (≠ last persisted user message): persist message with
`metadata {"queued": true}` → `turn_inbox.enqueue(session, message)` →
`agent_runs.publish(session, 'data: {"type":"turn_queued","data":{"position":N}}\n\n')` →
return `StreamingResponse(agent_runs.subscribe(session))` (frontend keeps receiving the live
run; on stream end it re-checks `/api/chat/stream_status` and resumes the next run).
Research mode unaffected (its path returns before `agent_runs.start`).
**Acceptance:** two rapid sends → turn 1 finishes fully, turn 2 starts automatically, both
persisted; no partial/cancelled turn.

### G1 — Goal driver trigger points · 3 h
**Files:** new `src/goal_driver.py` (thin scheduler around `src/goal_round_driver.py`),
`routes/goal_routes.py` (create route calls it), `app.py` startup.
**Spec:**
```python
async def maybe_start_goal_round(owner: str) -> None   # full idle-gate check, then start
def start_daemon() -> asyncio.Task                     # 5 s loop over data/goals/*.json where
                                                       # phase=='active'; cheap (file mtime check)
```
Called from: (a) `POST /api/goal` (F0.4) after create; (b) `agent_runs.on_finish` callback
(F0.2) mapping session→owner; (c) daemon (boot resume — re-arms after restart, mirroring DSH
resume semantics).
**Acceptance:** goal created while its chat is idle → a round starts within ~5 s; app restart
with an active goal → rounds resume.

### G2 — Idle-gating (the anti-self-ping fence) · 4 h
**Files:** `src/goal_driver.py`, `src/goal_round_driver.py`.
**Spec:** start a round **iff** ALL of:
1. goal `phase == 'active'` and `activation == 'armed'`;
2. `not agent_runs.is_active(goal['session_id'])` (F0.2);
3. `not turn_inbox.is_queued(goal['session_id'])` (F0.6);
4. no new user message since the last round — record `len(sess.history)` at round start
   (`competingQueued` equivalent); mismatch at re-check → skip;
5. `roundsStarted < maxGoalRounds` (else `update_goal(blocked, "maxGoalRounds reached")`).
Any failure → skip; the daemon re-checks next tick. The driver must **never** set
`activation='armed'` itself (human-only re-arm — DSH rule; `src/goal.py` already implements it).
**Acceptance:** user sends a message while a goal round is pending → round skipped and the
user turn runs instead; paused goal never resumes by itself.

### G3 — Round execution wiring · 5 h
**Files:** `src/goal_round_driver.py` (run_agent_round `:47-103`), `src/goal_driver.py`.
**Spec:** per round:
1. load `goal`, resolve `sess = session_manager.get_session(goal['session_id'])`;
2. build messages: goal frame (`=== ACTIVE GOAL (round r/max) ===` — reuse the injection at
   `agent_loop.py:1688-1709` as the system block) + checkpoint text (`src/checkpoint.py`) +
   synthetic user message `"Continue working toward the goal. Use the checkpoint; do not
   re-derive."`;
3. wrap in `agent_runs.start(goal['session_id'], stream_agent_loop(...,
   can_ask_user=True, owner=owner), owner=owner)` — the round is a **detached run**;
4. emit `{"type":"goal_round","data":{"round":r,"max":m}}` at start and `goal_update` at end
   (F0.2 `publish_to_owner`);
5. bump `roundsStarted` (loop-owned counter, as today); `finish_goal done/blocked` from the
   model terminates the loop (existing `finish_goal` contract).
**Acceptance:** a round runs in the background while the user chats elsewhere; the card's
"Ronda n" advances; no `[self-ping]` deltas appear for continuation (keep the in-turn self-ping
only for empty/malformed single-turn recovery).

### G4 — Run inventory endpoint (backend half of G8) · 2 h
**Files:** `routes/chat_routes.py`.
**Spec:** `GET /api/chat/active` → `{"runs":[{"session_id","status","queued":N,"owner"}...]}`
from F0.2 `list_active()` + F0.6; extend `GET /api/chat/stream_status/{sid}` to include
`"queued": N`.
**Acceptance:** three sessions (streaming, queued, idle) report correctly; endpoint is cheap
(single dict read, no DB).

---

## 5. Cluster D — Branching & subagents (G5)

> Depends on: F0.2 (settlement notices), F0.6/I1 (child runs use the same detached-run + queue
> infra), Q3 (children get `can_ask_user=False`), T1.1/T1.2 (child tool safety).

### B1 — Fork endpoint · 3 h
**Files:** new route in `routes/session_routes.py` (or `chat_routes.py`); uses
`core/session_manager.py:300 fork_branch(...)` and `core/database.py` `branch_id` (both exist).
**Spec:** `POST /api/session/{id}/fork` `{message?}` → child session: copy parent history as
seed (fork-in-process equivalent; `fork_branch` already supports `keep_count` — pass the full
history), set `branch_id` to parent, name `Fork: <title>` (or the message's first 40 chars),
return `{session_id, name}`. `_verify_session_owner`.
**Acceptance:** child renders under parent in the sidebar; parent history is intact in the
child; owner isolation holds.

### B2 — `src/subagents.py`: registry + runners · 6 h
**Files:** new `src/subagents.py`; uses `agent_runs.start` with `can_ask_user=False`.
**Spec:**
```python
SUBS: dict[str, Sub]   # Sub = {id, session_id, parent_id, depth, mode: 'one-shot'|'continuable',
                       #        status, started_at, result?}
async def run_subagent(*, parent_session_id: str, mode: 'spawn'|'fork', task: str,
                       background: bool, owner: str) -> dict
#  spawn: new empty session; fork: B1 seed copy (needs no HTTP hop — call session_manager directly)
#  depth check: parent depth + 1 > 3 → {"error_code":"MAX_DEPTH"}
#  child run: agent_runs.start(child_id, stream_agent_loop(..., can_ask_user=False,
#                    owner=owner, disabled_tools=<inherit-safe subset>), owner=owner)
#  one-shot: await child terminal with with_heartbeats + subagent_timeout (F0.5) →
#            {"kind":"foreground","output": <final assistant text>}   (DSH contract: partial
#            text is never reported as success)
#  continuable: return {"kind":"continuable","subagent_id":id} immediately
```
Settlement: on child terminal, `publish_to_owner(owner, subagent_done event)` so any parent
run learns it (continuable children).
**Acceptance:** sibling spawns in one round run concurrently (the existing tool pool already
parallelizes); a depth-4 delegation is refused; child timeout yields an errored result, not a
hang.

### B3 — Subagent control tools · 5 h
**Files:** `src/tool_schemas.py`, `src/tool_implementations.py`, `src/tool_execution.py`.
**Spec:** `subagent {mode: spawn|fork, task, run_in_background?}` (schema mirrors DSH
`tool-subagent`); `send_message {subagent_id, message}` (append user message to the child
session; start a detached run if idle — reuse I1 semantics); `list_agents {}` (from `SUBS`);
`interrupt_agent {subagent_id}` (`agent_runs.stop(child_session)`);
`job_output {job_id}` / `job_kill {job_id}` aliases over the one-shot registry entries.
**Acceptance:** the parent can list, message, and interrupt its children; children of other
owners are invisible.

### B4 — Parent-side notices (connection glue) · 2 h
**Files:** `src/subagents.py`, F0.2.
**Spec:** one-shot settlement is the tool result itself (B2 await). Continuable settlement →
`{"type":"subagent_done","data":{"subagent_id","status","summary"}}` published into every live
run of the owner (parent sees it in-chat as a system line; the model can `list_agents` to
decide follow-up). No auto-reply into the parent turn — the model decides (DSH behavior).
**Acceptance:** a continuable child finishing while the parent streams shows the notice without
interrupting the parent turn.

---

## 6. Cluster E — Presentation & hooks (G7 + middleman seam)

> Depends on: F0.2 (event injection), T1.1 (know which files were written).

### H1 — `present` event emission · 3 h
**Files:** `src/agent_loop.py` (turn-level `deliverables` list next to `tool_events`),
`src/tool_implementations.py` (`do_write_file` and siblings register `{name, path,
description}` on the turn), `routes/chat_routes.py` (`[DONE]` handlers at `:1029-1053`).
**Spec:** at turn end (before save), emit
`data: {"type":"present","data":{"files":[{name, path, description?}]}}` when non-empty;
frontend inserts `buildDeliverableCard` per file (renderer already exists,
`chatRenderer.js:233`).
**Acceptance:** an agent turn that writes 2 files → 1 `present` event with both files → 2
deliverable cards in chat.

### H2 — Hook-point seam in `llm_core` (DSH `packages/hooks` equivalent) · 5 h
**Files:** `src/llm_core.py` (wrappers `:1583-1654`), `src/tool_execution.py`,
`src/llm_middleman.py` (no behavior change).
**Spec:**
```python
class HookPoint(str, Enum): USER_PROMPT_SUBMIT="user_prompt_submit"; PRE_TOOL_USE="pre_tool_use";
                            POST_TOOL_USE="post_tool_use"; POST_RESPONSE="post_response"
async def run_hooks(point: HookPoint, payload: dict) -> dict      # ordered registry, contained errors
def register_hook(point: HookPoint, fn) -> None
```
- `post_response`: the middleman's `guard_stream`/`guard_response_async` becomes the
  registered hook (moved, not copied — identical call sites).
- `pre_tool_use`/`post_tool_use`: wrap `execute_tool_block` (cheap, one call each);
  `user_prompt_submit`: wraps the message just before context build in `chat_routes.py`.
- Default registries empty except `post_response` → zero behavior change today.
**Acceptance:** middleman regression suite (fences preserved, fail-open, heartbeats, non-delta
events verbatim) still passes with the hook indirection; a test hook on
`user_prompt_submit` sees every user message.

### H3 — Deliverable registry (small, feeds H1) · 2 h
**Files:** `src/agent_loop.py`.
**Spec:** per-turn `deliverables: list[dict]` populated by tool results that carry
`artifact: {name, path, description}` (add the field in T1.1-era `ToolResult`); H1 reads it at
turn end. Excluded from the model context (presentation-only).
**Acceptance:** artifact metadata never leaks into the model's messages; H1 consumes it.

---

## 6A. Cluster V — Voice preservation & two-way context bridge (constraint cluster)

> Not a "missing" cluster — a **preservation constraint**: the advanced voice mode must remain
> exactly as it exists, including its context. The DSH port must not break the two-way bridge
> between WebRTC voice, the neural-pipeline memory, and the chat session. Verified today:

**The bridge, direction 1 — chat ← voice** (`routes/chat_helpers.py:542-573`):
`build_chat_context` injects the voice-memory stratum for `chat-{session_id}` — compressed
spine (`spine_text(include_live=True)`) **and** the full live transcript
(`transcript_text(max_turns=200)`) — as untrusted, read-only DATA.

**Direction 2 — voice ← chat** (`routes/realtime_routes.py:135-190`):
`POST /api/realtime/session` builds the GPT-Live instructions = persona + user instructions +
`VOICE_CONTRACT` + per-chat trusted claims + `chat_summary_spine(sid, owner)[:4000]`
(`=== CURRENT CHAT THREAD ===`) + `session_documents_spine(sid, owner)[:4000]`
(`=== DOCUMENTS IN THIS CHAT ===`). This is exactly the requirement "everything accessible in
the session that matches the chat" — the voice session receives the matching chat's thread,
documents, and trusted claims.

**Auxiliary flows:** middleman `guard_rewrite` → voice-memory radar vectors
(`llm_middleman.py:224-280`); neural pipeline (push-to-talk) writes its own memory via
`services/voice/memory.py`; voice events/status/memory endpoints at
`realtime_routes.py:280-501`.

### V1 — Bridge contract doc + freeze · 2 h
**Files:** new `docs/voice-chat-bridge.md`; comments at the two injection points.
**Spec:** enumerate the three flows with exact functions, keys (`chat-{session_id}`), size caps
(200 turns / 4000 chars), and the read-only (untrusted DATA) rule. Convention: any PR touching
those functions must update the doc.
**Acceptance:** doc exists and matches the code; the two injection points cite it.

### V2 — Turn-type context matrix (port safety) · 3 h
**Files:** `docs/voice-chat-bridge.md` (matrix), code guards where missing.
**Spec — which new turn types get which voice context:**
| Turn type | Voice stratum injected? | Notes |
|---|---|---|
| chat turn (root) | ✅ (today's behavior) | frozen |
| goal round (Cluster C/G3) | ✅ | runs in `goal.session_id` → same `chat-{sid}` memory; same injection path |
| ask-user answer turn | ✅ | same session |
| subagent child (Cluster D/B2) | ❌ | new session → its own (empty) `chat-{child}` memory; never inherits the parent's transcript |
| verifier / bg_monitor turns | ❌ | as today (they already bypass chat context build — verify) |
| incognito / `no_memory` | ❌ | add explicit guard in `build_chat_context` if one is missing today |
**Acceptance:** matrix enforced by tests (V3); no voice transcript ever leaks into a child
session or an incognito turn.

### V3 — Bridge regression tests · 3 h
**Files:** `tests/test_voice_chat_bridge.py`.
**Spec:** fixture voice memory → assert chat preface contains spine + transcript blocks; assert
realtime instructions contain the `=== CURRENT CHAT THREAD ===` and
`=== DOCUMENTS IN THIS CHAT ===` blocks for the matching chat; assert a child subagent context
has no voice stratum; assert incognito excludes it.
**Acceptance:** green in CI with no network (mocked memory + settings).

### V4 — Voice surface untouched · 2 h
**Spec:** `/api/realtime/*`, `services/voice/*`, `services/stt`, `services/tts` are **out of
scope** of the DSH port. New clusters import none of these modules except through the two
injection points above. Existing voice tests keep passing unchanged.
**Acceptance:** `git diff` on voice modules across the port = comments/docs only.

---

## 6B. Cluster L — Legal corpus: SINALEVI (Costa Rica) + Corte IDH

> New capability. Strategy follows the research you pasted: **there are no official APIs**, and
> community scrapers rot because the PGR changes page markup — so build **query-first adapters
> plus an opt-in bulk crawler**, never a default mass scraper. Both sites are navigable for
> targeted queries (verified: Corte IDH full catalog at `instrumentos.cfm` +
> `jurisprudencia.corteidh.or.cr`; SINALEVI Búsqueda Avanzada with free/selective/thematic
> search, e.g. the "ambiente" theme lists all environmental norms).
>
> **Language:** Python (it runs inside Odysseus and reuses the existing stack — `httpx`,
> `src/search/content.py` SSRF-safe fetch, `src/docfind.py` PDF grep, `src/registros_cr.py` as
> the adapter pattern). **Data:** metadata index always; **full PDFs fetched on demand** and
> searched locally with `docfind` — bulk PDF download only when explicitly requested.

### L1 — `src/legal/sinalevi.py` (adapter) · 6 h
**Files:** new `src/legal/__init__.py`, `src/legal/sinalevi.py`.
**Spec:**
```python
@dataclass
class LawHit: title: str; number: str; url: str; pdf_url: str | None
              date: str | None; theme: str | None

async def search_sinalevi(*, term: str | None = None, theme: str | None = None,
                          number: str | None = None) -> list[LawHit]
```
POST-simulates the Búsqueda Avanzada (`BusquedaAvanzada/BusquedaLibre`); parses result tables
with `selectolax` (no Selenium). **Fallback when the form changes** (the exact failure mode you
described): route the query through `src/search/universal.py` with `site:sinalevi.go.cr` and
parse the hit URLs. Politeness: 1 req / 2 s, browser UA, `robots.txt` respected.
**Acceptance:** `search_sinalevi(theme="ambiente")` returns the environmental norms list from
saved-HTML fixtures; the site:-fallback path returns LawHits too; zero requests over the rate
limit in a 30 s test.

### L2 — `src/legal/corteidh.py` (adapter) · 5 h
**Files:** new `src/legal/corteidh.py`.
**Spec:**
```python
async def corteidh_catalog() -> list[Instrument]          # instrumentos.cfm → id, name, kind, url
async def corteidh_search(*, term: str) -> list[CaseHit]  # jurisprudencia.corteidh.or.cr
async def corteidh_case(case_id: str) -> CaseHit          # metadata + direct PDF links
```
Plain link/metadata extraction — the site publishes fichas técnicas + direct PDF links, so no
headless browser is justified. `CaseHit {case_id, title, country, date, pdf_urls, summary}`.
**Acceptance:** fixtures: catalog lists all instruments; a case returns its PDF URLs; parse
failures degrade to a structured error, not an exception.

### L3 — `src/legal/corpus.py` (store + opt-in bulk) · 5 h
**Files:** new `src/legal/corpus.py`.
**Spec:**
- Store: `data/legal/{sinalevi,corteidh}/index.jsonl` + `pdfs/`; **dedupe by unique key**
  (ley número / caso id) — the "unique identifier before name" rule from `DSH_TECNICAS.md`.
- `fetch_pdf(hit) -> local_path`: download on demand → text extraction (existing PDF stack) →
  `docfind` grep ready.
- `bulk_inventory(terms: list[str])`: **opt-in only** (admin flag / explicit request — "dímelo
  y lo monto"), checkpoint/resume (`src/checkpoint.py` pattern), per-source politeness,
  incremental append to the index.
**Acceptance:** two runs of the same query → zero duplicate index rows; interrupted bulk run
resumes from the checkpoint; PDFs land owner-scoped under `data/legal/`.

### L4 — Agent tools · 5 h
**Files:** `src/tool_schemas.py` (next to `docfind`/`registros_cr` entries at `:205/:223`),
`src/tool_implementations.py`, `src/tool_execution.py`.
**Spec:** `sinalevi_search {term?, theme?, number?}`, `corteidh_search {term}`,
`corteidh_case {case_id}`, `legal_fetch_document {url|hit_key}`. All return `ToolResult`
(T1.x contract: deadline from `TOOL_TIMEOUTS`, spill for long hit lists); disabled for
incognito; results always cite source URL + ley/caso number (auditable — the trust rules from
your middleman prompt: never fabricate, cite the primary document).
**Acceptance:** the agent can run a targeted legal query end-to-end (tool → adapter → docfind on
the PDF) and cite the source in its answer.

### L5 — Retrieval/index hook · 3 h
**Files:** `src/legal/corpus.py` (+ optional RAG wiring).
**Spec:** primary retrieval = `docfind` grep over `data/legal/**` (matches the proven technique
— deterministic, unblockable). Optional: when the corpus exceeds ~50 PDFs, register them into
the existing owner-scoped personal-docs/RAG pipeline (`src/rag_index.set_rag_manager` path).
**Acceptance:** `docfind --file <corpus pdf> --term <artículo>` finds the article in <1 s
(parity with the existing `DSH_TECNICAS.md` verification).

### L6 — Tests · 3 h
**Files:** `tests/test_legal_sinalevi.py`, `tests/test_legal_corteidh.py`.
**Spec:** saved-HTML fixtures (no network in CI), dedupe, rate-limit timer, SSRF guard on PDF
URLs, PDF extraction path, fallback routing.
**Acceptance:** full suite green offline; `pytest -k legal` runs in < 10 s.

---

## 7. Cluster F — Hardening & security (G10 residual + P8)

> Depends on: everything above. Ships last; items are independent of each other.

### S1 — `agent_runs` bounds · 2 h
Buffer cap `agent_run_buffer_max_events` (F0.5): beyond cap, keep head + tail (drop middle with
a `run_truncated_buffer` notice) — long runs stop growing memory; eviction grace already
setting-ized.
**Acceptance:** a 60-min run with a subscriber never exceeds the cap ×2 events.

### S2 — Heartbeat audit · 3 h
Grep-audit every `await` inside `stream_agent_loop`, `_drain`, `tool_execution`, new clusters:
any gap > 15 s without a heartbeat is a bug. Fix with F0.1. CI test: a mocked 60 s await in the
loop yields ≥ 3 heartbeats.
**Acceptance:** the audit table is committed (file `docs/heartbeat-audit.md`) and CI enforces
the stream test.

### S3 — Security pass on new surface · 3 h
- Every new route (`/api/goal` create, `/api/question/*`, `/api/chat/active`,
  `/api/session/{id}/fork`) → `_verify_session_owner` / `effective_user`.
- `Q1`: only the owning session's run may resolve a question; timeouts always free futures.
- `B2`: child sessions inherit owner; `list_agents`/`send_message`/`interrupt_agent` scoped to
  the caller's sessions; disabled-tool inheritance (children inherit the parent's
  `disabled_tools` minus nothing — never gain privileges).
- Spill files: owner-scoped directory; `read_file` path confinement (existing
  `tool_path_confinement` tests extended to `data/spills`).
**Acceptance:** cross-owner question/agent/spill access attempts → 401/404; a subagent cannot
call tools its parent chat had disabled.

### S4 — Concurrency soak · 4 h
pytest: 3 sessions simultaneously — one goal-driving (mock LLM), one parked on an unanswered
question (timeout path), one 20-tool turn — plus one `#!bg` research; assert all progress,
heartbeats keep flowing, `list_active()` consistent, no task leaks (`asyncio.all_tasks` before
vs after).
**Acceptance:** soak passes in CI-friendly time (< 60 s with mocked LLM latencies).

### S5 — Restart-durability statement (doc, 1 h)
Document exactly what survives restart (goals, checkpoint, DB messages, spill files, queued
messages as history) vs what does not (in-flight generators, pending question futures, inbox
queue order). Phase 4/G1 boot-resume covers goals; document the rest as accepted scope —
same as DSH desktop.

---

## 8. Connection catalog — every new SSE event (producer → consumer)

Single source of truth for payload shapes; the frontend consumes these verbatim.

| Event | Payload | Producer | Consumer |
|---|---|---|---|
| `goal_update` | `{goal: {id, objective, phase, roundsStarted, maxGoalRounds, revision, blockedReason?}}` | `src/goal.py` mutations via F0.2 `publish_to_owner` | `goalCard.js` refresh |
| `goal_round` | `{round, max}` | G3 at round start | `goalCard.js` (activity dot), chat |
| `ask_user` | `{questions:[{id,question,header?,options?[],multi_select?}], session_id}` | Q2/Q3 via progress queue | `questionCard.js` |
| `turn_queued` | `{position}` | I1 via F0.2 | chat (queued indicator) |
| `tool_timeout` | `{tool, deadline_s}` | T1.2 | agent-thread status line |
| `llm_retry` | `{attempt, model?}` | T1.5 in `stream_llm*` | agent-thread status line |
| `present` | `{files:[{name, path, description?}]}` | H1 at `[DONE]` | `buildDeliverableCard` |
| `subagent_done` | `{subagent_id, status, summary?}` | B4 via F0.2 | chat system line |

All events are plain JSON `data:` lines; non-delta events pass through the middleman's
`guard_stream` verbatim (regression-tested in H2).

## 9. Connection catalog — every new/changed route

| Route | Change | Backs |
|---|---|---|
| `POST /api/goal` | **new** (create; owner-keyed; stamps session_id) | F0.4, G1 trigger |
| `POST /api/goal/{action}` | unchanged surface; keying/auth verified | F0.3 |
| `POST /api/chat_stream` | **changed**: queue-don't-cancel branch | I1 |
| `GET /api/chat/stream_status/{sid}` | **extended**: `queued` field | G4 |
| `GET /api/chat/active` | **new** (run inventory) | G4, sidebar dots |
| `GET /api/question/{sid}` / `POST /api/question/{sid}/answer` | **new** | Q4 |
| `POST /api/session/{id}/fork` | **new** | B1 |

## 10. Wiring map (backend connections per cluster)

```
routes/chat_routes ──enqueue──► src/turn_inbox ──start_next──► agent_runs.start
agent_runs ──on_finish──► turn_inbox.start_next   (I1)
agent_runs ──on_finish──► goal_driver.maybe_start_goal_round   (G1)
routes/goal_routes ──create──► src.goal ──► goal_driver daemon   (F0.4/G1)
src.goal (mutations) ──publish_to_owner──► agent_runs ──SSE──► goalCard.js   (F0.2)
goal_driver ──► src.goal_round_driver ──► stream_agent_loop(can_ask_user=True) ──► agent_runs   (G3)
stream_agent_loop ──► tool_execution ──► ToolResult ──► spill.SpillStore   (T1.x)
tool_execution ──ask──► user_questions ──await──► routes /api/question/answer   (Q1-Q4)
tool_execution ──► subagents.run_subagent ──► agent_runs(child) ──► publish(subagent_done)   (B2/B4)
llm_core ──run_hooks──► [user_prompt_submit, pre_tool_use, post_tool_use, post_response=middleman]   (H2)
agent_loop.deliverables ──► present event ──► chatRenderer.buildDeliverableCard   (H1/H3)
```

## 11. Build order (start here)

> **Implementation status — ALL CLUSTERS ✅ IMPLEMENTED + TESTED** (this session).
>
> - **Layer 0 (F0.1–F0.6)**: `src/stream_util.py`, `src/turn_inbox.py`, agent_runs v2
>   (publish/owner/on_finish/list_active), goal keying unification + creation path, settings.
> - **Cluster A (T1.1–T1.6)**: `src/tool_result.py`, `src/spill.py` (SpillStore +
>   spill_or_truncate wired into bash/python/read_file), `TOOL_TIMEOUTS` pool deadlines +
>   `tool_timeout` event, repeat-tool reminder, `llm_retry` retry policy in the fallback chain.
> - **Cluster B (Q1–Q4)**: `src/user_questions.py` registry (root-only, timeout, marker),
>   `ask_user_question` tool + loop await with heartbeats + `can_ask_user` flag,
>   `routes/question_routes.py` (GET pending / POST answer) registered in app.py.
> - **Cluster C (I1, G1–G4)**: queue-don't-cancel in `chat_stream` + turn-inbox factory,
>   `src/goal_driver.py` (idle-gated rounds, detached runs, goal_round/goal_update events,
>   boot-resume daemon, 3-error block), `/api/chat/active` + queued counts.
> - **Cluster D (B1–B4)**: `POST /api/session/{sid}/fork`, `src/subagents.py`
>   (spawn/fork, one-shot/continuable, depth cap 3, settlement `subagent_done` notices),
>   subagent/send_message/list_agents/interrupt_agent/job_output/job_kill tools.
> - **Cluster E (H1–H3)**: `src/hooks.py` hook-point seam (USER_PROMPT_SUBMIT /
>   PRE_TOOL_USE / POST_TOOL_USE / POST_RESPONSE=middleman), `present` events +
>   deliverable registry.
> - **Cluster F (S1–S5)**: agent_runs buffer cap, `docs/heartbeat-audit.md`,
>   security pass (`/api/chat/active` owner filter, subagent disabled-tools inheritance),
>   `tests/test_soak_concurrency.py`, `docs/restart-durability.md`.
> - **Cluster V (V1–V4)**: `docs/voice-chat-bridge.md`, turn-type matrix + incognito guard
>   (`voice_memory_stratum` helper), `tests/test_voice_chat_bridge.py`, voice modules
>   verified untouched (empty git diff).
> - **Cluster L (L1–L6)**: `src/legal/` — sinalevi.py (POST-sim + site:-fallback),
>   corteidh.py (catalog/search/case), corpus.py (deduped index, docfind-style grep,
>   on-demand fetch_pdf, opt-in bulk), 4 legal agent tools, offline tests.
>
> ~150 new/updated tests across 17 test files, all green. App boots cleanly (`import app`).
> Full-suite result: **1465 passed / 10 failed** — the 10 failures are EXACTLY the
> pre-existing baseline set (verified via `git stash` before/after: companion_pairing,
> cookbook_dependency_completion, document_close ×3, service_search_provider_guards,
> skill_index_prompt_injection, sqlite_foreign_keys, tool_path_confinement,
> topic_analyzer). Zero regressions from the port.
>
> **FRONTEND ALSO IMPLEMENTED** (this session): chat.js handles all 10 new SSE events
> (ask_user/subagent_started/subagent_done/turn_queued/tool_timeout/llm_retry/present/
> goal_update/goal_round/run_buffer_truncated); new `static/js/questionCard.js` (render,
> answer POST, refresh restore on session switch); goalCard live refresh on goal events;
> present → `buildDeliverableCard`; sidebar working dots via `/api/chat/active`
> (sessions.js `_refreshRunDots`, 5s poll); live per-message duration ticker frozen at
> `metrics.response_time` (`setMsgDuration` in chatRenderer.js); copy cooldown guard;
> branch button now functional (existing `forkFrom` targets the new B1 fork route);
> CSS for question card + working dots + msg-duration. Pinned by
> `tests/test_port_frontend_js.py` (22 tests, all green; node syntax checks pass).
>
> Remaining known debt (pre-existing, out of port scope): the 10 order-dependent
> test-pollution failures above (module-level `sys.modules` stubs without teardown
> across several legacy test files). Attempted fixes showed they can shift the failure
> set, so they need a dedicated test-hygiene pass with binary-search per file.
>
> **TEST-HYGIENE PASS — DONE. FULL SUITE GREEN: 1476 passed / 1 skipped / 0 failed**
> (stable across two consecutive runs). Root causes fixed:
> 1. `test_companion_pairing` — module-level `sys.modules["core.database"]` overwrite
>    poisoned every later-collected module; now installed only per-test via an autouse
>    fixture that restores the real module (+ parent-package detach) in teardown.
> 2. `test_cookbook_dependency_completion_regression` — stale JS string pins; updated to
>    the current reconciliation branch.
> 3. `test_document_close_clears_active_route` — victim of #1 (fixed by #1).
> 4. `test_service_search_provider_guards` — fake `_Response` lacked `status_code`
>    (the real fallback checks 202); added.
> 5. `test_skill_index_prompt_injection` — obsolete: the skill INDEX block was removed
>    entirely from the chat context (stronger than the untrusted-wrap the test demanded);
>    test re-pinned to the removal invariant.
> 6. `test_sqlite_foreign_keys` / `test_topic_analyzer` — victims of #1/#7 (fixed).
> 7. `test_task_scheduler_session_delivery` — `test_null_owner_gates` leaked MagicMock
>    attributes onto the REAL core.database at runtime; added an autouse restore fixture;
>    also re-binds task_scheduler against the real DB in the test.
> 8. `test_tool_path_confinement::test_allows_project_data` — policy drift: the documented
>    contract ("paths under project data/ and /tmp are allowed") was broken when the bare
>    `DATA_DIR` root dropped out of `_tool_path_roots`; restored (sensitive-path deny list
>    still applies under data/).
> Also hardened `test_provider_endpoints`/`test_task_scheduler_cancel` (re-emerging
> order victims of the pairing stub leak) via the same per-test stub discipline.

```
F0.1 → F0.2 → F0.3 → F0.4 → F0.5 → F0.6          (Layer 0, ~16 h) ✅ DONE
   ↓
T1.1 → T1.2 → T1.3 → T1.4 → T1.5 → T1.6          (Cluster A, ~18 h)
   ↓
Q1 → Q2 → Q3 → Q4                                  (Cluster B, ~14 h)
   ↓
I1 → G1 → G2 → G3 → G4                             (Cluster C, ~18 h)
   ↓
B1 → B2 → B3 → B4                                  (Cluster D, ~16 h)
   ↓
H1 → H2 → H3                                       (Cluster E, ~10 h)
   ↓
S1 → S2 → S3 → S4 → S5                             (Cluster F, ~13 h)
```

```
V1 → V2 → V3 → V4                                   (Cluster V, ~10 h — constraint; build ANY time,
                                                     parallel, but land BEFORE Cluster C/D ship)
L1 → L2 → L3 → L4 → L5 → L6                          (Cluster L, ~27 h — independent of the DSH
                                                     critical path; can run in parallel with any layer)
```

Total ≈ **145 hours (~3.5–4 focused weeks)** for the backend (105 h DSH port + 10 h voice
constraint + 27 h legal corpus + slack). Parallelizable: Cluster A can
start once F0.1/F0.2 land (T1.3/T1.4/T1.5 are independent of T1.1 once the contract exists);
Cluster E can start after F0.2 without waiting for C/D; Clusters V and L are fully independent
of the critical path — V is a constraint that must be *verified* before C/D ship, L can land
whenever (its tools must consume T1.x contracts once Cluster A exists).

**Critical path** (what everything else waits on): F0.1 heartbeats → F0.2 publish/finish-callbacks
→ F0.6 turn inbox → I1 queue semantics → G2 idle-gating → G3 rounds. The ask-user seam (Q1)
and the tool contract (T1.1) are the two most-shared consumers; build them before their
dependent clusters, not after.

## 12. Test matrix (one row per component, pytest unless noted)

| Component | Test |
|---|---|
| F0.1 | 40 s await → ≥2 heartbeats + value |
| F0.2 | publish/replay/owner fan-out; finish-callback containment |
| F0.3 | `finish_goal` and card read the same file; injection only in own session |
| F0.4 | create via API and via tool → same goal; 409 on second active |
| F0.5 | settings honored end-to-end |
| F0.6 | enqueue→finish→start_next exactly once; restart loses run, keeps message |
| T1.1 | no raw dicts reach result assembly |
| T1.2 | hung bash cut at deadline; `tool_timeout` emitted |
| T1.3 | concurrent saves → distinct files; owner-scoped dirs |
| T1.4 | 3rd identical call → reminder in round 4 |
| T1.5 | flaky endpoint → `llm_retry` then `fallback` |
| T1.6 | 80k output → spill file; model reads offsets; no `[Truncated]` |
| Q1 | DELEGATED_CALLER; ALREADY_PENDING; timeout auto-resolve; marker survives refresh |
| Q2 | schema exposed; disabled in incognito |
| Q3 | root asks + resumes same call; bg_monitor gets DELEGATED_CALLER |
| Q4 | cross-device answer resolves; answer lands in history |
| I1 | two sends → turn 1 completes, turn 2 auto-starts |
| G1 | create→round within 5 s; boot resume |
| G2 | user message preempts round; paused never resumes alone |
| G3 | round runs detached; counter advances; no self-ping |
| G4 | inventory correct for streaming/queued/idle |
| B1 | fork seeds history; owner isolation |
| B2 | parallel siblings; depth cap; timeout → errored result |
| B3 | list/message/interrupt scoped to owner |
| B4 | continuable settlement notice lands in parent stream |
| H1 | 2 files → 1 `present` → 2 cards |
| H2 | middleman suite green through hooks; test hook sees all prompts |
| H3 | artifact metadata never in model context |
| S1 | buffer cap + head/tail retention |
| S2 | CI: 60 s await → ≥3 heartbeats |
| S3 | cross-owner 401/404; child inherits disabled tools |
| S4 | 3-session soak, no task leaks |
| S5 | doc exists and matches behavior |
| V1 | bridge doc matches code; injection points cite it |
| V2 | matrix enforced: child/incognito have no voice stratum; goal rounds do |
| V3 | bridge regression green offline (mocked memory + settings) |
| V4 | voice modules untouched (`git diff` = docs/comments only) |
| L1 | `theme="ambiente"` fixture returns norms; site:-fallback works; rate limit held |
| L2 | catalog + case fixtures; parse failure → structured error |
| L3 | no duplicate index rows; bulk resume from checkpoint; owner-scoped PDFs |
| L4 | agent runs legal query end-to-end and cites the primary source |
| L5 | `docfind` finds an article in a corpus PDF in <1 s |
| L6 | `pytest -k legal` green offline in <10 s |
