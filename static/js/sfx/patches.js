// ============================================================================
// Odysseus SFX — patch library · "XENO" voice
// ============================================================================
// The interface sounds like a machine that was not built by us: a calm,
// impossibly precise alien supercomputer. Five materials, used everywhere:
//
//   • xenocrystal — FM bells on *inharmonic* ratios (√2, 2.76, 3.17 …), so the
//                   overtones never line up the way earthly glass or metal do
//   • data chirp  — pitch that *steps* instead of gliding (seq), 8–40 ms per
//                   step: the machine "speaks" in quantized packets
//   • ion field   — high noise, ring-modulated into metallic sizzle: the
//                   transient of every click
//   • plasma      — sub tones with slow vibrato and amplitude flutter: the
//                   hum of something very large, very far away
//   • holo-lattice — detuned saw stacks through resonant band sweeps with a
//                   ring flutter: panels and dialogs materialising
//
// Fundamentals stay on D-major pentatonic (D E F# A B), so overlapping sounds
// still agree with each other; the alien colour lives in the overtones.
// Patch `gain` values are loudness-calibrated per sound (offline render,
// peak short-term RMS) so each stays in its tier: whisper < key < mid <
// action < big. Re-calibrate after edits with the Sound Lab's meter.
//
// Edit freely: every patch is plain data. Try changes live in the Sound Lab
// (/static/sfx-lab.html), then paste the JSON back here.
// ============================================================================

// Note table (Hz)
export const N = {
  D2: 73.42, A2: 110.0, D3: 146.83, A3: 220.0, D4: 293.66, E4: 329.63, Fs4: 369.99, A4: 440.0, B4: 493.88,
  D5: 587.33, E5: 659.26, Fs5: 739.99, A5: 880.0, B5: 987.77,
  D6: 1174.66, E6: 1318.51, Fs6: 1479.98, A6: 1760.0, B6: 1975.53, D7: 2349.32, A7: 3520.0,
};
// Pentatonic step multipliers from a root (for seq): root, 2nd, 3rd, 5th, 6th, octave …
const P = { r: 1, M2: 1.1225, M3: 1.2599, P5: 1.4983, M6: 1.6818, O: 2, O2: 2.245, O3: 2.5198, O5: 2.9966, OO: 4 };
// Inharmonic FM ratios — the "not from here" overtone sets.
const X = { root2: 1.4142, xeno: 2.7568, tri: 3.1713, phi: 1.618, odd: 3.5 };

// Materials ------------------------------------------------------------------
/** Xenocrystal: inharmonic FM bell. */
const xtal = (freq, { gain = 0.5, d = 0.14, r = 0.08, ratio = X.xeno, index = 520, at = 0, to, sweep, vib } = {}) => ({
  type: 'osc', wave: 'sine', freq, to, sweep, at, gain,
  env: { a: 0.0015, d, r },
  fm: { ratio, index, decay: d * 0.55 },
  ...(vib ? { vibrato: vib } : {}),
});
/** Data chirp: stepped pitch packet. */
const chirp = (freq, seq, step, { gain = 0.5, wave = 'triangle', at = 0, d, r = 0.02, crush, lp = 7000, fm } = {}) => ({
  type: 'osc', wave, freq, seq, step, at, gain,
  env: { a: 0.001, d: d ?? seq.length * step, r },
  filter: { type: 'lowpass', freq: lp, q: 0.9 },
  ...(crush ? { crush } : {}),
  ...(fm ? { fm } : {}),
});
/** Ion field: ring-modulated noise transient. */
const ion = (freq = 6000, { gain = 0.5, d = 0.01, at = 0, ring = 2300, q = 4, color = 'white' } = {}) => ({
  type: 'noise', color, gain, at,
  env: { a: 0.0004, d, r: 0.006 },
  filter: { type: 'bandpass', freq, q },
  ring: { freq: ring, depth: 0.85 },
});
/** Plasma: sub tone with flutter. */
const plasma = (freq, to, { gain = 0.6, d = 0.08, a = 0.002, at = 0, flutter = 31, sweep } = {}) => ({
  type: 'osc', wave: 'sine', freq, to, sweep: sweep ?? d, at, gain,
  env: { a, d, r: 0.04 },
  ring: { freq: flutter, depth: 0.35 },
});
/** Holo-lattice: detuned saw stack through a resonant band sweep. */
const lattice = (freq, from, to, { gain = 0.5, d = 0.16, a = 0.01, at = 0, q = 7, flutter = 14, voices = 4, spread = 28 } = {}) => ({
  type: 'osc', wave: 'sawtooth', freq, at, gain,
  unison: { voices, spread },
  env: { a, d, r: 0.06 },
  filter: { type: 'bandpass', freq: from, to, time: d, q },
  ring: { freq: flutter, depth: 0.3 },
});

