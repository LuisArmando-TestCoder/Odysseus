# Plan UI/UX Odysseus — metas continuas + render de documentos (extrapolado de DSH)

## 0. Diagnóstico (lo que inspeccioné hoy)

### Mi UI (DSH) — los componentes que sí tengo
| Componente | Código (extraído) |
|---|---|
| **Goal card** persistente (objetivo + fase + rondas + progreso) | `dsh-client-ui-goal`, `dsh-goal`, `dsh-goal-round-driver`, `dsh-command-goal` |
| **Deliverables** = file cards con preview + native-open | `dsh-tool-present`, `dsh-client-ui-deliverables` |
| **Document preview** en panel lateral (NO inline en el chat) | `dsh-client-ui-sidebar-documentpreview` |
| **Plan mode** (aprobación del plan antes de ejecutar) | `dsh-plan-mode`, `dsh-client-ui-plan` |

### Odysseus — el estado real
- **No hay sistema de goals**: no existe `routes/*goal*`, ni goal card, ni driver de rondas. Cero.
- **Documentos** = `attach-card` en `chatRenderer.js:165-190`, cuyo click **solo se registra si `att.id` existe** (líneas 168-177).

### Los dos bugs concretos (causa raíz encontrada)

**Bug 1 — "Click here" no clicable.** En `static/js/chatRenderer.js`:
```js
if (att.id) {
  card.dataset.fileId = att.id;
  card.style.cursor = 'pointer';
  card.addEventListener('click', () => { ... });
}
```
Si el attachment **no tiene `att.id`** (subida aún pendiente, o mensaje restaurado sin id persistido), el card se renderiza **sin handler y sin cursor** — parece clicable ("Click here") pero no hace nada.

**Bug 2 — Documentos largos truncados** (lo del caso Yoko Sur: "solo recibí el principio y el final"). El input largo se recorta antes de llegar al agente; no hay spill a archivo.

---

## 1. Fase A — arreglar el card de documento (rápido, ~1 h)

`static/js/chatRenderer.js`, en `buildAttachCards`:

```js
// Antes: solo clicable si att.id existe.
// Después: SIEMPRE clicable; si aún no hay id, muestra estado y difiere.
const card = document.createElement('div');
card.className = 'attach-card';
card.dataset.name = att.name;
card.style.cursor = 'pointer';
card.addEventListener('click', () => {
  if (!att.id) {
    // Subida pendiente → avisar, no un clic muerto.
    card.classList.add('attach-card-pending');
    card.title = 'Subiendo… vuelve a intentar en un momento';
    return;
  }
  if (window.chatModule?.openAttachment) window.chatModule.openAttachment(att, false);
  else window.open(`/api/upload/${att.id}`, '_blank');
});
```

**Aceptación:** un mensaje con un documento recién adjuntado (antes de que termine la subida) debe mostrar feedback al click, no un clic muerto; tras la subida, `updateMessageAttachments` re-renderiza el card con el id y abre el viewer.

---

## 2. Fase B — panel de preview lateral (como DSH `sidebar-documentpreview`)

Hoy `openAttachment` abre un "Documents viewer". El gap es que no hay **preview inline ligero** para PDFs/texto sin salir del chat.

- Añadir `static/js/documentPreview.js`: panel lateral (right sidebar) que carga el documento en un `<iframe>`/`<pre>` según tipo, con botón "Abrir en editor".
- El card de documento llama a `openPreview(att)` (preview) y un botón secundario "Editar" llama a `openAttachment(att, true)`.
- **Nunca** renderizar el contenido completo inline en el bubble (eso es lo que revienta el layout con documentos grandes).

---

## 3. Fase C — spill del input largo (no truncar)

El caso Yoko Sur es un fallo de **input**, no de output. Añadir en el backend:

- En `routes/chat_routes.py` (o `chat_processor`), si el mensaje de usuario supera un umbral (p. ej. 20 000 chars), **escribirlo a `data/uploads/spill_<ts>.txt`** y enviar al modelo solo el prefijo + `[CONTENIDO COMPLETO EN: <ruta>]`.
- El agente ya tiene `read_file` y `docfind` para leerlo. Esto elimina "solo recibí el principio y el final".

**Aceptación:** pegar un documento de 100 KB en el chat → el agente recibe el prefijo + la ruta, y puede `read_file` el resto.

---

## 4. Fase D — Goal card + driver de rondas (la pieza grande)

Extrapolar el goal system de DSH a Odysseus, **reusando `src/checkpoint.py`** que ya existe.

### Backend
- `src/goal.py`: modelo `Goal` (id, objective, phase `active|paused|blocked|complete`, revision, rounds_started, max_rounds, blocked_reason) + `create/get/update` (espejo de las tools `create_goal/get_goal/update_goal` de DSH).
- `routes/goal_routes.py`: `POST /api/goal`, `GET /api/goal`, `POST /api/goal/{action}` (pause/resume/complete/blocked).
- `src/goal_round_driver.py`: loop de auto-continuación que re-invoca el agente cada ronda hasta `complete`/`blocked`, persistiendo el progreso en `checkpoint` (ya existe).

### Frontend
- `static/js/goalCard.js`: card persistente en el sidebar:
  - objetivo (1 línea),
  - badge de fase (color: active=verde, paused=ámbar, blocked=rojo, complete=azul),
  - `Rondas: 4/256` + barra de progreso,
  - `blocked_reason` desplegable,
  - botones: Pausar / Reanudar / Completar / Desbloquear.
- Se re-renderiza en cada ronda (evento SSE `goal_update`).

**Aceptación:** crear una meta → aparece la card con fase "active" y rondas incrementando; al completar/bloquear, la card cambia de color y muestra la razón.

---

## 5. Fase E — belleza (sistema de diseño consistente)

- Un sistema de **cards** único para: documentos, deliverables, y goals (mismo radio, sombra, hover, íconos).
- **Progreso visible** (barra de rondas en goals; spinner de subida en documentos).
- **Estados, no excepciones**: cada card tiene estados (pending/ready/error/complete) con color + ícono, nunca un clic muerto.
- Tipografía y espaciado consistentes con el tema existente (`theme.js`); respetar `content-visibility` para listas largas (ya está).

---

## Orden y esfuerzo

| Fase | Esfuerzo | Impacto |
|---|---|---|
| A — fix card clicable | <1 h | mata el bug "Click here" |
| C — spill de input largo | ½ día | mata el truncamiento (caso Yoko Sur) |
| B — preview lateral | 1 día | documentos sin salir del chat |
| D — goal card + driver | 2–3 días | la capacidad de metas continuas |
| E — belleza | continuo | pulido |

**Orden recomendado:** A → C → B → D → E. A y C arreglan los dos bugs que ya sufriste; D es la capacidad nueva (metas continuas); E es el pulido final.




# UI SPEC — Odysseus ≈ DSH (goals + render de mensajes)

Archivo-por-archivo, con el código exacto. Todo va sobre lo que ya existe.

---

# PARTE 1 — Goal card (la capacidad nueva)

## 1.1 Dónde inyectar el HTML

`static/index.html` — dentro de `<div class="sidebar-inner" id="sidebar-inner">`
(empieza en línea 687), **inmediatamente después del bloque de búsqueda/nuevo chat**
y antes de la sección "Chats" (línea ~703). Insertar:

