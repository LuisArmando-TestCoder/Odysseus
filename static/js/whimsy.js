// ═══════════════════════════════════════════════════════════════════════════
// Odysseus · Whimsy layer
//
// Micro-interactions for the chat and Advanced Voice Mode, built the way
// Josh Comeau teaches in "Whimsical Animations":
//   • Particles   — procedural bursts on one shared <canvas> (immediate mode:
//                   one rAF loop, gravity + air drag, twinkle; idle = no loop).
//   • Springs     — every hover/press/orb tilt is a damped spring integrated
//                   per frame, so motion keeps its velocity when interrupted.
//   • Cursor      — getBoundingClientRect-based "curiosity": magnetic buttons,
//                   a voice launcher whose rings look at the pointer, an orb
//                   that leans toward it, light that follows it.
//   • Meaning     — motion tells you something: a comet carries your message
//                   from the composer to its bubble and voice output from the
//                   orb to the chat feed; a shockwave marks a voice link.
//
// Purely additive: it observes the DOM the app already renders, never wraps
// app functions. Respects prefers-reduced-motion and forced-colors, and can be
// switched off with Alt+Shift+W (stored in localStorage 'odysseus-whimsy').
// ═══════════════════════════════════════════════════════════════════════════

const LS_KEY = 'odysseus-whimsy';
const mqReduce = matchMedia('(prefers-reduced-motion: reduce)');
const mqFine = matchMedia('(hover: hover) and (pointer: fine)');
const mqForced = matchMedia('(forced-colors: active)');

let _userOn = true;
try { _userOn = localStorage.getItem(LS_KEY) !== 'off'; } catch { /* storage blocked */ }

const on = () => _userOn && !mqReduce.matches && !mqForced.matches;
const fine = () => mqFine.matches;
function _syncRootClass() { document.documentElement.classList.toggle('wh-on', on()); }
mqReduce.addEventListener?.('change', _syncRootClass);
mqForced.addEventListener?.('change', _syncRootClass);

const rand = (a, b) => a + Math.random() * (b - a);
const pick = arr => arr[(Math.random() * arr.length) | 0];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const center = r => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
const visible = r => r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;

// ── Ticker: one rAF loop for every spring and particle ─────────────────────
const _tickers = new Set();
let _raf = 0, _last = 0;
function addTicker(fn) {
  _tickers.add(fn);
  if (!_raf) { _last = performance.now(); _raf = requestAnimationFrame(_loop); }
}
function _loop(now) {
  const dt = Math.min(0.05, (now - _last) / 1000);
  _last = now;
  for (const fn of [..._tickers]) {
    let keep = false;
    try { keep = fn(dt, now) !== false; } catch (e) { console.warn('[whimsy]', e); }
    if (!keep) _tickers.delete(fn);
  }
  _raf = _tickers.size ? requestAnimationFrame(_loop) : 0;
}

// ── Spring: semi-implicit Euler with fixed sub-steps (stable at any fps) ───
class Spring {
  constructor(v = 0, { k = 220, c = 22 } = {}) { this.x = v; this.t = v; this.v = 0; this.k = k; this.c = c; }
  step(dt) {
    const n = Math.max(1, Math.ceil(dt / 0.008)), h = dt / n;
    for (let i = 0; i < n; i++) {
      const a = -this.k * (this.x - this.t) - this.c * this.v;
      this.v += a * h; this.x += this.v * h;
    }
    return Math.abs(this.x - this.t) > 0.002 || Math.abs(this.v) > 0.002;
  }
  snap(v) { this.x = this.t = v; this.v = 0; }
}

// ── Colour: resolve theme tokens to RGB through a probe element ────────────
let _probe = null;
const _colorCache = new Map();
function rgbOf(cssColor) {
  if (_colorCache.has(cssColor)) return _colorCache.get(cssColor);
  if (!_probe) {
    _probe = document.createElement('i');
    _probe.style.cssText = 'position:absolute;width:0;height:0;visibility:hidden;pointer-events:none';
    document.body.appendChild(_probe);
  }
  _probe.style.color = '';
  _probe.style.color = cssColor;
  const m = getComputedStyle(_probe).color.match(/[\d.]+/g) || [80, 210, 255];
  const rgb = [+m[0], +m[1], +m[2]];
  _colorCache.set(cssColor, rgb);
  return rgb;
}
function palette() {
  const acc = rgbOf('var(--accent-primary, var(--red, #4fd8ff))');
  const warm = rgbOf('var(--accent-warm, #ffb347)');
  const hi = acc.map(c => Math.round(c + (255 - c) * 0.6));
  return { acc, warm, hi, white: [255, 255, 255] };
}
// Theme switches change the tokens: drop the cache.
new MutationObserver(() => { _colorCache.clear(); _lightBg = null; })
  .observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme', 'data-odx-theme'] });
