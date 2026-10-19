// static/js/login.js — Login page controller (ES6 module).
//
// Owns the auth form state machine (setup / login / signup / TOTP) and drives
// it exclusively through the centralized authUi.js helpers + auth-hyper.css
// class hooks. This file contains NO inline styles.

import {
  showAlert, hideAlert, setButtonLoading,
} from './authUi.js';

const EYE_OPEN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_CLOSED = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><line x1="8" y1="16" x2="16" y2="8"/><line x1="8" y1="8" x2="16" y2="16"/></svg>';

const $ = (id) => document.getElementById(id);

async function loadVersion() {
  try {
    const res = await fetch('/api/version');
    if (res.ok) {
      const data = await res.json();
      const el = $('version-label');
      if (el) el.textContent = 'v' + data.version;
    }
  } catch (_) {}
}

function wireEyeToggle(btnId, inputId) {
  const btn = $(btnId);
  const inp = $(inputId);
  if (!btn || !inp) return;
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    const show = inp.type === 'password';
    inp.type = show ? 'text' : 'password';
    btn.innerHTML = show ? EYE_OPEN : EYE_CLOSED;
    btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    btn.setAttribute('aria-pressed', show ? 'true' : 'false');
    inp.focus();
  });
  // Keep the soft keyboard open on mobile.
  btn.addEventListener('touchstart', (e) => e.preventDefault(), { passive: false });
  btn.addEventListener('touchend', (e) => { e.preventDefault(); btn.click(); });
}

