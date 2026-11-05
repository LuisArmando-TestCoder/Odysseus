# Jarvis UI — design spec & progress log

The `jarvis` theme restyles Odysseus into a calm, cinematic holographic HUD.
It is the **default theme** for anyone who has not picked one; users with a
saved theme keep it. Built one verified slice per run.

## How it is wired

| Piece | Where |
|---|---|
| Preset colours (`bg/fg/panel/border/red` + `advanced`) | `THEMES.jarvis` in `static/js/theme.js` |
| Default theme / pattern / effect colour / intensity | `DEFAULT_THEME`, `THEME_DEFAULT_PATTERN` (`constellations`), `THEME_DEFAULT_EFFECT_COLOR`, `THEME_DEFAULT_INTENSITY` (0.35) |
| Theme name on the DOM | `<html data-odx-theme="…">` — set by the first head script in `static/index.html` (first paint; falls back to `jarvis` with its core colours when nothing is saved) and by `applyThemeName()` in `theme.js` (on `save()`, init, reset, server sync) |
| Phone-width shell | `static/css/jarvis-mobile.css`, linked after `tron.css` (same scoping) |
| Structural styling | `static/css/jarvis.css`, linked **last** in `index.html`. Every selector starts with `:root[data-odx-theme="jarvis"]`, so other presets and custom themes are untouched |

Keep the head-script fallback colours in sync with `THEMES.jarvis`.

## Design language

- **Canvas:** near-black blue `#060b14`; one overhead light source (arc-reactor
  cyan `#4fd8ff`, exposed as `--jv-cyan` = `--red`), a faint cool falloff at
  the floor. Amber `#ffb347` (`--jv-amber`) is reserved for alerts/attention;
  `--jv-danger` only for destructive actions.
- **Glow budget:** one knob, `--jv-glow-k` (set to 0 under `prefers-contrast:
  more`). Content is the light, not decoration.
- **Glass:** `.jv-glass` — translucent panel, 1px luminous hairline
  (`--jv-line`), faint top-lit inner gradient, soft drop shadow.
  `.jv-ticks` adds corner brackets — key surfaces only (composer, active
  modal), never on lists.
- **Texture:** 48px holo grid at ~5 % alpha + 1px/3px scanline veil at ~2 %,
  both inside `.chat-container::after`, radially masked so they read as depth.
- **Type:** Inter for UI (`--jv-ui`), Fira Code for data/labels (`--jv-mono`);
  micro-labels are mono, uppercase, 10.5px, `0.14em` tracking (`.jv-label`);
  tabular numbers globally.
- **Motion:** expo-out `--jv-ease`; `--jv-fast` 120ms / `--jv-base` 180ms /
  `--jv-slow` 240ms; `.jv-boot` panel reveal. All gated by
  `prefers-reduced-motion`; glass/texture removed under
  `prefers-reduced-transparency`; texture removed under `forced-colors`.
- **Accessibility:** text tokens ≥ 4.5:1 on canvas and panel; focus ring =
  2px canvas gap + 2px cyan (`--jv-focus`); 44px touch targets on mobile;
  no layout shift from any jarvis rule.
- **Token bridge:** jarvis re-points `--accent-primary`, `--accent-warm`,
  `--color-warning`, `--color-accent`, and cools the `--cin-*` aurora tokens
  from `cinematic-ai.css` to a single cyan source.

## Area order (one per run)

1. ✅ Foundation — preset, default, tokens, canvas, global primitives
2. ✅ Shell / sidebar (Tron pass)
3. ✅ Chat canvas + message bubbles (Tron "drawn" pass, `tron.css`)
4. ✅ Composer (Tron pass) — voice mode itself is out of scope (`voice-hyper.css`)
5. ✅ Modals / windows / dropdowns (Tron pass)
6. ✅ Settings (`tron.css` §16)
7. ✅ Notes / tasks / calendar
8. ✅ Documents / editor (+ library documents tab)
9. ✅ Email (inbox library, reader + pop-out readers, compose fields)
10. ✅ Cookbook / ✅ research / compare
11. ✅ Login (`auth-tron.css`) + ✅ landing (`static/landing.html` → `landing-jarvis.css`)
12. ✅ `/cases` Intel Board (`static/cases.html` → `cases-jarvis.css`)
13. Mobile pass — ✅ part 1 app shell (`jarvis-mobile.css`) · ✅ part 2 message tables/code + sheet close keys · ✅ part 3 sheet bodies on touch · ✅ part 4 settings panels on phones · ✅ part 5 chat overlays · ✅ part 6 voice overlay controls
14. Whole-app contrast / reduced-motion audit

## Progress log

### Run 1 — 2026-10-29 · Foundation
- Added `THEMES.jarvis` (+ default pattern `constellations` @ 0.35 intensity,
  cyan effect colour) and made it `DEFAULT_THEME`; Reset now returns to jarvis.
- Added `applyThemeName()` + `data-odx-theme` on `<html>` (first paint + every
  theme change).
- New `static/css/jarvis.css`: token layer, canvas (one light + holo grid +
  scanlines), focus/selection/scrollbar primitives, `.jv-label` / `.jv-glass`
  / `.jv-ticks` / `.jv-boot` utilities, a11y media gates.
- Verified with Playwright (1440×900, 390×844): jarvis (default and explicit),
  light and dark — light/dark pixel-identical apart from ambient animation.
  Theme picker shows the jarvis swatch active by default; switching to dark
  and back updates `data-odx-theme`.
- Known pre-existing (not jarvis): with `/api/*` mocked, an 8px unpainted
  strip shows above the chat column in headless Chromium in every theme.