let _lightBg = null;
function lightBg() {
  if (_lightBg !== null) return _lightBg;
  const m = (getComputedStyle(document.body).backgroundColor.match(/[\d.]+/g) || [0, 0, 0]).map(Number);
  _lightBg = (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255 > 0.55;
  return _lightBg;
}

// ═══════════════════════════════════════════════════════════════════════════
// PARTICLES
// ═══════════════════════════════════════════════════════════════════════════
let _cv = null, _ctx = null, _dpr = 1;
const _parts = [];
const MAX_PARTS = 700;

function _ensureCanvas() {
  if (_cv && _cv.isConnected) return true;
  _cv = document.createElement('canvas');
  _cv.className = 'wh-fx';
  _cv.setAttribute('aria-hidden', 'true');
  document.body.appendChild(_cv);
  _ctx = _cv.getContext('2d');
  _resize();
  return !!_ctx;
}
function _resize() {
  if (!_cv) return;
  _dpr = Math.min(2, devicePixelRatio || 1);
  _cv.width = Math.round(innerWidth * _dpr);
  _cv.height = Math.round(innerHeight * _dpr);
}
addEventListener('resize', _resize, { passive: true });

let _drawing = false;
function _spawn(p) {
  if (!on() || !_ensureCanvas()) return;
  if (_parts.length >= MAX_PARTS) _parts.shift();
  p.age = 0;
  _parts.push(p);
  if (!_drawing) { _drawing = true; addTicker(_drawParticles); }
}

const easeOutCubic = t => 1 - Math.pow(1 - t, 3);
const easeInOutCubic = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

function _drawParticles(dt) {
  const ctx = _ctx;
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);
  ctx.clearRect(0, 0, innerWidth, innerHeight);
  ctx.globalCompositeOperation = lightBg() ? 'source-over' : 'lighter';
  for (let i = _parts.length - 1; i >= 0; i--) {
    const p = _parts[i];
    p.age += dt;
    const t = p.age / p.life;
    if (t >= 1) { if (p.kind === 'comet' && p.onArrive) p.onArrive(); _parts.splice(i, 1); continue; }
    if (p.age < 0) continue; // delayed spawn

    if (p.kind === 'comet') { _drawComet(ctx, p, t); continue; }
    if (p.kind === 'trace') { _drawTrace(ctx, p); continue; }
    if (p.kind === 'ring') {
      const e = easeOutCubic(t);
      const r = p.r0 + (p.r1 - p.r0) * e;
      ctx.lineWidth = Math.max(0.4, p.w * (1 - e));
      ctx.strokeStyle = rgba(p.color, p.alpha * (1 - t));
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.stroke();
      continue;
    }

    // Physics: gravity, air drag, swirl (rotates the velocity vector), and
    // optional attraction toward a point (used by the voice orb's stardust).
    if (p.swirl) {
      const a = p.swirl * dt, cs = Math.cos(a), sn = Math.sin(a);
      const vx = p.vx * cs - p.vy * sn; p.vy = p.vx * sn + p.vy * cs; p.vx = vx;
    }
    if (p.attract) {
      const dx = p.attract.x - p.x, dy = p.attract.y - p.y, d = Math.hypot(dx, dy) || 1;
      p.vx += dx / d * p.attract.f * dt; p.vy += dy / d * p.attract.f * dt;
      if (d < p.attract.kill) p.age = p.life;
    }
    const drag = Math.exp(-(p.drag || 0) * dt);
    p.vx *= drag; p.vy = p.vy * drag + (p.g || 0) * dt;
    p.x += p.vx * dt; p.y += p.vy * dt;
    if (p.spin) p.rot += p.spin * dt;

    const fadeIn = Math.min(1, t / 0.08);
    let a = fadeIn * Math.pow(1 - t, 1.4) * (p.alpha ?? 1);
    if (p.twinkle) a *= 0.55 + 0.45 * Math.sin(p.age * p.twinkle + p.phase);
    if (a <= 0.004) continue;

    if (p.kind === 'spark') {
      const len = p.len ?? 0.045;
      ctx.strokeStyle = rgba(p.color, a);
      ctx.lineWidth = p.size; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x - p.vx * len, p.y - p.vy * len); ctx.stroke();
    } else if (p.kind === 'star') {
      const s = p.size * (1 - t * 0.4);
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot || 0);
      ctx.fillStyle = rgba(p.color, a);
      ctx.beginPath();
      ctx.moveTo(0, -s); ctx.quadraticCurveTo(0, 0, s, 0); ctx.quadraticCurveTo(0, 0, 0, s);
      ctx.quadraticCurveTo(0, 0, -s, 0); ctx.quadraticCurveTo(0, 0, 0, -s);
      ctx.fill(); ctx.restore();
    } else { // dot, with a cheap halo instead of shadowBlur
      const s = p.size * (p.shrink ? 1 - t * 0.7 : 1);
      ctx.fillStyle = rgba(p.color, a * 0.22);
      ctx.beginPath(); ctx.arc(p.x, p.y, s * 2.6, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = rgba(p.color, a);
      ctx.beginPath(); ctx.arc(p.x, p.y, s, 0, Math.PI * 2); ctx.fill();
    }
  }
  if (!_parts.length) { ctx.clearRect(0, 0, innerWidth, innerHeight); _drawing = false; return false; }
  return true;
}

function _drawComet(ctx, p, t) {
  const e = easeInOutCubic(t), u = 1 - e;
  const x = u * u * p.x0 + 2 * u * e * p.cx + e * e * p.x1;
  const y = u * u * p.y0 + 2 * u * e * p.cy + e * e * p.y1;
  // shed a trail of embers
  const n = p.trail ?? 2;
  for (let i = 0; i < n; i++) {
    _parts.push({ kind: 'dot', x: x + rand(-1.5, 1.5), y: y + rand(-1.5, 1.5), vx: rand(-25, 25), vy: rand(-25, 25),
      life: rand(0.22, 0.42), size: rand(0.8, 1.7), color: Math.random() < .3 ? p.hi : p.color, drag: 3, shrink: true, age: 0 });
  }
  ctx.fillStyle = rgba(p.color, 0.25);
  ctx.beginPath(); ctx.arc(x, y, p.size * 3.4, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = rgba(p.hi, 1);
  ctx.beginPath(); ctx.arc(x, y, p.size, 0, Math.PI * 2); ctx.fill();
}

/** A radial burst. Everything is randomised inside ranges so no two bursts look alike. */
function burst(x, y, o = {}) {
  if (!on()) return;
  const pal = palette();
  const colors = o.colors || [pal.acc, pal.hi, pal.white];
  const n = o.count ?? 16;
  const spread = o.spread ?? Math.PI * 2;
  const dir = o.angle ?? -Math.PI / 2;
  for (let i = 0; i < n; i++) {
    const ang = dir + (spread >= Math.PI * 2 ? (i / n) * Math.PI * 2 + rand(-.2, .2) : rand(-spread / 2, spread / 2));
    const sp = rand(...(o.speed || [140, 320]));
    const kind = pick(o.kinds || ['spark', 'dot', 'star']);
    _spawn({
      kind, x: x + Math.cos(ang) * (o.r0 || 0), y: y + Math.sin(ang) * (o.r0 || 0),
      vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp,
      life: rand(...(o.life || [0.45, 0.85])), size: rand(...(o.size || (kind === 'star' ? [3, 5.5] : [1.2, 2.6]))),
      color: pick(colors), g: o.gravity ?? 380, drag: o.drag ?? 2.4,
      rot: rand(0, Math.PI), spin: rand(-6, 6), twinkle: kind === 'star' ? rand(14, 26) : 0, phase: rand(0, 6),
      swirl: o.swirl ? o.swirl * (Math.random() < .5 ? 1 : 1.25) : 0, alpha: o.alpha ?? 1,
    });
  }
  if (o.ring !== false) ring(x, y, { r0: o.ringFrom ?? 4, r1: o.ringTo ?? 34, color: colors[0], w: o.ringWidth ?? 2.2, life: o.ringLife ?? 0.5 });
}
function ring(x, y, { r0 = 4, r1 = 40, color, w = 2, life = 0.55, alpha = 0.9, delay = 0 } = {}) {
  _spawn({ kind: 'ring', x, y, r0, r1, w, life, alpha, color: color || palette().acc, age: 0 });
  if (delay) _parts[_parts.length - 1].age = -delay;
}
// ── Circuit trace: an electron runs along a PCB route ──────────────────────
// Routing is printed-circuit style: straight runs joined by 45° bends, snapped
// to a 4px grid, with solder pads at every bend and a faint bus of parallel
// traces either side — a silicon plate, not a magic arc.
function _route(a, b) {
  const q = v => Math.round(v / 4) * 4;
  const dx = b.x - a.x, dy = b.y - a.y, adx = Math.abs(dx), ady = Math.abs(dy);
  const sx = Math.sign(dx) || 1, sy = Math.sign(dy) || 1;
  const pts = [{ x: a.x, y: a.y }];
  if (adx >= ady) {               // horizontal → 45° → horizontal
    const run = (adx - ady) * 0.42;
    pts.push({ x: q(a.x + sx * run), y: a.y });
    pts.push({ x: q(a.x + sx * (run + ady)), y: b.y });
  } else {                        // vertical → 45° → vertical
    const run = (ady - adx) * 0.42;
    pts.push({ x: a.x, y: q(a.y + sy * run) });
    pts.push({ x: b.x, y: q(a.y + sy * (run + adx)) });
  }
  pts.push({ x: b.x, y: b.y });
  // drop zero-length segments
  return pts.filter((p, i) => i === 0 || Math.hypot(p.x - pts[i - 1].x, p.y - pts[i - 1].y) > 0.5);
}
function _offsetPolyline(pts, d) {
  const out = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i], a = pts[i - 1] || p, b = pts[i + 1] || p;
    const n1 = i > 0 ? _norm(a, p) : _norm(p, b), n2 = i < pts.length - 1 ? _norm(p, b) : n1;
    let nx = n1.x + n2.x, ny = n1.y + n2.y; const l = Math.hypot(nx, ny) || 1; nx /= l; ny /= l;
    const k = 1 / Math.max(0.5, nx * n1.x + ny * n1.y);      // miter length
    out.push({ x: p.x + nx * d * k, y: p.y + ny * d * k });
  }
  return out;
}
function _norm(a, b) { const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1; return { x: -dy / l, y: dx / l }; }
function _cumLen(pts) { const c = [0]; for (let i = 1; i < pts.length; i++) c.push(c[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y)); return c; }
function _pointAt(pts, cum, d) {
  d = clamp(d, 0, cum[cum.length - 1]);
  let i = 1; while (i < cum.length - 1 && cum[i] < d) i++;
  const seg = cum[i] - cum[i - 1] || 1, u = (d - cum[i - 1]) / seg;
  return { x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * u, y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * u };
}
function _strokeRange(ctx, pts, cum, d0, d1) {
  if (d1 <= d0) return;
  ctx.beginPath();
  const a = _pointAt(pts, cum, d0); ctx.moveTo(a.x, a.y);
  for (let i = 1; i < pts.length; i++) if (cum[i] > d0 && cum[i] < d1) ctx.lineTo(pts[i].x, pts[i].y);
  const b = _pointAt(pts, cum, d1); ctx.lineTo(b.x, b.y);
  ctx.stroke();
}
const easeInOutQuad = t => t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;

