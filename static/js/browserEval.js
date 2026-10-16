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

function _extId() {
  try {
    return window.ODYSSEUS_EXT_ID || localStorage.getItem('ody_ext_id') || null;
  } catch (e) {
    return window.ODYSSEUS_EXT_ID || null;
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
    try {
      const resp = await chrome.runtime.sendMessage(_extId(), {
        type: 'ODY_EVAL',
        eval_token: token,
        eval_code: stripped,
      });
      if (resp && resp.ok) {
        // The extension already POSTed the result and resolved the Future.
        return true;
      }
      // Extension responded but reported failure → surface it, never hang.
      await _postResult(token, { error: (resp && resp.error) || 'extension reported failure' });
      return true;
    } catch (e) {
      // Extension unreachable → fail loudly so the agent's Future resolves.
      await _postResult(token, { error: 'extension unreachable: ' + String((e && e.message) || e) });
      return true;
    }
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