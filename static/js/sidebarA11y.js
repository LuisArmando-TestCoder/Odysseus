// static/js/sidebarA11y.js — keyboard + screen-reader layer for the sidebar.
//
// The sidebar is rendered by several modules (sessions.js, app.js, init.js,
// sidebar-layout.js) that were written mouse-first. Rather than thread ARIA
// through every render path, this module observes the rendered DOM and
// applies the WAI-ARIA Authoring Practices patterns on top of it:
//
//   • Chat list  → Tree view  (role=tree / treeitem, aria-level, aria-expanded,
//                  aria-selected, roving tabindex, type-ahead, F2 rename,
//                  Shift+F10 / ContextMenu for the row's action menu)
//   • Row + sort menus → Menu button (role=menu / menuitem[radio|checkbox],
//                  arrow keys, Home/End, type-ahead, Escape returns focus,
//                  ArrowRight/Left into and out of the "Move to folder" submenu)
//   • Icon rail  → Toolbar (one tab stop, arrows move between tools)
//   • Edge drag handle → Window splitter (role=separator, arrows resize)
//   • Sidebar toggles → aria-expanded kept in sync with the real state
//
// Mouse behaviour is untouched: every activation goes through the existing
// click handlers. The pure helpers at the top are exported for unit tests
// (tests/test_sidebar_a11y_js.py) and have no DOM dependency.

// ───────────────────────────── Pure helpers ─────────────────────────────

/** Next index for a list-navigation key. Returns `cur` for unknown keys. */
export function moveIndex(len, cur, key, wrap = false) {
  if (!len || len <= 0) return -1;
  switch (key) {
    case 'ArrowDown':
      if (cur < 0) return 0;
      return cur + 1 < len ? cur + 1 : (wrap ? 0 : cur);
    case 'ArrowUp':
      if (cur < 0) return len - 1;
      return cur > 0 ? cur - 1 : (wrap ? len - 1 : cur);
    case 'Home': return 0;
    case 'End': return len - 1;
    default: return cur;
  }
}

/**
 * Type-ahead: index of the next label starting with `buf` (case-insensitive).
 * A single character searches *after* the current item so repeated presses
 * cycle through matches; a longer buffer may stay on the current item.
 */
export function typeaheadIndex(labels, cur, buf) {
  const q = String(buf || '').toLowerCase();
  const n = labels ? labels.length : 0;
  if (!q.trim() || !n) return -1;
  const allSame = q.split('').every((c) => c === q[0]);
  const needle = allSame ? q[0] : q;
  const start = (allSame || cur < 0) ? cur + 1 : cur;
  for (let i = 0; i < n; i++) {
    const j = (((start + i) % n) + n) % n;
    if (String(labels[j] || '').trim().toLowerCase().startsWith(needle)) return j;
  }
  return -1;
}

/** Keyboard resize target for a vertical splitter, or null if key unhandled. */
export function resizeTarget(cur, key, opts = {}) {
  const { min = 200, max = 700, shift = false, mirrored = false } = opts;
  const step = shift ? 64 : 16;
  const clamp = (v) => Math.max(min, Math.min(max, Math.round(v)));
  let k = key;
  if (mirrored && (k === 'ArrowLeft' || k === 'ArrowRight')) {
    k = k === 'ArrowLeft' ? 'ArrowRight' : 'ArrowLeft';
  }
  switch (k) {
    case 'ArrowLeft': return clamp(cur - step);
    case 'ArrowRight': return clamp(cur + step);
    case 'Home': return min;
    case 'End': return max;
    default: return null;
  }
}

/** "Work (4)" → { name: "Work", count: 4 } */
export function parseFolderLabel(name, countText) {
  const m = String(countText || '').match(/(\d+)/);
  const count = m ? parseInt(m[1], 10) : null;
  return { name: String(name || '').trim(), count };
}

export function folderAccessibleName(name, count) {
  const n = String(name || '').trim() || 'Folder';
  if (count == null) return n;
  return `${n}, ${count} ${count === 1 ? 'chat' : 'chats'}`;
}

