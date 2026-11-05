// Odysseus Sound Lab — audition, edit and export every synthesized patch.
import sfx, { DEFAULTS, OVERRIDE_KEY } from './index.js';
import { PATCHES } from './patches.js';
import { renderPatch, encodeWav, patchLength } from './engine.js';

const $ = id => document.getElementById(id);
const FAMILY_ORDER = ['pointer', 'drag', 'keyboard', 'system', 'feedback'];
const FAMILY_LABEL = { pointer: 'Pointer', drag: 'Drag', keyboard: 'Keyboard', system: 'Windows · clipboard', feedback: 'Feedback' };
const knobs = { pitch: 0, pan: 0, rate: 1 };
let selected = null;
const cards = new Map();

// ── Overrides persistence ───────────────────────────────────────────────
const readOverrides = () => { try { return JSON.parse(localStorage.getItem(OVERRIDE_KEY) || '{}'); } catch { return {}; } };
const writeOverrides = o => { try { localStorage.setItem(OVERRIDE_KEY, JSON.stringify(o)); } catch {} };
const isModified = name => JSON.stringify(PATCHES[name]) !== JSON.stringify(DEFAULTS[name]);

// ── Waveform drawing from an offline render ─────────────────────────────
const waveCache = new Map();
async function peaks(name, n = 160) {
  const key = name + '|' + JSON.stringify(PATCHES[name]);
  if (waveCache.has(key)) return waveCache.get(key);
  const buf = await renderPatch(PATCHES[name], { sampleRate: 22050 });
  const d0 = buf.getChannelData(0), d1 = buf.getChannelData(1);
  // Crop the long reverb tail at -40 dB so the shape of the sound reads.
  let gmax = 0; for (let i = 0; i < d0.length; i++) gmax = Math.max(gmax, Math.abs(d0[i]), Math.abs(d1[i]));
  let end = d0.length - 1;
  while (end > 0 && Math.max(Math.abs(d0[end]), Math.abs(d1[end])) < gmax * 0.01) end--;
  end = Math.min(d0.length, end + Math.floor(buf.sampleRate * 0.01));
  const d = d0.subarray(0, end), d2 = d1.subarray(0, end);
  const step = Math.max(1, Math.floor(d.length / n));
  const out = new Float32Array(n);
  let max = 0;
  for (let i = 0; i < n; i++) {
    let m = 0;
    for (let j = i * step; j < Math.min(d.length, (i + 1) * step); j++) m = Math.max(m, Math.abs(d[j]), Math.abs(d2[j]));
    out[i] = m; max = Math.max(max, m);
  }
  const res = { peaks: out, max, ms: Math.round(end / buf.sampleRate * 1000) };
  waveCache.set(key, res);
  return res;
}
function drawPeaks(canvas, { peaks: p, max }, { color = '#4fd8ff', glow = 0 } = {}) {
  const dpr = devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const c = canvas.getContext('2d');
  c.scale(dpr, dpr);
  c.clearRect(0, 0, w, h);
  const norm = 1 / Math.max(1e-4, max);
  const bw = w / p.length;
  const g = c.createLinearGradient(0, 0, w, 0);
  g.addColorStop(0, color); g.addColorStop(1, 'rgba(79,216,255,.25)');
  c.fillStyle = g;
  if (glow) { c.shadowColor = color; c.shadowBlur = glow; }
  for (let i = 0; i < p.length; i++) {
    const a = Math.max(0.5, p[i] * norm * (h / 2 - 2));
    c.fillRect(i * bw, h / 2 - a, Math.max(1, bw - 0.6), a * 2);
  }
}

// ── Library grid ────────────────────────────────────────────────────────
function buildGrid() {
  const root = $('families');
  root.innerHTML = '';
  cards.clear();
  const byFam = {};
  for (const [name, p] of Object.entries(PATCHES)) (byFam[p.family || 'other'] ||= []).push(name);
  const fams = [...FAMILY_ORDER, ...Object.keys(byFam).filter(f => !FAMILY_ORDER.includes(f))];
  for (const fam of fams) {
    if (!byFam[fam]) continue;
    const sec = document.createElement('section');
    sec.className = 'family';
    sec.innerHTML = `<h2>${FAMILY_LABEL[fam] || fam} <span>${byFam[fam].length}</span></h2><div class="grid" role="listbox" aria-label="${FAMILY_LABEL[fam] || fam} sounds"></div>`;
    const grid = sec.querySelector('.grid');
    for (const name of byFam[fam]) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'card';
      b.setAttribute('role', 'option');
      b.setAttribute('aria-selected', 'false');
      b.dataset.sfx = 'off';               // the card plays its *own* sound instead
      b.dataset.name = name;
      b.innerHTML = `<div class="nm"><span></span></div><div class="ds"></div><canvas></canvas><span class="ms"></span>`;
      b.querySelector('.nm span').textContent = name;
      b.querySelector('.ds').textContent = PATCHES[name].desc || '';
      b.addEventListener('click', () => { select(name); audition(name); });
      grid.appendChild(b);
      cards.set(name, b);
      refreshCard(name);
    }
    root.appendChild(sec);
  }
}
async function refreshCard(name) {
  const b = cards.get(name);
  if (!b) return;
  const nm = b.querySelector('.nm');
  nm.querySelector('.mod')?.remove();
  if (isModified(name)) nm.insertAdjacentHTML('beforeend', '<span class="mod">EDITED</span>');
  try {
    const pk = await peaks(name, 90);
    drawPeaks(b.querySelector('canvas'), pk);
    b.querySelector('.ms').textContent = pk.ms + ' ms';
  } catch (e) { console.warn('[sfx-lab] render failed', name, e); }
}

