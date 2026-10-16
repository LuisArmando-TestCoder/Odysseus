// browserEval.js — chat-agent → browser JavaScript execution bridge.
//
// The chat agent may emit a `tool_progress` SSE event carrying an
// `eval_request` payload (token + JS source). This module evaluates that source
// in the page and POSTs the serialized result back to /api/browser/eval-result,
// which resolves the agent's pending Future.
//
// The agent only ever dispatches code ITS safety guard (JEV) already approved,
// so this module performs no further judgement — it just runs and reports.
// New Function is intentional: the agent is allowed to run arbitrary JS in the
// page, which requires the CSP script-src 'unsafe-eval' allowance.

function _serialize(value) {
  try {
    if (value instanceof Error) return { error: String(value && value.message || value) };
    if (typeof value === 'function') return { output: String(value) };
    if (typeof value === 'object' && value !== null) {
      return { output: JSON.stringify(value, null, 2) };
    }
    return { output: String(value) };
  } catch (e) {
    return { output: String(value) };
  }
}

// ── Extension forwarding (Google/YouTube AI mode) ───────────────────────────
// The agent can flag a script to run in the user's *external* tab (Google AI
// mode, YouTube "Ask") instead of this Odysseus page by prefixing the code with
// the marker comment `/*@external*/`. When that marker is present AND the
// Odysseus Browser Bridge extension is installed (its ID stored under
// localStorage 'ody_ext_id' or window.ODYSSEUS_EXT_ID), we forward the payload
// to the extension, which runs it in the active tab and POSTs the result back
// to /api/browser/eval-result itself. We must NOT also POST from here, or the
// single-use token would be double-resolved.

// The extension's manifest.json now pins a stable `key`, so its ID is
// deterministic: leabmickmdgchdpn. Fall back to it so the bridge works even
// before the user manually registers the ID in localStorage.
const _DEFAULT_EXT_ID = 'leabmickmdgchdpn';

function _extId() {
  try {
    return window.ODYSSEUS_EXT_ID || localStorage.getItem('ody_ext_id') || _DEFAULT_EXT_ID;
  } catch (e) {
    return window.ODYSSEUS_EXT_ID || _DEFAULT_EXT_ID;
  }
}

function _isExternal(code) {
  return /^\s*\/\*@external\*\//.test(code);
}

async function _postResult(token, result) {
  try {
    await fetch('/api/browser/eval-result', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, output: result.output || '', error: result.error || '' }),
    });
  } catch (e) {
    console.warn('browserEval: failed to POST result', e);
  }
}

export async function handleEvalRequest(payload) {
  const token = payload && payload.eval_token;
  const code = payload && payload.eval_code;
  if (!token || typeof code !== 'string' || !code.trim()) return false;

  // External-tab path: forward to the extension when flagged + installed.
  if (_isExternal(code) && _extId() && chrome && chrome.runtime && chrome.runtime.sendMessage) {
    const stripped = code.replace(/^\s*\/\*@external\*\/\s*/, '');
    const extId = _extId();

    // Watchdog: the server's Future must ALWAYS resolve. If the extension
    // never answers (MV3 cold-start, dead ID, hung relay), POST an error so
    // the agent gets a result instead of a 120s timeout. The first successful
    // resolution wins; the watchdog is a no-op once the token is resolved.
    let settled = false;
    const watchdog = setTimeout(() => {
      if (settled) return;
      settled = true;
      _postResult(token, { error: 'extension did not respond in time (is the Odysseus Browser Bridge loaded?)' });
    }, 15000);

    // Retry the external sendMessage — the first call often wakes a sleeping
    // MV3 service worker and loses the reply; a second call succeeds.
    let resp = null;
    let lastErr = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        resp = await chrome.runtime.sendMessage(extId, {
          type: 'ODY_EVAL',
          eval_token: token,
          eval_code: stripped,
        });
        if (resp && resp.ok) {
          clearTimeout(watchdog);
          settled = true;
          // The extension already POSTed the result and resolved the Future.
          return true;
        }
        lastErr = (resp && resp.error) || 'extension reported failure';
      } catch (e) {
        lastErr = String((e && e.message) || e);
      }
      // Brief backoff before retrying (cold-start wake-up).
      await new Promise((r) => setTimeout(r, 500));
    }

    clearTimeout(watchdog);
    if (!settled) {
      settled = true;
      await _postResult(token, { error: 'extension unreachable: ' + lastErr });
    }
    return true;
  }

  // In-page path (current behavior, unchanged).
  let result;
  try {
    // Evaluated as a function body so `return ...` and top-level statements
    // both work; non-returning scripts yield `undefined` → "no return value".
    const fn = new Function('"use strict";\n' + code);
    let ret = fn();
    if (ret && typeof ret.then === 'function') ret = await ret;   // async snippets
    result = { output: ((ret === undefined) ? '' : null) };
    if (ret !== undefined) {
      result = _serialize(ret);
    }
  } catch (err) {
    result = { error: String(err && err.message || err) };
  }

  await _postResult(token, result);
  return true;
}

export function initBrowserEval() {
  // The eval request rides the chat SSE stream via tool_progress. The chat loop
  // calls handleEvalRequest directly (see static/js/chat.js), so there's no
  // separate global listener needed; exported for clarity + potential reuse.
  if (!window.__odyBrowserEval) {
    window.__odyBrowserEval = { handleEvalRequest };
  }
}

export default { handleEvalRequest, initBrowserEval };