export const PATCHES = {
  // ── Pointer ─────────────────────────────────────────────────────────────
  'hover': {
    family: 'pointer', desc: 'Pointer enters a control — a sensor brushes it: ion whisper + a crystalline glint',
    gain: 0.867, throttle: 45, jitter: 0.4,
    layers: [
      xtal(N.D7, { gain: 0.45, d: 0.02, r: 0.02, ratio: X.root2, index: 260 }),
      ion(8200, { gain: 0.4, d: 0.012, ring: 3100 }),
    ],
  },
  'press': {
    family: 'pointer', desc: 'Pointer down — the control is scanned: ion click + a quantized drop into a plasma seat',
    gain: 2.837, jitter: 0.25,
    layers: [
      ion(4200, { gain: 0.75, d: 0.009, ring: 1700 }),
      chirp(N.A5, [1, 0.75, 0.5], 0.011, { gain: 0.5, wave: 'square', lp: 3200, crush: 6 }),
      plasma(170, 85, { gain: 0.35, d: 0.04 }),
    ],
  },
  'release': {
    family: 'pointer', desc: 'Click lands — a xenocrystal rings once; repeated clicks climb the scale',
    gain: 2.582, jitter: 0.25,
    layers: [
      ion(6500, { gain: 0.5, d: 0.008, ring: 2600 }),
      xtal(N.D6, { gain: 0.6, d: 0.06, r: 0.05, ratio: X.xeno, index: 480 }),
      chirp(N.A6, [1, P.O], 0.012, { gain: 0.18, lp: 9000 }),
    ],
  },
  'link': {
    family: 'pointer', desc: 'Navigation — a three-packet handshake, then the portal hums open',
    gain: 0.983, reverb: 0.14,
    layers: [
      chirp(N.D6, [1, P.P5, P.O], 0.022, { gain: 0.55, wave: 'square', lp: 3800, crush: 6 }),
      xtal(N.A6, { gain: 0.35, at: 0.06, d: 0.1, ratio: X.root2, index: 340, vib: { rate: 11, depth: 18 } }),
    ],
  },
  'submit': {
    family: 'pointer', desc: 'Send — the message is launched through a warp: FM sweep + ring warp + an outbound data burst',
    gain: 3.729, reverb: 0.32, echo: 0.2,
    layers: [
      { type: 'osc', wave: 'sine', freq: N.D4, to: N.D6, sweep: 0.16, curve: 'exp', gain: 0.55,
        env: { a: 0.004, d: 0.17, r: 0.06 }, fm: { ratio: X.root2, index: 700, decay: 0.14 },
        ring: { freq: 30, to: 420, sweep: 0.16, depth: 0.55 } },
      chirp(N.A6, [1, P.M3, P.P5, P.O], 0.026, { gain: 0.3, at: 0.1, fm: { ratio: X.xeno, index: 300, decay: 0.05 } }),
      ion(9000, { gain: 0.35, d: 0.03, at: 0.02, ring: 4400, q: 2 }),
    ],
  },
  'disabled': {
    family: 'pointer', desc: 'Pressing something disabled — access denied: a flat, buzzing plasma refusal',
    gain: 2.502,
    layers: [
      { type: 'osc', wave: 'triangle', freq: N.A2, gain: 0.7, env: { a: 0.002, d: 0.07, r: 0.03 },
        ring: { freq: 47, depth: 0.8 }, filter: { type: 'lowpass', freq: 700, q: 0.7 } },
      { type: 'noise', color: 'brown', gain: 0.5, env: { a: 0.001, d: 0.04, r: 0.02 }, filter: { type: 'lowpass', freq: 500, q: 0.7 } },
    ],
  },
  'toggle.on': {
    family: 'pointer', desc: 'Switch ON — a circuit closes: rising packet, then the node lights up',
    gain: 1.707, reverb: 0.1,
    layers: [
      chirp(N.D6, [1, P.P5, P.O], 0.018, { gain: 0.55 }),
      xtal(N.D7, { gain: 0.3, at: 0.05, d: 0.06, ratio: X.tri, index: 300 }),
      ion(7000, { gain: 0.3, d: 0.008, ring: 2900 }),
    ],
  },
  'toggle.off': {
    family: 'pointer', desc: 'Switch OFF — the same circuit opening: falling packet, darker, no glow',
    gain: 2.486,
    layers: [
      chirp(N.A6, [1, 2 / 3, 0.5], 0.018, { gain: 0.55, lp: 2600 }),
      plasma(140, 90, { gain: 0.3, d: 0.05, at: 0.03 }),
    ],
  },
  'expand': {
    family: 'pointer', desc: 'Disclosure opens — a holo-lattice unfolds upward',
    gain: 8.009,
    layers: [
      lattice(N.D4, 380, 3400, { gain: 0.5, d: 0.12, voices: 3 }),
      ion(5200, { gain: 0.3, d: 0.01, ring: 2100 }),
    ],
  },
  'collapse': {
    family: 'pointer', desc: 'Disclosure closes — the lattice folds back down into itself',
    gain: 1.569,
    layers: [
      lattice(N.D4, 3400, 380, { gain: 0.5, d: 0.11, voices: 3 }),
      plasma(120, 70, { gain: 0.25, d: 0.05, at: 0.05 }),
    ],
  },
  'select': {
    family: 'pointer', desc: 'Tab / option / <select> changed — an index register clicks to a new address',
    gain: 4.468,
    layers: [
      ion(3600, { gain: 0.6, d: 0.008, ring: 1500 }),
      chirp(N.E6, [1, P.O], 0.01, { gain: 0.45, wave: 'square', lp: 4200, crush: 4 }),
    ],
  },
  'double': {
    family: 'pointer', desc: 'Double-click — twin xenocrystals, the second answering the first',
    gain: 0.96, reverb: 0.16,
    layers: [
      xtal(N.A6, { gain: 0.5, d: 0.05, ratio: X.tri, index: 420 }),
      xtal(N.D7, { gain: 0.45, at: 0.055, d: 0.07, ratio: X.tri, index: 420 }),
    ],
  },
  'context': {
    family: 'pointer', desc: 'Right-click — deep-space sonar: a warbling crystal ping that echoes back',
    gain: 0.582, echo: 0.38,
    layers: [
      xtal(N.A4, { gain: 0.6, d: 0.22, r: 0.1, ratio: X.root2, index: 900, vib: { rate: 9, depth: 32 } }),
      ion(2800, { gain: 0.3, d: 0.012, ring: 1100 }),
    ],
  },

  // ── Drag ────────────────────────────────────────────────────────────────
  'drag.start': {
    family: 'drag', desc: 'Grab — a tractor beam locks on: plasma swells under a rising lattice',
    gain: 1.81,
    layers: [
      plasma(70, 160, { gain: 0.5, d: 0.12, a: 0.01, flutter: 23 }),
      lattice(N.A3, 300, 1600, { gain: 0.35, d: 0.12, voices: 3, flutter: 23 }),
      ion(5000, { gain: 0.3, d: 0.01, ring: 1900 }),
    ],
  },
  'drag.move': {
    family: 'drag', desc: 'Dragging — the beam ticks through a force grid (pitch follows pointer speed)',
    gain: 1.991, throttle: 55, jitter: 1.5,
    layers: [
      ion(5600, { gain: 0.6, d: 0.006, ring: 2400, q: 6 }),
      chirp(N.D6, [1, P.P5], 0.008, { gain: 0.18, lp: 6000 }),
    ],
  },
  'drag.end': {
    family: 'drag', desc: 'Drop — the beam releases and the object docks: click, quantized seat, crystal ring',
    gain: 1.042, reverb: 0.12,
    layers: [
      ion(4400, { gain: 0.6, d: 0.01, ring: 1800 }),
      chirp(N.D6, [P.O, 1], 0.016, { gain: 0.35, wave: 'square', lp: 3600, crush: 6 }),
      xtal(N.D5, { gain: 0.45, at: 0.025, d: 0.1, ratio: X.xeno, index: 380 }),
      plasma(150, 75, { gain: 0.3, d: 0.05 }),
    ],
  },
  'range': {
    family: 'drag', desc: 'Slider — a calibrated tone register: pitch follows the value (set at play time)',
    gain: 1.912, throttle: 35,
    layers: [
      { type: 'osc', wave: 'triangle', freq: N.D5, gain: 0.7, env: { a: 0.001, d: 0.024, r: 0.014 },
        ring: { freq: 2200, depth: 0.25 } },
      ion(5200, { gain: 0.25, d: 0.005, ring: 2600 }),
    ],
  },

  // ── Keyboard (navigation only — typing is silent) ──────────────────────
  'key.tab': {
    family: 'keyboard', desc: 'Tab — focus phases sideways: a warbling glide through an ion veil',
    gain: 1.973, throttle: 30,
    layers: [
      { type: 'osc', wave: 'sine', freq: N.B5, to: N.E6, sweep: 0.04, gain: 0.6,
        env: { a: 0.002, d: 0.05, r: 0.02 }, vibrato: { rate: 24, depth: 30 } },
      ion(4000, { gain: 0.3, d: 0.025, ring: 1300, q: 2 }),
    ],
  },
  'key.arrow': {
    family: 'keyboard', desc: 'Arrows — a cursor packet (↑ higher, ↓ lower, ←/→ panned)',
    gain: 1.576, throttle: 30,
    layers: [
      chirp(N.A5, [1, P.P5], 0.01, { gain: 0.6 }),
      ion(5200, { gain: 0.25, d: 0.005, ring: 2300 }),
    ],
  },
  'key.escape': {
    family: 'keyboard', desc: 'Escape — airlock purge: ring-torn noise vents downward under a falling FM tone',
    gain: 2.417,
    layers: [
      { type: 'noise', color: 'white', gain: 0.5, env: { a: 0.003, d: 0.1, r: 0.04 },
        filter: { type: 'bandpass', freq: 6500, to: 450, time: 0.11, q: 1.6 }, ring: { freq: 90, depth: 0.6 } },
      { type: 'osc', wave: 'sine', freq: N.E6, to: N.A4, sweep: 0.09, gain: 0.4,
        env: { a: 0.002, d: 0.09, r: 0.03 }, fm: { ratio: X.root2, index: 300, decay: 0.08 } },
    ],
  },
  'key.chord': {
    family: 'keyboard', desc: 'Shortcut (Ctrl/⌘ + key) — a command is acknowledged: stacked xenocrystals + a code packet',
    gain: 1.456, reverb: 0.15,
    layers: [
      xtal(N.D6, { gain: 0.4, d: 0.12, ratio: X.odd, index: 500 }),
      xtal(N.A6, { gain: 0.35, at: 0.008, d: 0.14, ratio: X.odd, index: 500 }),
      chirp(N.D7, [1, P.M3, P.P5], 0.012, { gain: 0.18, wave: 'square', lp: 6000, crush: 5, at: 0.02 }),
    ],
  },

  // ── Focus, clipboard ────────────────────────────────────────────────────
  'focus': {
    family: 'system', desc: 'Text field focused by keyboard — a channel opens: a soft power-on with descending flutter',
    gain: 1.658, throttle: 120,
    layers: [
      { type: 'osc', wave: 'triangle', freq: N.D5, to: N.A5, sweep: 0.07, gain: 0.6,
        env: { a: 0.01, d: 0.08, r: 0.04 }, unison: { voices: 2, spread: 12 },
        ring: { freq: 900, to: 40, sweep: 0.08, depth: 0.3 } },
    ],
  },
  'copy': {
    family: 'system', desc: 'Copy — the selection is sampled into memory: two rising packets + a stored-confirm glint',
    gain: 1.603, reverb: 0.12,
    layers: [
      chirp(N.D6, [1, P.P5], 0.03, { gain: 0.5, fm: { ratio: X.xeno, index: 220, decay: 0.03 } }),
      xtal(N.D7, { gain: 0.3, at: 0.06, d: 0.05, ratio: X.root2, index: 260 }),
    ],
  },
  'cut': {
    family: 'system', desc: 'Cut — a plasma blade: ring-torn noise slash over a falling FM tone',
    gain: 2.544,
    layers: [
      { type: 'noise', color: 'white', gain: 0.5, env: { a: 0.001, d: 0.06, r: 0.02 },
        filter: { type: 'bandpass', freq: 8500, to: 1200, time: 0.06, q: 2 }, ring: { freq: 3000, depth: 0.7 } },
      { type: 'osc', wave: 'sine', freq: N.A6, to: N.D5, sweep: 0.06, gain: 0.35,
        env: { a: 0.001, d: 0.06, r: 0.02 }, fm: { ratio: X.tri, index: 400, decay: 0.05 } },
    ],
  },
  'paste': {
    family: 'system', desc: 'Paste — data materialises: descending packets that dock with a plasma thud',
    gain: 2.215,
    layers: [
      chirp(N.A5, [P.O, P.P5, 1], 0.022, { gain: 0.5, fm: { ratio: X.xeno, index: 200, decay: 0.03 } }),
      plasma(160, 70, { gain: 0.4, d: 0.05, at: 0.05 }),
    ],
  },

  // ── Windows / overlays / feedback ───────────────────────────────────────
  'open': {
    family: 'system', desc: 'Dialog / panel opens — a holographic projection resolves out of the lattice',
    gain: 1.037, reverb: 0.3,
    layers: [
      lattice(N.D4, 300, 4200, { gain: 0.45, d: 0.22, a: 0.02, flutter: 12 }),
      xtal(N.A5, { gain: 0.35, at: 0.05, d: 0.2, r: 0.1, ratio: X.root2, index: 420, vib: { rate: 6, depth: 14 } }),
      plasma(60, 120, { gain: 0.35, d: 0.18, a: 0.03, flutter: 12 }),
    ],
  },
  'close': {
    family: 'system', desc: 'Dialog closes — the projection collapses inward to a point',
    gain: 1.71, reverb: 0.16,
    layers: [
      lattice(N.D4, 4200, 280, { gain: 0.45, d: 0.15, voices: 3, flutter: 18 }),
      { type: 'osc', wave: 'sine', freq: N.A5, to: N.D4, sweep: 0.12, gain: 0.35,
        env: { a: 0.002, d: 0.12, r: 0.03 }, fm: { ratio: X.root2, index: 300, decay: 0.1 } },
    ],
  },
  'success': {
    family: 'feedback', desc: 'Success — the machine sings: a stepped xenocrystal arpeggio resolving to a high star',
    gain: 1.112, reverb: 0.34, echo: 0.14,
    layers: [
      { type: 'osc', wave: 'sine', freq: N.D5, seq: [1, P.M3, P.P5, P.O, P.O3], step: 0.055, gain: 0.55,
        env: { a: 0.002, d: 0.3, r: 0.1 }, fm: { ratio: X.xeno, index: 320, decay: 0.3, sustain: 0.3 } },
      xtal(N.D7, { gain: 0.35, at: 0.28, d: 0.3, r: 0.15, ratio: X.root2, index: 300, vib: { rate: 5, depth: 10 } }),
      plasma(N.D2, N.D3, { gain: 0.3, d: 0.3, a: 0.04, flutter: 9 }),
    ],
  },
  'error': {
    family: 'feedback', desc: 'Error — system fault: a crushed, beating double-buzz (the one sound off the scale)',
    gain: 0.791,
    layers: [
      { type: 'osc', wave: 'square', freq: 110, gain: 0.5, env: { a: 0.002, d: 0.09, r: 0.03 },
        ring: { freq: 37, depth: 0.7 }, filter: { type: 'lowpass', freq: 1100, q: 1 }, crush: 4 },
      { type: 'osc', wave: 'square', freq: 116.5, gain: 0.45, env: { a: 0.002, d: 0.09, r: 0.03 },
        filter: { type: 'lowpass', freq: 1100, q: 1 }, crush: 4 },
      { type: 'osc', wave: 'square', freq: 98, gain: 0.5, at: 0.12, env: { a: 0.002, d: 0.11, r: 0.04 },
        ring: { freq: 37, depth: 0.7 }, filter: { type: 'lowpass', freq: 900, q: 1 }, crush: 4 },
    ],
  },
  'notify': {
    family: 'feedback', desc: 'Notification — incoming transmission: a warbling packet phrase, then a beacon',
    gain: 1.478, echo: 0.26, reverb: 0.22,
    layers: [
      chirp(N.A5, [1, P.P5, P.M3, P.O], 0.042, { gain: 0.5, fm: { ratio: X.root2, index: 180, decay: 0.15, sustain: 0.4 } }),
      xtal(N.D7, { gain: 0.35, at: 0.17, d: 0.16, r: 0.1, ratio: X.xeno, index: 360, vib: { rate: 7, depth: 16 } }),
    ],
  },
  'mute': {
    family: 'feedback', desc: 'Sound turned on — the machine powers up: plasma rises, a boot packet, a crystal',
    gain: 1.032, reverb: 0.22,
    layers: [
      plasma(55, 220, { gain: 0.45, d: 0.18, a: 0.02, flutter: 17 }),
      chirp(N.D5, [1, P.P5, P.O, P.O5], 0.035, { gain: 0.4, at: 0.06, wave: 'square', lp: 3500, crush: 6 }),
      xtal(N.D6, { gain: 0.35, at: 0.2, d: 0.14, ratio: X.xeno, index: 420 }),
    ],
  },
};

export default PATCHES;