// ── Editor ──────────────────────────────────────────────────────────────
function select(name, { focus = false } = {}) {
  selected = name;
  for (const [n, b] of cards) b.setAttribute('aria-selected', String(n === name));
  const p = PATCHES[name];
  $('ed-name').textContent = name;
  $('ed-fam').textContent = FAMILY_LABEL[p.family] || p.family || '';
  $('ed-desc').textContent = p.desc || '';
  $('ed-json').value = JSON.stringify(p, null, 2);
  setStatus('ok', isModified(name) ? 'edited · saved' : 'default');
  drawBig();
  if (focus) cards.get(name)?.focus();
  try { history.replaceState(null, '', '#' + encodeURIComponent(name)); } catch {}
}
async function drawBig() {
  if (!selected) return;
  try { drawPeaks($('ed-wave'), await peaks(selected, 260), { glow: 8 }); } catch {}
}
function setStatus(kind, msg) { const s = $('ed-status'); s.className = 'st ' + kind; s.textContent = msg; }

function audition(name = selected) {
  if (!name) return;
  sfx.play(name, { pitch: knobs.pitch, pan: knobs.pan, rate: knobs.rate, force: true });
  $('now-playing').textContent = name;
  const b = cards.get(name);
  if (b) { b.classList.remove('flash'); void b.offsetWidth; b.classList.add('flash'); }
}

let editTimer;
$('ed-json').addEventListener('input', () => {
  clearTimeout(editTimer);
  editTimer = setTimeout(() => {
    let p;
    try { p = JSON.parse($('ed-json').value); }
    catch (e) { return setStatus('bad', e.message.replace(/^JSON\.parse: /, '').slice(0, 60)); }
    if (!p || !Array.isArray(p.layers)) return setStatus('bad', 'needs a "layers" array');
    PATCHES[selected] = p;
    const o = readOverrides();
    if (isModified(selected)) o[selected] = p; else delete o[selected];
    writeOverrides(o);
    setStatus('ok', isModified(selected) ? 'edited · saved' : 'default');
    refreshCard(selected); drawBig();
  }, 220);
});
$('ed-json').addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); audition(); }
  if (e.key === 'Tab' && !e.shiftKey) {          // real tabs-as-spaces in the editor
    e.preventDefault();
    const t = e.target, s = t.selectionStart;
    t.setRangeText('  ', s, t.selectionEnd, 'end');
  }
});

$('ed-play').addEventListener('click', () => audition());
$('ed-wav').addEventListener('click', async () => {
  const buf = await renderPatch(PATCHES[selected], { opts: { pitch: knobs.pitch, pan: knobs.pan, rate: knobs.rate } });
  save(encodeWav(buf), `odysseus-${selected}.wav`);
  toast(`Saved odysseus-${selected}.wav`);
});
$('ed-copy').addEventListener('click', async () => {
  const txt = `'${selected}': ${JSON.stringify(PATCHES[selected], null, 2)},`;
  try { await navigator.clipboard.writeText(txt); toast('Patch JSON copied'); } catch { toast('Copy blocked by browser'); }
});
$('ed-reset').addEventListener('click', () => {
  PATCHES[selected] = JSON.parse(JSON.stringify(DEFAULTS[selected]));
  const o = readOverrides(); delete o[selected]; writeOverrides(o);
  select(selected); refreshCard(selected); toast('Reset to default');
});

for (const k of ['pitch', 'pan', 'rate']) {
  const inp = $('k-' + k), out = $('o-' + k);
  const fmt = v => k === 'pitch' ? `${v > 0 ? '+' : ''}${v} st` : k === 'pan' ? (Math.abs(v) < 0.03 ? 'C' : (v < 0 ? 'L' : 'R') + Math.round(Math.abs(v) * 100)) : (+v).toFixed(2) + '×';
  inp.addEventListener('input', () => { knobs[k] = +inp.value; out.textContent = fmt(+inp.value); });
  inp.addEventListener('change', () => audition());
}

