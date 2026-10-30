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
6. Settings
7. ✅ Notes / tasks / calendar
8. ✅ Documents / editor (+ library documents tab)
9. ✅ Email (inbox library, reader + pop-out readers, compose fields)
10. Cookbook / ✅ research / compare
11. ✅ Login (`auth-tron.css`) + landing (`static/landing.html`, not yet)
12. `/cases` Intel Board (`static/cases.html`)
13. Mobile pass

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