// ───────────────────────────── DOM layer ─────────────────────────────

const HAS_DOM = typeof document !== 'undefined' && typeof window !== 'undefined';

const TREE_ITEM_SEL = '.session-item[data-session-id], .session-folder-header, .session-show-more-btn';
const MENU_ITEM_SEL = '.dropdown-item, .dropdown-item-compact';
const EDITABLE_SEL = 'input, textarea, select, [contenteditable="true"], [contenteditable=""]';

function isShown(el) {
  if (!el || !el.isConnected) return false;
  if (el.hidden) return false;
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}

function setAttr(el, name, value) {
  if (!el) return;
  const v = String(value);
  if (el.getAttribute(name) !== v) el.setAttribute(name, v);
}

function hideDecorative(root, sel) {
  root.querySelectorAll(sel).forEach((n) => setAttr(n, 'aria-hidden', 'true'));
}

/** Every inline SVG inside an interactive control is decoration. */
function hideIcons(root) {
  root.querySelectorAll('svg:not([aria-hidden])').forEach((svg) => {
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
  });
}

/** Icon-only buttons get their tooltip as an accessible name. */
function nameIconButtons(root) {
  root.querySelectorAll('button[title]:not([aria-label]), [role="button"][title]:not([aria-label])').forEach((b) => {
    const text = (b.textContent || '').replace(/[✕×☰⋮]/g, '').trim();
    if (!text) b.setAttribute('aria-label', b.getAttribute('title'));
  });
}

// ── Menus ────────────────────────────────────────────────────────────

const menuState = new WeakMap(); // menu → { trigger, returnTo, observer, parentMenu }
let typeBuf = '';
let typeTimer = 0;

function menuItems(menu) {
  return [...menu.querySelectorAll('[role^="menuitem"]')].filter(isShown);
}

function itemLabel(el) {
  const clone = el.cloneNode(true);
  clone.querySelectorAll('.dropdown-shortcut, .sort-check, .rearrange-check, svg').forEach((n) => n.remove());
  return (clone.textContent || '').replace(/[↑↓●•]/g, '').trim();
}

function isMenuOpen(menu) {
  return !!menu && menu.style.display !== 'none' && !menu.classList.contains('hidden') && isShown(menu);
}

function prepMenu(menu, labelText) {
  if (!menu) return;
  setAttr(menu, 'role', 'menu');
  if (labelText && !menu.hasAttribute('aria-label')) menu.setAttribute('aria-label', labelText);
  hideIcons(menu);

  // Tidy row: a split button (action + "more" chevron) inside one row.
  const tidyRow = menu.querySelector('#auto-sort-sessions-row');
  if (tidyRow) {
    setAttr(tidyRow, 'role', 'none');
    const act = tidyRow.querySelector('#auto-sort-sessions-btn');
    const more = tidyRow.querySelector('#auto-sort-sessions-more');
    if (act) { setAttr(act, 'role', 'menuitem'); act.tabIndex = -1; }
    if (more) { setAttr(more, 'role', 'menuitem'); more.tabIndex = -1; setAttr(more, 'aria-haspopup', 'true'); }
  }

  menu.querySelectorAll(MENU_ITEM_SEL).forEach((it) => {
    if (it === tidyRow || it.getAttribute('role') === 'none') return;
    let role = 'menuitem';
    if (it.classList.contains('sort-option')) role = 'menuitemradio';
    else if (it.classList.contains('rearrange-toggle')) role = 'menuitemcheckbox';
    setAttr(it, 'role', role);
    it.tabIndex = -1;
    if (role === 'menuitemradio') {
      const chk = it.querySelector('.sort-check');
      setAttr(it, 'aria-checked', !!chk && chk.style.opacity === '1');
      hideDecorative(it, '.sort-check');
    } else if (role === 'menuitemcheckbox') {
      const chk = it.querySelector('.rearrange-check');
      setAttr(it, 'aria-checked', !!chk && chk.style.opacity === '1');
      hideDecorative(it, '.rearrange-check');
    }
    if (it._submenu) {
      setAttr(it, 'aria-haspopup', 'menu');
      setAttr(it, 'aria-expanded', isMenuOpen(it._submenu));
    }
    const sc = it.querySelector('.dropdown-shortcut');
    if (sc && !it.hasAttribute('aria-keyshortcuts')) {
      it.setAttribute('aria-keyshortcuts', sc.textContent.trim().replace(/\s+/g, ''));
      sc.setAttribute('aria-hidden', 'true');
    }
    if (it.classList.contains('dropdown-item-danger')) it.dataset.danger = '1';
  });
  // Inline separators are presentational.
  [...menu.children].forEach((c) => {
    if (!c.matches(MENU_ITEM_SEL) && !c.querySelector(MENU_ITEM_SEL) && !c.textContent.trim()) {
      setAttr(c, 'role', 'separator');
    }
  });
}

