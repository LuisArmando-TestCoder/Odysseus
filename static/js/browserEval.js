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

export async function handleEvalRequest(payload) {
  const token = payload && payload.eval_token;
  const code = payload && payload.eval_code;
  if (!token || typeof code !== 'string' || !code.trim()) return false;

  let result;
  try {
    // Evaluated as a function body so `return ...` and top-level statements
    // both work; non-returning scripts yield `undefined` → "no return value".
    const fn = new Function('"use strict";\n' + code);
    const ret = fn();
    result = { output: ((ret === undefined) ? '' : null) };
    if (ret !== undefined) {
      result = _serialize(ret);
    }
  } catch (err) {
    result = { error: String(err && err.message || err) };
  }

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