function _drawTrace(ctx, p) {
  const a = p.age, L = p.cum[p.cum.length - 1];
  const prog = Math.min(1, a / p.dur);
  const head = easeInOutQuad(prog) * L;
  const appear = Math.min(1, a / 0.12);
  const fade = a > p.dur ? Math.max(0, 1 - (a - p.dur) / p.fade) : 1;
  const k = appear * fade;
  if (!p.arrived && a >= p.dur) { p.arrived = true; p.onArrive?.(); }
  ctx.lineCap = 'square'; ctx.lineJoin = 'miter';

  // silicon: the bus of parallel ghost traces
  ctx.lineWidth = 1;
  ctx.strokeStyle = rgba(p.color, 0.07 * k);
  for (const bus of p.bus) { ctx.beginPath(); ctx.moveTo(bus[0].x, bus[0].y); for (let i = 1; i < bus.length; i++) ctx.lineTo(bus[i].x, bus[i].y); ctx.stroke(); }
  // the etched copper trace, end to end
  ctx.lineWidth = 2;
  ctx.strokeStyle = rgba(p.color, 0.16 * k);
  _strokeRange(ctx, p.pts, p.cum, 0, L);
  // the part the electron has energised stays lit
  ctx.strokeStyle = rgba(p.color, 0.38 * k);
  _strokeRange(ctx, p.pts, p.cum, 0, head);
  // hot trail right behind the electron (glow pass + core pass)
  const trail = 150, steps = 18;
  for (let j = 0; j < steps; j++) {
    const d0 = head - trail * (1 - j / steps), d1 = head - trail * (1 - (j + 1) / steps);
    if (d1 <= 0) continue;
    const w = Math.pow((j + 1) / steps, 1.6);
    ctx.lineWidth = 6; ctx.strokeStyle = rgba(p.color, 0.22 * w * fade);
    _strokeRange(ctx, p.pts, p.cum, Math.max(0, d0), d1);
    ctx.lineWidth = 2; ctx.strokeStyle = rgba(p.hi, 0.9 * w * fade);
    _strokeRange(ctx, p.pts, p.cum, Math.max(0, d0), d1);
  }
  // solder pads (start, every bend, end) flash as the electron crosses them
  for (let i = 0; i < p.pts.length; i++) {
    const pad = p.pts[i];
    if (p.passAt[i] == null && head >= p.cum[i] - 0.5) {
      p.passAt[i] = a;
      if (i > 0 && i < p.pts.length - 1) for (let n = 0; n < 3; n++) {   // sparks jump at the bend
        const ang = rand(0, Math.PI * 2), sp = rand(60, 160);
        _parts.push({ kind: 'spark', x: pad.x, y: pad.y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp, life: rand(0.12, 0.25), size: 1, len: 0.03, color: p.hi, drag: 6, age: 0 });
      }
    }
    const flash = p.passAt[i] != null ? Math.exp(-(a - p.passAt[i]) * 7) : 0;
    const r = (i === p.pts.length - 1 ? 3.6 : 2.8) + flash * 2;
    ctx.lineWidth = 1.2; ctx.strokeStyle = rgba(p.color, (0.35 + flash * 0.6) * k);
    ctx.beginPath(); ctx.arc(pad.x, pad.y, r, 0, Math.PI * 2); ctx.stroke();
    if (flash > 0.02) { ctx.fillStyle = rgba(p.hi, flash * 0.9 * fade); ctx.beginPath(); ctx.arc(pad.x, pad.y, r * 0.55, 0, Math.PI * 2); ctx.fill(); }
  }
  // the electron
  if (prog < 1) {
    const e = _pointAt(p.pts, p.cum, head);
    ctx.fillStyle = rgba(p.color, 0.12); ctx.beginPath(); ctx.arc(e.x, e.y, 13, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = rgba(p.color, 0.38); ctx.beginPath(); ctx.arc(e.x, e.y, 6, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,1)'; ctx.beginPath(); ctx.arc(e.x, e.y, 2.2, 0, Math.PI * 2); ctx.fill();
  }
}

/** An electron that runs a PCB route — trace(from, to, opts) or trace(points, opts); onArrive fires on contact. */
function trace(fromOrPts, to, opts) {
  if (Array.isArray(fromOrPts)) { opts = to; to = null; }
  const { color, speed = 1500, onArrive } = opts || {};
  if (!on()) { onArrive?.(); return; }
  const c = color || palette().acc;
  const hi = c.map(v => Math.round(v + (255 - v) * 0.7));
  const pts = Array.isArray(fromOrPts) ? fromOrPts : _route(fromOrPts, to);
  const cum = _cumLen(pts);
  const L = cum[cum.length - 1];
  const dur = clamp(L / speed, 0.28, 0.75);
  const bus = [-7, 7, 14].map(d => _offsetPolyline(pts, d));
  _spawn({ kind: 'trace', pts, cum, bus, passAt: [], dur, fade: 0.55, life: dur + 0.6, color: c, hi, onArrive, age: 0 });
}

/** A comet that flies along a gentle arc and calls onArrive when it lands. */
function comet(from, to, { color, dur = 0.42, arc = 0.28, size = 2.4, trail = 2, onArrive } = {}) {
  if (!on()) { onArrive?.(); return; }
  const pal = palette();
  const c = color || pal.acc;
  const hi = c.map(v => Math.round(v + (255 - v) * 0.65));
  const dx = to.x - from.x, dy = to.y - from.y;
  // control point: midpoint pushed perpendicular to the path
  const cx = (from.x + to.x) / 2 - dy * arc, cy = (from.y + to.y) / 2 + dx * arc;
  _spawn({ kind: 'comet', x0: from.x, y0: from.y, x1: to.x, y1: to.y, cx, cy, life: dur, size, color: c, hi, trail, onArrive });
}

// ═══════════════════════════════════════════════════════════════════════════
// SPRING-DRIVEN BUTTONS: magnetic hover + squishy press
// ═══════════════════════════════════════════════════════════════════════════
const MAGNETIC = [
  '.send-btn', '.input-icon-btn', '.model-picker-btn', '.mode-toggle-btn',
  '.msg-action-btn', '.footer-copy-btn', '.msg-more-btn',
  '.voice-mode-fab', '.scroll-nav-btn',
  '.vx-dock-btn', '.vx-icon-btn', '.vx-inject-acts button', '.vx-card-acts button', '.vx-notice-btn',
].join(',');
const _btn = new WeakMap();
const _activeBtns = new Set();

function _btnState(el) {
  let s = _btn.get(el);
  if (!s) {
    const big = el.matches('.voice-mode-fab, .vx-primary, .send-btn');
    s = { el, x: new Spring(0, { k: 260, c: 20 }), y: new Spring(0, { k: 260, c: 20 }), s: new Spring(1, { k: 520, c: 24 }),
      max: big ? 7 : 4, pull: big ? 0.28 : 0.22, hover: false };
    _btn.set(el, s);
  }
  return s;
}
function _wake(s) {
  _activeBtns.add(s);
  addTicker(_tickButtons);
}
function _tickButtons(dt) {
  for (const s of _activeBtns) {
    const a = s.x.step(dt), b = s.y.step(dt), c = s.s.step(dt);
    if (!a && !b && !c && !s.hover && !s.down) {
      s.el.style.translate = ''; s.el.style.scale = '';
      _activeBtns.delete(s);
      continue;
    }
    s.el.style.translate = `${s.x.x.toFixed(2)}px ${s.y.x.toFixed(2)}px`;
    s.el.style.scale = s.s.x.toFixed(4);
  }
  return _activeBtns.size > 0;
}

document.addEventListener('pointermove', e => {
  if (!on() || !fine()) return;
  const el = e.target.closest?.(MAGNETIC);
  if (!el || el.disabled) return;
  const s = _btnState(el), r = el.getBoundingClientRect();
  // The spring's own offset moved the rect; measure from the resting centre.
  const cx = r.left + r.width / 2 - s.x.x, cy = r.top + r.height / 2 - s.y.x;
  s.hover = true;
  s.x.t = clamp((e.clientX - cx) * s.pull, -s.max, s.max);
  s.y.t = clamp((e.clientY - cy) * s.pull, -s.max, s.max);
  _wake(s);
}, { passive: true });
document.addEventListener('pointerout', e => {
  const el = e.target.closest?.(MAGNETIC);
  if (!el || el.contains(e.relatedTarget)) return;
  const s = _btn.get(el); if (!s) return;
  s.hover = false; s.down = false; s.x.t = 0; s.y.t = 0; s.s.t = 1;
  _wake(s);
}, { passive: true });
document.addEventListener('pointerdown', e => {
  if (!on()) return;
  const el = e.target.closest?.(MAGNETIC);
  if (!el || el.disabled) return;
  const s = _btnState(el);
  s.down = true; s.s.t = 0.9;
  _wake(s);
}, { passive: true });
const _release = () => {
  for (const s of _activeBtns) if (s.down) { s.down = false; s.s.t = 1; s.s.v += 2.2; }
};
document.addEventListener('pointerup', _release, { passive: true });
document.addEventListener('pointercancel', _release, { passive: true });

// ── Confirmations: copy and toggles celebrate a little ─────────────────────
document.addEventListener('click', e => {
  if (!on()) return;
  const btn = e.target.closest?.('button, [role="button"]');
  if (!btn) return;
  const label = (btn.getAttribute('aria-label') || btn.title || btn.textContent || '').trim();
  const r = btn.getBoundingClientRect();
  if (!visible(r)) return;
  const c = center(r);

  if (/^copy\b/i.test(label) && !btn.dataset.busy) {
    _pop(btn);
    burst(c.x, c.y, { count: 12, speed: [90, 220], gravity: 520, kinds: ['star', 'dot', 'star'], ringTo: 22, life: [0.4, 0.75] });
    return;
  }
  // Pressable toggles (web search, shell, Agent/Chat …): only celebrate turning ON.
  if (btn.hasAttribute('aria-pressed') || btn.dataset.modeTool) {
    const was = btn.getAttribute('aria-pressed') === 'true' || btn.classList.contains('active');
    setTimeout(() => {
      const now = btn.getAttribute('aria-pressed') === 'true' || btn.classList.contains('active');
      if (now && !was) {
        _pop(btn);
        burst(c.x, c.y, { count: 10, speed: [70, 170], gravity: 200, kinds: ['spark', 'dot'], ringTo: Math.max(r.width, r.height) * 0.9, life: [0.35, 0.6] });
      }
    }, 0);
  }
}, true);

function _pop(el) {
  el.classList.remove('wh-pop');
  void el.offsetWidth;
  el.classList.add('wh-pop');
  el.addEventListener('animationend', () => el.classList.remove('wh-pop'), { once: true });
}

// ═══════════════════════════════════════════════════════════════════════════
// CHAT
// ═══════════════════════════════════════════════════════════════════════════
let _sendIntentAt = 0;
let _sendOrigin = null;

function _composerOrigin() {
  const send = document.querySelector('.chat-input-bar .send-btn');
  const r = send?.getBoundingClientRect();
  if (r && visible(r)) return center(r);
  const ta = document.getElementById('message')?.getBoundingClientRect();
  return ta && visible(ta) ? { x: ta.right - 24, y: ta.top + ta.height / 2 } : null;
}

// Remember that the user just sent something (and from where), so only the
// message *they* sent gets the comet — not a history load or a re-render.
document.addEventListener('keydown', e => {
  if (e.target?.id === 'message' && e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.target.value.trim()) {
    _sendIntentAt = performance.now(); _sendOrigin = _composerOrigin();
  }
  // Alt+Shift+W toggles the whole layer.
  if (e.altKey && e.shiftKey && (e.code === 'KeyW')) {
    e.preventDefault();
    _userOn = !_userOn;
    try { localStorage.setItem(LS_KEY, _userOn ? 'on' : 'off'); } catch { /* ignore */ }
    _syncRootClass();
    window.showToast?.(_userOn ? 'Whimsy on ✨' : 'Whimsy off');
  }
}, true);
document.addEventListener('pointerdown', e => {
  const send = e.target.closest?.('.chat-input-bar .send-btn');
  if (!send) return;
  const ta = document.getElementById('message');
  if (ta?.value.trim() || send.dataset.mode === 'newchat') { _sendIntentAt = performance.now(); _sendOrigin = center(send.getBoundingClientRect()); }
}, true);

function _onNewUserMsg(msg) {
  const fresh = performance.now() - _sendIntentAt < 2500;
  if (!fresh || !on()) return;
  _sendIntentAt = 0;
  msg.classList.add('wh-enter-user');
  msg.addEventListener('animationend', function done(ev) {
    if (ev.target !== msg) return;
    msg.classList.remove('wh-enter-user'); msg.removeEventListener('animationend', done);
  });
  const from = _sendOrigin || _composerOrigin();
  _sendOrigin = null;
  if (!from) return;
  burst(from.x, from.y, { count: 12, speed: [120, 260], angle: -Math.PI / 2, spread: Math.PI * 0.9, gravity: 260, kinds: ['spark', 'dot'], ringTo: 26 });
  requestAnimationFrame(() => {
    const r = msg.getBoundingClientRect();
    if (!visible(r)) return;
    const to = { x: r.right - Math.min(40, r.width / 3), y: r.top + Math.min(r.height / 2, 28) };
    comet(from, to, { dur: 0.34, arc: -0.22, size: 3, trail: 3, onArrive: () => {
      burst(to.x, to.y, { count: 8, speed: [60, 150], gravity: 120, kinds: ['dot', 'star'], ringTo: 18, life: [0.3, 0.55] });
    } });
  });
}

function _onNewAiMsg(msg) {
  if (!on() || !msg.classList.contains('streaming')) return;
  if (msg.classList.contains('msg-continuation')) return;
  msg.classList.add('wh-enter-ai');
  msg.addEventListener('animationend', function done(ev) {
    if (ev.target !== msg) return;
    msg.classList.remove('wh-enter-ai'); msg.removeEventListener('animationend', done);
  });
}

// Bubble overlays live in a fixed layer on <body>, never inside the message:
// other modules read a bubble's children (footers, last child, raw text).
function _overlayOn(msg, el) {
  const r = msg.getBoundingClientRect();
  const clipR = (document.getElementById('chat-history') || document.body).getBoundingClientRect();
  el.style.left = `${r.left}px`; el.style.top = `${r.top}px`;
  el.style.width = `${r.width}px`; el.style.height = `${r.height}px`;
  const cs = getComputedStyle(msg);
  el.style.borderRadius = cs.borderRadius;
  // Clip to the scrolling history so nothing paints over the header/composer;
  // otherwise borrow the bubble's own shape (the HUD themes chamfer corners).
  const t = Math.max(0, clipR.top - r.top), b = Math.max(0, r.bottom - clipR.bottom);
  el.style.clipPath = (t || b) ? `inset(${t}px 0 ${b}px 0)` : (cs.clipPath !== 'none' ? cs.clipPath : '');
  return r;
}

function _onAiDone(msg) {
  if (!on() || !msg.isConnected) return;
  const r = msg.getBoundingClientRect();
  if (!visible(r) || r.height < 24) return;
  const sw = document.createElement('span');
  sw.className = 'wh-sweep';
  sw.setAttribute('aria-hidden', 'true');
  document.body.appendChild(sw);
  _overlayOn(msg, sw);
  sw.addEventListener('animationend', () => sw.remove(), { once: true });
  setTimeout(() => sw.remove(), 1600);
  // a few embers rise from the bottom-left corner, where the footer appears
  const x = r.left + 22, y = Math.min(r.bottom - 10, innerHeight - 20);
  burst(x, y, { count: 9, speed: [50, 140], angle: -Math.PI / 2, spread: Math.PI * 0.7, gravity: -40, drag: 2, kinds: ['star', 'dot'], ring: false, life: [0.6, 1.1], alpha: 0.85 });
}

let _histObs = null;
function _watchChatHistory() {
  const hist = document.getElementById('chat-history');
  if (!hist || _histObs) return;
  _histObs = new MutationObserver(muts => {
    const added = [];
    for (const m of muts) {
      if (m.type === 'attributes') {
        const el = m.target;
        if (m.attributeName === 'class' && el.classList?.contains('msg-ai') &&
            (m.oldValue || '').split(/\s+/).includes('streaming') && !el.classList.contains('streaming')) {
          _onAiDone(el);
        }
        continue;
      }
      for (const n of m.addedNodes) if (n.nodeType === 1 && n.classList.contains('msg')) added.push(n);
    }
    // A batch of bubbles is a session load or re-render — never animate those.
    if (!added.length || added.length > 2) return;
    for (const n of added) {
      if (n.classList.contains('msg-user')) _onNewUserMsg(n);
      else if (n.classList.contains('msg-ai')) _onNewAiMsg(n);
    }
  });
  _histObs.observe(hist, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'], attributeOldValue: true });
}

// ── Composer: border light that follows the cursor; comet orbit while streaming
let _spot = null, _bar = null;
function _setupComposer() {
  const bar = document.querySelector('.chat-input-bar');
  if (!bar || _bar === bar) return;
  _bar = bar;
  _spot = document.createElement('span');
  _spot.className = 'wh-spot';
  _spot.setAttribute('aria-hidden', 'true');
  bar.appendChild(_spot);
  let raf = 0, px = 0, py = 0;
  bar.addEventListener('pointermove', e => {
    px = e.clientX; py = e.clientY;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const r = bar.getBoundingClientRect();
      _spot.style.setProperty('--wx', `${px - r.left}px`);
      _spot.style.setProperty('--wy', `${py - r.top}px`);
    });
  }, { passive: true });

  const send = bar.querySelector('.send-btn');
  const sync = () => bar.classList.toggle('wh-streaming', send?.dataset.mode === 'streaming');
  if (send) new MutationObserver(sync).observe(send, { attributes: true, attributeFilter: ['data-mode'] });
  sync();
}

