// ═══════════════════════════════════════════════════════════════════════════
// Odysseus · Advanced Voice Mode — "XENO" visual layer
//
// Effects around the arc reactor (the reactor itself is untouched):
//   • Scanner — a radar sweep over a tick ring; ticks flare as it passes,
//     faster while ALFRED talks or thinks.
//   • Boot — opening the HUD runs a scan beam down the console.
//
// Reads window.__vxSignal (published by voice.js each frame); falls back to
// idle values. Additive: observes the HUD, never wraps voice.js. Off under
// prefers-reduced-motion / forced-colors and when whimsy is switched off
// (Alt+Shift+W).
// ═══════════════════════════════════════════════════════════════════════════

const mqReduce = matchMedia('(prefers-reduced-motion: reduce)');
const mqForced = matchMedia('(forced-colors: active)');
const allowed = () => !mqReduce.matches && !mqForced.matches &&
  (() => { try { return localStorage.getItem('odysseus-whimsy') !== 'off'; } catch { return true; } })();

const TAU = Math.PI * 2;
// ═══════════════════════════════════════════════════════════════════════════
// The orb console (canvas 2D, layered over the WebGL orb)
// ═══════════════════════════════════════════════════════════════════════════
class XenoConsole {
  constructor(root) {
    this.root = root;
    this.wrap = root.querySelector('.vx-orb-wrap');
    this.gl = root.querySelector('#vx-canvas');
    this.cv = document.createElement('canvas');
    this.cv.className = 'vx-xeno';
    this.cv.setAttribute('aria-hidden', 'true');
    this.gl.after(this.cv);
    this.ctx = this.cv.getContext('2d');
    this.rot = 0; this.sweep = 0;
    this.boot = 0; this.last = 0; this.raf = 0; this.prevState = '';
    this.loop = this.loop.bind(this);
  }
  start() { this.boot = 0; this.last = performance.now(); if (!this.raf) this.raf = requestAnimationFrame(this.loop); }
  stop() { cancelAnimationFrame(this.raf); this.raf = 0; this.ctx.clearRect(0, 0, this.cv.width, this.cv.height); }

  colour() {
    const c = window.__vxSignal?.col;
    if (c) return [c[0] * 255, c[1] * 255, c[2] * 255];
    const s = this.root.style;
    return [+s.getPropertyValue('--vx-r') || 92, +s.getPropertyValue('--vx-g') || 158, +s.getPropertyValue('--vx-b') || 199];
  }

  loop(now) {
    this.raf = requestAnimationFrame(this.loop);
    if (document.hidden || !this.root.classList.contains('open')) return;
    if (now - (this._allowAt || 0) > 1000) { this._allowAt = now; this._allowed = allowed(); }
    if (!this._allowed) { this.ctx.clearRect(0, 0, this.cv.width, this.cv.height); return; }
    const dt = Math.min(0.05, (now - this.last) / 1000); this.last = now;
    const sig = window.__vxSignal || {};
    const lvl = sig.lvl || 0, out = sig.out || 0, think = sig.think || 0, fft = sig.fft;
    const state = this.root.dataset.state || 'idle';
    const live = state !== 'idle' && state !== 'error';

    // size
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round(this.cv.clientWidth * dpr), h = Math.round(this.cv.clientHeight * dpr);
    if (!w || !h) return;
    if (this.cv.width !== w || this.cv.height !== h) { this.cv.width = w; this.cv.height = h; }
    // follow the orb's cursor tilt (whimsy.js) with a little extra depth
    const tf = this.gl.style.transform;
    if (tf !== this._tf) { this._tf = tf; this.cv.style.transform = tf ? tf.replace(/rotateX\(([-\d.]+)deg\)/, (m, v) => `rotateX(${(v * 1.35).toFixed(3)}deg)`).replace(/rotateY\(([-\d.]+)deg\)/, (m, v) => `rotateY(${(v * 1.35).toFixed(3)}deg)`) : ''; }

    const ctx = this.ctx, S = Math.min(w, h), cx = w / 2, cy = h / 2;
    const [cr, cg, cb] = this.colour();
    const rgba = a => `rgba(${cr | 0},${cg | 0},${cb | 0},${a})`;
    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';

    this.boot = Math.min(1, this.boot + dt / 0.95);
    const energy = Math.max(lvl, out);
    const spin = (live ? 1 : 0.35) * (1 + energy * 3 + think * 2);
    this.rot -= dt * 0.028 * spin * TAU;
    this.sweep += dt * TAU * (0.22 + think * 0.9 + energy * 0.5) * (live ? 1 : 0.4);

    // ── Scanner: tick ring + sweep ──────────────────────────────────────
    const tr = S * 0.395, ticks = 120;
    for (let i = 0; i < ticks; i++) {
      const a = (i / ticks) * TAU + this.rot * 0.5;
      let behind = ((this.sweep - a) % TAU + TAU) % TAU;      // angle behind the sweep head
      const flare = Math.exp(-behind * 2.4);
      const major = i % 10 === 0;
      const al = (major ? 0.22 : 0.09) + flare * 0.75;
      const len = S * (major ? 0.018 : 0.009) * (1 + flare * 0.8);
      ctx.strokeStyle = rgba(al * (0.4 + 0.6 * this.boot));
      ctx.lineWidth = (major ? 1.4 : 1) * dpr;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * tr, cy + Math.sin(a) * tr);
      ctx.lineTo(cx + Math.cos(a) * (tr + len), cy + Math.sin(a) * (tr + len));
      ctx.stroke();
    }
    // sweep head: a bright arc with a fading tail
    for (let k = 0; k < 10; k++) {
      ctx.strokeStyle = rgba((1 - k / 10) * 0.5 * this.boot);
      ctx.lineWidth = 1.6 * dpr;
      ctx.beginPath();
      ctx.arc(cx, cy, tr - S * 0.006, this.sweep - (k + 1) * 0.06, this.sweep - k * 0.06);
      ctx.stroke();
    }

    ctx.globalCompositeOperation = 'source-over';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Wiring
// ═══════════════════════════════════════════════════════════════════════════
let _console = null, _root = null;

function _boot(root) {
  // Scan line runs down the console once.
  const scan = document.createElement('div');
  scan.className = 'vx-xscan';
  scan.setAttribute('aria-hidden', 'true');
  root.appendChild(scan);
  scan.addEventListener('animationend', () => scan.remove(), { once: true });
  setTimeout(() => scan.remove(), 1600);
  _console?.start();
}




function _attach(root) {
  if (_root === root) return;
  _root = root;
  if (!root.querySelector('#vx-canvas')) return;
  _console = new XenoConsole(root);
  let wasOpen = false;
  const sync = () => {
    const open = root.classList.contains('open');
    if (open && !wasOpen && allowed()) _boot(root);
    if (!open && wasOpen) _console.stop();
    wasOpen = open;
  };
  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ['class'] });
  sync();
}

function init() {
  const found = document.getElementById('vx-root');
  if (found) _attach(found);
  new MutationObserver(muts => {
    for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1 && n.id === 'vx-root') _attach(n);
  }).observe(document.body, { childList: true });
  const onPref = () => { if (!allowed()) _console?.stop(); else if (_root?.classList.contains('open')) _console?.start(); };
  mqReduce.addEventListener?.('change', onPref);
  mqForced.addEventListener?.('change', onPref);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

