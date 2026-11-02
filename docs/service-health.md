# Service Health

Odysseus depends on services that can quietly disappear: SearXNG behind web
search, ChromaDB behind document/memory recall, the ntfy server that carries
reminders, your mail servers, and its own data folder. When one goes away the
app keeps answering — just worse — and reminders silently stop arriving.

**Service Health** (scheduled task `check_services`, every 30 minutes) checks
them and tells you when something breaks, and again when it is fixed.

## What is checked

| Service | When | Check |
|---|---|---|
| Storage (database + data folder) | always | `src/readiness.py` — DB answers `SELECT 1`, `data/` is writable |
| Web search | provider `searxng` | HTTP GET of `search_url` (or `SEARXNG_INSTANCE`); any answer below 500 counts as up |
| Web search | keyed providers (Brave, Tavily, …) | the API key is set |
| Vector store (ChromaDB) | `chromadb` installed | TCP connect to `CHROMADB_HOST:CHROMADB_PORT` |
| Reminders (ntfy) | `reminder_channel = ntfy` | an enabled ntfy integration exists and `GET <base>/v1/health` answers |
| Email | each of *your* enabled accounts | TCP connect to the IMAP and SMTP host/port |

Search disabled, ChromaDB not installed or reminders not on ntfy → that
check is skipped, not reported.

## When you hear about it

- **Down**: after two failed checks in a row (one blip is ignored), once per
  outage, with the error and a fix, e.g.
  `DOWN Web search (SearXNG): http://localhost:8080: connection refused Fix: Start SearXNG …`
- **Recovered**: once, when a service you were told about answers again
  (`OK again Reminders (ntfy) (was down 2 hours)`).
- **Daily brief**: a "Degraded services" section lists anything down right
  now (read from the saved state; the brief does not probe).

Nothing new → the task finishes silently.

## Safety

- Probes are reachability checks: no request body is read and HTTP redirects
  are not followed. Every URL goes through `src/url_safety.check_outbound_url`
  first (link-local / metadata addresses refused).
- Usernames, passwords and query strings are stripped from URLs before they
  appear in a notification.
- Email accounts are owner-scoped (legacy null-owner rows only on single-user
  installs). State lives in `data/service_health/<owner>.json`.

## Tuning

Pause or reschedule "Service Health" in Tasks like any scheduled task. The
module (`src/service_health.py`) is stdlib-only with injectable probes;
tests in `tests/test_service_health.py`.
