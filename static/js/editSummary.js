// static/js/editSummary.js
// DSH parity — "Edited N files" card for an agent turn.
//   • collapsible header: pencil · Edited N files · +A −R
//   • one row per file: name, +/− stats, NEW badge, a diff button (hover =
//     preview, click = pinned popover) and a </> button that opens the file
//     in the document editor (documentModule.openFileByPath).
// Used both live (file_diff / present SSE events) and when history reloads
// (rebuilt from the persisted tool_events[].diff), so it never disappears.

const ICON_PENCIL = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
const ICON_CODE = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
const ICON_DIFF = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v14M5 10h14"/><path d="M5 21h14"/></svg>';
const ICON_CHEV = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>';

const _base = (p) => String(p || '').replace(/\\/g, '/').split('/').pop() || 'file';

/** Merge several diffs of the same file in one turn into one entry. */
export function mergeFiles(list) {
  const byPath = new Map();
  for (const f of list || []) {
    if (!f || !f.path) continue;
    const prev = byPath.get(f.path);
    if (!prev) { byPath.set(f.path, { ...f }); continue; }
    prev.added = (prev.added || 0) + (f.added || 0);
    prev.removed = (prev.removed || 0) + (f.removed || 0);
    if (f.lines && f.lines.length) prev.lines = f.lines;      // latest edit wins for the preview
    prev.new_file = prev.new_file || f.new_file;
  }
  return [...byPath.values()];
}

/** Files edited in a turn, from persisted metadata (history reload). */
export function filesFromMetadata(meta) {
  const out = [];
  for (const ev of (meta && meta.tool_events) || []) {
    if (ev && ev.diff && ev.diff.path) out.push(ev.diff);
    else if (ev && ev.tool === 'write_file' && ev.path && (ev.exit_code === 0 || ev.exit_code == null))
      out.push({ path: ev.path, added: null, removed: null, lines: [] });
  }
  return mergeFiles(out);
}

function _stat(f) {
  if (f.added == null && f.removed == null) return '';
  return `<span class="es-add">+${f.added || 0}</span><span class="es-del">−${f.removed || 0}</span>`;
}

function _renderDiffLines(pre, lines) {
  pre.textContent = '';
  for (const l of lines || []) {
    const row = document.createElement('div');
    row.className = 'es-dl' + (l.startsWith('+++') || l.startsWith('---') ? ' es-dl-file'
      : l.startsWith('@@') ? ' es-dl-hunk' : l.startsWith('+') ? ' es-dl-add' : l.startsWith('-') ? ' es-dl-del' : '');
    row.textContent = l || ' ';
    pre.appendChild(row);
  }
}

let _hoverTimer = 0;
function _closePopovers(exceptPinned) {
  document.querySelectorAll('.es-popover').forEach(p => { if (!(exceptPinned && p.dataset.pinned)) p.remove(); });
}

export function showDiffPopover(anchor, f, pinned) {
  _closePopovers(false);
  const pop = document.createElement('div');
  pop.className = 'es-popover' + (pinned ? ' pinned' : '');
  if (pinned) pop.dataset.pinned = '1';
  const head = document.createElement('div');
  head.className = 'es-pop-head';
  const title = document.createElement('span');
  title.className = 'es-pop-title';
  const _segs = String(f.path || 'file').replace(/\\/g, '/').split('/');
  title.textContent = String(f.path || '').startsWith('doc:') ? (f.name || 'document') + ' · document'
    : (_segs.length > 4 ? '…/' + _segs.slice(-4).join('/') : (f.path || 'file'));
  title.title = f.path || '';
  head.appendChild(title);
  const st = document.createElement('span');
  st.className = 'es-pop-stat';
  st.innerHTML = _stat(f) + (f.new_file ? '<span class="es-new">new</span>' : '');
  head.appendChild(st);
  const open = document.createElement('button');
  open.className = 'es-btn';
  open.title = 'Open in editor';
  open.innerHTML = ICON_CODE;
  open.addEventListener('click', (e) => { e.stopPropagation(); openInEditor(f.path); });
  head.appendChild(open);
  const close = document.createElement('button');
  close.className = 'es-btn';
  close.title = 'Close';
  close.textContent = '×';
  close.addEventListener('click', (e) => { e.stopPropagation(); pop.remove(); });
  head.appendChild(close);
  const pre = document.createElement('div');
  pre.className = 'es-pop-body';
  if (f.lines && f.lines.length) _renderDiffLines(pre, f.lines);
  else pre.textContent = 'No line diff recorded for this edit — open the file to see it.';
  if (f.truncated) {
    const more = document.createElement('div');
    more.className = 'es-dl es-dl-hunk';
    more.textContent = '… diff truncated — open the file for the rest';
    pre.appendChild(more);
  }
  pop.append(head, pre);
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  const w = Math.min(560, window.innerWidth - 24);
  pop.style.width = w + 'px';
  pop.style.left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12)) + 'px';
  const below = r.bottom + 8;
  const h = Math.min(420, pop.offsetHeight || 420);
  pop.style.top = (below + h > window.innerHeight - 12 ? Math.max(12, r.top - h - 8) : below) + 'px';
  if (!pinned) {
    pop.addEventListener('mouseenter', () => clearTimeout(_hoverTimer));
    pop.addEventListener('mouseleave', () => { _hoverTimer = setTimeout(() => { if (!pop.dataset.pinned) pop.remove(); }, 200); });
  } else {
    const dismiss = (ev) => {
      if (!pop.contains(ev.target) && !anchor.contains(ev.target)) { pop.remove(); document.removeEventListener('mousedown', dismiss, true); }
    };
    setTimeout(() => document.addEventListener('mousedown', dismiss, true), 0);
    const esc = (ev) => { if (ev.key === 'Escape') { pop.remove(); document.removeEventListener('keydown', esc); } };
    document.addEventListener('keydown', esc);
  }
  return pop;
}

