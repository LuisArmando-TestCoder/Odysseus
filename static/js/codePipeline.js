// codePipeline.js — the code-execution neural pipeline (decide → forge → execute).
//
// Mirrors the advanced-voice pipeline's multi-agent shape, but for code. It is
// deliberately compressor-free: the decision is a single yes/no ("does this
// need code?"), then the forger writes the script, then the executor runs it.
//
// The pipeline streams over SSE from POST /api/realtime/code-pipeline and is
// rendered two ways:
//   1. "Side lines" in the normal chat — a vertical 3-stage timeline injected
//      into #chat-history (like the agent-thread, but for the code pipeline).
//   2. A header badge in the advanced-voice HUD — driven by window CustomEvents
//      that voice.js listens for.
//
// Browser-target execution reuses the existing guarded bridge: the SSE stream
// carries an `eval_request` payload, which is handed to browserEval.js's
// handleEvalRequest, and the result is POSTed back to resolve the pipeline.

const STAGES = ['decide', 'forge', 'execute'];
const STAGE_META = {
  decide:  ['Decide',  'Needs code?'],
  forge:   ['Forge',   'Write script'],
  execute: ['Execute', 'Run it'],
};

let _active = false;

function _api(p) { return `${window.location.origin}${p}`; }

function _emit(name, detail) {
  try { window.dispatchEvent(new CustomEvent(name, { detail })); } catch (e) { /* ignore */ }
}

// ── Side-line UI (normal chat) ──────────────────────────────────────────────

function _buildPanel(request) {
  const chatBox = document.getElementById('chat-history');
  if (!chatBox) return null;

  const panel = document.createElement('div');
  panel.className = 'code-pipeline';
  panel.innerHTML = `
    <div class="code-pipeline-head">
      <span class="code-pipeline-title">Code Pipeline</span>
      <span class="code-pipeline-req">${_esc(request)}</span>
    </div>
    <div class="code-pipeline-stages">
      ${STAGES.map((s, i) => `
        <div class="code-pipeline-node" data-stage="${s}">
          <div class="code-pipeline-ic"><span>${i + 1}</span></div>
          <div class="code-pipeline-tx"><b>${STAGE_META[s][0]}</b><small>${STAGE_META[s][1]}</small></div>
          <div class="code-pipeline-ms"></div>
        </div>`).join('<div class="code-pipeline-link"><i></i></div>')}
    </div>
    <div class="code-pipeline-out" hidden></div>`;
  chatBox.appendChild(panel);
  return panel;
}

function _esc(s) {
  // Build entities via char codes to avoid literal &/< in source.
  const amp = '&' + 'amp;', lt = '&' + 'lt;', gt = '&' + 'gt;';
  const quot = '&' + 'quot;', apos = '&' + '#39;';
  const map = { '&': amp, '<': lt, '>': gt, '"': quot, "'": apos };
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => map[c]);
}

function _stage(panel, stage, status, ev) {
  const node = panel && panel.querySelector(`.code-pipeline-node[data-stage="${stage}"]`);
  if (!node) return;
  node.dataset.status = status;
  const ms = node.querySelector('.code-pipeline-ms');
  if (status === 'running') ms.textContent = '···';
  else if (ev && typeof ev.ms === 'number') ms.textContent = ev.ms < 1000 ? `${ev.ms}ms` : `${(ev.ms / 1000).toFixed(1)}s`;
  else ms.textContent = '';
  if (stage === 'decide' && ev && ev.engine) {
    node.querySelector('small').textContent = ev.engine === 'jev' ? 'JEV decision' : 'LLM decision';
  }
  if (stage === 'forge' && ev && ev.language) {
    node.querySelector('small').textContent = `${ev.language} · ${ev.target}`;
  }
  if (stage === 'execute' && ev && ev.target) {
    node.querySelector('small').textContent = ev.target === 'browser' ? 'in browser' : 'on system';
  }
  const idx = STAGES.indexOf(stage);
  const links = panel.querySelectorAll('.code-pipeline-link');
  if (status === 'done' && links[idx]) links[idx].classList.add('flow');
}

function _showOutput(panel, output, ok) {
  const out = panel && panel.querySelector('.code-pipeline-out');
  if (!out) return;
  out.hidden = false;
  out.className = 'code-pipeline-out' + (ok ? '' : ' error');
  out.innerHTML = `<pre>${_esc(output || '(no output)')}</pre>`;
}

// ── Pipeline runner ─────────────────────────────────────────────────────────

export async function runCodePipeline(request, sessionId = null) {
  if (_active) return { executed: false, reason: 'busy' };
  _active = true;
  _emit('code-pipeline-start', { request });

  const panel = _buildPanel(request);
  const ctrl = new AbortController();
  let result = { executed: false, reason: 'unknown' };

  try {
    const resp = await fetch(_api('/api/realtime/code-pipeline'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      signal: ctrl.signal,
      body: JSON.stringify({ request, session_id: sessionId }),
    });
    if (!resp.ok || !resp.body) throw new Error(`code pipeline HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
          _onEvent(ev, panel, result);
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.warn('code pipeline failed', e);
      _showOutput(panel, 'Pipeline unavailable — ' + (e.message || e), false);
      result = { executed: false, reason: 'error', error: String(e.message || e) };
    }
  } finally {
    _active = false;
    _emit('code-pipeline-end', { result });
  }
  return result;
}

function _onEvent(ev, panel, result) {
  switch (ev.type) {
    case 'hello':
      _emit('code-pipeline-hello', ev);
      break;
    case 'stage':
      _stage(panel, ev.stage, ev.status, ev);
      _emit('code-pipeline-stage', ev);
      break;
    case 'decision':
      _emit('code-pipeline-decision', ev.decision || {});
      break;
    case 'forged':
      _emit('code-pipeline-forged', ev);
      break;
    case 'executed':
      _showOutput(panel, ev.output, ev.exit_code === 0);
      _emit('code-pipeline-executed', ev);
      result.executed = true;
      result.exit_code = ev.exit_code;
      result.output = ev.output;
      break;
    case 'eval_request':
      // Browser-target execution: hand the guarded JS to the browser bridge.
      _emit('code-pipeline-eval-request', ev);
      if (window.__odyBrowserEval && window.__odyBrowserEval.handleEvalRequest) {
        window.__odyBrowserEval.handleEvalRequest(ev);
      } else {
        import('./browserEval.js').then(m => m.handleEvalRequest(ev));
      }
      break;
    case 'done':
      result.executed = !!ev.executed;
      result.reason = ev.reason;
      result.exit_code = ev.exit_code;
      _emit('code-pipeline-done', ev);
      break;
  }
}

export function isCodePipelineActive() { return _active; }

export default { runCodePipeline, isCodePipelineActive };