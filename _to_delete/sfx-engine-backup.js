// ============================================================================
// Odysseus SFX — procedural sound engine (no audio files, pure Web Audio)
// ============================================================================
//
// Every sound is a *patch*: a small JSON-able config of composed layers.
// The same graph builder drives realtime playback (AudioContext) and offline
// rendering (OfflineAudioContext → WAV), so what you hear in the app is
// byte-for-byte what you export from the Sound Lab.
//
// Patch schema (all fields optional unless noted):
// {
//   gain: 0.4,              // patch output level (0..1)
//   pan: 0,                 // -1..1 (callers may override per play)
//   reverb: 0.0,            // send to shared procedural reverb (0..1)
//   echo: 0.0,              // send to shared tempo-free feedback delay (0..1)
//   jitter: 0,              // random ± semitones per play (humanize)
//   layers: [               // REQUIRED — summed in parallel
//     {
//       type: 'osc' | 'noise',
//       // osc ----------------------------------------------------------
//       wave: 'sine'|'triangle'|'square'|'sawtooth'
//             | { harmonics: [1, .5, .25, ...] },   // custom PeriodicWave
//       freq: 880,          // Hz at start
//       to: 1320,           // Hz at end of sweep (optional)
//       sweep: 0.06,        // seconds for the glide
//       curve: 'exp'|'lin', // glide shape (default exp)
//       detune: 0,          // cents
//       unison: { voices: 3, spread: 14 },   // cents spread, stereo-fanned
//       fm: { ratio: 2, index: 400, decay: 0.05 }, // sine modulator → freq
//       // noise --------------------------------------------------------
//       color: 'white'|'pink'|'brown',
//       // shared -------------------------------------------------------
//       at: 0,              // start offset (s)
//       env: { a: .002, h: 0, d: .08, s: 0, r: .04, peak: 1 },
//       filter: { type: 'lowpass', freq: 4000, to: 800, time: .06, q: 1 },
//       gain: 1,
//       pan: 0,
//     }
//   ]
// }
//
// Play-time options: { pan, pitch (semitones), velocity (0..1), rate }
// ============================================================================

const SEMI = Math.pow(2, 1 / 12);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// ── Noise buffers are generated once per context and cached ────────────────
const _noiseCache = new WeakMap();
function noiseBuffer(ctx, color = 'white') {
  let byColor = _noiseCache.get(ctx);
  if (!byColor) { byColor = {}; _noiseCache.set(ctx, byColor); }
  if (byColor[color]) return byColor[color];
  const len = Math.floor(ctx.sampleRate * 1.5);
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  // Deterministic PRNG so offline renders are reproducible.
  let seed = 0x9e3779b9;
  const rnd = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return ((seed >>> 0) / 4294967296) * 2 - 1; };
  if (color === 'pink') {
    let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
    for (let i = 0; i < len; i++) {
      const w = rnd();
      b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759;
      b2 = 0.96900 * b2 + w * 0.1538520; b3 = 0.86650 * b3 + w * 0.3104856;
      b4 = 0.55000 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.0168980;
      d[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362) * 0.11;
      b6 = w * 0.115926;
    }
  } else if (color === 'brown') {
    let last = 0;
    for (let i = 0; i < len; i++) { last = (last + 0.02 * rnd()) / 1.02; d[i] = last * 3.5; }
  } else {
    for (let i = 0; i < len; i++) d[i] = rnd();
  }
  byColor[color] = buf;
  return buf;
}

// ── Procedural reverb impulse: stereo decaying noise with HF damping ───────
function impulse(ctx, seconds = 1.6, decay = 3.2) {
  const len = Math.floor(ctx.sampleRate * seconds);
  const buf = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = buf.getChannelData(c);
    let seed = 1234 + c * 999, lp = 0;
    for (let i = 0; i < len; i++) {
      seed = (seed * 16807) % 2147483647;
      const n = (seed / 2147483647) * 2 - 1;
      const t = i / len;
      lp += (n - lp) * (0.55 - 0.45 * t);          // darker tail
      d[i] = lp * Math.pow(1 - t, decay);
    }
  }
  return buf;
}

