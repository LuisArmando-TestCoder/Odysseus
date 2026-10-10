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

// window.open from an agent tool call is not a user gesture, so browsers often
// block it (returns null). Instead of failing silently, show a one-click
// "Open" toast — the user's click IS a gesture, so the tab opens reliably.
function _blockedOpenToast(url) {
  const box = document.createElement('div');
  box.setAttribute('role', 'alert');
  box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483647;display:flex;gap:10px;' +
    'align-items:center;max-width:min(92vw,520px);padding:12px 14px;border-radius:12px;' +
    'background:rgba(12,20,32,.96);color:#e8f4ff;border:1px solid rgba(120,200,255,.35);' +
    'box-shadow:0 10px 30px rgba(0,0,0,.45);font:13px/1.35 system-ui,sans-serif';
  const txt = document.createElement('span');
  txt.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
  txt.textContent = 'The assistant wants to open: ' + url;
  const go = document.createElement('button');
  go.textContent = 'Open';
  go.style.cssText = 'padding:6px 12px;border-radius:8px;border:0;background:#3aa0ff;color:#fff;cursor:pointer;font-weight:600';
  const x = document.createElement('button');
  x.textContent = '✕';
  x.setAttribute('aria-label', 'Dismiss');
  x.style.cssText = 'padding:4px 8px;border:0;background:transparent;color:#9fb4c8;cursor:pointer';
  go.onclick = () => { window.open(url, '_blank', 'noopener'); box.remove(); };
  x.onclick = () => box.remove();
  box.append(txt, go, x);
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 60000);
}

export async function handleEvalRequest(payload) {
  const token = payload && payload.eval_token;
  const code = payload && payload.eval_code;
  if (!token || typeof code !== 'string' || !code.trim()) return false;

  let result;
  const blocked = [];
  const realOpen = window.open;
  // Temporarily wrap window.open for this snippet only, to catch pop-up blocks.
  window.open = function (url, target, features) {
    // 'noopener' makes window.open return null even on success — strip it,
    // then sever the opener ourselves so the result reliably signals a block.
    const feats = String(features || '').split(',').map(s => s.trim())
      .filter(f => f && !/^no(opener|referrer)$/i.test(f)).join(',');
    const w = realOpen.call(window, url, target || '_blank', feats || undefined);
    if (w) { try { w.opener = null; } catch (e) { /* cross-origin */ } }
    else if (url) { blocked.push(String(url)); _blockedOpenToast(String(url)); }
    return w;
  };
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
  } finally {
    window.open = realOpen;
  }
  if (blocked.length && !result.error) {
    result.output = (result.output ? result.output + '\n' : '') +
      `Pop-up was blocked by the browser; the user was shown a one-click "Open" button for: ${blocked.join(', ')}. ` +
      'Tell the user to click it — do not retry.';
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