function focusMenuItem(menu, idx) {
  const items = menuItems(menu);
  if (!items.length) return;
  const i = Math.max(0, Math.min(items.length - 1, idx));
  items.forEach((it, j) => { it.tabIndex = j === i ? 0 : -1; });
  items[i].focus({ preventScroll: false });
}

function closeMenu(menu, { restore = true } = {}) {
  if (!menu) return;
  const st = menuState.get(menu);
  menu.style.display = 'none';
  if (st && st.trigger) setAttr(st.trigger, 'aria-expanded', 'false');
  if (restore && st && st.returnTo && st.returnTo.isConnected) st.returnTo.focus();
}

function watchMenu(menu) {
  const st = menuState.get(menu);
  if (!st || st.observer || !('MutationObserver' in window)) return;
  st.observer = new MutationObserver(() => {
    if (isMenuOpen(menu)) return;
    if (st.trigger) setAttr(st.trigger, 'aria-expanded', 'false');
    if (st.parentItem) setAttr(st.parentItem, 'aria-expanded', 'false');
    // An item action closed the menu and focus fell to <body> (or is stuck
    // inside the now-hidden menu): put it back where the user came from.
    const ae = document.activeElement;
    if (!ae || ae === document.body || menu.contains(ae)) {
      const back = (st.returnTo && st.returnTo.isConnected) ? st.returnTo : null;
      if (back) back.focus();
      else restoreTreeFocus();
    }
    st.observer.disconnect();
    st.observer = null;
  });
  st.observer.observe(menu, { attributes: true, attributeFilter: ['style', 'class'] });
}

function openMenu(menu, { trigger, returnTo, focus = true, label, parentMenu, parentItem } = {}) {
  if (!menu) return;
  prepMenu(menu, label);
  menuState.set(menu, Object.assign(menuState.get(menu) || {}, { trigger, returnTo, parentMenu, parentItem }));
  if (trigger) setAttr(trigger, 'aria-expanded', 'true');
  if (parentItem) setAttr(parentItem, 'aria-expanded', 'true');
  if (!menu._a11yKeys) {
    menu._a11yKeys = true;
    menu.addEventListener('keydown', onMenuKeydown);
  }
  watchMenu(menu);
  if (focus) focusMenuItem(menu, 0);
}

