# Restart durability (S5)

What survives a server restart, and what does not — accepted scope, matching
DSH desktop's process-local model.

## Survives (durable)

| State | Store |
|---|---|
| Chat messages (user/assistant, including queued-turn messages and question answers) | SQLite `core/database.py` |
| Goals (versioned state, phase, roundsStarted, activation, session_id) | `data/goals/<owner>.json` (atomic writes) |
| Checkpoint trail + continuous-task queue | `data/agent_state/<session>.md` / `.queue.json` |
| Spilled tool outputs | `data/spills/<owner>/<session>/` |
| Background `#!bg` job logs / exit files | `src/bg_jobs.py` on-disk records |
| Pending-question marker (questions re-render after refresh) | `data/agent_state/<session>.question.json` |
| Research results | `data/...` per research handler |
| Voice memory | `services/voice/memory.py` disk store |

## Does NOT survive (in-memory, by design)

| State | Where | Recovery |
|---|---|---|
| In-flight stream generators (any detached run) | `agent_runs` | client re-sends / reconnect fails; message history is intact |
| Turn-inbox queue ORDER (messages themselves are persisted) | `turn_inbox` | queued messages appear in history; user re-sends |
| Pending-question Futures | `user_questions` | marker file re-renders the card; a new answer 404s (resend) |
| Subagent registry + wait futures | `subagents` | children are real sessions (DB-persisted); registry rebuilt on next use |
| Goal driver daemon bookkeeping (consecutive error counts) | `goal_driver` | daemon restarts on boot and re-arms active goals |

## Boot resume

`app.py` startup wires `agent_runs.on_finish(turn_inbox.drain_on_finish)` +
`goal_driver.on_run_finished` and starts the goal daemon, which scans
`data/goals/*.json` every 5 s and resumes `phase=active, activation=armed`
goals in their session — the durable continuation path.
