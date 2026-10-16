// documentPreview.js — lightweight inline document preview (DSH sidebar-documentpreview).
//
// Opens a right-side panel that shows a document WITHOUT leaving the chat:
//   - PDFs → <iframe> (browser's built-in PDF viewer)
//   - text/code/markdown → <pre> (fetched via /api/upload/{id})
//   - everything else → a "download/open" fallback
// A secondary "Abrir en editor" button hands off to the full Documents viewer
// via window.chatModule.openAttachment(att, true).
//
// Never renders the full content inline in the chat bubble — that's what
// blows up the layout with large documents.

const TEXT_EXT = /\.(txt|md|markdown|js|ts|jsx|tsx|py|rb|go|rs|java|c|cpp|h|hpp|cs|php|html?|css|scss|sass|less|json|ya?ml|toml|ini|conf|env|sh|bash|sql|csv|tsv|xml|log|vue|svelte)$/i;

let _panel = null;
let _escHandler = null;

function _close() {
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
  if (_panel) { _panel.remove(); _panel = null; }
}

function _isPdf(att) {
  return (att.mime === 'application/pdf') || /\.pdf$/i.test(att.name || '');
}

function _isText(att) {
  return TEXT_EXT.test(att.name || '') || /^text\//.test(att.mime || '');
}

export function openPreview(att) {
  if (!att || !att.id) return;
  _close();

  const url = `/api/upload/${att.id}`;
  const name = att.name || 'Document';

  const overlay = document.createElement('div');
  overlay.className = 'doc-preview-overlay';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) _close(); });

  const panel = document.createElement('div');
  panel.className = 'doc-preview-panel';

  const header = document.createElement('div');
  header.className = 'doc-preview-header';
  const title = document.createElement('span');
  title.className = 'doc-preview-title';
  title.textContent = name;
  title.title = name;
  const actions = document.createElement('div');
  actions.className = 'doc-preview-actions';

  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.className = 'doc-preview-btn';
  editBtn.textContent = 'Abrir en editor';
  editBtn.addEventListener('click', () => {
    _close();
    if (window.chatModule?.openAttachment) window.chatModule.openAttachment(att, true);
    else window.open(url, '_blank');
  });

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'doc-preview-btn doc-preview-close';
  closeBtn.textContent = '✕';
  closeBtn.title = 'Cerrar';
  closeBtn.addEventListener('click', _close);

  actions.appendChild(editBtn);
  actions.appendChild(closeBtn);
  header.appendChild(title);
  header.appendChild(actions);
  panel.appendChild(header);

  const body = document.createElement('div');
  body.className = 'doc-preview-body';

  if (_isPdf(att)) {
    const iframe = document.createElement('iframe');
    iframe.className = 'doc-preview-iframe';
    iframe.src = url;
    iframe.title = name;
    body.appendChild(iframe);
  } else if (_isText(att)) {
    const pre = document.createElement('pre');
    pre.className = 'doc-preview-pre';
    pre.textContent = 'Cargando…';
    body.appendChild(pre);
    fetch(url, { credentials: 'same-origin' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
      .then(text => { pre.textContent = text; })
      .catch(e => { pre.textContent = 'No se pudo cargar el documento: ' + e.message; });
  } else {
    const fallback = document.createElement('div');
    fallback.className = 'doc-preview-fallback';
    const msg = document.createElement('p');
    msg.textContent = 'Este tipo de archivo no tiene vista previa inline.';
    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'doc-preview-btn';
    openBtn.textContent = 'Abrir archivo';
    openBtn.addEventListener('click', () => window.open(url, '_blank'));
    fallback.appendChild(msg);
    fallback.appendChild(openBtn);
    body.appendChild(fallback);
  }

  panel.appendChild(body);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  _panel = overlay;

  _escHandler = (e) => { if (e.key === 'Escape') _close(); };
  document.addEventListener('keydown', _escHandler);
}

export default { openPreview };