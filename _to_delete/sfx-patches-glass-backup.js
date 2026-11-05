// ============================================================================
// Odysseus SFX — patch library
// ============================================================================
// Every tonal element is tuned to D-major pentatonic (D E F# A B) so any
// overlap of sounds — typing while a modal opens while a message sends —
// lands consonant instead of clashing. Transients come from filtered noise
// ("glass", "air", "graphite"), bodies from FM/sine partials, and big
// moments get the shared reverb/echo sends. Patch `gain` values are
// loudness-calibrated in tiers (whisper < key < mid < action < big).
//
// Edit freely: every patch is plain data. Try changes live in the Sound Lab
// (/static/sfx-lab.html), then paste the JSON back here.
// ============================================================================

// Note table (Hz)
export const N = {
  D3: 146.83, A3: 220.0, D4: 293.66, E4: 329.63, Fs4: 369.99, A4: 440.0, B4: 493.88,
  D5: 587.33, E5: 659.26, Fs5: 739.99, A5: 880.0, B5: 987.77,
  D6: 1174.66, E6: 1318.51, Fs6: 1479.98, A6: 1760.0, B6: 1975.53, D7: 2349.32,
};

// Re-usable building blocks ---------------------------------------------------
const glassTick = (freq = 5200, gain = 0.6, d = 0.012) => ({
  type: 'noise', color: 'white', gain,
  env: { a: 0.0005, d, r: 0.006 },
  filter: { type: 'bandpass', freq, q: 3.5 },
});
const subThump = (freq = 140, to = 60, gain = 0.8, d = 0.07) => ({
  type: 'osc', wave: 'sine', freq, to, sweep: d, gain,
  env: { a: 0.001, d, r: 0.03 },
});
const bell = (freq, { gain = 0.5, d = 0.18, ratio = 3.5, index = 600, at = 0, to, sweep } = {}) => ({
  type: 'osc', wave: 'sine', freq, to, sweep, at, gain,
  env: { a: 0.002, d, r: 0.08 },
  fm: { ratio, index, decay: d * 0.6 },
});

