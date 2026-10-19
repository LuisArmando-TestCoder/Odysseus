// static/js/authUi.js — Authentication UI module (ES6)
//
// Centralized, reusable building blocks for every auth surface:
//   * login.html  — setup / login / signup / TOTP
//   * settings.js — 2FA setup/disable + change-password
//   * admin.js    — user list / create / rename / delete / privileges
//
// Consumers must use these factories. Styling comes from static/css/auth-hyper.css;
// this module emits the class hooks and manages interaction state (aria, events).
// No inline styles are produced here.

const EYE_OPEN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_CLOSED = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';

// Strict HTML-escape for interpolation of untrusted usernames etc.
// Entity strings are assembled via char codes so the literal HTML entities
// never appear in this source (avoids any encoding/decoding ambiguity).
var _AMP = String.fromCharCode(38); // '&'
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, function (c) {
    if (c === '&') return _AMP + 'amp;';
    if (c === '<') return _AMP + 'lt;';
    if (c === '>') return _AMP + 'gt;';
    if (c === '"') return _AMP + 'quot;';
    if (c === "'") return _AMP + '#39;';
    return c;
  });
}

// ── Alert box (error / success / info / warning) ───────────────────────────
// `kind` one of: 'error' | 'success' | 'info' | 'warning'
// `message` text (already safe to assign via textContent)
export function alertBox({ id = 'auth-alert', kind = 'error', live = 'assertive', role = 'alert' } = {}) {
  const el = document.createElement('div');
  el.id = id;
  el.className = `auth-alert auth-alert-${kind}`;
  el.setAttribute('role', role);
  el.setAttribute('aria-live', live);
  return el;
}

export function showAlert(el, { kind = 'error', message = '', role = 'alert', live = 'assertive' } = {}) {
  if (!el) return;
  el.className = `auth-alert auth-alert-${kind}`;
  el.setAttribute('role', role);
  el.setAttribute('aria-live', live);
  el.textContent = message || '';
  el.classList.toggle('auth-alert-visible', !!message);
}

export function hideAlert(el) {
  if (!el) return;
  el.textContent = '';
  el.classList.remove('auth-alert-visible');
}

// ── Input + label ──────────────────────────────────────────────────────────
export function inputField({ id, label, type = 'text', required = false, autofocus = false,
    autocomplete, value = '', placeholder = '', inputmode, maxlength, extraClass = '' }) {
  const wrap = document.createElement('div');
  wrap.className = `auth-field ${extraClass}`.trim();

  if (label) {
    const lbl = document.createElement('label');
    lbl.className = 'auth-label';
    lbl.htmlFor = id;
    lbl.textContent = label;
    wrap.appendChild(lbl);
  }

  const input = document.createElement('input');
  input.id = id;
  input.type = type;
  input.className = 'auth-input' + (inputmode === 'numeric' ? ' auth-input-code' : '');
  input.name = id;
  if (required) input.required = true;
  if (autofocus) input.autofocus = true;
  if (autocomplete) input.autocomplete = autocomplete;
  if (value) input.value = value;
  if (placeholder) input.placeholder = placeholder;
  if (inputmode) input.inputmode = inputmode;
  if (maxlength) input.maxLength = maxlength;
  wrap.appendChild(input);

  return { wrap, input };
}

// ── Password field with eye toggle (aria-pressed managed) ──────────────────
export function passwordField({ id, label, autocomplete, autofocus = false }) {
  const { wrap, input } = inputField({ id, label, type: 'password', autocomplete, autofocus });

  const pwWrap = document.createElement('div');
  pwWrap.className = 'auth-pw-wrap';
  pwWrap.appendChild(input);

  const eye = document.createElement('button');
  eye.type = 'button';
  eye.className = 'auth-eye';
  eye.setAttribute('aria-label', 'Show password');
  eye.setAttribute('aria-pressed', 'false');
  eye.tabIndex = -1;
  eye.innerHTML = EYE_CLOSED;

  function sync() {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    eye.innerHTML = show ? EYE_OPEN : EYE_CLOSED;
    eye.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    eye.setAttribute('aria-pressed', show ? 'true' : 'false');
  }

  eye.addEventListener('click', (e) => {
    e.preventDefault();
    sync();
    input.focus();
  });

  // Keep the soft keyboard open on mobile (don't steal focus).
  eye.addEventListener('touchstart', (e) => e.preventDefault(), { passive: false });
  eye.addEventListener('touchend', (e) => { e.preventDefault(); eye.click(); });

  pwWrap.appendChild(eye);
  wrap.replaceChild(pwWrap, wrap.querySelector('.auth-field > input') || wrap.lastChild);

  return { wrap, input, eye };
}