```html
<!-- Goal card (metas continuas) — renderizado por static/js/goalCard.js -->
<section class="goal-card" id="goal-card" aria-label="Meta activa" hidden>
  <div class="goal-card-header">
    <span class="goal-card-title">🎯 Meta</span>
    <span class="goal-badge" id="goal-badge">active</span>
  </div>
  <p class="goal-objective" id="goal-objective"></p>
  <div class="goal-progress-row">
    <div class="goal-progress-bar"><div class="goal-progress-fill" id="goal-progress-fill"></div></div>
    <span class="goal-rounds" id="goal-rounds"></span>
  </div>
  <p class="goal-blocked" id="goal-blocked" hidden></p>
  <div class="goal-actions">
    <button class="goal-btn" id="goal-pause" title="Pausar">⏸</button>
    <button class="goal-btn" id="goal-resume" title="Reanudar">▶</button>
    <button class="goal-btn" id="goal-complete" title="Completar">✓</button>
  </div>
</section>
```

## 1.2 CSS (nuevo archivo `static/css/goals.css` + enlazar)

Añadir en `static/index.html` `<head>` (junto al otro `<link rel="stylesheet">`):
```html
<link rel="stylesheet" href="/static/css/goals.css">
```

`static/css/goals.css`:
```css
/* Goal card — metas continuas (extrapolado de DSH) */
.goal-card {
  padding: 10px 12px;
  margin: 6px 8px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 10px;
  background: var(--card-bg, #ffffff);
  box-shadow: 0 1px 2px rgba(0,0,0,.04);
}
.goal-card-header { display: flex; justify-content: space-between; align-items: center; }
.goal-card-title { font-size: 11px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--muted, #6b7280); }
.goal-badge {
  font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 999px;
  text-transform: uppercase; letter-spacing: .04em;
}
.goal-badge.active   { background: #dcfce7; color: #15803d; }
.goal-badge.paused   { background: #fef3c7; color: #b45309; }
.goal-badge.blocked  { background: #fee2e2; color: #b91c1c; }
.goal-badge.complete { background: #dbeafe; color: #1d4ed8; }
.goal-objective {
  margin: 6px 0 8px; font-size: 12.5px; line-height: 1.4; color: var(--fg, #111827);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.goal-progress-row { display: flex; align-items: center; gap: 8px; }
.goal-progress-bar { flex: 1; height: 5px; border-radius: 999px; background: var(--border, #e5e7eb); overflow: hidden; }
.goal-progress-fill { height: 100%; width: 0%; border-radius: 999px; background: #10b981; transition: width .3s ease; }
.goal-blocked.complete .goal-progress-fill { background: #3b82f6; }
.goal-rounds { font-size: 10.5px; color: var(--muted, #6b7280); white-space: nowrap; }
.goal-blocked { margin: 6px 0 0; font-size: 11px; color: #b91c1c; }
.goal-actions { display: flex; gap: 6px; margin-top: 8px; }
.goal-btn {
  flex: 1; border: 1px solid var(--border, #e5e7eb); background: transparent;
  border-radius: 7px; padding: 4px 0; font-size: 12px; cursor: pointer; color: var(--fg, #111827);
}
.goal-btn:hover { background: var(--hover, #f3f4f6); }
```

## 1.3 JS (nuevo `static/js/goalCard.js`)

```js
// goalCard.js — renderiza la meta continua (espejo de la goal card de DSH).
const GOAL_PHASES = ['active', 'paused', 'blocked', 'complete'];

export function renderGoal(goal) {
  const card = document.getElementById('goal-card');
  if (!card) return;
  if (!goal) { card.hidden = true; return; }
  card.hidden = false;

  document.getElementById('goal-objective').textContent = goal.objective || '';
  const badge = document.getElementById('goal-badge');
  badge.textContent = goal.phase || 'active';
  badge.className = 'goal-badge ' + (goal.phase || 'active');

  const fill = document.getElementById('goal-progress-fill');
  const pct = goal.maxGoalRounds ? Math.min(100, (goal.roundsStarted / goal.maxGoalRounds) * 100) : 0;
  fill.style.width = pct + '%';
  document.getElementById('goal-rounds').textContent =
    `Ronda ${goal.roundsStarted ?? 0}${goal.maxGoalRounds ? '/' + goal.maxGoalRounds : ''}`;

  const blocked = document.getElementById('goal-blocked');
  const reason = goal.blockedReason?.message || '';
  blocked.hidden = !reason;
  blocked.textContent = reason;
  card.classList.toggle('complete', goal.phase === 'complete');
}

function bind(btnId, action) {
  const el = document.getElementById(btnId);
  el?.addEventListener('click', async () => {
    await fetch('/api/goal/' + action, { method: 'POST' });
    refreshGoal();
  });
}

export async function refreshGoal() {
  try {
    const r = await fetch('/api/goal');
    if (!r.ok) { renderGoal(null); return; }
    const data = await r.json();
    renderGoal(data.goal || null);
  } catch { /* sin meta */ }
}

export function initGoalCard() {
  bind('goal-pause', 'pause');
  bind('goal-resume', 'resume');
  bind('goal-complete', 'complete');
  refreshGoal();
  // Re-render on the agent's goal events (wire to your SSE here).
}
```