// ── Bubble glint: one reused overlay follows the cursor across messages ───
const _glint = document.createElement('span');
_glint.className = 'wh-glint';
_glint.setAttribute('aria-hidden', 'true');
let _glintHost = null, _glintRaf = 0, _gx = 0, _gy = 0;
function _placeGlint() {
  _glintRaf = 0;
  if (!_glintHost || !_glintHost.isConnected) { _glint.classList.remove('on'); _glintHost = null; return; }
  const r = _overlayOn(_glintHost, _glint);
  _glint.style.setProperty('--mx', `${_gx - r.left}px`);
  _glint.style.setProperty('--my', `${_gy - r.top}px`);
}
document.addEventListener('pointermove', e => {
  if (!on() || !fine()) return;
  const msg = e.target.closest?.('.chat-history .msg-user, .chat-history .msg-ai');
  if (msg !== _glintHost) {
    _glintHost = msg;
    if (!msg) { _glint.classList.remove('on'); return; }
    if (!_glint.isConnected) document.body.appendChild(_glint);
    _glint.classList.remove('on');
    _gx = e.clientX; _gy = e.clientY; _placeGlint();
    requestAnimationFrame(() => _glint.classList.add('on'));
    return;
  }
  if (!msg) return;
  _gx = e.clientX; _gy = e.clientY;
  if (!_glintRaf) _glintRaf = requestAnimationFrame(_placeGlint);
}, { passive: true });
// keep it glued to the bubble while the history scrolls under a still cursor
document.addEventListener('scroll', () => { if (_glintHost && !_glintRaf) _glintRaf = requestAnimationFrame(_placeGlint); }, { passive: true, capture: true });