// ── Button ─────────────────────────────────────────────────────────────────
export function button({ id, label, variant = '', type = 'submit', small = false, onClick }) {
  const btn = document.createElement('button');
  btn.id = id;
  btn.type = type;
  btn.className = ['auth-btn', variant ? `auth-btn-${variant}` : '', small ? 'auth-btn-sm' : ''].filter(Boolean).join(' ');
  const span = document.createElement('span');
  span.className = 'auth-btn-label';
  span.textContent = label;
  btn.appendChild(span);
  if (onClick) btn.addEventListener('click', onClick);
  return { btn, setLabel: (t) => { span.textContent = t; } };
}

// Loading / success state helpers for a button.
export function setButtonLoading(btn, loading, labelWhenLoading = '') {
  if (!btn) return;
  if (loading) {
    btn.disabled = true;
    btn.classList.add('auth-btn-loading');
    btn.setAttribute('aria-busy', 'true');
    const existing = btn.querySelector('.auth-spinner');
    if (labelWhenLoading && !existing) {
      const sp = document.createElement('span');
      sp.className = 'auth-spinner';
      sp.setAttribute('aria-hidden', 'true');
      btn.insertBefore(sp, btn.firstChild);
      const lbl = btn.querySelector('.auth-btn-label');
      if (lbl) lbl.textContent = labelWhenLoading;
    }
  } else {
    btn.disabled = false;
    btn.classList.remove('auth-btn-loading');
    btn.removeAttribute('aria-busy');
    const sp = btn.querySelector('.auth-spinner');
    if (sp) sp.remove();
  }
}

export function setButtonSuccess(btn, successLabel, revertLabel, ms = 1400) {
  if (!btn) return;
  btn.classList.add('auth-btn-success');
  const lbl = btn.querySelector('.auth-btn-label');
  if (lbl) lbl.textContent = successLabel;
  setTimeout(() => {
    btn.classList.remove('auth-btn-success');
    if (lbl) lbl.textContent = revertLabel;
    btn.disabled = false;
    btn.classList.remove('auth-btn-loading');
    btn.removeAttribute('aria-busy');
    const sp = btn.querySelector('.auth-spinner');
    if (sp) sp.remove();
  }, ms);
}

// ── Switch (role=switch) ───────────────────────────────────────────────────
export function switchControl({ id, checked = false, disabled = false, ariaLabel, onChange }) {
  const label = document.createElement('label');
  label.className = 'auth-switch';

  const input = document.createElement('input');
  input.type = 'checkbox';
  input.id = id;
  input.checked = checked;
  input.disabled = disabled;
  input.setAttribute('role', 'switch');
  input.setAttribute('aria-checked', checked ? 'true' : 'false');
  if (ariaLabel) input.setAttribute('aria-label', ariaLabel);

  const slider = document.createElement('span');
  slider.className = 'auth-slider';
  slider.setAttribute('aria-hidden', 'true');

  input.addEventListener('change', () => {
    input.setAttribute('aria-checked', input.checked ? 'true' : 'false');
    if (onChange) onChange(input.checked, input);
  });

  label.appendChild(input);
  label.appendChild(slider);
  return { label, input, setChecked: (v) => { input.checked = v; input.setAttribute('aria-checked', v ? 'true' : 'false'); } };
}

// ── Inline link ────────────────────────────────────────────────────────────
export function link({ text, onClick, href = '#' }) {
  const a = document.createElement('a');
  a.className = 'auth-link';
  a.href = href;
  a.textContent = text;
  if (onClick) a.addEventListener('click', (e) => { e.preventDefault(); onClick(e); });
  return a;
}

// ── Auth state machine (for login.html modes) ──────────────────────────────
// Modes: 'setup' | 'login' | 'signup'. The consumer maps each mode to DOM updates.
export function authStateMachine(initial = 'login') {
  const listeners = new Set();
  let mode = initial;

  return {
    get mode() { return mode; },
    set(value) {
      if (value === mode) return;
      mode = value;
      listeners.forEach((fn) => fn(value));
    },
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  };
}

export default {
  esc, alertBox, showAlert, hideAlert,
  inputField, passwordField, button, setButtonLoading, setButtonSuccess,
  switchControl, link, authStateMachine,
  EYE_OPEN, EYE_CLOSED,
};