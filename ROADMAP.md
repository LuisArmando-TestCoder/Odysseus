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
- **Goal Check-in** (`src/goal_watch.py`, scheduled task `check_goals`,
  hourly): notifies when your goal blocked (with the reason and how to
  unblock it), finished, stalled with no round for 6h, or sat paused for 2+
  days — once per spell. The daily brief shows the goal's state too.
- **Deadline Watch** (`src/deadline_watch.py`, scheduled task
  `check_deadlines`, 08:41 + 17:41): follows up on notes with a due date —
  ones that went past due while still open (the at-due-time ping may have
  been missed; recurring notes excluded) and ones due in the next 24h, each
  reported once per state and due date. The daily brief gets a "Deadlines"
  section (overdue + next 48h; 30+ day backlog summarized). See
  `docs/deadlines.md`.
- **Email deadlines** (`src/email_deadlines.py`): the Email Tags scan pulls
  "reply by Friday" / "deadline: Nov 20" / "due on the 10th" asks out of each
  new unread email (deterministic, resolved against the email's `Date:`),
  and Deadline Watch + the daily brief follow up on them like notes — due
  soon, then overdue while still unread. Bulk/spam mail skipped; owner-scoped.
- **Snooze / mark done** (`src/deadline_followups.py`, `/deadlines`,
  `GET /api/deadlines`, `POST /api/deadlines/{snooze|done|restore}`): a
  Deadlines page with ✓ Done (per due date; note/email untouched) and Snooze
  (tomorrow 8:00 / 3 days / 1 week) on every followed-up item, with Undo.
  Held items leave alerts and the daily brief ("(n snoozed)"); a snooze that
  runs out is reported again. Deadline Watch alerts end with a
  "Snooze or mark done" link. Owner-scoped; only your current deadlines.
- **Deadlines in the main app** (`deadline_followups.deadline_status`,
  `GET /api/deadlines/status`, `static/js/deadlineLink.js`): a "Deadlines"
  entry under Intel Board in the Tools sidebar + icon rail, and a
  `/deadlines` (`/due`, `/overdue`) slash command. The badge counts overdue
  items still being followed up (snoozed / done excluded); the tooltip adds
  what's due in 24h, how many are snoozed, and the next item. Follows the
  Intel Board entry's Appearance toggle; degrades to a plain link.
- Next: deadlines from calendar events; an LLM fallback for deadline phrasing the
  regexes miss; treat a sent reply (not just opening the email) as done.

## Service Health (shipped)

- **Service Health** (`src/service_health.py`, scheduled task
  `check_services`, every 30 min at :07/:37): probes web search (SearXNG
  reachability, or the API key for keyed providers), the vector store
  (ChromaDB, when installed), ntfy (when reminders are routed there), each of
  your enabled email accounts' IMAP/SMTP servers, and storage (database +
  writable data folder). Notifies once when a service has failed two checks
  in a row — with the error and a concrete fix — and once when it recovers;
  a single blip stays silent. The daily brief lists services that are down
  right now. URLs pass `check_outbound_url`, redirects are not followed,
  credentials/query strings are redacted. See `docs/service-health.md`.
- **Status in the app** (`health_status`/`live_status`,
  `routes/service_health_routes.py`, `static/js/serviceHealth.js`): a
  "Services" row in the Tools sidebar with a status dot (ok / flaky / down /
  stale — task not running — / unknown), an expandable panel listing what is
  wrong with the fix and last-check time, and a rate-limited "Check now"
  (live probes, not saved, so notifications stay with the task).
  `GET /api/health/services`, `POST /api/health/services/check`.
- **Model endpoint probes** (`llm_probes`/`probe_llm`): the LLM endpoints
  behind your Chat / Utility / Task / Research models and their fallbacks
  (Anthropic, OpenAI-compatible incl. vLLM/OpenRouter/Groq, native Ollama)
  get a model-list call with their own key — unreachable, 5xx and a
  rejected/missing API key (401/403) are outages in the same down/recovered
  flow and sidebar dot; a role pointing at a deleted or disabled endpoint is
  flagged with "pick a model in AI Defaults".
