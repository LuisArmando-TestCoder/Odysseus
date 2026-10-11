---
name: create-and-run-script
description: Write a script to disk AND execute it in the same turn, then report the real output — never claim you "can't execute" code.
version: 1.0.0
category: general
tags: [scripting, bash, python, write-file, execute, run]
status: published
confidence: 0.95
source: bundled
---

## When to Use

The user asks you to "create a script", "write a script", "make a script",
"execute", "run it", or "do X for me" where X is a concrete coding task
(convert an image, rename files, parse data, fetch something, etc.). The user
expects the script to be BOTH written AND run, with the result brought back —
in one go, not a lecture about how they could run it themselves.

You have the tools to do this. You are running on the Odysseus server with
`bash`, `python`, and `write_file` available. There is no reason to say
"I can't execute scripts" or "I can't access files" — you can.

## Procedure

1. **Decide the language.** Python for most tasks (image/PDF/data/HTTP);
   bash for shell plumbing. If a library is needed (e.g. Pillow), install it
   first with `bash` (`pip install Pillow`) — or prefer stdlib-only code so
   nothing needs installing.

2. **Write the script to a file.** Use `write_file` with a real path under a
   temp dir, e.g. `/tmp/odysseus-task/script.py`. NEVER try to run multi-line
   Python through `python -c "..."` — shell quoting mangles newlines. Always
   write to a file first.

3. **Run it in the SAME turn.** Immediately follow the `write_file` block with
   a `bash` block: `python /tmp/odysseus-task/script.py` (or `bash script.sh`).
   Capture the output.

4. **Report the actual result.** Quote the real stdout/stderr and exit code.
   If it produced a file (e.g. a converted image), state the output path and
   offer to open it. If it failed, show the error and fix it — do not stop at
   "here's a script you can run".

## Pitfalls

- **Do NOT** reply with "I can't execute scripts directly" or "run this
  yourself". That is wrong — you have `bash` and `python`. The user is
  frustrated by exactly this.
- **Do NOT** treat the word "execute" as a dictionary lookup or a security
  prompt. It means "run the script you just wrote".
- **Do NOT** paste a script and stop. Writing + running + reporting is one
  atomic task.
- **Multi-line code** must go through `write_file` → `python <file>`, never
  `python -c`.
- **Long-running work** (installs, downloads, builds): put `#!bg` as the first
  line of the `bash` block so it runs in the background and you get re-invoked
  with the output instead of blocking.

## Safety guardrails

- Before running anything destructive (deletes, overwrites, `rm -rf`,
  `sudo`, network egress to unknown hosts), state what it will do and get
  explicit confirmation. A read-only transform (image conversion, parsing,
  formatting) is safe to run immediately.
- Write outputs to `/tmp/odysseus-task/` (or the user's stated target), never
  over the user's source files unless they asked for in-place edits.
- If a command needs a secret or credential, do not hardcode it — ask.

## Verification

- The `bash`/`python` block returned exit code 0 and you quoted its output.
- Any produced artifact exists at the path you reported (check with `ls` if
  unsure).
- The user received the result (or the output file path), not just the code.