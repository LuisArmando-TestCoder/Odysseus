// static/js/questionCard.js — Cluster B ask-user UI.
//
// Renders the mid-task question card(s) the agent asked via ask_user_question,
// POSTs the human's answers to /api/question/{sid}/answer, and restores an
// unanswered card on page refresh / session switch (the backend persists the
// pending-question marker). The awaiting turn resumes server-side; the stream
// stays alive on keep-alive heartbeats while the user answers.
//
// UI niceties (U1): entrance animation, "(Recommended)" badge on the
// recommended option, numbered questions when >1, "✓ answered" state before
// removal, focus + aria-live=polite, Escape to dismiss.

const API_BASE = (typeof window !== 'undefined' && window.API_BASE) || '';

let _current = null;      // { sessionId, questions }
let _escHandler = null;

function _wrap() {
  let wrap = document.getElementById('question-card-wrap');
  if (wrap) return wrap;
  wrap = document.createElement('div');
  wrap.id = 'question-card-wrap';
  wrap.hidden = true;
  const anchor = document.getElementById('message');
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(wrap, anchor);
  else document.body.appendChild(wrap);
  return wrap;
}

function _unbindEsc() {
  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler, true);
    _escHandler = null;
  }
}

function _buildQuestion(q, idx, total) {
  const el = document.createElement('div');
  el.className = 'question-item';
  el.dataset.qid = q.id || '';
  const title = document.createElement('div');
  title.className = 'question-title';
  const num = total > 1 ? `${idx + 1}. ` : '';
  title.textContent = num + (q.header ? q.header + ': ' : '') + (q.question || '');
  el.appendChild(title);

  const options = q.options || [];
  const group = 'q-' + (q.id || Math.random().toString(36).slice(2));
  options.forEach(opt => {
    const label = document.createElement('label');
    label.className = 'question-option';
    const input = document.createElement('input');
    input.type = q.multi_select ? 'checkbox' : 'radio';
    input.name = group;
    input.value = opt.label || '';
    // "(Recommended)" lives in the label text per the tool contract — split it
    // into a visible badge instead of rendering it as part of the option.
    let text = opt.label || '';
    let recommended = false;
    const rec = /\(recommended\)\s*$/i;
    if (rec.test(text)) {
      recommended = true;
      text = text.replace(rec, '').trim();
    }
    const span = document.createElement('span');
    span.textContent = text;
    if (recommended) {
      const badge = document.createElement('em');
      badge.className = 'question-opt-recommended';
      badge.textContent = 'Recommended';
      span.appendChild(document.createTextNode(' '));
      span.appendChild(badge);
    }
    if (opt.description) label.title = opt.description;
    label.appendChild(input);
    label.appendChild(span);
    el.appendChild(label);
  });

  const custom = document.createElement('input');
  custom.type = 'text';
  custom.className = 'question-custom';
  custom.placeholder = options.length ? '…or type a custom answer' : 'type your answer…';
  el.appendChild(custom);
  return el;
}

export function renderQuestions(questions, sessionId) {
  if (!questions || !questions.length) return;
  _current = { sessionId, questions };
  const wrap = _wrap();
  wrap.innerHTML = '';
  wrap.hidden = false;

  const card = document.createElement('div');
  card.className = 'question-card question-card-enter';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-live', 'polite');
  card.setAttribute('aria-label', 'Assistant needs your input');
  card.tabIndex = -1;

  const head = document.createElement('div');
  head.className = 'question-card-header';
  head.textContent = '\uD83D\uDE4B Assistant needs your input — the task is paused until you answer';
  card.appendChild(head);

  questions.forEach((q, i) => card.appendChild(_buildQuestion(q, i, questions.length)));

  const actions = document.createElement('div');
  actions.className = 'question-actions';
  const send = document.createElement('button');
  send.type = 'button';
  send.className = 'question-send-btn';
  send.textContent = 'Send answers';
  send.addEventListener('click', () => _submit(send));
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'question-dismiss-btn';
  dismiss.textContent = 'Ignore (times out)';
  dismiss.addEventListener('click', () => clearQuestions());
  actions.appendChild(send);
  actions.appendChild(dismiss);
  card.appendChild(actions);

  wrap.appendChild(card);
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  // Focus the card (not the first input — radio groups need a beat to render).
  setTimeout(() => card.focus({ preventScroll: true }), 50);

  // Escape = ignore (the backend resolves with a timeout after ask_user_timeout).
  _unbindEsc();
  _escHandler = (ev) => {
    if (ev.key === 'Escape') {
      ev.stopPropagation();
      clearQuestions();
    }
  };
  document.addEventListener('keydown', _escHandler, true);
}

async function _submit(sendBtn) {
  if (!_current || !_current.sessionId) return;
  const wrap = _wrap();
  const answers = Array.from(wrap.querySelectorAll('.question-item')).map(item => {
    const selected = Array.from(
      item.querySelectorAll('input[type="radio"]:checked, input[type="checkbox"]:checked')
    ).map(i => i.value);
    const custom = (item.querySelector('.question-custom')?.value || '').trim();
    const ans = { id: item.dataset.qid || undefined, selected };
    if (custom) ans.custom = custom;
    return ans;
  });
  const sid = _current.sessionId;

  // U1 — "✓ answered" state before removal.
  if (sendBtn && !sendBtn.dataset.sent) {
    sendBtn.dataset.sent = '1';
    sendBtn.textContent = '\u2713 answered';
    sendBtn.disabled = true;
    wrap.querySelectorAll('input').forEach(i => { i.disabled = true; });
  }

  try {
    const res = await fetch(`${API_BASE}/api/question/${encodeURIComponent(sid)}/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ answers }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      console.error('question answer failed:', res.status, d);
    }
  } catch (e) {
    console.error('question answer error:', e);
  }
  // Brief ✓ state, then clear.
  setTimeout(() => clearQuestions(), 600);
}

export function clearQuestions() {
  _current = null;
  _unbindEsc();
  const wrap = document.getElementById('question-card-wrap');
  if (wrap) { wrap.hidden = true; wrap.innerHTML = ''; }
}

export async function restorePendingQuestions(sessionId) {
  if (!sessionId) return;
  try {
    const res = await fetch(`${API_BASE}/api/question/${encodeURIComponent(sessionId)}`, {
      credentials: 'same-origin',
    });
    if (!res.ok) return;
    const data = await res.json();
    if (data.questions && data.questions.length) renderQuestions(data.questions, sessionId);
  } catch (_) { /* no pending question */ }
}

export default { renderQuestions, clearQuestions, restorePendingQuestions };