function onMenuKeydown(e) {
  const menu = e.currentTarget;
  const items = menuItems(menu);
  const cur = items.indexOf(document.activeElement);
  const st = menuState.get(menu) || {};
  const k = e.key;

  if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Home' || k === 'End') {
    e.preventDefault(); e.stopPropagation();
    focusMenuItem(menu, moveIndex(items.length, cur, k, true));
    return;
  }
  if (k === 'Escape' || (k === 'ArrowLeft' && st.parentMenu)) {
    e.preventDefault(); e.stopPropagation();
    closeMenu(menu);
    return;
  }
  if (k === 'Tab') {
    // Leave the menu like a native one: close it, let focus move on.
    closeMenu(menu, { restore: false });
    return;
  }
  const item = items[cur];
  if (!item) return;
  if ((k === 'ArrowRight' || k === 'Enter' || k === ' ') && item._submenu) {
    e.preventDefault(); e.stopPropagation();
    if (!isMenuOpen(item._submenu)) item.click();
    requestAnimationFrame(() => {
      if (isMenuOpen(item._submenu)) {
        openMenu(item._submenu, { returnTo: item, parentMenu: menu, parentItem: item, label: 'Move to folder' });
      }
    });
    return;
  }
  if (k === 'Enter' || k === ' ') {
    e.preventDefault(); e.stopPropagation();
    item.click();
    // Radio/checkbox items may stay open — refresh their checked state.
    requestAnimationFrame(() => { if (isMenuOpen(menu)) prepMenu(menu); });
    return;
  }
  if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
    clearTimeout(typeTimer);
    typeBuf += k;
    typeTimer = setTimeout(() => { typeBuf = ''; }, 600);
    const j = typeaheadIndex(items.map(itemLabel), cur, typeBuf);
    if (j >= 0) { e.preventDefault(); focusMenuItem(menu, j); }
  }
}

// Triggers whose own click handlers call stopPropagation — listen in capture
// phase, then look at the menu one frame later once it has been shown.
const STATIC_MENUS = [
  { trigger: '#session-sort-btn', menu: '#session-sort-dropdown', label: 'Sort chats' },
  { trigger: '#model-sort-btn', menu: '#model-sort-dropdown', label: 'Sort models' },
];

function resolveMenuFor(trigger) {
  if (trigger.classList.contains('session-menu-btn')) {
    const row = trigger.closest('.session-item');
    return row ? { menu: row._sessionDropdown, returnTo: row, label: 'Chat actions' } : null;
  }
  for (const m of STATIC_MENUS) {
    if (trigger.matches(m.trigger)) return { menu: document.querySelector(m.menu), returnTo: trigger, label: m.label };
  }
  return null;
}

function onTriggerClickCapture(e) {
  const trigger = e.target.closest && e.target.closest('.session-menu-btn, #session-sort-btn, #model-sort-btn');
  if (!trigger) return;
  const viaKeyboard = e.detail === 0;
  requestAnimationFrame(() => {
    const r = resolveMenuFor(trigger);
    if (!r || !r.menu) return;
    if (isMenuOpen(r.menu)) {
      openMenu(r.menu, { trigger, returnTo: r.returnTo, focus: viaKeyboard, label: r.label });
    } else {
      setAttr(trigger, 'aria-expanded', 'false');
    }
  });
}

function wireStaticMenuTriggers() {
  STATIC_MENUS.forEach(({ trigger, menu, label }) => {
    const t = document.querySelector(trigger);
    const m = document.querySelector(menu);
    if (!t || !m) return;
    if (!m.id) return;
    setAttr(t, 'aria-haspopup', 'menu');
    setAttr(t, 'aria-controls', m.id);
    setAttr(t, 'aria-expanded', 'false');
    if (!t.hasAttribute('aria-label')) t.setAttribute('aria-label', label);
    prepMenu(m, label);
    // ArrowDown / ArrowUp on the closed trigger opens the menu.
    t.addEventListener('keydown', (e) => {
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !isMenuOpen(m)) {
        e.preventDefault();
        t.click();
      }
    });
  });
}

// ── Chat tree ────────────────────────────────────────────────────────

const tree = {
  list: null,
  focusKey: null,     // key of the item that owns the roving tab stop
  focusedEl: null,    // the element that had DOM focus inside the tree
  raf: 0,
};

function keyOf(el) {
  if (!el) return null;
  if (el.dataset && el.dataset.sessionId) return 's:' + el.dataset.sessionId;
  if (el.classList.contains('session-folder-header')) {
    const f = el.parentElement;
    return 'f:' + (el.dataset.folderName || (f && (f.dataset.folderName || f.dataset.taxPath)) ||
      (el.querySelector('.folder-name') || {}).textContent || '');
  }
  if (el.classList.contains('session-show-more-btn')) return 'more';
  return null;
}

function findByKey(list, key) {
  if (!key) return null;
  return [...list.querySelectorAll(TREE_ITEM_SEL)].find((el) => keyOf(el) === key) || null;
}