- Next: "snooze this service" for known outages; auto-switch a role to its
  first healthy fallback while its endpoint is down (with a notification).

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
- **New cross-case links in the daily brief** (`CaseStore.recent_overlaps`,
  `format_recent_overlaps`): selectors that became shared between your cases
  in the last 24h are listed as `value — Case A ↔ Case B` (closed-only links
  and old links re-mentioned are skipped).
- **Badge: cross-case links + case-goal state** (`intel_status`): selectors
  that became shared between your cases in the last 24h and a case-bound goal
  that is blocked now count in the Intel Board badge; the tooltip names the
  goal's state ("goal blocked on “X”: reason" / paused / driving) and new
  links, and the JSON adds `goal`, `new_links`, `links`.
- **One-click case goals** (`case_goal.resume_case_goal`,
  `POST /api/cases/{id}/resume`, `case_action_link`): a blocked or paused
  case goal shows *why it stopped* and a **Resume goal** button on `/cases`;
  Intel Cold Cases and Goal Check-in notifications end with a deep link
  (`/cases?case=…&do=drive|resume`, absolute when `app_public_url` is set)
  that opens the case with the action highlighted and focused — one click,
  never an automatic change on load. Resume is human-only like
  `/api/goal/resume`.
- **Answer the blocker** (`case_goal.answer_case_blocker`,
  `POST /api/cases/{id}/answer`): the "Why it stopped" panel on `/cases` has
  an answer box — the reply is posted into the goal's chat (naming the case
  and quoting the blocker, so the next round's steering block connects it),
  then the goal resumes. Human-only; refused while the chat is mid-reply and
  never resumes unless the answer landed. `&do=resume` deep links focus it.
- Next: show the
  goal's last round output (what it tried) under "Why it stopped".

## Jarvis UI (in progress)

