// ============================================================================
// Odysseus SFX — global microinteraction bindings
// ============================================================================
// One delegated listener set on `document` gives every pointer and keyboard
// microinteraction a synthesized sound. No per-component wiring needed.
//
//   • Opt out a subtree:   <div data-sfx="off">
//   • Force a patch:       <button data-sfx="success">
//   • From JS / the agent: window.OdysseusSFX.play('notify')
//   • Toggle:              Alt+Shift+S   (sticky, saved in localStorage)
//
// Accessibility: sounds never carry meaning on their own (every one mirrors
// a visual change), TTS/media playback ducks them, and the mute is sticky.
// ============================================================================

import { SfxEngine, renderPatch, encodeWav, downloadPatch } from './engine.js';
import { PATCHES } from './patches.js';

// Patches edited in the Sound Lab are saved as overrides and applied here,
// so tweaks made in the lab are what the whole app plays.
export const OVERRIDE_KEY = 'odysseus.sfx.overrides.v2';   // v2 = "XENO" voice; v1 edits were for the old glass sounds
export const DEFAULTS = JSON.parse(JSON.stringify(PATCHES));
try {
  const o = JSON.parse(localStorage.getItem(OVERRIDE_KEY) || '{}');
  for (const [k, v] of Object.entries(o)) if (v && Array.isArray(v.layers)) PATCHES[k] = v;
} catch {}

const sfx = new SfxEngine(PATCHES);

const INTERACTIVE = [
  'button', 'a[href]', 'summary', 'select', 'label', 'input:not([type=hidden])',
  '[role=button]', '[role=tab]', '[role=menuitem]', '[role=option]', '[role=switch]',
  '[role=checkbox]', '[role=radio]', '[role=link]', '[data-sfx]',
  '.clickable', '.session-item', '.admin-tab', '.settings-nav-item', '.vis-row',
].join(',');
const EDITABLE = 'textarea, input:not([type]), input[type=text], input[type=search], input[type=email], input[type=url], input[type=password], input[type=number], input[type=tel], [contenteditable=""], [contenteditable=true]';

const optedOut = el => !!el?.closest?.('[data-sfx="off"]');
const interactiveOf = el => (el && el.closest ? el.closest(INTERACTIVE) : null);
const isEditable = el => !!(el && el.matches && el.matches(EDITABLE));
const isDisabled = el => !!(el && (el.disabled || el.getAttribute?.('aria-disabled') === 'true' || el.closest?.('fieldset[disabled]')));
const panFromX = x => Math.max(-0.7, Math.min(0.7, ((x / Math.max(1, innerWidth)) * 2 - 1) * 0.7));
const forced = el => {
  const n = el?.closest?.('[data-sfx]');
  const v = n?.getAttribute('data-sfx');
  return v && v !== 'off' && PATCHES[v] ? v : null;
};

// ── Rising-pitch combo (Josh Comeau's "Like" trick, generalised) ─────────
// Rapid repeat clicks on the same target climb the pentatonic scale.
const PENTA = [0, 2, 4, 7, 9, 12, 14, 16, 19, 21, 24];
let comboEl = null, comboN = 0, comboT = 0;
function comboPitch(el) {
  const now = performance.now();
  if (el === comboEl && now - comboT < 420) comboN = Math.min(comboN + 1, PENTA.length - 1);
  else comboN = 0;
  comboEl = el; comboT = now;
  return PENTA[comboN];
}

// ── Keyboard geography: pan + micro-pitch from the physical key position ─
const ROWS = ['1234567890-=', 'qwertyuiop[]', "asdfghjkl;'", 'zxcvbnm,./'];
function keyGeo(key) {
  const k = (key || '').toLowerCase();
  for (let r = 0; r < ROWS.length; r++) {
    const c = ROWS[r].indexOf(k);
    if (c >= 0) return { pan: ((c + r * 0.4) / 11 - 0.5) * 0.9, pitch: (1.5 - r) * 0.7 };
  }
  return { pan: 0, pitch: 0 };
}

// ── State ────────────────────────────────────────────────────────────────
let hoverEl = null;
let lastInputKeyboard = false;
let drag = null;               // { x, y, t, active, el }
let suppressClickUntil = 0;

const play = (name, opts) => sfx.play(name, opts);

