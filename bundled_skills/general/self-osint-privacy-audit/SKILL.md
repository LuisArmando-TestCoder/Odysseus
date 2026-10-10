---
name: self-osint-privacy-audit
description: Self-directed OSINT privacy audit — enumerate and assess the user's own public footprint, then produce a structured exposure report with remediation steps.
version: 1.0.0
category: general
tags: [osint, privacy, sherlock, self-audit, exposure-report]
status: published
confidence: 0.95
source: bundled
---

## When to Use

The user wants to see how publicly exposed THEY are — their own usernames,
handles, names, emails, or sites — so they can lock down or remove what they
don't want public. This is a legitimate self-directed privacy audit, not
profiling of a third party. If the request is about someone else, do not run
this; ask the user to confirm it is their own footprint.

## Inputs you need (ask once, up front, if not already given)

- Usernames / handles (the most important — Sherlock keys off these)
- Real name variants (for search-engine cross-referencing)
- Emails, personal sites, or known accounts

If the user already listed identifiers, use them verbatim — do not ask again.

## Procedure

1. **Enumerate usernames with Sherlock.** For each handle the user gave, run
   `sherlock <handle>` (or the Python module `sherlock.sherlock`). Capture the
   output. Treat every hit as a CANDIDATE — Sherlock flags a site when it does
   not show a "not found" page, so false positives are common.
2. **Verify candidates.** For each hit, fetch the profile URL directly
   (`web_fetch`) and confirm the account actually belongs to the user (matching
   display name, avatar, bio, or content). Drop anything that is a different
   person or a parked/empty page.
3. **Cross-reference the real name.** Search the name variants on the configured
   search providers. Note only results that clearly match the user.
4. **Do NOT** launch a remote-debugging Chrome profile, install websocket/CDP
   helpers, or drive bot-walled pages for a self-audit. Those are for
   investigative research on third parties. If a source blocks you, note it and
   move on — do not spend time bypassing walls.
5. **Produce the exposure report** (see format below). This is the deliverable.

## Exposure report format

Write the report as a markdown document (and offer to save it to the editor):

```
# OSINT Self-Audit — <name or handles>

## Confirmed public accounts
| Handle | Site | URL | What it reveals |
|---|---|---|---|
| ... | ... | ... | ... |

## Unverified / possible matches
- <handle> on <site> — could not confirm it is you

## Name search
- <what the real-name search surfaced, if anything>

## Exposure summary
- <2-4 sentences: how findable you are, what is most sensitive>

## Recommended actions
- [ ] <concrete step, e.g. "set GitHub profile to private", "remove old TikTok bio">
```

## Rules

- Only public, already-available information. Never attempt to access private
  accounts, guess credentials, or use the user's logged-in sessions.
- If a handle returns nothing, say so plainly — do not fabricate results.
- Keep the report factual; flag anything sensitive (phone, address, employer)
  so the user can decide what to remove.