export function openInEditor(path) {
  if (!path) return;
  const dm = window.documentModule;
  const m = /^doc:(.+)$/.exec(String(path));
  if (m) { if (dm && dm.loadDocument) dm.loadDocument(m[1]); return; }
  if (dm && dm.openFileByPath) dm.openFileByPath(path);
}

/** Build the "Edited N files" card. Returns null when there is nothing to show. */
export function buildEditSummary(files, { collapsed = false } = {}) {
  files = mergeFiles(files);
  if (!files.length) return null;
  const totA = files.reduce((s, f) => s + (f.added || 0), 0);
  const totR = files.reduce((s, f) => s + (f.removed || 0), 0);
  const hasStats = files.some(f => f.added != null || f.removed != null);

  const card = document.createElement('div');
  card.className = 'edit-summary' + (collapsed ? ' collapsed' : '');
  card.dataset.count = String(files.length);
  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'es-head';
  head.setAttribute('aria-expanded', String(!collapsed));
  head.innerHTML = `<span class="es-ico">${ICON_PENCIL}</span>`
    + `<span class="es-title">Edited ${files.length} file${files.length === 1 ? '' : 's'}</span>`
    + (hasStats ? `<span class="es-tot"><span class="es-add">+${totA}</span><span class="es-del">−${totR}</span></span>` : '')
    + `<span class="es-chev">${ICON_CHEV}</span>`;
  head.addEventListener('click', () => {
    const c = card.classList.toggle('collapsed');
    head.setAttribute('aria-expanded', String(!c));
  });
  const list = document.createElement('ul');
  list.className = 'es-list';
  for (const f of files) {
    const li = document.createElement('li');
    li.className = 'es-row';
    const _isDoc = String(f.path).startsWith('doc:');
    li.title = _isDoc ? `Document · ${f.name || ''}` : f.path;
    const name = document.createElement('span');
    name.className = 'es-name';
    name.textContent = _isDoc ? (f.name || 'document') : _base(f.path);
    const dir = document.createElement('span');
    dir.className = 'es-dir';
    const parts = String(f.path).replace(/\\/g, '/').split('/');
    dir.textContent = _isDoc ? 'document' : (parts.length > 1 ? parts.slice(-3, -1).join('/') : '');
    const stat = document.createElement('span');
    stat.className = 'es-stat';
    stat.innerHTML = _stat(f) + (f.new_file ? '<span class="es-new">new</span>' : '');
    const diffBtn = document.createElement('button');
    diffBtn.type = 'button';
    diffBtn.className = 'es-btn es-diff';
    diffBtn.title = 'Show diff (hover to preview, click to pin)';
    diffBtn.innerHTML = ICON_DIFF;
    diffBtn.addEventListener('mouseenter', () => {
      clearTimeout(_hoverTimer);
      _hoverTimer = setTimeout(() => { if (!document.querySelector('.es-popover.pinned')) showDiffPopover(diffBtn, f, false); }, 260);
    });
    diffBtn.addEventListener('mouseleave', () => {
      clearTimeout(_hoverTimer);
      _hoverTimer = setTimeout(() => _closePopovers(true), 220);
    });
    diffBtn.addEventListener('click', (e) => { e.stopPropagation(); clearTimeout(_hoverTimer); showDiffPopover(diffBtn, f, true); });
    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'es-btn es-open';
    openBtn.title = 'Open in editor';
    openBtn.innerHTML = ICON_CODE;
    openBtn.addEventListener('click', (e) => { e.stopPropagation(); openInEditor(f.path); });
    name.addEventListener('click', () => showDiffPopover(diffBtn, f, true));
    li.append(name, dir, stat, diffBtn, openBtn);
    list.appendChild(li);
  }
  card.append(head, list);
  return card;
}

/** Live: add/update a file in the turn's running summary card inside `container`. */
export function upsertLiveSummary(container, fileDiff) {
  if (!container || !fileDiff || !fileDiff.path) return null;
  let card = container.querySelector(':scope > .edit-summary.es-live');
  const files = card && card._files ? card._files : [];
  files.push(fileDiff);
  const fresh = buildEditSummary(files);
  if (!fresh) return card;
  fresh.classList.add('es-live');
  fresh._files = files;
  if (card) card.replaceWith(fresh); else container.appendChild(fresh);
  return fresh;
}

export default { buildEditSummary, filesFromMetadata, mergeFiles, showDiffPopover, openInEditor, upsertLiveSummary };
