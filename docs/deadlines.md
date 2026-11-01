# Deadline Watch

Notes can carry a due date. The note pinger (`action_ping_notes`) fires one
reminder in the minute the note is due. If that ping is missed — phone
asleep, app offline, notification swiped away — nothing mentioned the note
again. Deadline Watch closes that gap.

## What it does

`src/deadline_watch.py` sorts every open note with a due date into:

| Kind | When |
|---|---|
| overdue | the due time passed and the note is still open — not archived, and a checklist still has unchecked items. Only the last 30 days count; older ones are summarized as one "long overdue" line. |
| due soon | due within the next 24h (scheduled alert) / 48h (daily brief) |

Recurring notes that are past due are skipped: the Notes UI rolls their due
date forward on its own. Date-only due dates (`2026-11-03`) count as the end
of that day; naive timestamps are server-local, `Z`/offset ones are honored.

## Where it shows up

- **Daily brief** — a `Deadlines:` section listing overdue and next-48h notes.
- **Scheduled task "Deadline Watch"** (`check_deadlines`, 08:41 and 17:41) —
  notifies with new overdue / due-soon notes. Each note is reported once per
  state and due date: rescheduling a note, or a due-soon note going overdue,
  is reported again; nothing new is silent (`TaskNoop`). Dedup state lives in
  `data/deadline_alerts/<owner>.json` and only keeps notes still in play.

Data is owner-scoped; legacy null-owner notes are included only on
single-user installs (same rule as the daily brief). Titles are stripped of
control characters and capped before they reach a notification.

## Finishing a deadline

Check off the checklist items, archive the note, or give it a new due date.
