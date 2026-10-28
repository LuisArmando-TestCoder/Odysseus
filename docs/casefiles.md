# Case Files & Watchlist — the Intel Board

Odysseus keeps everything an investigation produces in **one organized place**
instead of scattering it across chats, notes and memory. Open the board at
**`/cases`**, or just ask the agent ("open a case on acme.io", "keep an eye on
this page and tell me when it mentions Doe").

## Case Files (`src/casefiles.py`)

One case per investigation / research thread. A case holds:

| Part | What it is |
|---|---|
| **Entities** | people, orgs, usernames, emails, phones, domains, URLs, IPs, locations, crypto wallets… Auto-typed, **normalized and deduped** (`Ops@Acme.io` and `ops@acme.io` are one entity with two sources). Corroboration raises confidence; aliases resolve to the same entity. |
| **Findings** | atomic facts with source URL + confidence (0–1). Selectors in the text (emails, handles, URLs, IPs, phones, domains, wallets) are **auto-extracted** into entities. Identical findings are de-duplicated. |
| **Relationships** | typed links between entities (`owns`, `alias_of`, `works_at`…), with evidence. |
| **Next steps** | the case's open loops. `pivots` derives concrete next moves from what is known (username → Sherlock, domain → RDAP/crt.sh/DNS/Wayback, email → breach check…), weakest-sourced entities first. |
| **Timeline** | append-only audit trail. |

Export any case as a **markdown dossier** (button on the board, `op=export`
for the agent, `GET /api/cases/{id}/export`).

Storage: `data/cases/<owner>/<case_id>.json`, written atomically, owner-scoped.

## Watchlist (`src/watchlist.py`)

Watch any URL on an interval (min 15 min). Each check fetches the page
(SSRF-guarded, private hosts blocked, redirects re-validated), reduces it to
visible text, ignores volatile tokens (clock times, CSRF/nonces, hashes), and
diffs against the last snapshot.

- **Keywords** turn a watch into an alert: it only fires when a keyword shows up
  in newly added text.
- **Linked case**: changes are filed as findings in the case, so new selectors
  that appear on the page land in the dossier automatically.
- **Failures back off** exponentially (up to 8× the interval) and the watch
  pauses itself after 10 consecutive failures.

- **Feeds**: RSS 2.0/1.0, Atom and JSON feeds (JSON Feed, or a JSON array /
  `{"items": [...]}` of objects) are auto-detected. A feed watch remembers item
  ids and alerts on **new items** (title + link) rather than a text diff;
  keywords match an item's title/summary; each new item becomes its own
  finding in the linked case, sourced to the item's link. Force a mode with
  `kind: "page"` or `kind: "feed"` (default `auto`). XML that declares a
  DOCTYPE/ENTITY is refused, and item links that are not http(s) are dropped.

The built-in scheduled task **Intel Watchlist** (`check_watchlist`, every 30
minutes) re-checks due watches and delivers changes as notifications. It is a
silent no-op when nothing is watched or nothing changed.

The **Daily brief** now includes open cases, one suggested next move per case,
and watchlist health.

## Agent tools

```case_file
{"op": "open", "title": "Acme recon", "objective": "Who operates acme.io?"}
```

ops: `open`, `list`, `get`, `update`, `add_entity`, `add_finding`, `link`,
`alias`, `add_step`, `done_step`, `pivots` (`queue: true` to add them as steps),
`search` (across all cases), `export`, `delete`. Cases can be referenced by id,
id prefix or title.

```watch_target
{"op": "add", "url": "https://example.gov/docket", "keywords": ["Doe"], "interval_minutes": 120, "case": "Doe matter"}
```

ops: `add` (`kind`: `auto`|`page`|`feed`), `list`, `check`, `pause`, `resume`, `remove`.

## REST API

| Method | Path | |
|---|---|---|
| GET/POST | `/api/cases` | list (`?status=open\|paused\|closed\|all`) / create |
| GET | `/api/cases/search?q=` | search all cases |
| GET/PATCH/DELETE | `/api/cases/{id}` | read (+pivots) / update / delete |
| POST | `/api/cases/{id}/entities` · `/findings` · `/links` · `/steps` | add (`/steps` with `{"pivots": true}` queues all pivots) |
| POST | `/api/cases/{id}/steps/{step}/done` | complete a step |
| GET | `/api/cases/{id}/export` | markdown dossier |
| GET/POST | `/api/watchlist` | list / add |
| DELETE | `/api/watchlist/{id}` | remove |
| POST | `/api/watchlist/{id}/active` · `/check` | pause/resume · check now |

## Responsible use

Case Files are for lawful research: due diligence, your own exposure audits,
journalism, security work on assets you're authorized to assess. Don't use them
to stalk or harass private individuals. Respect site terms and rate limits —
the watchlist's 15-minute floor and backoff exist for that reason.