function folderDepth(el, list) {
  let d = 0;
  for (let p = el.parentElement; p && p !== list; p = p.parentElement) {
    if (p.classList.contains('session-folder')) d++;
  }
  return d;
}

function folderExpanded(header) {
  const folder = header.parentElement;
  if (!folder) return false;
  const content = folder.querySelector(':scope > .session-folder-content');
  return !!content && content.style.display !== 'none';
}

function treeItems(list) {
  return [...list.querySelectorAll(TREE_ITEM_SEL)].filter(isShown);
}

function levelOf(el) { return parseInt(el.getAttribute('aria-level') || '1', 10); }

function syncTree() {
  tree.raf = 0;
  const list = tree.list;
  if (!list || !list.isConnected) return;

  setAttr(list, 'role', 'tree');
  if (!list.hasAttribute('aria-label') && !list.hasAttribute('aria-labelledby')) {
    setAttr(list, 'aria-labelledby', 'chats-section-label');
  }
  if (document.getElementById('sb-tree-help')) setAttr(list, 'aria-describedby', 'sb-tree-help');

  list.querySelectorAll('.session-folder, .session-folder-content').forEach((n) => setAttr(n, 'role', 'none'));

  const all = [...list.querySelectorAll(TREE_ITEM_SEL)];
  let selectedEl = null;
  all.forEach((el) => {
    setAttr(el, 'role', 'treeitem');
    if (el.getAttribute('tabindex') !== '-1') el.tabIndex = -1;

    if (el.classList.contains('session-folder-header')) {
      const depth = Math.max(0, folderDepth(el, list) - 1);
      setAttr(el, 'aria-level', depth + 1);
      setAttr(el, 'aria-expanded', folderExpanded(el));
      const nm = el.querySelector('.folder-name');
      const ct = el.querySelector('.folder-count');
      const { name, count } = parseFolderLabel(nm && nm.textContent, ct && ct.textContent);
      setAttr(el, 'aria-label', folderAccessibleName(name, count));
      hideDecorative(el, '.folder-toggle, .folder-drag-handle, .folder-count');
      const del = el.querySelector('.folder-delete-btn');
      if (del) { del.tabIndex = -1; setAttr(del, 'aria-label', `Delete folder ${name}`); }
      return;
    }

    if (el.classList.contains('session-show-more-btn')) {
      setAttr(el, 'aria-level', 1);
      return;
    }

    // Chat row
    setAttr(el, 'aria-level', folderDepth(el, list) + 1);
    const selected = el.classList.contains('active-session') || el.classList.contains('active');
    setAttr(el, 'aria-selected', selected);
    if (selected) { setAttr(el, 'aria-current', 'page'); selectedEl = el; }
    else if (el.hasAttribute('aria-current')) el.removeAttribute('aria-current');

    hideDecorative(el, '.item-drag-handle, .run-dot, .session-star, .session-icon:not(.session-fav), .swipe-hint');
    const fav = el.querySelector('.session-fav');
    if (fav) { setAttr(fav, 'role', 'img'); setAttr(fav, 'aria-label', 'Favorite'); }
    hideIcons(el);

    const btn = el.querySelector('.session-menu-btn');
    if (btn) {
      btn.tabIndex = -1;
      setAttr(btn, 'aria-haspopup', 'menu');
      if (!btn.hasAttribute('aria-expanded')) btn.setAttribute('aria-expanded', 'false');
      const title = ((el.querySelector('.grow') || {}).textContent || '').split(' · ')[0].trim();
      setAttr(btn, 'aria-label', title ? `Actions for ${title}` : 'Chat actions');
    }
    const cb = el.querySelector('.session-select-cb');
    if (cb) {
      setAttr(cb, 'role', 'checkbox');
      cb.tabIndex = -1;
      setAttr(cb, 'aria-checked', !!cb._checked);
      setAttr(cb, 'aria-label', 'Select chat');
    }
  });

  // Roving tab stop: remembered item → selected chat → first visible item.
  const visible = all.filter(isShown);
  let owner = findByKey(list, tree.focusKey);
  if (!owner || !isShown(owner)) owner = (selectedEl && isShown(selectedEl)) ? selectedEl : visible[0];
  if (owner) owner.tabIndex = 0;

  // A re-render destroyed the element the user was on (folder toggle, sort,
  // rename): put focus back on its replacement so keyboard users keep place.
  const ae = document.activeElement;
  if (tree.focusedEl && !tree.focusedEl.isConnected && (!ae || ae === document.body)) {
    const again = findByKey(list, tree.focusKey) || owner;
    if (again) again.focus({ preventScroll: true });
    tree.focusedEl = again || null;
  }
}

