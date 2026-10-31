# Roadmap / Help Wanted

Odysseus is on a voyage, but not home yet. It works great for me (lol), but this is ship is moving fast and feedback/help would be appreciated! (I dont know what I'm doing hlep).

If you see weird CSS, strange layout behavior, or a suspiciously murky corner of
the codebase, you are probably right to stay away.

## Harness (shipped — DeepSeek-Harness port)

Done and tested (architecture in `docs/harness-port.md`, plan in the repo history):

- Continuous **goals** with harness-driven auto-continuing rounds (idle-gated,
  boot-resume, blocked-after-3), owner-keyed goal state + goal card.
- **Background turns + message queue**: closing the tab never kills a turn;
  new messages queue behind the live one instead of cancelling it
  (`/api/chat/resume`, `/api/chat/active`, sidebar working dots).
- **Ask-user mid-task** (`ask_user_question` tool → question card → answer
  resumes the exact turn; root-only, 10-min timeout).
- **Subagents & branching**: fork a chat into a child session, spawn children,
  steer them (`send_message`/`list_agents`/`interrupt_agent`), depth cap 3.
- **No-truncation spill**: oversized tool output goes to disk with a locator
  (`data/spills/`), never silently cut.
- **Tool guardrails**: per-call deadlines (`tool_timeout`), repeat-call
  reminders, `llm_retry` retry policy + model fallback chain.
- **Hook points** (the response middleman was removed 2026-10-31 — it rewrote answers and caused hallucinations);
  `user_prompt_submit` / `pre_tool_use` / `post_tool_use` / `post_response`.
- **Legal corpus** (SINALEVI + Corte IDH): query-first adapters + tools +
  opt-in bulk inventory (`docs/legal-corpus.md`).
- **Voice mode untouched**; two-way voice↔chat context bridge documented and
  regression-tested (`docs/voice-chat-bridge.md`).
- Full test suite green (1476 passed / 1 skipped).

Remaining (small): i18n for the new UI strings, cross-process run resume,
a workflow-tool equivalent, and the UI niceties tracked in the commit log.

## Long-horizon autonomy (shipped)

- Requests that need sustained work are detected and run as a goal on their
  own (`src/long_horizon.py`, settings `auto_goal_mode` / `auto_goal_threshold`);
  a goal running in the chat is steered by new messages instead of replaced.
- Goal rounds and autopilot turns stream visibly: self-messages, live thinking,
  commands and their output; `run_javascript` works inside them. See
  `docs/long-horizon.md`.

## Intel Board (shipped)

- **Case Files** (`src/casefiles.py`, `case_file` tool, `/api/cases`, `/cases`):
  organized per-investigation store with deduped entities, sourced findings,
  relationships, next steps, pivot suggestions, cross-case search and markdown
  dossier export.
- **Watchlist** (`src/watchlist.py`, `watch_target` tool, `/api/watchlist`):
  SSRF-guarded page-change monitoring with keyword alerts and backoff; the
  **Intel Watchlist** scheduled task (every 30 min) notifies on changes and
  files them into the linked case. Daily brief includes cases + watchlist.
- **Feed watches** (`src/feeds.py`): RSS 2.0/1.0, Atom and JSON feeds are
  auto-detected on any watch (`kind: auto|page|feed`). Feed watches alert on
  *new items* (title + link) instead of a line diff, keywords match item
  title/summary, and each new item is filed into the linked case as its own
  sourced finding. DOCTYPE/ENTITY-bearing XML is refused.
- **Link graph** (`CaseStore.graph`, `GET /api/cases/{id}/graph`): SVG entity
  graph on `/cases` from typed relationships + finding co-mentions, with hubs,
  unconnected selectors, node focus, and a node cap for big cases.
- **Case ↔ goal binding** (`src/case_goal.py`, `case_file op=drive|release`,
  `POST /api/cases/{id}/drive`, "Drive with goal" on `/cases`): a goal works a
  case's open steps round after round — record findings, close steps, queue
  pivots, finish when resolved — with the case text fenced as untrusted data.
- **Cold-case nudges** (`src/case_nudges.py`, `check_stale_cases` scheduled
  task "Intel Cold Cases", daily brief section): open cases idle 3+ days with
  no goal driving them get one concrete next move (drive with goal / top
  pivot), nudged once per quiet spell.
- **Intel Board in the main app** (`src/intel_status.py`,
  `GET /api/intel/status`, `static/js/intelLink.js`): "Intel Board" in the
  Tools sidebar + icon rail and a `/cases` (`/intel`) slash command, with a
  badge counting what wants attention — cases going cold, watches changed in
  the last 24h, watches failing 3+ times — and a tooltip summary (open cases,
  open steps, the case a goal is driving). Hideable in Appearance; degrades
  to a plain link if the status call fails.
- **Cross-case overlap** (`CaseStore.overlaps`, `case_file op=overlap`,
  `GET /api/cases/{id}/overlap`, `also_in` on graph nodes): selectors shared
  with your other cases (canonical keys + aliases, owner-scoped) are ringed in
  the graph with jump-to-case buttons and a "Shared with other cases" row;
  recording a selector another case already holds adds a `Cross-case:` note
  to the agent's tool result.
- Next: one-click "Drive with goal" from a cold-case notification; surface
  new cross-case hits in the daily brief.

## Jarvis UI (in progress)

`jarvis` — a calm holographic-HUD theme — is now the default preset
(users with a saved theme keep theirs). Foundation shipped: tokens, canvas,
global primitives in `static/css/jarvis.css`, scoped to
`:root[data-odx-theme="jarvis"]`; the "drawn in light" Tron layer
(`static/css/tron.css`) now covers shell, chat, composer, modals, settings,
research/compare, login, notes/tasks/calendar, the document editor +
library, and email (inbox, reader, compose). Next areas, one per pass:
cookbook → landing → /cases → mobile. Spec + log: `docs/JARVIS_UI.md`.

## High Priority

- SQUASH BUGS
- Fresh install smoke tests on Linux, macOS, and Windows. Docker, native Python,
  and WSL all need coverage.

- Integration audit: do integrations even work? Confirm what works, what needs setup docs, and what should be removed or hidden. 
- Self-host troubleshooting cookbook. Document the weird 30-second fixes that otherwise become 30-minute searches: Dovecot cleartext auth for local stacks, ntfy Android Instant Delivery for non-ntfy.sh servers, clipboard limits on plain-HTTP Tailscale URLs, Radicale collection URLs, and similar traps.
- Cookbook reliability on other computers. This is probably the area most likely to need work across different machines, GPUs, drivers, shells, and Python environments.
- Cookbook SGLang support across platforms. Make sure SGLang setup/serve works
  predictably on Linux, Windows/WSL, macOS where possible, Docker, and common
  NVIDIA/AMD hardware paths.
- Deep Research model presets by hardware. Recommend approved model/parameter
  profiles for small, medium, and large local setups so people with different
  hardware can use Deep Research without guessing. Surface this either in Deep
  Research settings or as a Cookbook scan/dropdown suggestion.
- Cookbook model scan/download ranking. Prioritize newer architectures and
  better hardware-fit models instead of scoring everything almost the same.
  Ranking should account for architecture age, quant format, VRAM/RAM fit,
  backend support, vision/mmproj requirements, and likely serve reliability.
- Cookbook error feedback and logging. Failed downloads, dependency installs,
  preflights, and serve jobs should show the actual command/output/error in the
  UI, with copyable logs and clear next steps instead of just "crashed".
- Agent prompt/context bloat. Agent mode is too heavy for smaller local models:
  tool schemas, skills, memory, documents, and instructions can eat the context
  before the user request really starts. We need slimmer prompts, better tool
  selection, smaller default tool sets, and clearer guidance for models with
  4k/8k/16k context windows.
- Skill/tool prompt-injection audit. User-editable skills, notes, documents,
  fetched pages, and memories should be treated as untrusted data. Keep testing
  whether models follow malicious instructions from those surfaces.
- Better degraded-state reporting for ChromaDB, SearXNG, email, ntfy, and provider probes.
- Email performance audit. Fetching, searching, opening, deleting, and sending
  email can feel slow, especially over IMAP/SMTP providers with high latency.
  Need someone who knows mail performance to profile the current flow, identify
  whether the bottleneck is IMAP folder select/fetch, cache invalidation,
  attachment/body loading, SMTP handshakes, or frontend refresh behavior, then
  propose safer caching/prefetch/batching without breaking multi-account state.
- Provider setup/probing audit for Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, and DeepSeek.

## Refactor Targets
- CSS cleanup. `static/style.css` basically Calypso's island atm.
- Tour core helper. The onboarding tours have too much copy-pasted scaffolding; promote a shared `tour-core.js` helper before adding more tours.
- Modal/window positioning cleanup. Some window controls have improved, but the
  underlying popup/dropdown/fixed-position behavior is still too fragile.
- Mobile media override discoverability. A lot of "CSS did not move" bugs are mobile `@media` overrides of the same selector; comments or linting around desktop/mobile paired rules would help.
- Dead code pass for old routes, stale feature flags, and unused UI states.

## Frontend

- Expand the Editor for quicker, more robust everyday use. Better file/document
  handling, smoother window behavior, clearer save/export flows, stronger image
  editing affordances, and fewer brittle edge cases.
- Better AI integration for Notes and Todos. Notes should be easier for the
  agent to read, update, summarize, and turn into actions. Todos should be
  assignable to an agent from the UI, possibly through a button, task action,
  or dedicated skill/tool flow.
- Mobile gallery/editor polish. Easier to launch/download inpaint model or any missing pieces.
- Accessibility pass: keyboard navigation, focus states, contrast, reduced motion.
- Improve empty states and error messages on fresh installs.
- Tighten first-run setup, hints, and tours so they do not repeat or fight each other.
- Vendor CDN assets eventually for a more fully self-hosted/offline mode.

## Backend

- More tests around endpoint probing and provider setup.
- Better task scheduler defaults and visibility.
- Backup/restore guide and helper flow for `data/`.
- Security hardening around admin-only tools and clear docs for their risk.

## Not The Focus Right Now

I prob shouldnt add more themes.
