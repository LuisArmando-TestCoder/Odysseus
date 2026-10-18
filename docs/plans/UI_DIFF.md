# UI DIFF — los 7 gaps (goal loop continuo + visibilidad)

Diffs exactos sobre el código actual de Odysseus.

---

## DIFF 1 — `static/index.html`: goal card ampliada + banner in-chat

### 1a. Dentro de `<section class="goal-card" id="goal-card">` (línea 703)

Después de la línea 711 (`<span class="goal-rounds" id="goal-rounds">…`) y **antes** de
`<p class="goal-blocked" id="goal-blocked">` (línea 713), insertar:

```html
        <!-- Actividad + cola de items (tareas continuas) -->
        <span class="goal-activity" id="goal-activity" title="Estado" data-state="idle"></span>
        <div class="goal-queue-row" id="goal-queue-row" hidden>
          <div class="goal-progress-bar"><div class="goal-progress-fill goal-queue-fill" id="goal-queue-fill"></div></div>
          <span class="goal-rounds" id="goal-queue-count"></span>
        </div>
        <p class="goal-current" id="goal-current" hidden></p>
        <!-- Trail (checkpoint) desplegable -->
        <details class="goal-trail" id="goal-trail">
          <summary>📜 Qué ha hecho</summary>
          <pre class="goal-trail-body" id="goal-trail-body"></pre>
        </details>
```

### 1b. Banner in-chat (dentro de `<main id="chat-container">`, antes de `<div id="chat-history">` línea 983)

```html
        <div class="goal-banner" id="goal-banner" hidden></div>
```

---

## DIFF 2 — `static/css/goals.css`: estilos nuevos (append al final)

```css
/* Actividad: working (pulso) / idle / blocked */
.goal-activity {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: var(--border, #e5e7eb); margin: 8px 4px 0 0;
}
.goal-activity[data-state="working"] { background: #10b981; animation: goalPulse 1.2s ease-in-out infinite; }
.goal-activity[data-state="blocked"] { background: #ef4444; }
@keyframes goalPulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }

/* Cola de items + item actual */
.goal-queue-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.goal-queue-fill { background: #8b5cf6; }
.goal-current {
  margin: 6px 0 0; font-size: 11px; color: var(--muted, #6b7280);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* Trail (checkpoint) */
.goal-trail { margin-top: 8px; font-size: 11px; }
.goal-trail summary { cursor: pointer; color: var(--muted, #6b7280); font-weight: 600; }
.goal-trail-body {
  max-height: 160px; overflow: auto; margin: 6px 0 0; padding: 8px;
  background: var(--code-bg, #0b1020); color: #e5e7eb; border-radius: 8px;
  font-family: ui-monospace, Menlo, monospace; font-size: 10.5px;
  white-space: pre-wrap; word-break: break-word;
}

/* Transición de fase + toast */
.goal-card { transition: border-color .2s ease, box-shadow .2s ease; }
.goal-card.blocked { border-color: #ef4444; box-shadow: 0 0 0 1px #ef4444; }
.goal-card.complete { border-color: #3b82f6; }
.goal-toast {
  position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%);
  padding: 10px 16px; border-radius: 10px; font-size: 13px; z-index: 20000;
  color: #fff; box-shadow: 0 6px 24px rgba(0,0,0,.25); animation: goalToastIn .25s ease;
}
.goal-toast.done { background: #16a34a; }
.goal-toast.blocked { background: #dc2626; }
@keyframes goalToastIn { from { opacity: 0; transform: translate(-50%, 8px); } to { opacity: 1; transform: translate(-50%, 0); } }

/* Banner in-chat */
.goal-banner {
  padding: 7px 14px; font-size: 12px; border-bottom: 1px solid var(--border, #e5e7eb);
  background: #f0fdf4; color: #15803d; cursor: pointer; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}

/* Thumbnail + badge de deliverable */
.deliverable-thumb {
  width: 44px; height: 44px; border-radius: 8px; object-fit: cover;
  border: 1px solid var(--border, #e5e7eb); background: #000;
}
.deliverable-badge {
  font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
  padding: 1px 6px; border-radius: 999px; background: #ede9fe; color: #6d28d9;
}

/* Separador de rondas en el chat */
.round-sep {
  display: flex; align-items: center; gap: 10px; margin: 14px 0 6px;
  color: var(--muted, #6b7280); font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .06em;
}
.round-sep::before, .round-sep::after { content: ""; flex: 1; height: 1px; background: var(--border, #e5e7eb); }
```

---

## DIFF 3 — `static/js/goalCard.js`: render extendido

Reemplazar `renderGoal(goal)` y `refreshGoal()` por:

```js
let _lastPhase = null;

export function renderGoal(goal, extra = {}) {
  const card = document.getElementById('goal-card');
  if (!card) return;
  if (!goal) { card.hidden = true; document.getElementById('goal-banner') && (document.getElementById('goal-banner').hidden = true); return; }
  card.hidden = false;

  document.getElementById('goal-objective').textContent = goal.objective || '';
  const badge = document.getElementById('goal-badge');
  badge.textContent = goal.phase || 'active';
  badge.className = 'goal-badge ' + (goal.phase || 'active');

  // Rondas (meta amplia)
  const fill = document.getElementById('goal-progress-fill');
  const pct = goal.maxGoalRounds ? Math.min(100, (goal.roundsStarted / goal.maxGoalRounds) * 100) : 0;
  fill.style.width = pct + '%';
  document.getElementById('goal-rounds').textContent =
    `Ronda ${goal.roundsStarted ?? 0}${goal.maxGoalRounds ? '/' + goal.maxGoalRounds : ''}`;

  // ── GAP 1: cola de items (tarea continua) ──
  const queue = extra.queue || { remaining: [], done: [] };
  const total = queue.remaining.length + queue.done.length;
  const qRow = document.getElementById('goal-queue-row');
  const cur = document.getElementById('goal-current');
  if (total > 0) {
    qRow.hidden = false;
    document.getElementById('goal-queue-fill').style.width =
      (total ? (queue.done.length / total) * 100 : 0) + '%';
    document.getElementById('goal-queue-count').textContent = `${queue.done.length}/${total}`;
    cur.hidden = !queue.remaining.length;
    cur.textContent = queue.remaining.length ? `· procesando: ${queue.remaining[0]}` : '· cola completa';
  } else {
    qRow.hidden = true;
    cur.hidden = true;
  }

  // ── GAP 2: actividad ──
  setGoalActivity(extra.activity || (goal.phase === 'blocked' ? 'blocked' : 'idle'));

  // ── GAP 3: trail (checkpoint) ──
  const trail = document.getElementById('goal-trail-body');
  if (trail && extra.checkpoint !== undefined) {
    trail.textContent = extra.checkpoint || '(aún no hay trail)';
  }

  const blocked = document.getElementById('goal-blocked');
  const reason = goal.blockedReason?.message || '';
  blocked.hidden = !reason;
  blocked.textContent = reason;

  // ── GAP 5: toast en transición de fase ──
  if (_lastPhase && _lastPhase !== goal.phase) {
    if (goal.phase === 'complete') showGoalToast('✅ Meta completada', 'done');
    else if (goal.phase === 'blocked') showGoalToast('⛔ Meta bloqueada: ' + (reason || '—'), 'blocked');
  }
  _lastPhase = goal.phase;
  card.classList.toggle('blocked', goal.phase === 'blocked');
  card.classList.toggle('complete', goal.phase === 'complete');

  // ── GAP 7: banner in-chat ──
  const banner = document.getElementById('goal-banner');
  if (banner) {
    banner.hidden = false;
    banner.textContent = `🎯 ${goal.phase === 'active' ? 'Meta activa' : 'Meta ' + goal.phase}: ${goal.objective}`;
    banner.onclick = () => document.getElementById('goal-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

export function setGoalActivity(state) {
  const el = document.getElementById('goal-activity');
  if (el) el.dataset.state = state || 'idle';
}

function showGoalToast(msg, kind) {
  const t = document.createElement('div');
  t.className = 'goal-toast ' + kind;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

export async function refreshGoal() {
  try {
    const [g, q, c] = await Promise.all([
      fetch('/api/goal').then(r => r.ok ? r.json() : null),
      fetch('/api/goal/queue').then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/goal/checkpoint').then(r => r.ok ? r.text() : '').catch(() => ''),
    ]);
    renderGoal(g?.goal || null, { queue: q || { remaining: [], done: [] }, checkpoint: c });
  } catch { renderGoal(null); }
}
```

> **Backend asumido (2 endpoints nuevos):** `GET /api/goal/queue` →
> `checkpoint_queue_state()`; `GET /api/goal/checkpoint` → `checkpoint_read()`.

---

## DIFF 4 — `static/js/chatRenderer.js`: separador de rondas + thumbnail/badge

### 4a. Separador de rondas — en `addMessage`, al inicio (antes de crear `wrap`)

```js
export function addRoundSeparator(roundNum) {
  const sep = document.createElement('div');
  sep.className = 'round-sep';
  sep.textContent = `ronda ${roundNum}`;
  return sep;
}
```
Y en el loop de `addMessagesBatch`/`addMessage`, cuando `metadata.round` cambie, insertar `addRoundSeparator(metadata.round)` antes del bubble.

### 4b. Thumbnail + badge — en `buildDeliverableCard` (línea 236)

Reemplazar el `<span class="deliverable-icon">📄</span>` por:

```js
  const icon = file.previewUrl
    ? `<img class="deliverable-thumb" src="${file.previewUrl}" alt="">`
    : '<span class="deliverable-icon">📄</span>';
  card.innerHTML = `
    ${icon}
    <div class="deliverable-meta">
      <div class="deliverable-name"></div>
      <div class="deliverable-desc"></div>
    </div>
    <span class="deliverable-badge">deliverable</span>
    <button class="deliverable-btn" data-act="preview">Previsualizar</button>
    <button class="deliverable-btn primary" data-act="open">Abrir</button>`;
```

---

## Resumen de dónde va cada gap

| Gap | Archivo | Sección |
|---|---|---|
| 1 cola | `index.html` + `goalCard.js` + `goals.css` | DIFF 1a, 3, 2 |
| 2 actividad | `goalCard.js` (`setGoalActivity`) + CSS pulse | DIFF 3, 2 |
| 3 trail | `index.html` (`<details>` + `<pre>`) + `goalCard.js` | DIFF 1a, 3 |
| 4 rondas | `chatRenderer.js` (`addRoundSeparator`) + CSS | DIFF 4a, 2 |
| 5 toast | `goalCard.js` (`showGoalToast`) + CSS | DIFF 3, 2 |
| 6 thumbnail/badge | `chatRenderer.js` + CSS | DIFF 4b, 2 |
| 7 banner | `index.html` + `goalCard.js` + CSS | DIFF 1b, 3, 2 |