### Run 2 — 2026-10-29 · Shell / sidebar ("blue Tron")
Direction from the user: *blue, Tron-like*. Recorded as part of the language:
tight 4px radii, a single 1px **neon edge** where the shell meets the canvas
(light-cycle trail, brightest mid-height), and **neon bars** (inset 2px cyan)
instead of filled pills for hover/active state.
- `.sidebar` / `.icon-rail`: dark glass with a cyan top-light, neon edge in
  `::after` (flips to the left edge for `.sidebar.right-side`).
- Brand: uppercase, 0.32em tracked wordmark in `--jv-cyan-hi` with a soft
  bloom, over a fading circuit rule.
- Section headers: mono micro-labels, cyan icons, gradient hairline rule.
- Rows: Inter 13px (14px on touch), dim → full text on hover with a neon bar;
  active session = cyan bar + gradient wash + hairline + soft glow; focus ring
  is an inset cyan hairline.
- Status: background-run dot and notification dots are **amber** (attention),
  queued runs dim cyan. User bar: neon top rule, ringed avatar, mono name.
- Touch: 44px rows / rail buttons; mobile drawer gets an opaque panel.
- A11y: reduced-transparency → solid panel; more-contrast → solid edge, no
  glow, full-strength row text.
- Note: inside jarvis, sidebar rows use Inter regardless of the font setting
  (the font setting still drives chat content).
- Verified at 1440×900 + 390×844 (drawer open) in jarvis and light; light is
  pixel-identical apart from ambient animation; hover state checked.

**Next:** chat canvas + message bubbles — HUD transcript: hairline-framed AI
turns with a mono role label + timestamp, user turns as right-aligned cyan
glass, Tron-style code blocks, welcome hero wordmark.

### Run 3 — 2026-10-29 · "Drawn in light" Tron pass (interactive session)
Direction from the user: *hypermodernize everything so it looks like a Tron UI —
every component drawn — except Advanced Voice Mode (already done).*
- New `static/css/tron.css`, linked right after `jarvis.css`, every selector
  under `:root[data-odx-theme="jarvis"]`. One frame recipe for every surface:
  the component keeps its box (nothing clipped, menus still escape) and a
  `::before` behind it carries fill + 1px light line + 45° chamfered corners
  whose diagonals are drawn + brighter ticks at both cuts. Knobs per surface:
  `--c` (cut), `--ln` (line), `--hi` (ticks), `--fill`.
- Bold move: **user turns are orange light, AI turns cyan** (Tron's two
  programs). Everything else cyan; amber stays attention.
- Covered: chat bubbles, code blocks/tables/quotes, thinking panel, agent
  threads + tool nodes (diamond dots), round separators, action groups,
  deep-dive header, edit summary + diff popovers, composer + send/queue button,
  queue tray, modals + headers, dropdowns/menus, settings nav + admin cards,
  inputs/selects focus glow, deep-research pane/jobs/chips, goal card, status
  notes, deliverable cards, sidebar rows squared.
- Login: `static/css/auth-tron.css` — void + horizon + perspective light-grid
  floor; chamfered card whose outline is an SVG drawn by `login.js`
  (`drawTronFrame`) and traced once on load; fixed inputs overflowing the card
  (`box-sizing: border-box`).
- Voice mode untouched (no `.vx` selectors).
- Verified headless at 1440×900 and 390×844 (chat, settings, deep research,
  login) + a radius sweep of open panels (no soft corners left outside voice).

**Next:** documents / editor, email, cookbook /
compare, landing, `/cases`, then a mobile pass — reuse the `tron.css` frame
recipe (add the surface to the selector list, tune `--c`).

### Run 4 — 2026-10-29 · Notes / tasks / calendar
New section 12 in `static/css/tron.css` (all under the jarvis scope).
- **Cards drawn, not filled:** note cards, task cards and the note editor
  (`.note-form`) get the chamfered frame (`--c` 9px / 7px / 12px, ticks on
  both cuts); pinned notes burn brighter, paused tasks dim their line.
- **Note colours = light:** `note-color-*` sets `--tint`, which drives the
  frame line, the ticks and a faint top wash — no more solid colour slabs.
- **Attention is amber:** an overdue reminder chip is amber (in jarvis
  `--red` is cyan, so the old "overdue" looked identical to a normal one);
  paused task badge amber, active badge `--jv-ok`.
- **Squared everything** inside `#notes-pane`, `.note-fullscreen-overlay`,
  `#tasks-modal`, `#calendar-modal` (native colour swatches excepted);
  checklist ticks are square, the pin is a diamond, event/filter dots are
  diamonds.
- **Notes pane:** solid dark glass with a lit inner edge, mono uppercase
  title (matches modal headers), mono filter chips with a lit underline
  when active; quick-add is a lit input slot (`inset 2px` cyan bar).
- **Calendar:** grid drawn in cyan hairlines, mono uppercase weekdays /
  title / view toggle (active = underline), today = cyan outline + top bar
  + square day chip, selected = dim outline; primary "New" is a chamfered
  cyan slab; quick-add row is the same lit slot as notes.
- Mobile: task meta + note filter chips fall back to Inter (mono is wider
  and collided); tag chips keep a ~32px touch area via an invisible
  `::after` on coarse pointers.
- Verified headless (1440×900, 390×844) — notes list, note editor, tasks,
  calendar month + week — in jarvis and light; light screenshots match
  the pre-change ones apart from ambient animation (clock, pulses).
  No console errors besides the blocked CDN fetches.

**Next:** documents / editor (`document.js`, `documentLibrary.js`).

### Run 5 — 2026-10-30 · Documents / editor + library
New section 13 in `static/css/tron.css` (all under the jarvis scope).
- **Docked console:** the doc pane is near-void glass with a faint overhead
  light; the drag divider is the neon seam (brightest mid-height, like the
  sidebar edge); collapse/hide handles squared.
- **Tab strip:** squared, Inter 12px, dim → ink on hover; active tab =
  cyan text + rising wash + glowing bottom bar. Version tags (`v3`) in tabs,
  header and library cards are squared mono chips (the library badge gets a
  `doclib-ver-badge` class hook in `documentLibrary.js` — no base styles).