export const PATCHES = {
  // ── Pointer: hover ──────────────────────────────────────────────────────
  'hover': {
    family: 'pointer', desc: 'Pointer enters an interactive element — a breath of air + a high glint',
    gain: 0.67, throttle: 45, jitter: 0.4,
    layers: [
      { type: 'osc', wave: 'sine', freq: N.D7, to: N.E6 * 2, sweep: 0.02, env: { a: 0.002, d: 0.025, r: 0.02 }, gain: 0.5 },
      { type: 'noise', color: 'pink', env: { a: 0.004, d: 0.03, r: 0.02 }, filter: { type: 'highpass', freq: 6000, q: 0.7 }, gain: 0.5 },
    ],
  },

  // ── Pointer: press / release (the physical "glass key") ─────────────────
  'press': {
    family: 'pointer', desc: 'Pointer down on a control — glass key travels down',
    gain: 2.51, jitter: 0.25,
    layers: [
      glassTick(3800, 0.7, 0.01),
      { type: 'osc', wave: 'sine', freq: N.A5, to: N.D5, sweep: 0.04, env: { a: 0.001, d: 0.045, r: 0.02 }, gain: 0.6 },
      subThump(180, 90, 0.35, 0.04),
    ],
  },
  'release': {
    family: 'pointer', desc: 'Pointer up / click lands — key springs back with a crystalline pop',
    gain: 2.26, jitter: 0.25,
    layers: [
      glassTick(6200, 0.45, 0.008),
      { type: 'osc', wave: 'sine', freq: N.D5, to: N.A5, sweep: 0.03, env: { a: 0.001, d: 0.06, r: 0.03 }, gain: 0.7, fm: { ratio: 2, index: 220, decay: 0.03 } },
    ],
  },
  'link': {
    family: 'pointer', desc: 'Anchor / navigation — a quick two-step portal blip',
    gain: 0.87, reverb: 0.12,
    layers: [
      bell(N.Fs6, { gain: 0.5, d: 0.06, ratio: 2, index: 300 }),
      bell(N.B6, { gain: 0.45, d: 0.1, ratio: 2, index: 250, at: 0.045 }),
    ],
  },
  'submit': {
    family: 'pointer', desc: 'Send / submit — FM launch sweep with a shimmering tail',
    gain: 1.03, reverb: 0.3, echo: 0.18,
    layers: [
      { type: 'osc', wave: 'sine', freq: N.D4, to: N.D6, sweep: 0.14, env: { a: 0.004, h: 0.04, d: 0.14, r: 0.1 }, gain: 0.55, fm: { ratio: 1.5, index: 500, decay: 0.12 } },
      { type: 'noise', color: 'white', env: { a: 0.02, d: 0.12, r: 0.06 }, filter: { type: 'bandpass', freq: 800, to: 9000, time: 0.16, q: 2 }, gain: 0.35 },
      bell(N.A6, { gain: 0.3, d: 0.3, ratio: 3.01, index: 900, at: 0.12 }),
      subThump(120, 50, 0.5, 0.09),
    ],
  },
  'disabled': {
    family: 'pointer', desc: 'Pressing something disabled — muted rubber thud, no pitch',
    gain: 2.48,
    layers: [
      { type: 'noise', color: 'brown', env: { a: 0.001, d: 0.05, r: 0.02 }, filter: { type: 'lowpass', freq: 500, q: 0.8 }, gain: 0.9 },
      subThump(110, 80, 0.4, 0.05),
    ],
  },
  'toggle.on': {
    family: 'pointer', desc: 'Checkbox / switch ON — rising digital chirp, two notes',
    gain: 0.66, reverb: 0.08,
    layers: [
      glassTick(5000, 0.4, 0.008),
      { type: 'osc', wave: { harmonics: [1, 0, 0.3, 0, 0.12] }, freq: N.A5, env: { a: 0.001, d: 0.04, r: 0.02 }, gain: 0.55 },
      { type: 'osc', wave: { harmonics: [1, 0, 0.3, 0, 0.12] }, freq: N.D6, at: 0.045, env: { a: 0.001, d: 0.08, r: 0.04 }, gain: 0.6 },
    ],
  },
  'toggle.off': {
    family: 'pointer', desc: 'Checkbox / switch OFF — the same chirp, falling and darker',
    gain: 0.73,
    layers: [
      glassTick(3200, 0.4, 0.008),
      { type: 'osc', wave: { harmonics: [1, 0, 0.3, 0, 0.12] }, freq: N.D6, env: { a: 0.001, d: 0.04, r: 0.02 }, gain: 0.5 },
      { type: 'osc', wave: { harmonics: [1, 0, 0.3, 0, 0.12] }, freq: N.A5 / 1.5, at: 0.045, env: { a: 0.001, d: 0.07, r: 0.04 }, gain: 0.55 },
    ],
  },
  'expand': {
    family: 'pointer', desc: 'Disclosure opens (aria-expanded=true / <details>) — unfolding filter sweep',
    gain: 1.79,
    layers: [
      { type: 'osc', wave: 'sawtooth', freq: N.D5, env: { a: 0.004, d: 0.09, r: 0.04 }, filter: { type: 'lowpass', freq: 500, to: 5000, time: 0.08, q: 6 }, gain: 0.35 },
      { type: 'osc', wave: 'sine', freq: N.Fs5, to: N.A5, sweep: 0.06, env: { a: 0.002, d: 0.08, r: 0.03 }, gain: 0.4 },
    ],
  },
  'collapse': {
    family: 'pointer', desc: 'Disclosure closes — folding filter sweep down',
    gain: 1.52,
    layers: [
      { type: 'osc', wave: 'sawtooth', freq: N.A4, env: { a: 0.004, d: 0.08, r: 0.04 }, filter: { type: 'lowpass', freq: 4500, to: 400, time: 0.07, q: 6 }, gain: 0.35 },
      { type: 'osc', wave: 'sine', freq: N.A5, to: N.Fs5, sweep: 0.05, env: { a: 0.002, d: 0.07, r: 0.03 }, gain: 0.35 },
    ],
  },
  'select': {
    family: 'pointer', desc: '<select> / tab / option changed — notched index click',
    gain: 2.73,
    layers: [
      glassTick(4400, 0.5, 0.01),
      bell(N.E6, { gain: 0.45, d: 0.07, ratio: 1.5, index: 200 }),
    ],
  },
  'double': {
    family: 'pointer', desc: 'Double-click — twin sparkle',
    gain: 1.11, reverb: 0.15,
    layers: [
      bell(N.B6, { gain: 0.4, d: 0.05, index: 400 }),
      bell(N.D7, { gain: 0.4, d: 0.1, index: 400, at: 0.035 }),
    ],
  },
  'context': {
    family: 'pointer', desc: 'Right-click / context menu — sonar ping with echo',
    gain: 0.74, echo: 0.35,
    layers: [
      { type: 'osc', wave: 'sine', freq: N.E6, env: { a: 0.002, d: 0.12, r: 0.05 }, gain: 0.6, fm: { ratio: 1.0, index: 120, decay: 0.1 } },
      glassTick(7000, 0.3, 0.006),
    ],
  },

  // ── Pointer: drag & scroll ──────────────────────────────────────────────
  'drag.start': {
    family: 'drag', desc: 'Grab — magnetic clutch: thump + rising hum',
    gain: 2.28,
    layers: [
      subThump(200, 110, 0.55, 0.06),
      { type: 'osc', wave: 'triangle', freq: N.D4, to: N.A4, sweep: 0.08, env: { a: 0.006, d: 0.08, r: 0.04 }, filter: { type: 'lowpass', freq: 1800, q: 2 }, gain: 0.4 },
      glassTick(2400, 0.4, 0.012),
    ],
  },
  'drag.move': {
    family: 'drag', desc: 'Dragging — granular detent ticks (pitch follows pointer speed)',
    gain: 0.79, throttle: 55, jitter: 1.5,
    layers: [
      glassTick(3000, 0.7, 0.006),
      { type: 'osc', wave: 'sine', freq: N.A5, env: { a: 0.001, d: 0.015, r: 0.01 }, gain: 0.4 },
    ],
  },
  'drag.end': {
    family: 'drag', desc: 'Drop — snap-lock: click + harmonic seat',
    gain: 1.4, reverb: 0.1,
    layers: [
      glassTick(5200, 0.6, 0.01),
      subThump(160, 70, 0.5, 0.05),
      { type: 'osc', wave: { harmonics: [1, 0.5, 0, 0.25] }, freq: N.D5, at: 0.012, env: { a: 0.002, d: 0.12, r: 0.05 }, gain: 0.45 },
    ],
  },
  'range': {
    family: 'drag', desc: 'Slider moves — pitched tick mapped to the value (pitch is set at play time)',
    gain: 1.48, throttle: 35,
    layers: [
      { type: 'osc', wave: 'triangle', freq: N.D5, env: { a: 0.001, d: 0.025, r: 0.015 }, gain: 0.7 },
      glassTick(5000, 0.3, 0.005),
    ],
  },

  // ── Keyboard ────────────────────────────────────────────────────────────
  'key.tab': {
    family: 'keyboard', desc: 'Tab focus travel — horizontal glide',
    gain: 1.99, throttle: 30,
    layers: [
      { type: 'osc', wave: 'sine', freq: N.B5, to: N.E6, sweep: 0.04, env: { a: 0.002, d: 0.05, r: 0.02 }, gain: 0.6 },
      { type: 'noise', color: 'pink', env: { a: 0.004, d: 0.03, r: 0.01 }, filter: { type: 'bandpass', freq: 1500, to: 6000, time: 0.04, q: 2 }, gain: 0.3 },
    ],
  },
  'key.arrow': {
    family: 'keyboard', desc: 'Arrow keys — directional pip (↑ higher, ↓ lower, ←/→ panned)',
    gain: 1.25, throttle: 30,
    layers: [
      { type: 'osc', wave: 'triangle', freq: N.A5, env: { a: 0.001, d: 0.025, r: 0.012 }, gain: 0.7 },
      glassTick(4800, 0.3, 0.004),
    ],
  },
  'key.escape': {
    family: 'keyboard', desc: 'Escape / dismiss — air-lock vent: noise sweep down + falling tone',
    gain: 2.24,
    layers: [
      { type: 'noise', color: 'white', env: { a: 0.003, d: 0.1, r: 0.04 }, filter: { type: 'bandpass', freq: 6000, to: 500, time: 0.11, q: 1.5 }, gain: 0.5 },
      { type: 'osc', wave: 'sine', freq: N.E6, to: N.A4, sweep: 0.09, env: { a: 0.002, d: 0.09, r: 0.03 }, gain: 0.4 },
    ],
  },
  'key.chord': {
    family: 'keyboard', desc: 'Shortcut (Ctrl/⌘ + key) — command chord: stacked fifth bell',
    gain: 1.53, reverb: 0.14,
    layers: [
      bell(N.D6, { gain: 0.4, d: 0.12, ratio: 3.5, index: 500 }),
      bell(N.A6, { gain: 0.35, d: 0.14, ratio: 3.5, index: 500, at: 0.008 }),
      glassTick(6000, 0.4, 0.006),
    ],
  },

  // ── Focus, clipboard ──────────────────────────────────────────────
  'focus': {
    family: 'system', desc: 'Text field gains focus — soft power-on sliver',
    gain: 1.19, throttle: 120,
    layers: [
      { type: 'osc', wave: 'triangle', freq: N.D5, to: N.A5, sweep: 0.07, env: { a: 0.01, d: 0.08, r: 0.04 }, gain: 0.6, unison: { voices: 2, spread: 10 } },
    ],
  },
  'copy': {
    family: 'system', desc: 'Copy — two-step ascending capture',
    gain: 0.8, reverb: 0.12,
    layers: [
      bell(N.D6, { gain: 0.45, d: 0.05, ratio: 2, index: 250 }),
      bell(N.A6, { gain: 0.45, d: 0.11, ratio: 2, index: 250, at: 0.05 }),
    ],
  },
  'cut': {
    family: 'system', desc: 'Cut — blade swipe: noise slash + falling tone',
    gain: 1.59,
    layers: [
      { type: 'noise', color: 'white', env: { a: 0.002, d: 0.06, r: 0.02 }, filter: { type: 'highpass', freq: 9000, to: 2000, time: 0.06, q: 1 }, gain: 0.5 },
      bell(N.A6, { gain: 0.35, d: 0.07, ratio: 2, index: 250, to: N.D6, sweep: 0.06 }),
    ],
  },
  'paste': {
    family: 'system', desc: 'Paste — drop-in: descending blip that lands on a soft thud',
    gain: 0.72,
    layers: [
      bell(N.A6, { gain: 0.4, d: 0.04, ratio: 2, index: 200 }),
      bell(N.D6, { gain: 0.4, d: 0.08, ratio: 2, index: 200, at: 0.04 }),
      { ...subThump(160, 70, 0.4, 0.05), at: 0.04 },
    ],
  },

  // ── Windows / overlays / feedback ───────────────────────────────────────
  'open': {
    family: 'system', desc: 'Modal / dialog / panel opens — holographic bloom',
    gain: 1.41, reverb: 0.28,
    layers: [
      { type: 'noise', color: 'pink', env: { a: 0.05, d: 0.08, r: 0.06 }, filter: { type: 'bandpass', freq: 400, to: 5000, time: 0.12, q: 2.5 }, gain: 0.5 },
      { type: 'osc', wave: 'triangle', freq: N.D5, to: N.D6, sweep: 0.12, env: { a: 0.03, d: 0.12, r: 0.08 }, gain: 0.35, unison: { voices: 3, spread: 16 } },
      bell(N.Fs6, { gain: 0.2, d: 0.2, index: 500, at: 0.1 }),
    ],
  },
  'close': {
    family: 'system', desc: 'Modal / dialog closes — bloom reversed, collapsing inward',
    gain: 2.3, reverb: 0.15,
    layers: [
      { type: 'noise', color: 'pink', env: { a: 0.01, d: 0.09, r: 0.04 }, filter: { type: 'bandpass', freq: 5000, to: 400, time: 0.1, q: 2.5 }, gain: 0.45 },
      { type: 'osc', wave: 'triangle', freq: N.D6, to: N.D5, sweep: 0.1, env: { a: 0.005, d: 0.1, r: 0.05 }, gain: 0.3, unison: { voices: 3, spread: 16 } },
    ],
  },
  'success': {
    family: 'feedback', desc: 'Success — major-pentatonic arpeggio in glass bells',
    gain: 0.84, reverb: 0.3, echo: 0.12,
    layers: [
      bell(N.D6, { gain: 0.4, d: 0.12, ratio: 3, index: 500 }),
      bell(N.Fs6, { gain: 0.4, d: 0.12, ratio: 3, index: 500, at: 0.07 }),
      bell(N.A6, { gain: 0.45, d: 0.3, ratio: 3, index: 500, at: 0.14 }),
    ],
  },
  'error': {
    family: 'feedback', desc: 'Error / invalid — low dissonant double-buzz (the only non-pentatonic sound)',
    gain: 0.38,
    layers: [
      { type: 'osc', wave: 'square', freq: 196, env: { a: 0.002, d: 0.07, r: 0.02 }, filter: { type: 'lowpass', freq: 900, q: 2 }, gain: 0.35 },
      { type: 'osc', wave: 'square', freq: 207.6, env: { a: 0.002, d: 0.07, r: 0.02 }, filter: { type: 'lowpass', freq: 900, q: 2 }, gain: 0.35 },
      { type: 'osc', wave: 'square', freq: 196, at: 0.1, env: { a: 0.002, d: 0.09, r: 0.03 }, filter: { type: 'lowpass', freq: 700, q: 2 }, gain: 0.35 },
      { type: 'osc', wave: 'square', freq: 207.6, at: 0.1, env: { a: 0.002, d: 0.09, r: 0.03 }, filter: { type: 'lowpass', freq: 700, q: 2 }, gain: 0.35 },
    ],
  },
  'notify': {
    family: 'feedback', desc: 'Notification / toast — crystal two-tone with echo',
    gain: 1.01, echo: 0.25, reverb: 0.2,
    layers: [
      bell(N.A6, { gain: 0.45, d: 0.1, ratio: 4, index: 700 }),
      bell(N.E6, { gain: 0.4, d: 0.25, ratio: 4, index: 700, at: 0.09 }),
    ],
  },
  'mute': {
    family: 'feedback', desc: 'Sound turned on — the confirmation you hear when re-enabling SFX',
    gain: 1.16, reverb: 0.2,
    layers: [
      bell(N.D6, { gain: 0.4, d: 0.08, index: 300 }),
      bell(N.A6, { gain: 0.4, d: 0.16, index: 300, at: 0.06 }),
    ],
  },
};

export default PATCHES;