async function init() {
  loadVersion();

  // Prefill last username.
  const usernameInput = $('username');
  const savedUser = localStorage.getItem('odysseus-last-user');
  if (savedUser && usernameInput) {
    usernameInput.value = savedUser;
    const pw = $('password');
    if (pw) pw.focus();
  }

  const form = $('authForm');
  const errEl = $('error');
  const setupNote = $('setupNote');
  const confirmGroup = $('confirmGroup');
  const submitBtn = $('submitBtn');
  const toggleArea = $('toggleArea');
  const toggleLink = $('toggleLink');
  const toggleText = $('toggleText');
  const rememberToggle = $('rememberToggle');

  let mode = 'login';
  let signupAllowed = false;

  function setMode(m) {
    mode = m;
    hideAlert(errEl);
    if (m === 'setup') {
      setupNote.textContent = 'First-time setup — create your admin account';
      setupNote.style.display = 'block';
      confirmGroup.style.display = 'block';
      submitBtn.textContent = 'Create Admin Account';
      toggleArea.style.display = 'none';
      rememberToggle.style.display = 'none';
      const pw = $('password');
      if (pw) pw.type = 'password';
    } else if (m === 'signup') {
      setupNote.style.display = 'none';
      confirmGroup.style.display = 'block';
      submitBtn.textContent = 'Create Account';
      toggleArea.style.display = 'block';
      toggleText.textContent = 'Already have an account? ';
      toggleLink.textContent = 'Sign in';
      rememberToggle.style.display = 'none';
      const pw = $('password');
      if (pw) pw.type = 'password';
    } else {
      setupNote.style.display = 'none';
      confirmGroup.style.display = 'none';
      submitBtn.textContent = 'Sign In';
      toggleArea.style.display = signupAllowed ? 'block' : 'none';
      toggleText.textContent = "Don't have an account? ";
      toggleLink.textContent = 'Sign up';
      rememberToggle.style.display = '';
    }
  }

  // Auth status → redirect if authenticated, else pick initial mode.
  try {
    const res = await fetch('/api/auth/status', { credentials: 'same-origin' });
    const data = await res.json();
    if (data.authenticated) {
      window.location.replace('/');
      return;
    }
    signupAllowed = !!data.signup_enabled;
    setMode(data.configured ? 'login' : 'setup');
  } catch (_) {
    setMode('login');
  }

  toggleLink.addEventListener('click', (e) => {
    e.preventDefault();
    setMode(mode === 'login' ? 'signup' : 'login');
  });

  // Finalize login: prefetch session/features/settings then redirect.
  function finishLogin(username) {
    const remember = $('remember').checked;
    if (remember) localStorage.setItem('odysseus-last-user', username);
    else localStorage.removeItem('odysseus-last-user');
    sessionStorage.removeItem('ody-session-active');
    setButtonLoading(submitBtn, true, '');
    Promise.all([
      fetch('/api/sessions', { credentials: 'same-origin' }).then((r) => r.json()),
      fetch('/api/auth/features', { credentials: 'same-origin' }).then((r) => r.json()),
      fetch('/api/auth/settings', { credentials: 'same-origin' }).then((r) => r.json()),
    ]).then(([sess, feat, sett]) => {
      sessionStorage.setItem('ody-prefetch-sessions', JSON.stringify(sess));
      sessionStorage.setItem('ody-prefetch-features', JSON.stringify(feat));
      sessionStorage.setItem('ody-prefetch-settings', JSON.stringify(sett));
    }).catch(() => {}).finally(() => { window.location.replace('/'); });
  }

  async function doLogin(username, password, remember, totpCode) {
    const body = { username, password, remember };
    if (totpCode) body.totp_code = totpCode;
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Login failed');
    return data;
  }

  // Inject the TOTP verification step (one-time).
  function showTotpStep(username, password) {
    const existing = $('totp-input');
    if (existing) return;
    form._totpMode = true;

    const wrap = document.createElement('div');
    wrap.className = 'auth-field';
    wrap.style.marginTop = '12px';

    const label = document.createElement('label');
    label.className = 'auth-label';
    label.htmlFor = 'totp-input';
    label.textContent = '2FA Code';
    wrap.appendChild(label);

    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'totp-input';
    input.className = 'auth-input auth-input-code';
    input.placeholder = 'Enter 6-digit code';
    input.setAttribute('aria-label', 'Two-factor authentication code');
    input.autocomplete = 'one-time-code';
    input.inputMode = 'numeric';
    input.maxLength = 8;
    wrap.appendChild(input);

    form.insertBefore(wrap, submitBtn);
    submitBtn.textContent = 'Verify';
    input.focus();

    // The main submit handler reads form._totpMode and resubmits.
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideAlert(errEl);
    submitBtn.disabled = true;

    const username = $('username').value.trim();
    const password = $('password').value;
    const remember = $('remember').checked;

    // TOTP mode: submit the code directly.
    if (form._totpMode) {
      const totpInput = $('totp-input');
      const code = totpInput ? totpInput.value.trim() : '';
      if (!code) { totpInput.focus(); submitBtn.disabled = false; return; }
      try {
        const data = await doLogin(username, password, remember, code);
        if (!data.ok) throw new Error('Login failed');
        form._totpMode = false;
        finishLogin(username);
      } catch (err) {
        showAlert(errEl, { kind: 'error', message: err.message });
        submitBtn.disabled = false;
      }
      return;
    }

    // Setup / signup validation.
    if (mode === 'setup' || mode === 'signup') {
      const confirm = $('confirmPassword').value;
      if (password !== confirm) {
        showAlert(errEl, { kind: 'error', message: 'Passwords do not match' });
        submitBtn.disabled = false;
        return;
      }
      if (password.length < 8) {
        showAlert(errEl, { kind: 'error', message: 'Password must be at least 8 characters' });
        submitBtn.disabled = false;
        return;
      }
    }

    // Setup or signup first.
    if (mode === 'setup' || mode === 'signup') {
      const endpoint = mode === 'setup' ? '/api/auth/setup' : '/api/auth/signup';
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ username, password }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Account creation failed');
      } catch (err) {
        showAlert(errEl, { kind: 'error', message: err.message });
        submitBtn.disabled = false;
        return;
      }
    }

    // Login (auto-login after setup/signup too).
    try {
      const data = await doLogin(username, password, remember);
      if (data.requires_totp) {
        submitBtn.disabled = false;
        showTotpStep(username, password);
        return;
      }
      finishLogin(username);
    } catch (err) {
      showAlert(errEl, { kind: 'error', message: err.message });
      submitBtn.disabled = false;
    }
  });

  // Eye toggles.
  wireEyeToggle('pwToggle', 'password');
  wireEyeToggle('pwToggleConfirm', 'confirmPassword');

  // Prevent button/eye from stealing focus on mobile.
  document.querySelectorAll('#submitBtn, .auth-eye').forEach((btn) => {
    btn.addEventListener('touchstart', (e) => e.preventDefault(), { passive: false });
    btn.addEventListener('touchend', (e) => { e.preventDefault(); btn.click(); });
  });

  // Mobile keyboard: shift card up when the virtual keyboard opens.
  if (window.visualViewport) {
    const card = document.querySelector('.auth-card');
    window.visualViewport.addEventListener('resize', () => {
      const vvh = window.visualViewport.height;
      const wh = window.innerHeight;
      if (vvh < wh * 0.8) {
        document.body.style.paddingTop = '5vh';
        if (document.activeElement && document.activeElement.tagName === 'INPUT') {
          document.activeElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      } else {
        document.body.style.paddingTop = '';
      }
    });
  }
}

// Drive the background effect from the saved theme, mirroring the bootstrap's
// chosen pattern (set in window.__loginBgPattern by the inline bootstrap).
async function initBackground() {
  try {
    const tm = await import('/static/js/theme.js');
    const pattern = window.__loginBgPattern;
    if (pattern && tm.applyBgPattern) tm.applyBgPattern(pattern);
  } catch (e) {
    console.error('[login-bg] failed:', e);
  }
}

init();
initBackground();