// ── Sprite export: every sound in one WAV + a use-sound-compatible map ──
$('export-sprite').addEventListener('click', async () => {
  const names = Object.keys(PATCHES);
  const sr = 48000, gap = 0.12;
  const bufs = [];
  for (const n of names) bufs.push(await renderPatch(PATCHES[n], { sampleRate: sr }));
  const total = bufs.reduce((s, b) => s + b.length + Math.round(gap * sr), 0);
  const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const out = new OAC(2, total, sr).createBuffer(2, total, sr);
  const sprite = {};
  let off = 0;
  bufs.forEach((b, i) => {
    for (let c = 0; c < 2; c++) out.getChannelData(c).set(b.getChannelData(c), off);
    sprite[names[i]] = [Math.round(off / sr * 1000), Math.round(b.duration * 1000)];
    off += b.length + Math.round(gap * sr);
  });
  save(encodeWav(out), 'odysseus-sfx-sprite.wav');
  save(new Blob([JSON.stringify({ src: 'odysseus-sfx-sprite.wav', sprite }, null, 2)], { type: 'application/json' }), 'odysseus-sfx-sprite.json');
  toast(`Exported ${names.length} sounds · ${(total / sr).toFixed(1)} s sprite`);
});

function save(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = filename;
  a.dataset.sfx = 'off';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
let toastT;
function toast(msg) {
  const t = $('lab-toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2200);
}

// ── Keyboard: ↑/↓ browse, Space plays ───────────────────────────────────
document.addEventListener('keydown', e => {
  const tag = e.target.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT' || tag === 'SELECT') return;
  const names = [...cards.keys()];
  const i = names.indexOf(selected);
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const n = names[(i + (e.key === 'ArrowDown' ? 1 : -1) + names.length) % names.length];
    select(n, { focus: true }); audition(n);
  } else if (e.key === ' ' && !e.target.closest('button, a, summary')) {
    e.preventDefault(); audition();
  }
});

// Demo controls
$('demo-link').addEventListener('click', e => e.preventDefault());
(() => {
  const el = $('dragme');
  let sx = 0, sy = 0, on = false;
  el.addEventListener('pointerdown', e => { on = true; sx = e.clientX; sy = e.clientY; el.setPointerCapture(e.pointerId); });
  el.addEventListener('pointermove', e => { if (on) el.style.transform = `translate(${e.clientX - sx}px, ${e.clientY - sy}px)`; });
  const end = () => { on = false; el.style.transition = 'transform .35s cubic-bezier(.3,1.6,.5,1)'; el.style.transform = ''; setTimeout(() => { el.style.transition = ''; }, 360); };
  el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
})();

// ── Live oscilloscope on the master bus ─────────────────────────────────
let analyser = null;
function ensureAnalyser() {
  if (analyser || !sfx.ctx) return analyser;
  analyser = sfx.ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.6;
  sfx.chain.master.connect(analyser);
  return analyser;
}
const scope = $('scope');
const sc = scope.getContext('2d');
let td, fd;
function frame() {
  const dpr = devicePixelRatio || 1, w = scope.clientWidth, h = scope.clientHeight;
  if (scope.width !== w * dpr) { scope.width = w * dpr; scope.height = h * dpr; }
  sc.setTransform(dpr, 0, 0, dpr, 0, 0);
  sc.clearRect(0, 0, w, h);
  // grid
  sc.strokeStyle = 'rgba(79,216,255,.06)'; sc.lineWidth = 1;
  for (let x = 0; x < w; x += 40) { sc.beginPath(); sc.moveTo(x + .5, 0); sc.lineTo(x + .5, h); sc.stroke(); }
  sc.beginPath(); sc.moveTo(0, h / 2 + .5); sc.lineTo(w, h / 2 + .5); sc.strokeStyle = 'rgba(79,216,255,.14)'; sc.stroke();
  const a = ensureAnalyser();
  if (a) {
    td ||= new Float32Array(a.fftSize); fd ||= new Uint8Array(a.frequencyBinCount);
    a.getFloatTimeDomainData(td); a.getByteFrequencyData(fd);
    // spectrum (log-ish bins) as faint amber bars
    const bars = 96;
    for (let i = 0; i < bars; i++) {
      const idx = Math.floor(Math.pow(i / bars, 2) * fd.length * 0.7);
      const v = fd[idx] / 255;
      sc.fillStyle = `rgba(255,179,71,${0.08 + v * 0.25})`;
      sc.fillRect(i * (w / bars), h - v * h * 0.9, w / bars - 1, v * h * 0.9);
    }
    // waveform (cyan, glowing)
    sc.beginPath();
    for (let i = 0; i < td.length; i++) {
      const x = (i / (td.length - 1)) * w, y = h / 2 - td[i] * h * 0.9;
      i ? sc.lineTo(x, y) : sc.moveTo(x, y);
    }
    sc.strokeStyle = '#4fd8ff'; sc.lineWidth = 1.6; sc.shadowColor = '#4fd8ff'; sc.shadowBlur = 10;
    sc.stroke(); sc.shadowBlur = 0;
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ── Boot ────────────────────────────────────────────────────────────────
buildGrid();
const fromHash = decodeURIComponent(location.hash.slice(1));
select(PATCHES[fromHash] ? fromHash : 'submit');
addEventListener('resize', () => { for (const n of cards.keys()) refreshCard(n); drawBig(); });
window.__sfxLab = { patchLength };