// ── The voice launcher looks at you ────────────────────────────────────────
// Like the googly-eyed Like button: the orb's inner ring and pupil slide toward
// the cursor, and the pupil dilates when the pointer comes close.
let _fab = null, _fabParts = null, _fabTarget = { x: 0, y: 0, d: 0 };
function _setupFab() {
  const fab = document.getElementById('voice-mode-fab');
  if (!fab || _fab === fab) return;
  _fab = fab;
  const svg = fab.querySelector('.vx-fab-orb');
  const circles = svg ? svg.querySelectorAll('circle') : [];
  if (circles.length < 3) return;
  _fabParts = [
    { el: circles[1], max: 1.3, x: new Spring(0, { k: 160, c: 14 }), y: new Spring(0, { k: 160, c: 14 }) },
    { el: svg.querySelector('path'), max: 0.6, x: new Spring(0, { k: 120, c: 13 }), y: new Spring(0, { k: 120, c: 13 }) },
    { el: circles[2], max: 3.2, x: new Spring(0, { k: 200, c: 15 }), y: new Spring(0, { k: 200, c: 15 }), s: new Spring(1, { k: 260, c: 16 }) },
  ].filter(p => p.el);
}
function _tickFab(dt) {
  if (!_fabParts) return false;
  let moving = false;
  for (const p of _fabParts) {
    const reach = clamp(_fabTarget.d, 0, 1);
    p.x.t = _fabTarget.x * p.max * reach; p.y.t = _fabTarget.y * p.max * reach;
    moving = p.x.step(dt) | p.y.step(dt) || moving;
    let sc = 1;
    if (p.s) { p.s.t = _fabTarget.near ? 1.45 : 1; moving = p.s.step(dt) || moving; sc = p.s.x; }
    p.el.style.transform = `translate(${p.x.x.toFixed(3)}px, ${p.y.x.toFixed(3)}px)` + (p.s ? ` scale(${sc.toFixed(3)})` : '');
    if (p.s) p.el.style.transformOrigin = `${12}px ${12}px`;
  }
  return moving;
}
let _fabRaf = 0, _fabPX = 0, _fabPY = 0;
document.addEventListener('pointermove', e => {
  if (!on() || !fine() || !_fabParts) return;
  _fabPX = e.clientX; _fabPY = e.clientY;
  if (_fabRaf) return;
  _fabRaf = requestAnimationFrame(() => {
    _fabRaf = 0;
    const r = _fab.getBoundingClientRect();
    if (!visible(r)) return;
    const svgR = _fab.querySelector('.vx-fab-orb')?.getBoundingClientRect() || r;
    const c = center(svgR);
    const dx = _fabPX - c.x, dy = _fabPY - c.y, d = Math.hypot(dx, dy) || 1;
    // Attention falls off with distance: fully curious within ~420px.
    _fabTarget = { x: dx / d, y: dy / d, d: clamp(1.25 - d / 520, 0, 1), near: d < 70 };
    addTicker(_tickFab);
  });
}, { passive: true });
document.addEventListener('pointerleave', () => { _fabTarget = { x: 0, y: 0, d: 0 }; if (_fabParts) addTicker(_tickFab); });

