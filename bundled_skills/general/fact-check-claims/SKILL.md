---
name: fact-check-claims
description: Verify a public claim the way Doble Check (UCR, IFCN) does — exact quote, primary documents, timeline, recalculation, equivalent comparisons, the alluded party's version, explicit verdict — and surface what is off.
version: 1.0.0
category: general
tags: [fact-check, verification, disinformation, osint, costa-rica, doble-check]
status: published
confidence: 0.9
source: bundled
---

## When to Use
The user asks whether something is true, wants a statement, figure, post or speech checked, or an
investigation hits a claim that matters to the conclusion.

## Steps
1. `factcheck` action=claim with the EXACT words (+ speaker, date, the source they cite as `source_url`, `run: true`).
2. Pin the quote: video + minute, or document page.
3. Isolate what is verifiable (figures, dates, "never/always/any", comparisons, attributions); opinions and promises are out of scope.
4. For each red-flag signal the tool reports, test the candidate vice — don't assume it.
5. Fetch the PRIMARY document the tool points to (acta, law in SINALEVI, financial statement, audit, PISA technical note…) and read it whole.
6. Timeline from the FIRST document (award → contract → refrendo → start order → delivery → inauguration).
7. `factcheck` action=recalc for every figure (currency at the date's rate; gap %); compare equivalent groups.
8. Separate blended concepts (exists/available, cap/increase, build/rent, inaugurate/build).
9. Get the alluded party's version; record non-responses as facts, never as proof.
10. When several sources disagree, `factcheck` action=anomalies.
11. Verdict: **Verdadero / Verdad a medias / Engañoso / Falso / No verificable aún** — first what IS true, then the size of the distortion, then what was omitted (audits, CGR, Estado de la Nación), then the systemic context. Declare conflicts of interest; apply the same rigour to every side, including fact-checkers.
