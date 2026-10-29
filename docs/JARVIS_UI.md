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
3. Chat canvas + message bubbles
4. Composer + voice/core indicator
5. Modals / windows / dropdowns
6. Settings
7. Notes / tasks / calendar
8. Documents / editor
9. Email
10. Cookbook / research / compare
11. Login + landing (`static/login.html`, `static/landing.html`)
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