// ═══════════════════════════════════════════════════════════════════════════
// ADVANCED VOICE MODE
// ═══════════════════════════════════════════════════════════════════════════
let _vx = null;
const _orb = {
  tx: new Spring(0, { k: 90, c: 13 }), ty: new Spring(0, { k: 90, c: 13 }),   // wrap parallax (px)
  rx: new Spring(0, { k: 110, c: 12 }), ry: new Spring(0, { k: 110, c: 12 }), // canvas tilt (deg)
  lx: new Spring(0, { k: 140, c: 14 }), ly: new Spring(0, { k: 140, c: 14 }), // label parallax (px)
};
let _orbHover = false, _lastMote = 0, _lastPulse = 0, _prevState = 'idle';

function _vxOpen() { return _vx && _vx.isConnected && _vx.classList.contains('open') && getComputedStyle(_vx).display !== 'none'; }
function _orbGeom() {
  const w = _vx?.querySelector('.vx-orb-wrap');
  if (!w) return null;
  const r = w.getBoundingClientRect();
  if (!r.width) return null;
  // undo our own parallax so the geometry is the resting one
  return { x: r.left + r.width / 2 - _orb.tx.x, y: r.top + r.height / 2 - _orb.ty.x, R: Math.min(r.width, r.height) / 2 * 0.5 };
}
function _stateColor() {
  if (!_vx) return palette().acc;
  const cs = _vx.style;
  const v = ['--vx-r', '--vx-g', '--vx-b'].map(k => parseFloat(cs.getPropertyValue(k)));
  return v.every(Number.isFinite) ? v : palette().acc;
}