function scheduleTreeSync() {
  if (tree.raf) return;
  tree.raf = requestAnimationFrame(syncTree);
}

function restoreTreeFocus() {
  const list = tree.list;
  if (!list) return;
  const el = findByKey(list, tree.focusKey) || list.querySelector('[role="treeitem"][tabindex="0"]');
  if (el) el.focus();
}

function focusTreeItem(el) {
  if (!el) return;
  const list = tree.list;
  list.querySelectorAll('[role="treeitem"][tabindex="0"]').forEach((n) => { n.tabIndex = -1; });
  el.tabIndex = 0;
  tree.focusKey = keyOf(el);
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: 'nearest' });
}

function toggleFolder(header) {
  tree.focusKey = keyOf(header);
  tree.focusedEl = header;
  header.click();
  // Sync now (not next frame) so screen readers announce the new
  // expanded/collapsed state with the keypress. Folder modes that re-render
  // the list synchronously are covered too: syncTree restores focus.
  syncTree();
}

function openRowMenu(row) {
  const btn = row.querySelector('.session-menu-btn');
  if (!btn) return;
  btn.click(); // detail === 0 → capture listener focuses the first item
}

function renameRow(row) {
  const dd = row._sessionDropdown;
  if (!dd) return;
  const item = [...dd.querySelectorAll(MENU_ITEM_SEL)].find((it) => /^\s*rename\b/i.test(it.textContent));
  if (item) item.click();
}

function onTreeKeydown(e) {
  const list = e.currentTarget;
  const t = e.target;
  if (!t || (t.matches && t.matches(EDITABLE_SEL))) return;
  const item = t.closest(TREE_ITEM_SEL);
  if (!item || !list.contains(item)) return;
  if (e.ctrlKey || e.metaKey) return; // leave app shortcuts alone

  const vis = treeItems(list);
  const idx = vis.indexOf(item);
  const isFolder = item.classList.contains('session-folder-header');
  const isChat = !!item.dataset.sessionId;
  const k = e.key;

  if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Home' || k === 'End') {
    e.preventDefault();
    focusTreeItem(vis[moveIndex(vis.length, idx, k)]);
    return;
  }
  if (k === 'ArrowRight') {
    if (!isFolder) return;
    e.preventDefault();
    if (item.getAttribute('aria-expanded') === 'false') toggleFolder(item);
    else {
      const nxt = vis[idx + 1];
      if (nxt && levelOf(nxt) > levelOf(item)) focusTreeItem(nxt);
    }
    return;
  }
  if (k === 'ArrowLeft') {
    e.preventDefault();
    if (isFolder && item.getAttribute('aria-expanded') === 'true') { toggleFolder(item); return; }
    const lvl = levelOf(item);
    for (let i = idx - 1; i >= 0; i--) {
      if (vis[i].classList.contains('session-folder-header') && levelOf(vis[i]) < lvl) { focusTreeItem(vis[i]); return; }
    }
    return;
  }
  if ((k === 'Enter' || k === ' ') && isFolder) {
    e.preventDefault(); e.stopPropagation();
    toggleFolder(item);
    return;
  }
  if (isChat && (k === 'ContextMenu' || (k === 'F10' && e.shiftKey))) {
    e.preventDefault();
    openRowMenu(item);
    return;
  }
  if (isChat && k === 'F2') {
    e.preventDefault();
    renameRow(item);
    return;
  }
  if (k.length === 1 && k !== ' ' && !e.altKey) {
    clearTimeout(typeTimer);
    typeBuf += k;
    typeTimer = setTimeout(() => { typeBuf = ''; }, 600);
    const labels = vis.map((el) => el.getAttribute('aria-label') ||
      ((el.querySelector('.grow') || el).textContent || ''));
    const j = typeaheadIndex(labels, idx, typeBuf);
    if (j >= 0) { e.preventDefault(); focusTreeItem(vis[j]); }
  }
}

