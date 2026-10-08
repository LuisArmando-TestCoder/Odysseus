---
name: osint-research-via-cdp-browser-and-sherlock
description: OSINT Research via CDP Browser and Sherlock
version: 1.0.0
category: general
tags: [osint, cdp, sherlock, web-scraping, research-automation]
status: published
confidence: 0.95
source: bundled
---

## When to Use

Need to investigate a politically sensitive OSINT topic across bot-walled and public sources, recursively connecting entities, usernames, case numbers, and media reports.

## Procedure

1. Launch Chrome with remote debugging and a persistent cloned profile, e.g.: chrome --remote-debugging-port=9222 --user-data-dir=/path/to/profile
2. Connect to the CDP websocket, list page targets, select a target, and use Runtime.evaluate to navigate or extract document.body.innerText from bot-walled pages.
3. In parallel, fetch non-walled primary sources directly with web_fetch and install Sherlock if needed (pip install sherlock).
4. Run Sherlock against relevant usernames/aliases and cross-reference the results with names, IDs, case numbers, and entities found in articles.
5. When a tool or source is blocked, dead, or returning empty results, do not retry the same wall; pivot to an alternative source or method.
6. Append verified findings, source URLs, and relationships into a structured document as the investigation progresses. Use a remote-debugging Chrome profile for logged-in or bot-walled pages, web_fetch for open primary sources, Sherlock for username enumeration, and pivot immediately when a source blocks or errors.