Registrar en `static/js/init.js` (o donde se inicializan los módulos):
```js
import { initGoalCard } from './goalCard.js';
// ... junto a las demás init
initGoalCard();
```

> **Prerequisito backend** (fuera del alcance de esta spec de UI): `src/goal.py`
> (objeto `Goal` con `id/revision/phase/rounds/blockedReason`) + `routes/goal_routes.py`
> con `GET /api/goal` y `POST /api/goal/{pause,resume,complete,blocked}` + el
> driver de rondas (`goal_round_driver.py` reusando `checkpoint.py`). El JS de
> arriba ya asume esas rutas.

---

# PARTE 2 — Fix del card de documento ("Click here" no clicable)

## 2.1 Función a cambiar: `buildAttachCards`

`static/js/chatRenderer.js`, líneas 165-177. **Before:**
```js
      const card = document.createElement('div');
      card.className = 'attach-card';
      card.dataset.name = att.name;
      if (att.id) {
        card.dataset.fileId = att.id;
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => {
          if (window.chatModule?.openAttachment) window.chatModule.openAttachment(att, false);
          else window.open(`/api/upload/${att.id}`, '_blank');
        });
      }
```

**After:**
```js
      const card = document.createElement('div');
      card.className = 'attach-card';
      card.dataset.name = att.name;
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => {
        if (!att.id) {
          card.classList.add('attach-card-pending');
          card.title = 'Subiendo… espera un momento y vuelve a intentar';
          return;
        }
        card.classList.remove('attach-card-pending');
        if (window.chatModule?.openAttachment) window.chatModule.openAttachment(att, false);
        else window.open(`/api/upload/${att.id}`, '_blank');
      });
      if (att.id) card.dataset.fileId = att.id;
```

## 2.2 CSS del estado "pending" (en `messages-hyper.css`)

```css
.attach-card-pending { opacity: .55; }
.attach-card-pending::after {
  content: "⏳ subiendo…";
  font-size: 10px; color: var(--muted, #6b7280);
  margin-left: auto;
}
```

---

# PARTE 3 — Render de mensajes (pulido ≈ DSH)

## 3.1 Burbujas: roles y cuerpo (`messages-hyper.css`)

Añadir/ajustar (ya existen `.msg`, `.msg-ai`, `.msg-user`):

```css
.msg { display: flex; flex-direction: column; gap: 4px; padding: 8px 12px; }
.msg .role {
  font-size: 11px; font-weight: 700; letter-spacing: .03em;
  color: var(--muted, #6b7280); display: flex; align-items: center; gap: 6px;
}
.msg .body {
  font-size: 14px; line-height: 1.6; color: var(--fg, #111827);
  overflow-wrap: anywhere;
}
.msg-user .body { white-space: pre-wrap; }
.msg-ai .body { max-width: 100%; }
```

## 3.2 Bloques de tool (agente) — legibilidad

Ya existen `.agent-thread`, `.agent-thread-node`, `.agent-tool-output`. Añadir:

```css
.agent-thread { border-left: 2px solid var(--border, #e5e7eb); margin: 6px 0; padding-left: 10px; }
.agent-thread-node { font-size: 12.5px; margin: 4px 0; }
.agent-tool-output { font-size: 12px; }
.agent-tool-output summary { cursor: pointer; color: var(--muted, #6b7280); font-weight: 600; }
.agent-tool-output pre {
  background: var(--code-bg, #0b1020); color: #e5e7eb;
  padding: 8px; border-radius: 8px; overflow-x: auto; font-size: 12px;
}
```

## 3.3 Footer de mensaje (acciones hover)

Ya existe `createMsgFooter` (chatRenderer.js:1380) con `.msg-footer`/`.msg-actions`. Ajustar CSS para que las acciones aparezcan al hover (patrón de DSH):

```css
.msg-footer { opacity: 0; transition: opacity .12s ease; }
.msg:hover .msg-footer { opacity: 1; }
.msg-action-btn {
  background: transparent; border: none; cursor: pointer;
  color: var(--muted, #6b7280); font-size: 12px; padding: 2px 6px; border-radius: 6px;
}
.msg-action-btn:hover { background: var(--hover, #f3f4f6); color: var(--fg, #111827); }
```

---

# PARTE 4 — Deliverable card (equivalente de `present`)

Cuando el agente genera un archivo entregable (reporte, doc), en vez de un bubble
genérico, renderizar una **card de deliverable** con "Abrir" + "Previsualizar".

## 4.1 CSS (en `goals.css` o `messages-hyper.css`)

```css
.deliverable-card {
  display: flex; align-items: center; gap: 10px;
  border: 1px solid var(--border, #e5e7eb); border-radius: 10px;
  padding: 10px 12px; margin: 6px 0; background: var(--card-bg, #fff);
}
.deliverable-icon { font-size: 20px; }
.deliverable-meta { flex: 1; min-width: 0; }
.deliverable-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.deliverable-desc { font-size: 11px; color: var(--muted, #6b7280); }
.deliverable-btn {
  border: 1px solid var(--border, #e5e7eb); background: transparent;
  border-radius: 7px; padding: 5px 10px; font-size: 12px; cursor: pointer;
}
.deliverable-btn.primary { background: #10b981; border-color: #10b981; color: #fff; }
```

## 4.2 Función (nueva) en `chatRenderer.js`

```js
export function buildDeliverableCard(file) {
  const card = document.createElement('div');
  card.className = 'deliverable-card';
  card.innerHTML = `
    <span class="deliverable-icon">📄</span>
    <div class="deliverable-meta">
      <div class="deliverable-name"></div>
      <div class="deliverable-desc"></div>
    </div>
    <button class="deliverable-btn" data-act="preview">Previsualizar</button>
    <button class="deliverable-btn primary" data-act="open">Abrir</button>`;
  card.querySelector('.deliverable-name').textContent = file.name || file.path?.split('/').pop() || 'Archivo';
  card.querySelector('.deliverable-desc').textContent = file.description || '';
  card.querySelector('[data-act="preview"]').addEventListener('click', () =>
    window.chatModule?.openAttachment?.({ name: file.name, id: file.id }, false));
  card.querySelector('[data-act="open"]').addEventListener('click', () =>
    window.chatModule?.openAttachment?.({ name: file.name, id: file.id }, true));
  return card;
}
```

Llamar desde `addMessage` cuando `metadata.deliverables?.length` (o al recibir el
evento `present` del backend), insertando `buildDeliverableCard(f)` por cada archivo.

---

## Resumen de cambios (dónde / qué)

