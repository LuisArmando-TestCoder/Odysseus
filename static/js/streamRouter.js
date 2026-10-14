// streamRouter.js — centralized, visibility-aware streaming router.
//
// Every streaming mechanism in the app (chat responses, advanced voice mode's
// neural pipeline, contextual-awareness streams) routes through here so they
// all share ONE source of truth for "is the tab visible / in view".
//
// Two routes:
//   - "frame-aware"  — used when the tab is visible and in view. Rendering is
//                      paced to the frame rate (requestAnimationFrame) so the
//                      UI stays smooth. This is the fast path, but it is
//                      visibility-dependent: when the tab is hidden the browser
//                      throttles rAF and the fetch reader, so this route alone
//                      would stall.
//   - "event-loop"   — used when the window is NOT in view. There is no frame
//                      rate to protect, so we keep the stream alive on the
//                      event loop (accumulate without DOM writes, keep the
//                      reader draining) and re-subscribe to the detached
//                      server run when the tab returns.
//
// The router never aborts a stream on visibility change. A hidden tab must not
// kill a server-side run — it should only switch which *client* strategy is
// draining/rendering it.

const _listeners = new Set();

let _visible = (() => {
  try {
    return document.visibilityState === 'visible' && !document.hidden;
  } catch (_) {
    return true;
  }
})();

function _refresh() {
  let next;
  try {
    next = document.visibilityState === 'visible' && !document.hidden;
  } catch (_) {
    next = true;
  }
  if (next === _visible) return;
  _visible = next;
  for (const cb of _listeners) {
    try { cb(_visible); } catch (_) {}
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', _refresh);
  window.addEventListener('pageshow', _refresh);
  window.addEventListener('pagehide', _refresh);
  window.addEventListener('focus', _refresh);
  window.addEventListener('blur', _refresh);
}

/** True when the tab is visible AND in view (the frame-aware route is safe). */
export function isTabVisible() {
  return _visible;
}

/** The active route: 'frame-aware' when visible, 'event-loop' otherwise. */
export function route() {
  return _visible ? 'frame-aware' : 'event-loop';
}

/** Subscribe to visibility changes. Returns an unsubscribe function. */
export function onVisibilityChange(cb) {
  _listeners.add(cb);
  return () => _listeners.delete(cb);
}

/**
 * Run `frameFn` on the next animation frame only when the tab is visible;
 * otherwise run `loopFn` immediately (event-loop path). Returns a handle with
 * a cancel() method so callers can stop a scheduled tick.
 */
export function scheduleTick(frameFn, loopFn) {
  let rafId = 0;
  let cancelled = false;
  const run = () => {
    if (cancelled) return;
    if (_visible) {
      rafId = requestAnimationFrame(() => {
        if (cancelled) return;
        frameFn();
      });
    } else {
      loopFn();
    }
  };
  run();
  return { cancel: () => { cancelled = true; if (rafId) cancelAnimationFrame(rafId); } };
}

export default { isTabVisible, route, onVisibilityChange, scheduleTick };