const _waveCache = new WeakMap();
function periodicWave(ctx, harmonics) {
  let m = _waveCache.get(ctx);
  if (!m) { m = new Map(); _waveCache.set(ctx, m); }
  const key = harmonics.join(',');
  if (m.has(key)) return m.get(key);
  const real = new Float32Array(harmonics.length + 1);
  const imag = new Float32Array(harmonics.length + 1);
  harmonics.forEach((h, i) => { imag[i + 1] = h; });
  const w = ctx.createPeriodicWave(real, imag);
  m.set(key, w);
  return w;
}

// ── Envelope helper. Returns total voice length (s). ───────────────────────
function applyEnv(param, t0, env = {}, level = 1) {
  const a = env.a ?? 0.002, h = env.h ?? 0, d = env.d ?? 0.08;
  const s = env.s ?? 0, r = env.r ?? 0.04, peak = (env.peak ?? 1) * level;
  const floor = 0.00001;
  param.cancelScheduledValues(t0);
  param.setValueAtTime(floor, t0);
  param.linearRampToValueAtTime(peak, t0 + a);
  param.setValueAtTime(peak, t0 + a + h);
  const sus = Math.max(floor, peak * s);
  param.exponentialRampToValueAtTime(sus, t0 + a + h + d);
  param.setValueAtTime(sus, t0 + a + h + d);
  param.exponentialRampToValueAtTime(floor, t0 + a + h + d + r);
  return a + h + d + r;
}

function glide(param, t0, from, to, time, curve = 'exp') {
  param.setValueAtTime(from, t0);
  if (to == null || to === from) return;
  const end = t0 + Math.max(0.001, time ?? 0.05);
  if (curve === 'lin') param.linearRampToValueAtTime(to, end);
  else param.exponentialRampToValueAtTime(Math.max(1, to), end);
}

/** Total length in seconds a patch needs (incl. tail for fx). */
export function patchLength(patch) {
  let len = 0;
  for (const L of patch.layers || []) {
    const e = L.env || {};
    const l = (L.at || 0) + (e.a ?? 0.002) + (e.h ?? 0) + (e.d ?? 0.08) + (e.r ?? 0.04);
    len = Math.max(len, l);
  }
  const tail = (patch.reverb ? 1.2 : 0) + (patch.echo ? 0.9 : 0);
  return len + Math.max(0.03, tail);
}

/**
 * Build one patch's voice graph into `ctx`, connected to `out`
 * (and optional fx sends). Returns the voice length in seconds.
 */
