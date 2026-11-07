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

## Deadlines that arrive by email

"Please confirm by Friday", "send it over before 5pm Monday",
"Deadline: Nov 20", "your invoice is due on the 10th of December" — asks
like these never become a note. When the **Email Tags** scheduled task is on
(`check_email_urgency`; it ships paused), each newly scanned unread email is
also run through `src/email_deadlines.py`:

- A deterministic extractor (no LLM) looks in the subject + first ~1.6k
  chars for an ask verb + `by` / `before` / `no later than` + a date,
  `deadline: <date>`, or `due (by|on) <date>`. Dates: today / tonight /
  tomorrow, EOD / COB, end of the week / month, weekdays (`next Friday`),
  `Nov 12`, `15 December`, ISO `2026-11-20`, with an optional time
  (`5pm`, `15:30`, `noon`). Ambiguous numeric dates (`11/12`) are ignored.
- Relative dates resolve against the email's own `Date:` header, so
  "tomorrow" means the day after it was sent. Deadlines before the send time
  or more than 120 days after it are ignored (contract terms, event dates).
- The result is stored on the email's triage verdict as `deadline`.

Deadline Watch and the daily brief then treat it like a note: "Due in 6
hours: Email from Bob: Contract — "reply by Friday"", once per state, and
overdue while the email is still unread. Spam, marketing, newsletters,
shopping and social mail are skipped. **An email drops off once you open
it** — Odysseus can't tell whether you replied, so opening it counts as
handled. Only the scan-state file for your own user is read
(`data/email_urgency_state_<owner>.json`, checked against its `owner`).

## Finishing a deadline

Check off the checklist items, archive the note, or give it a new due date.
For an email deadline, open (or reply to) the email.

## Snooze / mark done (`/deadlines`)

Sometimes you already know: the bill is paid outside Odysseus, the reply
went from your phone, the report will be late and that is fine. The
**Deadlines** page (`/deadlines`) lists everything Deadline Watch is following
up on — overdue and the next 7 days, notes and email asks — with one-click
actions on each:

- **✓ Done** — stop following up on this deadline. Keyed by item *and* due
  date, so giving the note a new due date brings it back. The note and the
  email are not changed.
- **Snooze · Tomorrow 8:00 / 3 days / 1 week** — hidden from alerts and the
  daily brief until then; when the snooze runs out it is reported again.
- **Follow up again** (and the toast's **Undo**) restores either.

Every Deadline Watch notification ends with `Snooze or mark done: <link>`
(absolute when `app_public_url` is set). Opening the link changes nothing;
`/deadlines?item=<id>` highlights and focuses one item. The daily brief notes
how many deadlines are snoozed so nothing disappears silently.

Answers live in `data/deadline_followups/<owner>.json` (checked against its
`owner`, capped at 500). The scheduled task drops answers for items that left
play (archived, re-dated, email opened) and expired snoozes. The API —
`GET /api/deadlines`, `POST /api/deadlines/{snooze|done|restore}` with
`{id, due_date[, preset]}` — only accepts an item that is one of *your*
current deadlines (404 otherwise). Code: `src/deadline_followups.py`,
`routes/deadline_routes.py`, `static/deadlines.html`.