| Archivo | Cambio |
|---|---|
| `static/index.html` | insertar goal card en `#sidebar-inner` + `<link>` a `goals.css` |
| `static/css/goals.css` | **nuevo** — estilos de goal card + deliverable card |
| `static/js/goalCard.js` | **nuevo** — render + botones de goal |
| `static/js/init.js` | registrar `initGoalCard()` |
| `static/js/chatRenderer.js` | arreglar `buildAttachCards` (2.1) + añadir `buildDeliverableCard` (4.2) |
| `static/css/messages-hyper.css` | estilos de `.msg`/`.role`/`.body`/tool blocks/footer (3.x) + `.attach-card-pending` (2.2) |
| backend (fuera de UI) | `src/goal.py` + `routes/goal_routes.py` + `goal_round_driver.py` |

# GOAL HARNESS SPEC — Odysseus (espejo de DSH)

Backend que empareja la goal card del `UI_SPEC.md`. Mismo modelo que DSH:
un `Goal` es un **objeto versionado**, no texto del chat.

---

## 1. STATE MODEL

```json
{
  "id": "goal-<uuid>",
  "session_id": "<session>",
  "objective": "Dotar a Odysseus de ...",
  "revision": 2,
  "phase": "active",              // active | paused | blocked | complete
  "roundsStarted": 6,
  "maxGoalRounds": 256,
  "blockedReason": { "code": "model-reported", "message": "..." } | null,
  "activation": "armed",          // armed | disarmed
  "createdAt": 1710000000,
  "updatedAt": 1710000000
}
```

Campos no cosméticos:
- **`revision`** — contador monotónico para **concurrencia optimista**: `update_goal`
  exige la revisión exacta; una revisión vieja → conflicto (evita carreras entre rondas).
- **`phase`** — máquina de estados finita.
- **`roundsStarted`** — progreso + tope de auto-continuación.
- **`activation`** — `armed`/`disarmed`: tras resume/fork se desarma hasta que un
  humano pide continuar.
- **`blockedReason`** — objeto `{code, message}`, no string suelto.

---

## 2. FUNCTIONS

### `create_goal(session_id, objective, max_goal_rounds=None) -> Goal`
Crea y arma. Se usa cuando el pedido humano es un objetivo de larga duración,
**aunque no diga "goal"**; no para trabajo de un solo turno.

### `get_goal(session_id) -> Goal | None`
Lee el goal actual (incluye `id` y `revision` que `update_goal` exige).

### `update_goal(session_id, revision, action, ...) -> Goal`
Transiciona la fase. **Exige `revision` exacta.**

| action | Efecto | Quién puede |
|---|---|---|
| `edit` | cambia objective/max_goal_rounds; reabre si estaba complete/blocked; re-arma | humano (top-level) |
| `pause` | phase → paused, desarma | humano (top-level) |
| `resume` | phase → active, limpia blockedReason, re-arma | humano (top-level) |
| `complete` | phase → complete, desarma | humano o auto-continuación |
| `blocked` | phase → blocked, fija blockedReason, desarma | auto-continuación |

---

## 3. FRAMING (reglas de ciclo de vida, verbatim de DSH)

1. **`edit`/`pause`/`resume` requieren pedido humano directo.** La auto-continuación
   no puede pausar ni redefinir el objetivo sola.
2. **`complete`/`blocked` sí se permiten durante auto-continuación.**
3. **`complete` solo cuando el objetivo está realmente logrado** (con evidencia),
   no cuando "ya no sé qué más hacer".
4. **`blocked` solo tras 3 rondas consecutivas con la misma condición** bloqueante,
   y con `blocked_reason` concreto. *Dificultad, incertidumbre o trabajo útil
   restante NO es "blocked".*
5. **Tras resume/fork, un goal activo se desarma** (`activation: disarmed`);
   cuando un humano pide "continuar/resumir" en cualquier idioma, se re-arma con
   `update_goal(..., action="resume")`.
6. **Tope:** al alcanzar `maxGoalRounds` sin completar → `blocked` con
   `"maxGoalRounds reached"`.

---

## 4. INFRAESTRUCTURA

### 4.1 Persistencia — `data/goals/<session>.md.json` (escritura atómica)