- **Controls at full strength:** toolbar / header / footer icons were 35 %
  opacity ghosts (below 3:1); in jarvis they are `--tr-dim` at full opacity,
  hover = cyan + 2px light bar. Edit/preview and code/run toggles are a
  squared segment with the same underline for the active side.
- **Editor:** drawn gutter (mono tabular line numbers, hairline rule, faint
  cyan wash — the old 18 % opacity numbers were near invisible), cyan caret
  and selection. Find matches are amber (attention).
- **Preview reads like a briefing:** Inter 14px / 1.65, 76ch measure; H1 in
  cyan-hi over a fading light rule, H2 with a diamond bullet, cyan list
  markers, lit code slots + cyan-bar quotes (same as chat), mono uppercase
  table headers. CSV grid in hairlines with mono headers.
- **Footer:** path readout in mono, squared language picker, Copy/Export split
  as a squared cyan outline with mono uppercase label.
- **Semantics fixed:** diff accept/reject now `--jv-ok` / `--jv-danger`
  (in jarvis `--red` is cyan, so "reject" looked primary); library Delete
  is danger-red; the streaming "editing" indicator is amber.
- **Library (documents tab):** cards drawn with the chamfered frame (scoped
  to `#doclib-modal` — the email library reuses `.doclib-card` and is next
  run's job), lit preview slot on desktop, squared action buttons.
- Touch: toolbar buttons ≥ 40px wide, footer controls ≥ 40px tall on coarse
  pointers. More-contrast: no glows, full-strength gutter, solid card line.
- Verified headless (1440×900, 390×844) — markdown source, markdown preview,
  python source, library with an expanded card — in jarvis and light; light
  screenshots match the pre-change ones apart from ambient animation.
  Pre-existing, not jarvis: loading several docs back-to-back without
  waiting can leave the CSV preview visible over a markdown doc; hljs has
  no `csv` grammar (console error from the library preview).

**Next:** email (inbox library `#email-lib-modal`, reader, compose — the doc
pane's email mode shares `.doc-email-actions` / `.email-send-btn`).


### Run 6 — 2026-10-30 · Email
New section 14 in `static/css/tron.css` (all under the jarvis scope;
covers `#email-lib-modal`, `.email-reader-tab-modal`, `.email-window-modal`
and the doc pane's compose fields).
- **Inbox = signal log:** every message is a drawn card (documents-library
  chamfer, `--c` 8px; expanded 11px on a darker fill). **Unread is the warm
  light:** amber left edge + amber diamond (replaces the sender-hued dot,
  which read as decoration) + white semibold subject; the unread badge in the
  title bar is an amber outlined mono chip. Done mail dims its subject; the
  done check is `--jv-ok`.
- Meta line (sender · date) in mono tabular; account chips mono uppercase,
  active = light bar; inline search toggles (done/reminders/attachments) are
  bare icons that light up when on.
- **Reader = briefing:** header band with a faint top-light, mono `FROM / TO /
  CC` micro-labels, squared address chips (from-chip lit cyan). Action rack is
  squared keys with mono uppercase labels sized to fit — "REPLY ALL" was
  clipped at 48px; desktop keys are now auto-width (min 50px), mobile keeps
  the 48px grid.
- **Digits bug (jarvis-scoped fix):** the base `.email-reader-body` sets
  `font-variant-emoji: emoji`, which made 0-9 render from the colour-emoji
  font — dim and spaced ("case 1 4", "0 1:1 0"). Jarvis sets it to `normal`
  and uses Inter 13.5/1.62 for body copy. *Other themes still have this bug —
  worth fixing in `style.css` (one line) in a non-UI pass.*
- Attachments: mono header, chips as lit slots (cyan left bar). AI summary:
  lit slot with a cyan bar and mono header. Quote/signature folds: mono
  uppercase summary rows. Links cyan-hi; quotes cyan bar; `<mark>` amber.
- Thread bubbles follow chat: **yours in orange light, theirs in cyan**,
  squared, mono heads.
- Tags squared mono chips (semantic hues kept); urgent / reply-soon burn amber.
- Compose (doc pane): mono uppercase field labels, inputs as dark slots with a
  cyan inset bar on focus; Discard hovers danger-red. Mobile "New" FAB is a
  chamfered cyan slab.
- A11y: more-contrast → solid card line, no glows, full-strength done check;
  reduced-motion → static unread diamond; coarse pointers → 36px account chips.
- Verified headless (1440×900, 390×844) — inbox list, expanded reader, compose
  — in jarvis and light; light renders match the pre-change ones apart from
  ambient animation and the clock. Pre-existing with mocked APIs, not
  email: the first-run "Pro tip" card and an empty Environment Variables modal
  can pop over the canvas.

**Next:** cookbook (then landing → `/cases` → mobile pass).

### Run 7 — 2026-10-31 · Cookbook
New section 15 in `static/css/tron.css` (all under `#cookbook-modal`, jarvis
scope). The window and its `.admin-card` sections already had the frame
recipe; this pass draws what lives inside them.
- **Tabs** are mono micro-labels on a hairline; active = cyan-hi text, a rising
  wash and a glowing bottom bar (Inter fallback spacing on ≤640px).
- **Section headings** lose the shrink-wrapped underline: mono label led by a
  lit diamond.
- **Scan table = data grid:** solid header on a cyan rule, mono tabular
  numbers, hairline rows, hover/active row = cyan bar + wash, score in
  cyan-hi. FIT and SPEED columns widened in jarvis (mono is wider — "TOO
  TIGHT" ran into the logo and "62.0 t/s" into the score).
- **Fit is a signal scale:** perfect `--jv-ok` → good dim-ok → marginal amber →
  too tight `--jv-danger`. `_fitColors` paints too_tight with `--red` (cyan in
  jarvis), so it read as the best option. Rows now carry a `data-fit`
  attribute (`cookbook-hwfit.js`, no base styles) for the jarvis override.
- Toolbar keys (RAM/GPU/RESCAN/EDIT/SELECT) are squared mono keys; active =
  lit outline instead of a solid slab. Detected-hardware chips are a mono
  readout with a cyan tick. CTX readout tabular cyan-hi.
- **Model cards** (trending + cached) use the library chamfer; names read as
  ink (family hue stays on the logo + filter chips), meta mono tabular,
  "download stalled" is amber. HF links + serve-dir pill are lit mono slots.
- **Dependencies:** rows on a hairline with a left bar (green-ok when
  installed); tags mono outlined; Install = chamfered cyan outline;
  Installed = ok; a **missing system binary is amber** (attention).
- **Settings:** server entries as lit slots; status LEDs are diamonds — ok
  green, testing amber, **fail now danger-red** (was `--red` = cyan, so a dead
  server glowed like a healthy accent). "+ Add" squared cyan outline.
- Sidebar: the cookbook notification dot is amber, its error variant danger.
- Touch: 40px tabs/rows, 32px filter chips on coarse pointers; mobile chips
  fall back to Inter so they stay on one row. More-contrast: solid lines,
  no glows, full-strength dim text. Reduced motion: LEDs stop pulsing.
- Verified headless (1440×900, 390×844) — download/scan, expanded model row,
  serve, dependencies, settings — in jarvis and light; light renders match
  the pre-change ones apart from ambient animation (voice orb, LED pulse).

**Next:** landing (`static/landing.html`), then `/cases` → mobile pass.
Settings (area 6) only got the run-3 sweep — worth a dedicated pass later.

### Run 8 — 2026-10-31 · Landing (`static/landing.html`)
The landing is a standalone page (own inline tokens, no theme.js), so it gets
its own sheet: `static/css/landing-jarvis.css`, linked after the inline
`<style>`, every rule under `:root[data-odx-theme="jarvis"]`, which the page
now sets on `<html>`. Removing the attribute renders the original page
pixel-identical (verified). Fonts use private family names (`JV Inter`,
`JV Fira Code`, relative `../fonts/` URLs) so the un-themed page never picks
them up.
- **One canvas:** the slate/black alternating slabs are gone — near-black
  void, one overhead cyan light, the 48px holo grid + scanline veil (fixed,
  radially masked). Pages are divided by a seam of light; even pages get a
  faint top-light for rhythm. Hero ends on a glowing horizon line.
- **Re-pointed the page tokens** (`--accent` coral → cyan, `--gold` → amber,
  `--muted` → 9.6:1 dim text…) so inline styles in the markup follow.
- **Frame recipe, landing edition:** fill clipped to a 45° chamfer, light line
  + drawn diagonals + bright ticks in `::after` on top (so preview videos are
  framed too). The base rule sits in `:where()` so per-surface knobs
  (`--c`, `--tick`, `--fill`) win. Used on buttons, feature cards,
  testimonial card, terminal, preview panels, get-started card (big ticks =
  the one key surface), hamburger menu, arrows.
- **Hero:** tracked uppercase wordmark in cyan-hi beside the logo in a
  reactor ring (the page's one "core"); mono slogan; Inter 600 headline with a
  cyan→cyan-hi lit phrase. Primary CTA is a chamfered cyan slab (dark ink,
  11.8:1), secondary a drawn outline; both mono uppercase, 44px tall.
- Nav: dark glass + neon bottom edge brightest at centre; mono uppercase
  links with a growing underline; 44px hamburger.
- Testimonials: drawn card, amber stars, mono role; Polyphemus burns
  danger-red (line + quote). Dots are bars (active = longer, cyan) with a
  23px hit area.
- Features: lit icon slots (cyan inset bar), hover brightens the line.
  Terminal: mono uppercase title bar, cyan prompt, squared window keys
  (close = danger).
- Get started: codeblock as a lit slot that wraps on phones, pills as mono
  chips with a diamond. Footer mono on a seam.
- **Layout fixes (jarvis-scoped):** `.wrap` inside the flex-column pages
  shrink-wrapped to its content, so the preview strip was ~440px wide on
  desktop and **0px wide on phones** (three hairlines). `.wrap` now takes the
  full measure and stacked previews stretch. On phones the carousel arrows
  moved under the card (they covered the quote) and the avatar sits above
  the name.
- A11y: chamfered controls light a 2px frame on focus (clip-path would cut an
  outline); reduced-motion / reduced-transparency / more-contrast /
  forced-colors gates as in the app.
- Verified headless (1440×900, 390×844): full page, hero, feature hover,
  mobile menu, Polyphemus card, minimised terminal, keyboard focus; no
  horizontal overflow, no console errors (external testimonial images were
  blocked in the harness).
- Not done: `docs/index.html` is the GitHub Pages copy of the landing and has
  drifted (dot grid, extra sections). It does not load this sheet; port it
  when the two are reconciled.

**Next:** `/cases` Intel Board (`static/cases.html`), then the mobile pass;
settings (area 6) still deserves a dedicated pass.

### Run 9 — 2026-11-02 · `/cases` Intel Board
`static/cases.html` is standalone (own inline tokens, no theme.js), so — like
the landing — it gets its own sheet, `static/css/cases-jarvis.css`, linked
after the inline `<style>`; every rule under `:root[data-odx-theme="jarvis"]`.
Unlike the landing, the theme is **not** hard-coded: a nonce'd head script
copies the saved theme name from `localStorage['odysseus-theme']` onto
`<html>` exactly like `index.html` (nothing saved → `jarvis`). Users on any
other theme get the original page, pixel-identical (verified by image diff
at 1440×900 and 390×844, incl. the interaction state). Fonts use the private
`JV Inter` / `JV Fira Code` names with absolute `/static/fonts/` URLs (the page
is served at `/cases`).
- **Re-pointed the page tokens** (`--bg/--panel/--panel2/--border/--muted/
  --accent/--good/--warn/--bad/--chip/--mono`) so the JS-built markup and
  inline styles follow without touching the script.
- **Canvas + header:** void, overhead cyan light, 48px holo grid + scanline
  veil (fixed, masked). Header is dark glass with a neon bottom edge brightest
  at centre; "INTEL BOARD" tracked uppercase cyan-hi beside the page's one
  core (lit diamond in a ring, slow breathe; static under reduced motion);
  subtitle a mono micro-label behind a hairline; back link a 40px (44 touch)
  squared key.
- **Every card drawn** with the frame recipe (chamfer 10px, ticks on both
  cuts, `.jv-boot`-style reveal); the open case's header card is the key
  surface — brighter line, 14px cut, 22px ticks, overhead wash.
- Section labels mono micro-labels led by a lit diamond; buttons squared mono
  keys, primary = chamfered cyan slab (dark ink; focus lights an inner frame
  since clip-path cuts outlines), Delete/Remove danger-red; inputs dark slots
  with the lit-slot focus (cyan line + inset 2px bar); checkboxes square with a
  cyan core (done steps ok-green).
- **Case list:** rows dim → ink with a neon bar on hover; selected = cyan bar +
  wash + cyan-hi title; counts mono tabular.
- **Goal bar = status strip:** left light bar coloured by phase — active
  `--jv-ok`, paused amber, blocked danger; phase label mono uppercase.
- **Data rows:** entities/relationships/findings/watches on hairlines; entity
  values + conf mono tabular; type groups as faint mono micro-labels;
  findings and watches hang off a 1px left rail (amber when a watch is paused).
- **Link graph = scope:** dark slot with a 40px radar grid and centre glow;
  relation edges cyan with a soft bloom, co-mention edges faint dashed; nodes
  keep their categorical type colours (data, not decoration) with a void ring;
  **"also in another case" ring is amber** (overlap = attention), as are the
  "Also in" / "Shared with other cases" chips. Legend dots are diamonds.
- Toast: lit slot (cyan bar), danger variant for errors.
- Touch: 44px controls on coarse pointers (verified: header key, search,
  inputs, selects, buttons), 20px checkboxes, taller list rows.
- A11y gates: reduced-motion / reduced-transparency / more-contrast /
  forced-colors as in the app.
- Verified headless (1440×900, 390×844 + mobile touch/reduced-motion) — case
  open, search hits, graph node focused with overlap, keyboard focus on the
  primary — in jarvis and light; no horizontal overflow, no console errors.
  Pre-existing, not jarvis: the deterministic layout can stack labels near the
  graph's bottom edge on dense graphs.

**Next:** mobile pass (app shell at 390px end-to-end), then the dedicated
settings pass (area 6).

### Run 10 — 2026-11-02 · Mobile pass, part 1: the app shell at 390px
New sheet `static/css/jarvis-mobile.css`, linked after `tron.css`; every rule
under `:root[data-odx-theme="jarvis"]`. Audited the shell at 390×844 (welcome,
chat with table/code, drawer open, and each sidebar tool view).
- **Voice launcher = the living core.** The dashed ring in `voice-mode.css`
  spun inside the *pill*, so it rotated as a tall ellipse sweeping outside the
  button (all widths, all themes). Jarvis pins it to the orb as a 34px circle.
- **Phones: compact 44px core.** The floating "VOICE" pill (z-index 9990,
  `bottom: 110px`) sat on the composer's model picker, on top of the open
  drawer and over modal sheets. At ≤768px it is now a 44px circular core
  (label visually hidden, aria-label intact), z-index 150 — under the drawer,
  its scrim and modal sheets. In the chat view it **docks in the top bar**,
  opposite the hamburger; views that hide the hamburger (documents, notes,
  compare) keep the original floating anchor, compact. The top bar gets
  symmetric 52px gutters so the session title centres; the Nobody-mode
  indicator shifts to 54px to clear the core.
- **Drawer scrim.** `#sidebar-backdrop` is `display:none !important`
  upstream (deliberately). Jarvis paints it as a *visual-only* layer (void
  62 % + 2px blur + a faint cyan corner wash, 180ms expo fade) with
  `pointer-events: none`, so taps still go to the existing click-outside
  handler — zero behaviour change. The drawer's inner edge gets a lit
  hairline.
- **Composer:** no desktop resize grip on touch.
- **Welcome:** tip was `opacity: .2` and clipped at `max-height: 60px` (4-line
  tips cut mid-line) → `--jv-text-faint` at full opacity, 7.5em cap, 30ch
  measure on phones. Nobody chip `opacity: .25` → `.85` dim text, cyan on
  hover/focus, 44px tall on touch.
- A11y gates: reduced-motion (no scrim fade, no press scale),
  reduced-transparency (solid scrim/core), more-contrast (solid cyan core
  border, full-ink chip), forced-colors.
- Verified headless at 1440×900 + 390×844 (welcome, chat, drawer, 10 tool
  views) in jarvis and light. Light: pixel-identical (only diffs are the
  animated background canvas and the injected welcome→chat transition
  timing). No horizontal overflow; no new console errors. FAB still opens
  voice mode; tapping outside the drawer still closes it.
- Seen, not fixed (later passes): message tables wrap words mid-token at
  390px ("Linked/In") and code blocks soft-wrap mid-flag; the Calendar sheet
  header shows no close key at 390px.

**Next:** mobile pass part 2 — message bubbles (scrollable tables, code
blocks), modal sheets at 390px (close keys, safe areas), then settings.

### Run 11 — 2026-11-03 · Mobile pass, part 2: tables, code blocks, sheet keys
- **Tables rendered at all when they contain links (bug fix, all themes).**
  `mdToHtml` skipped any pipe table containing an `___ALLOWED_HTML_`
  placeholder — meant for `<details>` blocks, but inline `<a>` links (bare
  domains, `[text](url)`) use the same placeholder, so a table with a URL in a
  cell came out as raw `| … |` paragraphs. Link placeholders are now tracked
  (`inlineLinkIdx`) and don't block the table; `<details>`/fenced code still
  do. Tests: `tests/test_markdown_rendering_js.py` (+2; the node harness now
  stubs the DOM-based sanitizer).
- **Table scroll slot.** Tables are emitted inside
  `<div class="md-table-scroll" tabindex="0" role="region" aria-label="Table">`
  (unstyled in other themes — verified identical in light). Swipe-to-open
  the drawer ignores it (`sidebar-layout.js` EXCLUDE). Jarvis (`tron.css`):
  cells break only at spaces (bubble body's `overflow-wrap:anywhere` had
  split "Confi|dence", "20|26-|10" — desktop too), headers nowrap, cells cap
  at 34ch; the slot scrolls sideways with a thin cyan scrollbar and a focus
  ring. Phones: columns take natural width (cap 28ch) instead of rows ten
  lines tall. Tried a local/scroll background edge fade — it drew visible
  bands on the translucent bubble, so dropped it; the cut column is the cue.
- **Code blocks on phones** (`jarvis-mobile.css` §6): Run/Edit/Copy sit in a
  reserved 34px key strip with a hairline; code keeps its real lines
  (`white-space: pre`) and scrolls sideways. One-line commands (`.pre-compact`)
  lost their 200px desktop right gutter, which had left ~120px of code per
  line. The near-top `.bottom` flip is pinned back to the strip.
- **Sheet close keys** (§7): `style.css` hides every sheet's ✕ at ≤768px
  (swipe-down only — no single-tap way out, WCAG 2.5.1). Jarvis restores it as
  a 44px square key at the header's trailing edge in Calendar, Tasks,
  Library, Gallery, Cookbook, Research, Theme and Settings (verified present
  and 44×44 in all eight); swipe still works; minimise stays hidden.
  More-contrast: full-ink key, solid cyan border.
- Verified headless at 1440×900 + 390×844 in jarvis and light: chat with a
  linked table + long command, and the eight sheets. No horizontal page
  overflow; desktop table fits without scroll; light identical apart from the
  table now rendering. No new console errors (only the harness's
  service-worker fetch).
- Seen, not fixed: light/other themes still crush table columns mid-word
  (jarvis-only rules); desktop `.pre-compact` keeps its 200px gutter, so a
  long one-liner wraps at ~440px; scrollable `pre` blocks aren't
  keyboard-focusable.

**Next:** mobile pass part 3 — tool views and sheet bodies at 390px
(Settings tab strip overflow, Cookbook/Research forms, Library cards), then
the dedicated settings pass.

### Run 12 — 2026-11-04 · Mobile pass, part 3: sheet bodies on touch
`static/css/jarvis-mobile.css` §8, gated `(max-width: 768px) and (pointer:
coarse)` so desktop density (and a narrow desktop window) is unchanged.
Audit at 390×844 touch, every tool sheet (Cookbook, Research, Library,
Gallery, Theme, Tasks, Calendar, Settings, Brain, Prompt): toolbar keys were
24px (Library/Tasks/Brain), selects 24–32px, Cookbook hardware fields 28px,
every tab strip 32px; form fields at 11–13px (iOS Safari zooms the page on
focus below 16px); tab strips scroll with a hidden scrollbar, so
"Integrations", the Brain's "Settings" tab, etc. were cut off with no cue.
- **44px keys:** tabs (`cookbook/memory/admin/lib/gallery/preset` tabs,
  settings nav), toolbar keys (`memory-toolbar-btn`, `theme-io-btn`, gallery
  Select, hwfit GPU keys, research categories/add/start/settings toggle,
  `cal-btn`, admin add/sm, modal footers, the Cookbook download card
  header). Filter chips 40px. Upstream 1–3px `top:` nudges zeroed on those
  rows (they only misalign once everything shares one height).
  `tron.css` coarse-pointer Cookbook tab/chip minimums raised to match.
- **Fields:** selects/inputs 44px, all sheet fields 16px text.
- **Gallery toolbar** wraps: search on its own row, Sources/Sort/Select on
  the next (at 16px the single row truncated to "Search phot" / "All sourc").
- **Strips say "more":** snap tab-by-tab, plus a scroll-driven edge mask
  (`animation-timeline: scroll(self inline)` animating two `@property`
  lengths): at rest only the right edge fades, mid-scroll both, at the end
  only the left; a strip that fits has no fade (inactive timeline). No
  support → no fade. Removed under `prefers-contrast: more` and
  `forced-colors`. Focus ring inset 3px (strips clip overflow-y).
- Verified headless (390×844 touch + 1440×900) in jarvis and light: every
  audited control ≥44px (chips 40); no horizontal page overflow; mask values
  checked at start/middle/end of the Settings strip; light at 390px
  identical (≤6 px of antialiasing noise); jarvis desktop differs only by
  the randomly placed pro-tip and ambient canvas. Style tests (40) pass, same
  as baseline. Console: only the harness's pre-existing mock error
  (`userTemplates.map`, `/api/*` returns `{}`).
- Seen, not fixed: `.preset-tabs` is 5px wider than the sheet at 390px
  (negative margins, all themes, hidden by the sheet's overflow); Settings
  panel bodies (endpoint lists, admin cards) not audited at 390px yet —
  belongs with the settings pass.

**Next:** area 6, the dedicated settings pass (nav rail, panel cards, endpoint
rows, toggles), including its 390px panel bodies.


### Run 13 — 2026-11-04 · Settings
`static/css/tron.css` §16, every rule scoped to `#settings-modal` under the
jarvis root. Audit (1440×900 + 390×844, mocked admin with three endpoints,
two users): fields in a card started at different x (label `min-width:70px`
+ one card with a 12px inline gap); selects had no chevron, so they read as
text inputs; endpoint/user rows were 9px-rounded pills inside chamfered
cards; status badges were filled pills (offline and disabled both red);
"ADMIN" nav label and "Click to manage …" hints at 35–40 % opacity
(~2:1); the user avatar tint used `--accent`, empty in jarvis, so only the
letter showed; on phones row actions squeezed the endpoint name to two
lines and the add-user fields truncated their placeholders.
- **Form rhythm:** 88px mono label column (`--st-label-w`) for
  `.settings-row` and any `label + select/input` pair (vision row's inline
  gap normalised to 8px); 32px fields on a darker well; selects get a drawn
  cyan chevron (`--st-chev`, removed under `forced-colors`); fallback chains
  indent under the field column.
- **Nav rail:** 34px items, hover tint, active icon lit; dividers fade out;
  "ADMIN" is an amber micro-label (attention = admin-only power).
- **Rows:** square hairline rows, dashed when disabled, 2px cyan edge + tint
  on hover; endpoint URL in mono; hints at the dim token. Avatars (users +
  account) are lit squares.
- **Chips:** mono uppercase 9.5px, hairline outline — cyan = live/count,
  amber = offline, neutral inside a disabled row (a choice, not a fault).
- **Keys:** row actions one height, mono; Delete/Remove/Logout outlined and
  quiet, full danger only on hover/focus.
- **Phones (≤768px):** intro caption hidden; label stacks over field; row
  actions drop to their own right-aligned line; add-user fields full width;
  44px row keys on touch.
- Removed a duplicate `.settings-sidebar-divider` rule from `style.css`.
- Verified headless: jarvis before/after on Add Models, AI Defaults, Users,
  Appearance, Account at 1440 and 390; light rendered from a clean `HEAD`
  worktree vs. the change on Account/AI (1440) and Add Models/Users (390) —
  pixel-identical apart from the animated voice button. No horizontal
  overflow. CSS-touching tests 33 pass, same as baseline. Console: only the
  harness's existing mock errors (`_notes.filter`, `userTemplates.map`).
- Seen, not fixed: the phone sheet leaves a ~100px dark band under its last
  card (pre-existing); the window's minimise key has a brighter border than
  the close key; the intro caption ("Toggle on/off visibility …") is the
  same copy on every tab.

**Next:** mobile pass part 4 — chat overlays at 390px plus the settings
panels not yet audited (Integrations, Email, Reminders, Search, Shortcuts,
Agent Tools, System), and the sheet's bottom band.

### Run 14 — 2026-11-04 · Tab-strip follow-up: legibility, settings underline, no snap nudge
Small follow-up to Run 12 (this run started on the mobile tab strips before
Runs 12/13 had landed locally; overlapping work was dropped, only the gaps
kept). `jarvis-mobile.css` §8:
- **Inactive Brain/Gallery tabs legible at every width.** Upstream stacks
  50–60 % opacity on a 55 %-alpha ink (~3:1, below AA). Now full opacity on
  `--jv-text-dim`; full ink under more-contrast.
- **Settings active tab = underline on phones.** The rail becomes a
  horizontal strip at ≤768px but kept its desktop left-edge bar; it now uses
  the same bottom underline as the other strips (Run 13's active colour and
  lit icon still apply).
- **Removed the strips' `scroll-snap`.** Each strip's own inline padding
  (Gallery: 16px) ≠ `scroll-padding-inline: 12px`, so proximity snap opened
  Gallery at `scrollLeft: 4` — waking the leading edge fade over the active
  "Photos" tab. Without snap it opens at 0 with only the trailing fade.
  Strips get `overscroll-behavior-x: contain`; tabs `nowrap` + `flex-shrink: 0`.
- Verified headless at 1440×900 + 390×844 touch in jarvis and light (six
  sheets): computed tab opacity/colour/box-shadow/mask diff vs. Run 12 shows
  only the three intended changes; light identical. CSS tests 11/11, same as
  baseline. No page errors.

**Next:** unchanged — mobile pass part 4 (chat overlays at 390px, remaining
settings panels, the sheet's bottom band).

### Run 15 — 2026-11-04 · Mobile pass, part 4: Settings panels on phones
`static/css/jarvis-mobile.css` §9 (+ one desktop-wide rule in `tron.css`
§16). Audit of all twelve Settings panels at 390×844 touch with a scripted
metric (sheet band, controls < 44px, elements past the viewport):
- **No dead band.** On phones the sheet is full height, but
  `.settings-layout` kept its desktop cap (`85vh − 60px`), so the last
  112px of the sheet was empty and every panel scrolled in a shorter window.
  Now `flex: 1 1 0; max-height: none` (≤768px); panels get a safe-area
  bottom pad and `overscroll-behavior-y: contain`. Band 112–369px → 1px.
- **44px on touch, for real.** Run 13's ID-scoped `--st-field-h: 32px`
  out-ranked Run 12's 44px field minimum, so every Settings field was
  32–34px. The variable is 44px under `(max-width:768px) and (pointer:
  coarse)`; Wipe, Logout, Test/Add, Add fallback, Local/API/Quickstart
  section heads, shortcut caps, the reset key (28px) and visibility rows are
  44px too. Toggles keep their drawn 30×16 size and get an invisible 44×44
  hit area (`.admin-switch::after`, part of the label — hit-tested).
  Controls under 44px: 98 → 0 (toggles' boxes and one inline text link
  excluded).
- **Rows wrap instead of overflowing.** `.settings-row` puts its label on
  its own line and lets the rest share the next (Search's Test key sat 33px
  past the card); Add Models form rows put inputs full width and the
  type select + Test + Add on one row (the API row ran 89px past the card;
  the Local URL needed `!important` to beat its inline `flex:1`).
- **Provider pickers squared** (all widths, `tron.css`): Add Models' and
  Search's picker were the last 7px-rounded fields; now the same square
  well + hairline as every other settings field.
- Shortcuts caption at 40 % opacity → `--tr-dim` on phones.
- Verified headless, clean `HEAD` worktree vs. change, all 12 panels:
  jarvis 390 touch (intended diffs only), jarvis 1440 (only the two
  provider pickers change, ~330/750px), light 390 + 1440 pixel-identical.
  No horizontal page overflow. CSS tests 11/11 pass, same as baseline.
  Console: only the harness's existing mock error (`userTemplates.map`).
- Seen, not fixed: light theme's Search Test key also overflows at 390px
  (upstream, not jarvis); the Integrations/Agent Tools panels were only
  audited empty (mocks return no integrations/tools).

**Next:** mobile pass part 5 — chat overlays at 390px (slash menu, model
picker, message action bar, toasts, the voice overlay's controls).

### Run 16 — 2026-11-05 · Mobile pass, part 5: chat overlays
`static/css/jarvis-mobile.css` §10 (phones) + `tron.css` §5 (jarvis, all
widths). Audit at 390×844 touch with a scripted hit-size metric on the
message action bar, its ··· menu, the slash popup, the model picker and the
undo toast:
- **Selectors that never matched.** §5's popover rule listed
  `.slash-autocomplete`, but the popup's class is
  `.slash-autocomplete-popup` — it was never squared/lit. Added it and
  `.msg-overflow-menu` (was the rounded 8px glass from messages-hyper).
  Popover rows are square with the cyan rail on the active row; slash
  category labels are mono `--tr-dim`.
- **Slash usage hint yields** (all widths): it was `flex-shrink: 0`, so
  `/goal`'s long example squeezed its help to nothing and ran past the
  popup edge. Now ellipsised at ≤45 %; on phones it drops to its own line
  under the help text.
- **44px on touch:** message actions 21–38×32 → 44×44; ··· menu rows,
  slash rows, model rows 32/27/30 → 44; model search 32px at ~11px type →
  44px at 16px (no iOS focus-zoom); add key 30 → 44; fav dot 30×30 → 44×44;
  toast Undo 52×32 → 64×44, × 15×20 → 44×44 (it was flex-shrinking to 32
  inside the full-width toast — now `flex: 0 0 auto`). Sub-44 controls in
  these overlays: 13 → 0. Model list gets `min(360px, 50dvh)` on phones.
- **Toast:** jarvis had no toast styling and the base rule's
  `var(--accent)` rail is invalid here, so it had no rail. Now a square
  glass strip lit by a 2px cyan left edge; errors amber (alert colour);
  Undo is a mono cyan hairline key. On phones it spans the top gutter
  (12px each side, below the safe-area inset).
- Verified headless (mocked `/api/*`, nonce injected): jarvis 390 touch +
  1440, light 390 + 1440 — light's hit-size metrics identical before/after,
  jarvis 1440 changes only the popover/toast styling. No console errors.
  CSS tests 11/11 pass, same as baseline.

**Next:** mobile pass part 6 — the voice overlay's controls at 390px
(`voice-mode.css`/`voice-hyper.css`; another task is editing voice-mode
locally, so coordinate), then a whole-app contrast / reduced-motion audit.

### Run 17 — 2026-11-05 · Mobile pass, part 6: voice overlay controls
`static/css/jarvis-mobile.css` §11 (jarvis, ≤768px + coarse pointer) and
one all-theme bug fix in `static/css/voice-hyper.css`. `voice-mode.css` was
left alone (another task has local edits there). Audit at 390×844 touch
with a scripted hit-size metric over every visible control in `#vx-root`:
- **The core was mostly not tappable (all themes).** `style.css` gives
  every `button` `height: 32px`, which beat `.vx-orb-hit`'s `inset: 0`, so
  only a 32px strip across the top of the orb started/ended a call — the
  orb's centre hit the canvas. `.vx .vx-orb-hit` is now 100 % × 100 %
  (invisible; verified with `elementFromPoint` at the orb's centre).
- **44px on touch:** errors/close 38 → 44; scroll-to-bottom 34 → 44 (the
  transcript one re-lifted to 128px so it clears the taller paste-box row);
  Add context / Ask 26 → 44; Event log / Copy log 32 → 44; Memory "reset"
  39×32 → 44 tall (pulled to the panel edge with a negative margin); error
  pane copy/clear 44; dock toggles 17–20 → 44 tall.
- **Dock no longer squeezes:** 386px of content in a 370px dock made Mute
  and Send-now flex-shrink to 42/40px. Keys are `flex: 0 0 auto`, 44px
  (primary 56px), gap 4px — fits with room at 390px.
- **Toggle names kept:** upstream hides the toggle words with
  `display: none` ≤900px, which also strips the checkbox's accessible name;
  jarvis visually hides them instead.
- Paste box 13 → 16px type on phones (no iOS focus-zoom).
- Verified headless (mocked `/api/*`, nonce injected, reduced motion):
  jarvis 390 touch sub-44 controls 15 → 0, no element past the viewport,
  no horizontal overflow; jarvis 1440 unchanged (only the animated badge
  dots differ); light 390 pixel-identical, light 1440 differs only inside
  the animated orb shader. No console errors beyond aborted external
  fonts. CSS/JS tests 41/41 pass, same as baseline.
- Seen, not fixed: light/other themes keep the sub-44 voice controls on
  phones (out of jarvis scope); the overlay's own rounded glass language
  still differs from the square Tron panels — left as the voice mode's
  identity.

**Next:** whole-app contrast / reduced-motion audit (jarvis): scripted
AA check of text tokens across every view, then every animation under
`prefers-reduced-motion`.