export function buildVoice(ctx, patch, out, sends = {}, opts = {}, t0 = ctx.currentTime) {
  const pitchMul = Math.pow(SEMI, (opts.pitch || 0) + (patch.jitter ? (Math.random() * 2 - 1) * patch.jitter : 0));
  const rate = opts.rate || 1;
  const vel = opts.velocity ?? 1;

  const bus = ctx.createGain();
  bus.gain.value = (patch.gain ?? 0.4) * vel;
  const pan = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
  if (pan) { pan.pan.value = clamp(opts.pan ?? patch.pan ?? 0, -1, 1); bus.connect(pan).connect(out); }
  else bus.connect(out);
  const tap = pan || bus;
  if (patch.reverb && sends.reverb) { const g = ctx.createGain(); g.gain.value = patch.reverb; tap.connect(g).connect(sends.reverb); }
  if (patch.echo && sends.echo) { const g = ctx.createGain(); g.gain.value = patch.echo; tap.connect(g).connect(sends.echo); }

  let longest = 0;
  for (const L of patch.layers || []) {
    const start = t0 + (L.at || 0) / rate;
    const env = L.env ? { ...L.env } : {};
    if (rate !== 1) for (const k of ['a', 'h', 'd', 'r']) if (env[k] != null) env[k] /= rate;

    const vca = ctx.createGain();
    let head = vca;               // what sources connect into
    let tail = vca;
    if (L.filter) {
      const f = ctx.createBiquadFilter();
      f.type = L.filter.type || 'lowpass';
      f.Q.value = L.filter.q ?? 0.8;
      const fFrom = (L.filter.freq ?? 4000) * (L.filter.track ? pitchMul : 1);
      glide(f.frequency, start, fFrom, L.filter.to != null ? L.filter.to * (L.filter.track ? pitchMul : 1) : null, (L.filter.time ?? 0.06) / rate);
      vca.connect(f); tail = f;
    }
    let lp = null;
    if (L.pan != null && ctx.createStereoPanner) { lp = ctx.createStereoPanner(); lp.pan.value = L.pan; tail.connect(lp).connect(bus); }
    else tail.connect(bus);

    const len = applyEnv(vca.gain, start, env, L.gain ?? 1);
    const stop = start + len + 0.02;
    longest = Math.max(longest, (start - t0) + len);

    if (L.type === 'noise') {
      const src = ctx.createBufferSource();
      src.buffer = noiseBuffer(ctx, L.color || 'white');
      src.loop = true;
      src.playbackRate.value = (L.speed || 1);
      src.connect(head);
      src.start(start, Math.random() * 1.2); src.stop(stop);
      continue;
    }

    // Oscillator (with optional unison + FM)
    const voices = Math.max(1, L.unison?.voices || 1);
    const spread = L.unison?.spread || 0;
    const f0 = (L.freq ?? 440) * pitchMul;
    const f1 = L.to != null ? L.to * pitchMul : null;
    for (let v = 0; v < voices; v++) {
      const o = ctx.createOscillator();
      if (typeof L.wave === 'object' && L.wave?.harmonics) o.setPeriodicWave(periodicWave(ctx, L.wave.harmonics));
      else o.type = L.wave || 'sine';
      const off = voices > 1 ? (v / (voices - 1) - 0.5) * 2 : 0;
      o.detune.value = (L.detune || 0) + off * spread;
      glide(o.frequency, start, f0, f1, (L.sweep ?? 0.05) / rate, L.curve);

      if (L.fm) {
        const m = ctx.createOscillator();
        const mg = ctx.createGain();
        m.frequency.value = f0 * (L.fm.ratio ?? 2);
        if (f1 != null) glide(m.frequency, start, f0 * (L.fm.ratio ?? 2), f1 * (L.fm.ratio ?? 2), (L.sweep ?? 0.05) / rate, L.curve);
        const idx = (L.fm.index ?? 300) * pitchMul;
        mg.gain.setValueAtTime(idx, start);
        mg.gain.exponentialRampToValueAtTime(Math.max(0.01, idx * (L.fm.sustain ?? 0.02)), start + (L.fm.decay ?? 0.06) / rate);
        m.connect(mg).connect(o.frequency);
        m.start(start); m.stop(stop);
      }

      let dest = head;
      if (voices > 1 && ctx.createStereoPanner) {
        const p = ctx.createStereoPanner(); p.pan.value = off * 0.6;
        const g = ctx.createGain(); g.gain.value = 1 / Math.sqrt(voices);
        o.connect(g).connect(p).connect(head);
      } else o.connect(dest);
      o.start(start); o.stop(stop);
    }
  }
  return longest;
}

/** Shared master chain: comp → master gain → destination, plus fx sends. */
function buildMaster(ctx, volume = 0.6) {
  const master = ctx.createGain(); master.gain.value = volume;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -14; comp.knee.value = 12; comp.ratio.value = 4;
  comp.attack.value = 0.002; comp.release.value = 0.12;
  const input = ctx.createGain();
  input.connect(comp).connect(master).connect(ctx.destination);

  const rev = ctx.createConvolver(); rev.buffer = impulse(ctx);
  const revIn = ctx.createGain(); revIn.gain.value = 0.9;
  const revHp = ctx.createBiquadFilter(); revHp.type = 'highpass'; revHp.frequency.value = 280;
  revIn.connect(revHp).connect(rev).connect(input);

  const dl = ctx.createDelay(1.0); dl.delayTime.value = 0.155;
  const fb = ctx.createGain(); fb.gain.value = 0.38;
  const dlf = ctx.createBiquadFilter(); dlf.type = 'lowpass'; dlf.frequency.value = 3200;
  const echoIn = ctx.createGain();
  const ping = ctx.createStereoPanner ? ctx.createStereoPanner() : ctx.createGain();
  if (ping.pan) ping.pan.value = 0.45;
  echoIn.connect(dl); dl.connect(dlf).connect(fb).connect(dl); dlf.connect(ping).connect(input);

  return { input, master, sends: { reverb: revIn, echo: echoIn } };
}

// ============================================================================
// Realtime engine
// ============================================================================
const STORE_KEY = 'odysseus.sfx.v1';