// Unlock audio on the very first gesture (autoplay policy).
const unlockOnce = () => { sfx.unlock(); };
addEventListener('pointerdown', unlockOnce, { capture: true, once: true });
addEventListener('keydown', unlockOnce, { capture: true, once: true });

// ── Pointer ──────────────────────────────────────────────────────────────
document.addEventListener('pointerover', e => {
  if (e.pointerType !== 'mouse') return;
  const el = interactiveOf(e.target);
  if (el === hoverEl) return;
  hoverEl = el;
  if (!el || optedOut(el) || isDisabled(el) || drag?.active) return;
  play('hover', { pan: panFromX(e.clientX) });
}, { passive: true, capture: true });

document.addEventListener('pointerdown', e => {
  lastInputKeyboard = false;
  if (e.button !== 0 || optedOut(e.target)) return;
  const el = interactiveOf(e.target);
  drag = { x: e.clientX, y: e.clientY, t: performance.now(), active: false, el: e.target, lx: e.clientX, ly: e.clientY };
  if (!el) return;
  if (isDisabled(el)) { play('disabled', { pan: panFromX(e.clientX) }); return; }
  if (el.matches('input[type=range]')) return;   // range has its own voice
  play('press', { pan: panFromX(e.clientX) });
}, { passive: true, capture: true });

document.addEventListener('pointermove', e => {
  if (!drag || !(e.buttons & 1)) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (!drag.active) {
    if (dx * dx + dy * dy < 64) return;
    // Selecting text (in an editor or in the chat) is not a "drag" and
    // stays silent.
    if (isEditable(drag.el) || drag.el?.matches?.('input[type=range]')) { drag = null; return; }
    // The browser extends a text selection only after this event, so decide
    // on the next frame whether this gesture is a selection or a real drag.
    if (drag.pending) return;
    drag.pending = true;
    const d = drag, x = e.clientX;
    requestAnimationFrame(() => {
      if (drag !== d) return;
      const sel = getSelection();
      if (sel && !sel.isCollapsed) { drag = null; return; }
      d.active = true;
      play('drag.start', { pan: panFromX(x) });
    });
    return;
  }
  const mx = e.clientX - drag.lx, my = e.clientY - drag.ly;
  const dist = Math.hypot(mx, my);
  if (dist < 22) return;
  const now = performance.now();
  const speed = dist / Math.max(1, now - (drag.mt || drag.t));   // px/ms
  drag.lx = e.clientX; drag.ly = e.clientY; drag.mt = now;
  play('drag.move', { pan: panFromX(e.clientX), pitch: Math.min(12, speed * 6) - 2, velocity: Math.min(1, 0.5 + speed * 0.3) });
}, { passive: true, capture: true });

const endDrag = e => {
  if (drag?.active) {
    play('drag.end', { pan: panFromX(e.clientX) });
    suppressClickUntil = performance.now() + 80;
  }
  drag = null;
};
document.addEventListener('pointerup', endDrag, { passive: true, capture: true });
document.addEventListener('pointercancel', () => { drag = null; }, { passive: true, capture: true });

// Click = the "release" voice, specialised by what the element *is*.
document.addEventListener('click', e => {
  if (performance.now() < suppressClickUntil) return;
  const el = interactiveOf(e.target);
  if (!el || optedOut(el) || isDisabled(el)) return;
  const pan = e.detail ? panFromX(e.clientX) : 0;
  const f = forced(el);
  if (f) return play(f, { pan });

  // Clicking a <label> forwards a synthetic click to its input — let that one speak.
  if (el.tagName === 'LABEL' && el.control && e.target !== el.control) return;

  const type = (el.getAttribute('type') || '').toLowerCase();
  const role = el.getAttribute('role');
  // Checkbox / radio / switch: state is already updated when click fires.
  if (type === 'checkbox' || type === 'radio' || role === 'switch' || role === 'checkbox') {
    const on = el.checked ?? (el.getAttribute('aria-checked') === 'true');
    return play(on ? 'toggle.on' : 'toggle.off', { pan });
  }
  if (type === 'range' || el.tagName === 'SELECT' || isEditable(el)) return;
  if (el.hasAttribute('aria-pressed')) {
    requestAnimationFrame(() => play(el.getAttribute('aria-pressed') === 'true' ? 'toggle.on' : 'toggle.off', { pan }));
    return;
  }
  if (el.tagName === 'SUMMARY') {
    const open = !el.parentElement?.open;           // toggles after click
    return play(open ? 'expand' : 'collapse', { pan });
  }
  if (el.hasAttribute('aria-expanded')) {
    // Most handlers flip aria-expanded synchronously; read on next frame.
    requestAnimationFrame(() => play(el.getAttribute('aria-expanded') === 'true' ? 'expand' : 'collapse', { pan }));
    return;
  }
  if (role === 'tab' || role === 'option' || el.classList.contains('admin-tab') || el.classList.contains('settings-nav-item')) return play('select', { pan });
  if (type === 'submit' || el.id === 'send-btn' || el.matches('[aria-label*="Send" i]')) return play('submit', { pan });
  if (el.tagName === 'A' && el.getAttribute('href') && !el.getAttribute('href').startsWith('#')) return play('link', { pan });
  play('release', { pan, pitch: comboPitch(el) });
}, { capture: true });