`jarvis` — a calm holographic-HUD theme — is now the default preset
(users with a saved theme keep theirs). Foundation shipped: tokens, canvas,
global primitives in `static/css/jarvis.css`, scoped to
`:root[data-odx-theme="jarvis"]`; the "drawn in light" Tron layer
(`static/css/tron.css`) now covers shell, chat, composer, modals, settings,
research/compare, login, notes/tasks/calendar, the document editor +
library, email (inbox, reader, compose) and the cookbook (scan table,
serve list, dependencies, servers), the landing page
(`static/css/landing-jarvis.css`) and the `/cases` Intel Board
(`static/css/cases-jarvis.css`). Mobile pass part 1 (app shell at phone
width — compact voice core in the top bar, drawer scrim, legible welcome)
lives in `static/css/jarvis-mobile.css`; part 2 made message tables and
code blocks scroll instead of breaking mid-word and gave phone sheets a
tappable close key; part 3 gave sheet bodies 44px tabs/keys, 16px fields
(no iOS focus-zoom) and tab strips that fade where there is more to scroll.
The settings pass (tron.css §16) aligned every form row on one label
column, drew select chevrons, squared endpoint/user rows with mono status
chips (cyan live, amber attention) and stacks rows on phones. Mobile part 4
audited all twelve Settings panels on phones: the sheet body now fills the
sheet (no dead band), every field/key/toggle hits 44px on touch, and the
Search and Add Models rows wrap instead of running off the card. Mobile
part 5 made the chat overlays touchable: message actions, the ··· menu,
slash-command rows, the model picker and the undo toast all hit 44px on
phones, slash usage hints no longer clip, and the toast is a jarvis glass
strip. Mobile part 6 made the voice overlay touchable on phones (every
key, toggle and dock button 44px; the dock no longer squeezes) and fixed
the core's tap target in every theme (a global button height had left only
a 32px strip of the orb clickable). The whole-app contrast audit (run 18)
swept every text node and placeholder across all tool windows and settings
tabs at 1440 and 390: sub-AA secondary text (counters, empty states,
placeholders, micro-labels, Compare's off toggles) now uses the jarvis
faint tone at full opacity (≥ 5 : 1); under reduced motion no animation
runs in any view. Part 2 (run 19) ran the same sweep over a seeded agent
turn and the voice overlay: chat content and the voice overlay are now 0
sub-AA strings in jarvis (timestamps/metrics via `--color-muted-alt`,
source domains, voice `--vx-faint`/`--vx-dim`), ordered-list numbers are no
longer clipped, wrapped code lines align, and the voice chat feed / spoken
answers no longer read the sources box aloud ("2 Web sources1crt.sh…").
Part 3 (run 20) audited gradient-clipped headings (all pass) and rebuilt
the one left in the app, the welcome hero: reactor-reticle emblem with a
slow dashed "core" ring, tracked light-caps wordmark, circuit rule, square
Nobody key; Nobody mode turns the hero amber. The hero now docks 28px above
the empty-state composer on desktop (it slid under it at ≤768px tall), and
its sub/tip reach AA (2.9 / 2.2 → 8.3 / 5.7 : 1).
Part 4 (run 21) re-ran the probe with `pointer-events: none` overlays
hit-testable: no new failures; Calendar's empty state lifted to AA, and the
queue tray no longer sits under the model picker (header row, orange HUD
styling, 44px touch keys).
Run 22 gave every "working" signal in chat one HUD language — a small
rotating reactor core, a mono micro-label and a scan line replace the ASCII
▁▂▃ waves (pending turn, agent "Thinking", running tool) and the whirlpool
in the live thinking header; running tools get a ping ring and a drawn
"STDOUT · LIVE" readout; the live thinking timer reaches AA (3.2 → 5 : 1).
Under reduced motion the ASCII spinner now holds still in every theme.
Next: light-theme contrast report; streaming polish (document stream cursor,
research progress).
Spec + log: `docs/JARVIS_UI.md`.

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
  (Shipped: **Injection Guard** — `src/injection_guard.py`, `docs/injection-guard.md`:
  every tool result from web/email/calendar/OSINT/notes/MCP tools is scanned for
  overrides, role/tool/fence spoofing, secret-exfil asks, image-exfil links and
  hidden Unicode; control tokens and fence markers are defanged; flagged output
  gets a one-line "treat as data" warning and a note on the tool step. The
  `UNTRUSTED_SOURCE_DATA` fence can no longer be closed from inside.
  Shipped: **Action Gate** — `src/action_gate.py`: once any tool output in a
  reply is flagged, later side-effecting calls (send/delete email, bash/python/JS,
  write_file, api_call, vault reads, skill/memory/settings/endpoint/token writes,
  deletes, action-verb MCP tools) wait for a Block / Allow once / Allow for this
  reply question card; with nobody to ask (goals, scheduled tasks, subagents)
  they are refused and reported. Setting `injection_action_gate`.
  Also fixed: `stream_agent_loop` crashed with UnboundLocalError (`get_setting`)
  whenever a caller passed `max_rounds` (scheduled LLM tasks, bg monitor,
  skill tests).
  Shipped: **URL exfil gate** — on a tainted reply, `web_fetch` / `download_pdf` /
  `scrapebadger` / URL-bearing `mcp__*` calls are held when the URL smuggles data
  (secret-looking tokens, base64/hex blobs, 200+ char query strings, 12+ params,
  multi-line text, several emails, `user:pw@`); ordinary page/search URLs pass.
  Next: a live (streaming) badge for flagged tool steps and held calls; an eval
  harness that replays the payload corpus against small local models; also hold
  URLs that echo text the turn read from email/vault/memory (taint-aware DLP).)
- Better degraded-state reporting for ChromaDB, SearXNG, email, ntfy, and provider probes.
  (Background checks + notifications + sidebar status dot shipped as Service
  Health; still open: LLM provider probes.)
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