export class SfxEngine {
  constructor(patches = {}) {
    this.patches = patches;
    this.ctx = null;
    this.chain = null;
    this.active = 0;
    this.maxVoices = 24;
    this._last = new Map();
    const saved = (() => { try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch { return {}; } })();
    this.enabled = saved.enabled ?? true;
    this.volume = saved.volume ?? 0.55;
    this.listeners = new Set();
  }

  _persist() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ enabled: this.enabled, volume: this.volume })); } catch {}
    this.listeners.forEach(fn => { try { fn(this); } catch {} });
  }
  onChange(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  setEnabled(on) { this.enabled = !!on; this._persist(); }
  toggle() { this.setEnabled(!this.enabled); return this.enabled; }
  setVolume(v) {
    this.volume = clamp(+v || 0, 0, 1);
    if (this.chain) this.chain.master.gain.setTargetAtTime(this.volume, this.ctx.currentTime, 0.02);
    this._persist();
  }

  /** Create/resume the context. Must be called from a user gesture first time. */
  unlock() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      this.ctx = new AC({ latencyHint: 'interactive' });
      this.chain = buildMaster(this.ctx, this.volume);
    }
    if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
    return this.ctx;
  }

  /** Is something else talking? (TTS / media) → duck the UI sounds. */
  _duck() {
    try {
      if (window.speechSynthesis?.speaking) return 0.35;
      for (const m of document.querySelectorAll('audio,video')) if (!m.paused && !m.muted) return 0.35;
    } catch {}
    return 1;
  }

  /**
   * Play a patch by name or object.
   * opts: { pan, pitch, velocity, rate, throttle (ms), force }
   */
  play(nameOrPatch, opts = {}) {
    if (!this.enabled && !opts.force) return 0;
    const patch = typeof nameOrPatch === 'string' ? this.patches[nameOrPatch] : nameOrPatch;
    if (!patch) return 0;
    const key = typeof nameOrPatch === 'string' ? nameOrPatch : patch;
    const now = performance.now();
    const thr = opts.throttle ?? patch.throttle ?? 0;
    if (thr && now - (this._last.get(key) || 0) < thr) return 0;
    this._last.set(key, now);
    if (this.active >= this.maxVoices) return 0;
    const ctx = this.unlock();
    if (!ctx || ctx.state !== 'running') {
      // First gesture may still be resuming — schedule once it is.
      if (ctx && !opts._retry) ctx.resume().then(() => this.play(nameOrPatch, { ...opts, _retry: true, throttle: 0 })).catch(() => {});
      return 0;
    }
    const v = (opts.velocity ?? 1) * this._duck();
    const len = buildVoice(ctx, patch, this.chain.input, this.chain.sends, { ...opts, velocity: v }, ctx.currentTime + 0.003);
    this.active++;
    setTimeout(() => { this.active--; }, (len + 0.1) * 1000);
    return len;
  }
}

// ============================================================================
// Offline rendering + WAV encoding (Sound Lab export)
// ============================================================================
export async function renderPatch(patch, { sampleRate = 48000, opts = {}, volume = 0.9 } = {}) {
  const len = patchLength(patch) + 0.05;
  const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const ctx = new OAC(2, Math.ceil(len * sampleRate), sampleRate);
  const chain = buildMaster(ctx, volume);
  buildVoice(ctx, patch, chain.input, chain.sends, opts, 0.002);
  return ctx.startRendering();
}

export function encodeWav(buffer, { bitDepth = 16 } = {}) {
  const ch = buffer.numberOfChannels, sr = buffer.sampleRate, n = buffer.length;
  const bps = bitDepth / 8, block = ch * bps;
  const out = new ArrayBuffer(44 + n * block);
  const v = new DataView(out);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + n * block, true); str(8, 'WAVE');
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, ch, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * block, true); v.setUint16(32, block, true); v.setUint16(34, bitDepth, true);
  str(36, 'data'); v.setUint32(40, n * block, true);
  const chans = Array.from({ length: ch }, (_, i) => buffer.getChannelData(i));
  let o = 44;
  for (let i = 0; i < n; i++) for (let c = 0; c < ch; c++) {
    const s = clamp(chans[c][i], -1, 1);
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); o += 2;
  }
  return new Blob([out], { type: 'audio/wav' });
}

export async function downloadPatch(name, patch, opts) {
  const buf = await renderPatch(patch, opts);
  const blob = encodeWav(buf);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `odysseus-${name.replace(/[^a-z0-9.-]+/gi, '-')}.wav`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  return blob;
}