Reusar `core/atomic_io.atomic_write_json` (ya existe) — es el mismo `dsh-atomic-write`.

### 4.2 `src/goal.py` (código)

```python
"""Persistent, versioned goal state — mirror of DSH's goal system."""
import json, os, re, time, uuid
from typing import Any, Dict, Optional

PHASES = ("active", "paused", "blocked", "complete")


class GoalConflict(Exception):
    """update_goal called with a stale revision."""


def _path(session_id: str) -> str:
    from src.constants import DATA_DIR
    d = os.path.join(DATA_DIR, "goals")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default"))
    return os.path.join(d, f"{safe}.json")


def _read(session_id: str) -> Optional[Dict[str, Any]]:
    p = _path(session_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(session_id: str, goal: Dict[str, Any]) -> None:
    from core.atomic_io import atomic_write_json
    atomic_write_json(_path(session_id), goal)


def create_goal(session_id: str, objective: str,
                max_goal_rounds: Optional[int] = None) -> Dict[str, Any]:
    goal = {
        "id": f"goal-{uuid.uuid4()}",
        "session_id": session_id,
        "objective": (objective or "").strip(),
        "revision": 1,
        "phase": "active",
        "roundsStarted": 0,
        "maxGoalRounds": max_goal_rounds or 256,
        "blockedReason": None,
        "activation": "armed",
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }
    _write(session_id, goal)
    return goal


def get_goal(session_id: str) -> Optional[Dict[str, Any]]:
    return _read(session_id)


def update_goal(session_id: str, revision: int, action: str,
                objective: Optional[str] = None,
                max_goal_rounds: Optional[int] = None,
                blocked_reason: Optional[str] = None) -> Dict[str, Any]:
    goal = _read(session_id)
    if not goal:
        raise KeyError("no goal for this session")
    if goal["revision"] != revision:
        raise GoalConflict(f"stale revision: have {goal['revision']}, got {revision}")

    if action == "edit":
        if objective is not None:
            goal["objective"] = objective.strip()
        if max_goal_rounds is not None:
            goal["maxGoalRounds"] = max_goal_rounds
        if goal["phase"] in ("complete", "blocked"):
            goal["phase"] = "active"
            goal["blockedReason"] = None
        goal["activation"] = "armed"
    elif action == "pause":
        goal["phase"] = "paused"
        goal["activation"] = "disarmed"
    elif action == "resume":
        if goal["phase"] in ("paused", "blocked"):
            goal["phase"] = "active"
            goal["blockedReason"] = None
        goal["activation"] = "armed"
    elif action == "complete":
        goal["phase"] = "complete"
        goal["activation"] = "disarmed"
    elif action == "blocked":
        goal["phase"] = "blocked"
        goal["blockedReason"] = {"code": "model-reported", "message": blocked_reason or ""}
        goal["activation"] = "disarmed"
    else:
        raise ValueError(f"unknown action {action!r}")

    goal["revision"] += 1
    goal["updatedAt"] = time.time()
    _write(session_id, goal)
    return goal
```

### 4.3 `routes/goal_routes.py` (código)

```python
def setup_goal_routes(owner_resolver) -> APIRouter:
    router = APIRouter(prefix="/api/goal", tags=["goal"])

    @router.get("")
    async def goal_get(request: Request):
        sid = owner_resolver(request)
        g = get_goal(sid)
        return {"goal": g} if g else {"goal": None}

    @router.post("/{action}")
    async def goal_update(request: Request, action: str):
        sid = owner_resolver(request)
        body = await request.json() if (await request.body()) else {}
        g = get_goal(sid)
        if not g:
            raise HTTPException(404, "no active goal")
        if action in ("edit", "pause", "resume") and not _is_top_level_human(request):
            raise HTTPException(403, "this action requires a direct human request")
        try:
            g = update_goal(sid, int(body.get("revision", g["revision"])), action,
                            objective=body.get("objective"),
                            max_goal_rounds=body.get("max_goal_rounds"),
                            blocked_reason=body.get("blocked_reason"))
        except GoalConflict:
            raise HTTPException(409, "stale revision — reload and retry")
        return {"goal": g}

    return router
```