document.addEventListener('dblclick', e => {
  if (optedOut(e.target) || isEditable(e.target)) return;
  play('double', { pan: panFromX(e.clientX) });
}, { capture: true });

document.addEventListener('contextmenu', e => {
  if (optedOut(e.target)) return;
  play('context', { pan: panFromX(e.clientX) });
}, { capture: true });

// Native HTML5 drag & drop (session reordering, file drops, …)
document.addEventListener('dragstart', e => { if (!optedOut(e.target)) play('drag.start'); }, { capture: true });
document.addEventListener('drop', e => { if (!optedOut(e.target)) play('drag.end', { pan: panFromX(e.clientX) }); }, { capture: true });

// Sliders: pitch follows the value, so you can *hear* where it is.
document.addEventListener('input', e => {
  const el = e.target;
  if (!el.matches?.('input[type=range]') || optedOut(el)) return;
  const min = +el.min || 0, max = +el.max || 100, t = (+el.value - min) / Math.max(1e-9, max - min);
  play('range', { pitch: Math.round(t * 24) - 7 });
}, { capture: true });

document.addEventListener('change', e => {
  if (e.target.tagName === 'SELECT' && !optedOut(e.target)) play('select');
}, { capture: true });

document.addEventListener('invalid', e => { if (!optedOut(e.target)) play('error', { throttle: 300 }); }, { capture: true });

// Text fields: focus chime only when arriving by keyboard (Tab) — mouse
// focus already got press/release.
document.addEventListener('focusin', e => {
  if (lastInputKeyboard && isEditable(e.target) && !optedOut(e.target)) play('focus');
}, { capture: true });

document.addEventListener('copy', e => { if (!optedOut(e.target)) play('copy'); }, { capture: true });
document.addEventListener('cut', e => { if (!optedOut(e.target)) play('cut'); }, { capture: true });
document.addEventListener('paste', e => { if (!optedOut(e.target)) play('paste'); }, { capture: true });

// ── Keyboard ─────────────────────────────────────────────────────────────
const MODS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'AltGraph', 'Fn']);
document.addEventListener('keydown', e => {
  lastInputKeyboard = true;

  // Global toggle — works even while muted.
  if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyS') {
    e.preventDefault();
    const on = sfx.toggle();
    if (on) sfx.play('mute', { force: true });
    announce(on ? 'Interface sounds on' : 'Interface sounds muted');
    return;
  }
  if (e.isComposing || optedOut(e.target)) return;
  const k = e.key;
  const vel = e.repeat ? 0.55 : 1;
  const ed = isEditable(e.target);

  if (k === 'Escape') return play('key.escape');

  // Writing is silent: no sounds for keystrokes in text fields (letters,
  // space, delete, newline, arrows, modifiers). The only sound is the send
  // chime when Enter sends the chat message.
  if (ed) {
    if (k === 'Enter' && e.target.id === 'message' && !e.shiftKey && e.target.value.trim()) play('submit');
    return;
  }
  if (MODS.has(k)) return;
  if ((e.ctrlKey || e.metaKey) && k.length === 1) {
    if (/^[cxv]$/i.test(k)) return;   // clipboard combos sound via copy/cut/paste events
    return play('key.chord', { pan: keyGeo(k).pan });
  }
  if (k === 'Tab') return play('key.tab', { pan: e.shiftKey ? -0.4 : 0.4 });
  if (k.startsWith('Arrow')) {
    const dir = { ArrowUp: [5, 0], ArrowDown: [-5, 0], ArrowLeft: [0, -0.5], ArrowRight: [0, 0.5] }[k];
    return play('key.arrow', { pitch: dir[0], pan: dir[1], velocity: vel });
  }
}, { capture: true });