function initTree() {
  const list = document.getElementById('session-list');
  if (!list) return;
  tree.list = list;
  list.addEventListener('keydown', onTreeKeydown);
  list.addEventListener('focusin', (e) => {
    const it = e.target.closest && e.target.closest(TREE_ITEM_SEL);
    if (it) {
      tree.focusedEl = it;
      tree.focusKey = keyOf(it);
      list.querySelectorAll('[role="treeitem"][tabindex="0"]').forEach((n) => { if (n !== it) n.tabIndex = -1; });
      it.tabIndex = 0;
    }
  });
  // Leaving the tree on purpose (click or focus elsewhere) — don't pull focus back.
  document.addEventListener('pointerdown', (e) => { if (!list.contains(e.target)) tree.focusedEl = null; }, true);
  document.addEventListener('focusin', (e) => {
    if (!list.contains(e.target) && !(e.target.closest && e.target.closest('.dropdown'))) tree.focusedEl = null;
  });
  // A click on a folder header via mouse can toggle in place.
  list.addEventListener('click', scheduleTreeSync);

  if ('MutationObserver' in window) {
    new MutationObserver(scheduleTreeSync).observe(list, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'],
    });
  }
  syncTree();
}

// ── Icon rail → toolbar ──────────────────────────────────────────────

