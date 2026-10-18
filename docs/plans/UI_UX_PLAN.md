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