// ── Windows: modals / dialogs / popovers opening and closing ─────────────
const OVERLAY = '.modal, dialog, [role=dialog], [role=alertdialog], [role=menu], [role=listbox]';
const shown = el => !el.classList.contains('hidden') && !el.hidden && (el.tagName !== 'DIALOG' || el.open) && el.getAttribute('aria-hidden') !== 'true';
const overlayState = new WeakMap();
let bootQuiet = true; setTimeout(() => { bootQuiet = false; }, 1500);
let toastWas = false;
const mo = new MutationObserver(muts => {
  if (bootQuiet || !sfx.enabled) return;
  for (const m of muts) {
    const el = m.target;
    if (el.id === 'toast') {
      const on = el.classList.contains('show');
      if (on && !toastWas) play(el.classList.contains('error') ? 'error' : 'notify', { throttle: 400 });
      toastWas = on;
      continue;
    }
    if (!(el instanceof Element) || !el.matches(OVERLAY) || optedOut(el)) continue;
    const now = shown(el);
    const was = overlayState.has(el) ? overlayState.get(el) : !now;
    overlayState.set(el, now);
    if (now === was) continue;
    const small = el.matches('[role=menu],[role=listbox]');
    if (small) play(now ? 'expand' : 'collapse', { throttle: 60 });
    else play(now ? 'open' : 'close', { throttle: 120 });
  }
});
const startObserver = () => {
  document.querySelectorAll(OVERLAY).forEach(el => overlayState.set(el, shown(el)));
  mo.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class', 'open', 'hidden', 'aria-hidden'] });
};
if (document.body) startObserver(); else addEventListener('DOMContentLoaded', startObserver);

// Any module can fire a sound without importing:
//   document.dispatchEvent(new CustomEvent('odysseus:sfx', { detail: 'success' }))
document.addEventListener('odysseus:sfx', e => {
  const d = e.detail;
  if (typeof d === 'string') play(d); else if (d?.name) play(d.name, d);
});

// ── Screen-reader announcement for the toggle ────────────────────────────
let live;
function announce(msg) {
  if (!live) {
    live = document.createElement('div');
    live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
    live.style.cssText = 'position:fixed;left:-9999px;width:1px;height:1px;overflow:hidden';
    document.body.appendChild(live);
  }
  live.textContent = msg;
}

// ── Settings › Appearance › Sound card wiring (if present) ───────────────
function wireSettings() {
  const tog = document.getElementById('sfx-enabled');
  const vol = document.getElementById('sfx-volume');
  const out = document.getElementById('sfx-volume-out');
  if (!tog || !vol) return;
  const sync = () => {
    tog.checked = sfx.enabled;
    vol.value = Math.round(sfx.volume * 100);
    if (out) out.textContent = `${vol.value}%`;
    vol.disabled = !sfx.enabled;
  };
  sync();
  sfx.onChange(sync);
  tog.addEventListener('change', () => { sfx.setEnabled(tog.checked); if (tog.checked) sfx.play('mute', { force: true }); });
  vol.addEventListener('input', () => { sfx.setVolume(vol.value / 100); if (out) out.textContent = `${vol.value}%`; });
}
if (document.readyState === 'loading') addEventListener('DOMContentLoaded', wireSettings); else wireSettings();

// ── Public API (also usable by the agent's run_javascript tool) ──────────
window.OdysseusSFX = {
  play: (name, opts) => sfx.play(name, opts),
  patches: PATCHES,
  engine: sfx,
  get enabled() { return sfx.enabled; },
  setEnabled: on => sfx.setEnabled(on),
  toggle: () => sfx.toggle(),
  setVolume: v => sfx.setVolume(v),
  render: (name, opts) => renderPatch(typeof name === 'string' ? PATCHES[name] : name, opts),
  toWav: encodeWav,
  download: (name, opts) => downloadPatch(typeof name === 'string' ? name : 'custom', typeof name === 'string' ? PATCHES[name] : name, opts),
  /** Register or override a patch at runtime. */
  define: (name, patch) => { PATCHES[name] = patch; return name; },
};

export default sfx;