function initRail() {
  const rail = document.getElementById('icon-rail');
  if (!rail) return;
  setAttr(rail, 'role', 'toolbar');
  setAttr(rail, 'aria-orientation', 'vertical');
  if (!rail.hasAttribute('aria-label')) rail.setAttribute('aria-label', 'Quick tools');
  hideIcons(rail);
  nameIconButtons(rail);
  const del = document.getElementById('rail-delete-session');
  if (del) setAttr(del, 'aria-label', 'Delete current chat');

  const btns = () => [...rail.querySelectorAll('.icon-rail-btn')].filter(isShown);
  const syncStop = () => {
    const list = btns();
    if (!list.length) return;
    const owner = list.find((b) => b.tabIndex === 0) || list[0];
    rail.querySelectorAll('.icon-rail-btn').forEach((b) => { b.tabIndex = b === owner ? 0 : -1; });
  };
  syncStop();
  rail.addEventListener('focusin', (e) => {
    const b = e.target.closest('.icon-rail-btn');
    if (!b) return;
    rail.querySelectorAll('.icon-rail-btn').forEach((x) => { x.tabIndex = x === b ? 0 : -1; });
  });
  rail.addEventListener('keydown', (e) => {
    const k = e.key;
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(k)) return;
    const list = btns();
    const i = list.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const n = list[moveIndex(list.length, i, k, true)];
    if (n) n.focus();
  });
  if ('MutationObserver' in window) {
    // Dynamic indicators appear/disappear; keep exactly one tab stop.
    new MutationObserver(() => requestAnimationFrame(syncStop))
      .observe(rail, { subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
  }
}

// ── Splitter (sidebar edge) ──────────────────────────────────────────

async function initSplitter() {
  const sidebar = document.getElementById('sidebar');
  const handle = document.getElementById('sidebar-resize-handle');
  if (!sidebar || !handle) return;
  const MIN = 200, MAX = 700;
  let Storage = null;
  try { Storage = await import('./storage.js'); } catch (_) {}

  setAttr(handle, 'role', 'separator');
  setAttr(handle, 'aria-orientation', 'vertical');
  setAttr(handle, 'aria-label', 'Resize sidebar');
  setAttr(handle, 'aria-controls', 'sidebar');
  setAttr(handle, 'aria-valuemin', MIN);
  setAttr(handle, 'aria-valuemax', MAX);
  handle.tabIndex = 0;
  const syncValue = () => setAttr(handle, 'aria-valuenow', Math.round(sidebar.getBoundingClientRect().width) || MIN);
  syncValue();
  handle.addEventListener('focus', syncValue);
  handle.addEventListener('mouseup', syncValue);
  handle.addEventListener('keydown', (e) => {
    const cur = Math.round(sidebar.getBoundingClientRect().width);
    const next = resizeTarget(cur, e.key, {
      min: MIN, max: MAX, shift: e.shiftKey, mirrored: sidebar.classList.contains('right-side'),
    });
    if (next == null) return;
    e.preventDefault();
    sidebar.style.width = next + 'px';
    setAttr(handle, 'aria-valuenow', next);
    try { if (Storage) Storage.set(Storage.KEYS.SIDEBAR_WIDTH, String(next)); } catch (_) {}
    window.dispatchEvent(new Event('resize'));
  });
}

// ── Disclosure state for the sidebar toggles ─────────────────────────

function initToggles() {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  const toggles = ['sidebar-toggle-btn', 'hamburger-btn', 'mobile-menu-btn']
    .map((id) => document.getElementById(id)).filter(Boolean);
  toggles.forEach((b) => {
    setAttr(b, 'aria-controls', 'sidebar');
    if (!b.hasAttribute('aria-label')) b.setAttribute('aria-label', 'Toggle sidebar');
  });
  const sync = () => {
    const open = !sidebar.classList.contains('hidden') && !document.body.classList.contains('sidebar-collapsed');
    toggles.forEach((b) => setAttr(b, 'aria-expanded', open));
  };
  sync();
  if ('MutationObserver' in window) {
    const mo = new MutationObserver(sync);
    mo.observe(sidebar, { attributes: true, attributeFilter: ['class'] });
    mo.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }
}

// ── Select-all control in bulk mode ──────────────────────────────────

function initBulkBar() {
  const dot = document.getElementById('session-select-all-dot');
  const box = document.getElementById('session-select-all');
  if (!dot || !box) return;
  setAttr(dot, 'role', 'checkbox');
  setAttr(dot, 'tabindex', '0');
  setAttr(dot, 'aria-label', 'Select all chats');
  const sync = () => setAttr(dot, 'aria-checked', !!box.checked);
  sync();
  dot.addEventListener('click', () => requestAnimationFrame(sync));
  dot.addEventListener('keydown', (e) => {
    if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); dot.click(); }
  });
  const lbl = document.getElementById('session-select-all-label');
  if (lbl) setAttr(lbl, 'aria-hidden', 'true');
  const bar = document.getElementById('session-bulk-bar');
  if (bar) { setAttr(bar, 'role', 'toolbar'); setAttr(bar, 'aria-label', 'Bulk actions'); }
}

// ── Notification dots: give them words ──────────────────────────────

const DOT_LABELS = {
  'chats-notif-dot': 'New activity',
  'email-unread-dot': 'Unread email',
  'assistant-notif-dot': 'New task activity',
  'cookbook-notif-dot': 'Cookbook update',
};

function initDots() {
  Object.entries(DOT_LABELS).forEach(([id, label]) => {
    const d = document.getElementById(id);
    if (!d) return;
    setAttr(d, 'role', 'img');
    setAttr(d, 'aria-label', label);
  });
}

function init() {
  if (!HAS_DOM) return;
  const sidebar = document.getElementById('sidebar');
  if (sidebar) { hideIcons(sidebar); nameIconButtons(sidebar); }
  initToggles();
  initDots();
  initBulkBar();
  wireStaticMenuTriggers();
  initTree();
  initRail();
  initSplitter();
  document.addEventListener('click', onTriggerClickCapture, true);
  document.documentElement.classList.add('sb-a11y-ready');
}

if (HAS_DOM) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}
