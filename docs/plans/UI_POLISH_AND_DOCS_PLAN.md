# UI_POLISH_AND_DOCS_PLAN — Odysseus (post-port)

Plan for the remaining UI niceties and the README/docs pass. Status column
updates as items land. Base: `/Users/luisarmandooriens/Desktop/projects/odysseus`.

## Part 1 — UI niceties

| ID | Item | Files | Acceptance |
|---|---|---|---|
| U1 | **Question-card polish**: entrance animation (slide/fade), `(Recommended)` badge on the recommended option, "✓ answered" state before removal, focus + `aria-live=polite` on the card, `Escape` = ignore, numbered questions when >1 | `static/js/questionCard.js`, `static/css/messages-hyper.css` | ask mid-task → card animates in, recommended option shows a badge, answering shows ✓ then clears; Escape closes; screen-reader announces |
| U2 | **Clickable subagent notes**: `subagent_started` note becomes a chip that opens the child chat (`json.data.session_id` → `sessionModule.selectSession`); `subagent_done` keeps a muted line | `static/js/chat.js`, CSS | clicking the started-note opens the child session; hover affordance (cursor + underline) |
| U3 | **Status-note consolidation**: shared `.status-note` base for `turn_queued`/`tool_timeout`/`llm_retry` (+ `subagent_done`), `role="status"`, consistent inline style | `static/js/chat.js`, `messages-hyper.css` | all three notes render with one class; ARIA role present |
| U4 | **Goal-card activity lifecycle**: `goal_round` → `working` (pulse), `goal_update` → `idle` (or `blocked` when phase=blocked); existing `goal-activity` dot already styles these states | `static/js/chat.js` (goal handlers), `static/js/goalCard.js` | during a round the card pulses; after it, idle; blocked shows red dot |
| U5 | **Deliverable-card polish**: extension-aware icon (pdf/xlsx/docx/png/other), entrance animation, existing badge kept | `static/js/chatRenderer.js` (`buildDeliverableCard`), CSS | a written report.pdf shows a PDF glyph and animates in with the `present` event |
| U6 | **Duration tooltip**: `.msg-duration` span gets `title` = exact seconds + `metrics.response_time` source note | `static/js/chatRenderer.js` (`setMsgDuration`) | hovering the frozen `3.2s` shows the exact value |
| U7 | **Working-dot polish**: ensure dots never shift row layout (fixed size — already), tooltip already wired; clear stale dots when `/api/chat/active` returns empty (poll already handles) | `static/js/sessions.js`, `sidebar-hyper.css` | running/queued states verified visually; no layout jitter |
| U8 | **Reduced motion + a11y**: all new animations wrapped for `prefers-reduced-motion`; question card + status notes keyboard/ARIA-safe | CSS files | with reduced-motion enabled, no new animations run |

## Part 2 — Docs & README

| ID | Item | Files | Acceptance |
|---|---|---|---|
| D1 | **README**: new "Harness features" block under Features (goals & auto-continuation, background turns + queue, ask-user, subagents/branching, no-truncation spill, retry/deadlines, response middleman + hooks, legal corpus SINALEVI + Corte IDH, voice bridge preserved); new Configuration rows for the 5 new harness settings (`agent_max_rounds`, `ask_user_timeout_seconds`, `subagent_timeout_seconds`, `agent_run_evict_grace_seconds`, `agent_run_buffer_max_events`) | `README.md` | features documented with one-liners; settings table complete |
| D2 | **ROADMAP**: mark the DSH-port items as shipped; add a "Harness (shipped)" section listing what landed + short remaining list (i18n of new strings, workflow tool, cross-process run resume) | `ROADMAP.md` | roadmap reflects reality |
| D3 | **MODULE_SUMMARY**: add `questionCard.js` entry (and note the new chat.js event handlers) | `static/js/MODULE_SUMMARY.md` | module list current |
| D4 | **New `docs/harness-port.md`**: architecture summary — cluster→file map, SSE event catalog (10 events), new/changed routes, settings keys, turn-type context matrix pointer, test file map | `docs/harness-port.md` | doc lets a new dev find any port piece from one page |
| D5 | **New `docs/legal-corpus.md`**: SINALEVI + Corte IDH adapters, agent tools, corpus layout (`data/legal/`), on-demand PDFs, opt-in `bulk_inventory`, politeness/rate limits, offline tests | `docs/legal-corpus.md` | usage + limits documented |

## Verification

- `node --check` on every touched JS file.
- Full pytest suite stays **1476 passed / 1 skipped / 0 failed**.
- `python -c "import app"` still boots.

---

## STATUS — ALL ITEMS IMPLEMENTED ✅ (this session)

- **U1–U8 done**: `questionCard.js` rewritten (entrance animation, `(Recommended)`
  badge, numbered questions, "✓ answered" state, `aria-live` + focus, Escape to
  dismiss); subagent notes are clickable chips opening the child chat; shared
  `.status-note` base + `role="status"`; goal-card activity lifecycle
  (`goal_round`→working pulse, `goal_update`→idle/blocked); deliverable cards
  get extension-aware icons + entrance animation; `.msg-duration` gets an exact
  tooltip; reduced-motion extended for all new animations (sidebar block already
  covered the run-dot).
- **D1–D5 done**: README "Harness" feature line + harness settings table;
  ROADMAP "Harness (shipped)" section; MODULE_SUMMARY `questionCard.js` entry;
  new `docs/harness-port.md` and `docs/legal-corpus.md`.
- **Verification**: JS syntax OK; `test_port_frontend_js.py` 22/22;
  full suite **1476 passed / 1 skipped / 0 failed**.