function _tickOrb(dt) {
  if (!_vx) return false;
  const wrap = _vx.querySelector('.vx-orb-wrap');
  const canvas = _vx.querySelector('#vx-canvas');
  const label = _vx.querySelector('.vx-orb-label');
  let m = false;
  for (const k in _orb) m = _orb[k].step(dt) || m;
  if (wrap) wrap.style.translate = `${_orb.tx.x.toFixed(2)}px ${_orb.ty.x.toFixed(2)}px`;
  if (canvas) canvas.style.transform = `perspective(1100px) rotateX(${_orb.rx.x.toFixed(3)}deg) rotateY(${_orb.ry.x.toFixed(3)}deg)`;
  if (label) label.style.translate = `${_orb.lx.x.toFixed(2)}px ${_orb.ly.x.toFixed(2)}px`;
  if (!m && !_orbHover) {
    if (wrap) wrap.style.translate = '';
    if (canvas) canvas.style.transform = '';
    if (label) label.style.translate = '';
    return false;
  }
  return true;
}

let _vxRaf = 0, _vxPX = 0, _vxPY = 0;
function _onVxPointer(e) {
  if (!on() || !fine() || !_vxOpen()) return;
  _vxPX = e.clientX; _vxPY = e.clientY;
  if (_vxRaf) return;
  _vxRaf = requestAnimationFrame(() => {
    _vxRaf = 0;
    const g = _orbGeom(); if (!g) return;
    const dx = _vxPX - g.x, dy = _vxPY - g.y;
    const d = Math.hypot(dx, dy);
    // normalised offset, capped so the far corners don't over-rotate
    const nx = clamp(dx / (g.R * 4), -1, 1), ny = clamp(dy / (g.R * 4), -1, 1);
    _orbHover = true;
    _orb.tx.t = nx * 10; _orb.ty.t = ny * 10;
    _orb.ry.t = nx * 9; _orb.rx.t = -ny * 9;
    _orb.lx.t = nx * 6; _orb.ly.t = ny * 6;
    addTicker(_tickOrb);

    // Stardust: near the orb, the pointer sheds motes the core drinks in.
    const now = performance.now();
    if (d < g.R * 2.4 && d > g.R * 0.75 && now - _lastMote > 28) {
      _lastMote = now;
      const col = _stateColor();
      _spawn({ kind: 'dot', x: _vxPX + rand(-4, 4), y: _vxPY + rand(-4, 4), vx: rand(-30, 30), vy: rand(-30, 30),
        life: rand(0.7, 1.2), size: rand(0.8, 1.8), color: Math.random() < .3 ? [255, 255, 255] : col,
        drag: 1.2, attract: { x: g.x, y: g.y, f: 900, kill: g.R * 0.55 }, twinkle: rand(10, 20), phase: rand(0, 6), alpha: 0.8 });
    }
  });
}
function _onVxLeave() {
  _orbHover = false;
  for (const k in _orb) _orb[k].t = 0;
  addTicker(_tickOrb);
}

function _halo() {
  const w = _vx?.querySelector('.vx-orb-wrap');
  if (!w || !on()) return;
  const h = document.createElement('div');
  h.className = 'wh-halo';
  h.setAttribute('aria-hidden', 'true');
  w.prepend(h);
  h.addEventListener('animationend', () => h.remove(), { once: true });
  setTimeout(() => h.remove(), 1400);
}

function _onVxState(next, prev) {
  if (!on() || !_vxOpen()) return;
  const g = _orbGeom(); if (!g) return;
  const col = _stateColor();
  const hi = col.map(v => Math.round(v + (255 - v) * 0.6));
  const now = performance.now();

  if (next === 'connecting') {
    // Charge-up: motes gather from a wide ring into the core.
    for (let i = 0; i < 46; i++) {
      const a = rand(0, Math.PI * 2), r = g.R * rand(2.0, 2.8);
      _spawn({ kind: 'dot', x: g.x + Math.cos(a) * r, y: g.y + Math.sin(a) * r,
        vx: -Math.sin(a) * 60, vy: Math.cos(a) * 60, life: rand(0.9, 1.4), size: rand(0.8, 2),
        color: Math.random() < .35 ? hi : col, drag: 1.4, attract: { x: g.x, y: g.y, f: 1300, kill: g.R * 0.45 },
        twinkle: rand(10, 22), phase: rand(0, 6), age: -rand(0, 0.35) });
    }
    return;
  }
  if (next === 'listening' && (prev === 'connecting' || prev === 'idle')) {
    // Link established: double shockwave + a swirl of sparks.
    _halo();
    ring(g.x, g.y, { r0: g.R * 0.9, r1: g.R * 2.6, color: hi, w: 3, life: 0.9 });
    ring(g.x, g.y, { r0: g.R * 0.9, r1: g.R * 2.0, color: col, w: 2, life: 0.8, delay: 0.12 });
    burst(g.x, g.y, { r0: g.R * 0.95, count: 54, speed: [180, 420], gravity: 0, drag: 2.2, swirl: 2.4,
      kinds: ['spark', 'spark', 'dot', 'star'], colors: [col, hi, [255, 255, 255]], ring: false, life: [0.6, 1.1] });
    return;
  }
  if (next === 'error') {
    const w = _vx.querySelector('.vx-orb-wrap');
    if (w) { w.classList.remove('wh-shake'); void w.offsetWidth; w.classList.add('wh-shake'); setTimeout(() => w.classList.remove('wh-shake'), 600); }
    burst(g.x, g.y, { r0: g.R * 0.9, count: 18, speed: [80, 200], gravity: 320, kinds: ['spark'], colors: [col, hi], ring: false });
    return;
  }
  if (next === 'idle' && prev !== 'idle' && prev !== 'connecting') {
    // Disconnect: the ring collapses inward.
    _spawn({ kind: 'ring', x: g.x, y: g.y, r0: g.R * 2.0, r1: g.R * 0.6, w: 2, life: 0.6, alpha: 0.7, color: col });
    return;
  }
  if (next === 'muted') {
    _spawn({ kind: 'ring', x: g.x, y: g.y, r0: g.R * 1.5, r1: g.R * 0.8, w: 1.6, life: 0.45, alpha: 0.6, color: col });
    return;
  }
  // Turn-taking pulses (user starts talking / ALFRED starts speaking), rate-limited.
  if ((next === 'user' || next === 'speaking') && now - _lastPulse > 650) {
    _lastPulse = now;
    _halo();
    ring(g.x, g.y, { r0: g.R * 0.95, r1: g.R * 1.55, color: col, w: 1.6, life: 0.7, alpha: 0.55 });
  }
}