### 4.4 `src/goal_round_driver.py` — el loop de auto-continuación

```python
async def goal_round_loop(session_id: str, run_agent_round, blocked_conditions: list):
    """Auto-continue rounds until complete/blocked/paused, mirroring DSH's
    goal-round-driver. Persists progress in the checkpoint (already exists)."""
    from src.goal import get_goal, update_goal, GoalConflict

    same_blocked = 0
    last_blocked_reason = None
    while True:
        goal = get_goal(session_id)
        if not goal or goal["phase"] in ("complete", "blocked", "paused"):
            break
        if goal["roundsStarted"] >= goal["maxGoalRounds"]:
            update_goal(session_id, goal["revision"], "blocked",
                        blocked_reason="maxGoalRounds reached")
            break

        # 1) run one agent round (the agent appends to checkpoint via src/checkpoint.py)
        outcome = await run_agent_round(goal)

        # 2) bump progress
        try:
            goal = update_goal(session_id, goal["revision"], "edit")  # no-op edit to bump rev
        except GoalConflict:
            goal = get_goal(session_id)
        goal["roundsStarted"] += 1
        # (persist roundsStarted — the loop owns the counter, not the model)

        # 3) decide continue vs stop
        if outcome.get("done"):
            update_goal(session_id, goal["revision"], "complete")
            break
        if outcome.get("blocked_reason"):
            if outcome["blocked_reason"] == last_blocked_reason:
                same_blocked += 1
            else:
                same_blocked = 1
                last_blocked_reason = outcome["blocked_reason"]
            if same_blocked >= 3:  # DSH rule: blocked only after 3 consecutive rounds
                update_goal(session_id, goal["revision"], "blocked",
                            blocked_reason=last_blocked_reason)
                break
        else:
            same_blocked = 0
```

---

## 5. WIRING al agent loop

Igual que el checkpoint: al iniciar cada ronda, inyectar el goal en el prompt.

En `src/agent_loop.py`, junto a la inyección de checkpoint (línea ~1574):

```python
    # Inject the active goal so the agent stays on-task across rounds.
    try:
        from src.goal import get_goal as _get_goal
        _goal = _get_goal(session_id)
    except Exception:
        _goal = None
    if _goal and _goal["phase"] == "active":
        _goal_msg = (
            "=== ACTIVE GOAL ===\n"
            f"objective: {_goal['objective']}\n"
            f"round {_goal['roundsStarted']}/{_goal['maxGoalRounds']} · phase {_goal['phase']}\n"
            "Work toward this objective; use the checkpoint to resume, not re-derive. "
            "When the objective is truly achieved, stop and report completion."
        )
        messages.insert(0, {"role": "system", "content": _goal_msg})
```

---

## 6. Resumen (dónde / qué)

| Archivo | Qué |
|---|---|
| `src/goal.py` | modelo `Goal` + `create/get/update` con `revision` y `phase` FSM |
| `routes/goal_routes.py` | `GET /api/goal` + `POST /api/goal/{action}` con guard de "humano directo" |
| `src/goal_round_driver.py` | loop de auto-continuación + regla de 3 rondas para `blocked` |
| `src/agent_loop.py` | inyectar `=== ACTIVE GOAL ===` en el prompt (junto al checkpoint) |
| `core/atomic_io.py` | ya existe — reusar para persistencia atómica |
| `src/checkpoint.py` | ya existe — el driver lo usa para persistir progreso entre rondas |

**Framing en una línea:** el goal es un **objeto versionado** (`revision` para
concurrencia, `phase` para el ciclo de vida, `activation` para armed/disarmed)
que **el harness posee y el driver consume** — el modelo solo trabaja en la ronda;
no "recuerda" el estado, lo lee del harness.