// Data flow made visible: an electron runs a circuit trace from the reactor
// to each new chat-feed message and forged card.
let _lastComet = 0;   // rate limit for orb → panel electrons
function _electronTo(el, { land = true } = {}) {
  if (!on() || !_vxOpen()) return;
  if (land) {
    el.classList.add('wh-land');
    el.addEventListener('animationend', () => el.classList.remove('wh-land'), { once: true });
  }
  const now = performance.now();
  if (now - _lastComet < 350) return;
  _lastComet = now;
  const g = _orbGeom(); if (!g) return;
  requestAnimationFrame(() => {
    const r = el.getBoundingClientRect();
    if (!visible(r)) return;
    // Measure where the element will settle (the land animation shifts it).
    const tl = (getComputedStyle(el).translate || '').split(' ').map(parseFloat);
    const ox = Number.isFinite(tl[0]) ? tl[0] : 0, oy = Number.isFinite(tl[1]) ? tl[1] : 0;
    const box = { left: r.left - ox, right: r.right - ox, top: r.top - oy, bottom: r.bottom - oy };
    const c = { x: (box.left + box.right) / 2, y: (box.top + box.bottom) / 2 };
    const dx = c.x - g.x, dy = c.y - g.y;
    let pts, to;
    if (Math.abs(dx) >= Math.abs(dy)) {
      // Beside the reactor: leave on the horizontal axis, land on the facing edge.
      to = { x: dx < 0 ? box.right - 3 : box.left + 3, y: c.y };
      pts = _route({ x: g.x + Math.sign(dx) * g.R * 1.08, y: g.y }, to);
    } else {
      // Below/above: leave the rim at 45°, run a lane clear of the state label,
      // then bend into the element's facing edge.
      const sx = dx >= 0 ? 1 : -1, sy = Math.sign(dy) || 1;
      // half-width of the state label's actual text (its boxes span the wrap)
      let half = 0;
      for (const t of _vx.querySelectorAll('.vx-state, .vx-state-sub')) {
        const rg = document.createRange(); rg.selectNodeContents(t);
        const tr = rg.getBoundingClientRect();
        if (tr.width) half = Math.max(half, tr.right - g.x, g.x - tr.left);
      }
      const laneX = Math.round(g.x + sx * Math.max(g.R * 1.05, half + 22));
      const exit = { x: g.x + sx * g.R * 0.78, y: g.y + sy * g.R * 0.78 };
      const laneTop = { x: laneX, y: exit.y + sy * Math.abs(laneX - exit.x) };
      to = { x: clamp(laneX, box.left + 14, box.right - 14), y: sy > 0 ? box.top + 3 : box.bottom - 3 };
      const laneEnd = { x: laneX, y: to.y - sy * Math.abs(to.x - laneX) };
      pts = (laneEnd.y - laneTop.y) * sy > 8
        ? [exit, laneTop, laneEnd, to].filter((q, i, a) => i === 0 || Math.hypot(q.x - a[i - 1].x, q.y - a[i - 1].y) > 0.5)
        : _route(exit, to);   // no room for a lane: plain PCB route
    }
    const col = _stateColor();
    trace(pts, { color: col, onArrive: () => {
      el.classList.remove('wh-hit'); void el.offsetWidth; el.classList.add('wh-hit');
      setTimeout(() => el.classList.remove('wh-hit'), 1000);
      ring(to.x, to.y, { r0: 2, r1: 16, color: col, w: 1.6, life: 0.4 });
      burst(to.x, to.y, { count: 7, speed: [60, 160], gravity: 0, drag: 5, kinds: ['spark'], colors: [col, [255, 255, 255]], ring: false, life: [0.15, 0.3] });
    } });
  });
}

function _attachVx(root) {
  if (_vx === root) return;
  _vx = root;
  _prevState = root.dataset.state || 'idle';
  new MutationObserver(() => {
    const s = root.dataset.state || 'idle';
    if (s === _prevState) return;
    const prev = _prevState; _prevState = s;
    _onVxState(s, prev);
  }).observe(root, { attributes: true, attributeFilter: ['data-state'] });

  root.addEventListener('pointermove', _onVxPointer, { passive: true });
  root.addEventListener('pointerleave', _onVxLeave, { passive: true });

  // Both panes are rebuilt wholesale by voice.js (the chat feed on every
  // streamed token, the queue on every status change), so "a node was added"
  // means nothing. Fire only for genuinely new items:
  //   • chat feed — the chat gained a message (count of chat bubbles);
  //   • queue — a card id we have not seen yet.
  const openedAt = () => root._whOpenedAt || 0;
  const chatCount = () => document.querySelectorAll('#chat-history .msg-user, #chat-history .msg-ai:not(.msg-continuation)').length;
  const feed = root.querySelector('#vx-chatlog');
  let feedKey = null;
  if (feed) new MutationObserver(() => {
    const rows = feed.querySelectorAll('.vx-cmsg');
    const last = rows[rows.length - 1];
    if (!last) return;
    const key = chatCount() + ':' + (last.classList.contains('vx-cmsg-ai') ? 'A' : 'U');
    const fresh = feedKey !== null && key !== feedKey && performance.now() - openedAt() > 900;
    feedKey = key;
    if (fresh) _electronTo(last);
  }).observe(feed, { childList: true });

  const queue = root.querySelector('#vx-queue');
  const seen = new Set();
  if (queue) new MutationObserver(() => {
    const quiet = performance.now() - openedAt() < 900;
    for (const card of queue.querySelectorAll('.vx-card[data-id]')) {
      const id = card.dataset.id;
      if (seen.has(id)) continue;
      seen.add(id);
      if (!quiet) _electronTo(card);
    }
  }).observe(queue, { childList: true });

  new MutationObserver(() => { if (root.classList.contains('open') && !root._whWasOpen) root._whOpenedAt = performance.now(); root._whWasOpen = root.classList.contains('open'); })
    .observe(root, { attributes: true, attributeFilter: ['class'] });
  root._whOpenedAt = performance.now(); root._whWasOpen = root.classList.contains('open');
}

function _watchBody() {
  const found = document.getElementById('vx-root');
  if (found) _attachVx(found);
  new MutationObserver(muts => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType === 1 && n.id === 'vx-root') _attachVx(n);
    }
  }).observe(document.body, { childList: true });
}

// ═══════════════════════════════════════════════════════════════════════════
// BOOT
// ═══════════════════════════════════════════════════════════════════════════
function boot() {
  _syncRootClass();
  _watchChatHistory();
  _setupComposer();
  _setupFab();
  _watchBody();
  // The composer / FAB can be re-rendered by other modules; re-check lazily.
  let t = 0;
  new MutationObserver(() => {
    clearTimeout(t);
    t = setTimeout(() => { _watchChatHistory(); _setupComposer(); _setupFab(); }, 400);
  }).observe(document.getElementById('chat-container') || document.body, { childList: true });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();

// Small public surface for other modules and for debugging in the console.
window.odysseusWhimsy = {
  burst, ring, comet, trace,
  get enabled() { return on(); },
  toggle(v) { _userOn = v ?? !_userOn; try { localStorage.setItem(LS_KEY, _userOn ? 'on' : 'off'); } catch { /* */ } _syncRootClass(); return _userOn; },
};
