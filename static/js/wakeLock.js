// static/js/wakeLock.js
//
// Shared, reference-counted Screen Wake Lock manager.
//
// Keeps the display (and therefore the system) awake while any registered
// holder is active — e.g. a chat stream in flight or an active voice-mode
// session. The lock is only released when the LAST holder releases it.
//
// The Screen Wake Lock API is best-effort and has two real-world caveats we
// handle here:
//   1. The browser auto-releases the lock when the tab is hidden. We re-request
//      it on `visibilitychange` while holders remain.
//   2. The OS can force-release the lock (e.g. low battery). The `release`
//      event nulls our internal handle so the next sync re-requests it.

const _holders = new Set();
let _lock = null;          // the live WakeLockSentinel, or null
let _requesting = false;   // guards against overlapping async requests

const _supported = typeof navigator !== 'undefined' && 'wakeLock' in navigator;

async function _request() {
  if (!_supported || _lock || _requesting) return;
  _requesting = true;
  try {
    const sentinel = await navigator.wakeLock.request('screen');
    // A newer release may have happened while we awaited — drop the stale lock.
    if (_holders.size === 0) {
      try { await sentinel.release(); } catch (_) {}
      return;
    }
    _lock = sentinel;
    sentinel.addEventListener('release', () => {
      // OS/browser released it out from under us. Clear the handle so the next
      // acquire()/sync() re-requests it while holders remain.
      if (_lock === sentinel) _lock = null;
    });
  } catch (_) {
    // Best-effort: permission denied, unsupported, or transient failure.
  } finally {
    _requesting = false;
  }
}

async function _release() {
  if (!_lock) return;
  const sentinel = _lock;
  _lock = null;
  try { await sentinel.release(); } catch (_) {}
}

/** Register a holder. Acquires the lock if this is the first holder. */
export function acquire(key) {
  if (!_supported || !key) return;
  _holders.add(key);
  _request();
}

/** Unregister a holder. Releases the lock when the last holder is gone. */
export function release(key) {
  if (!key) return;
  _holders.delete(key);
  if (_holders.size === 0) _release();
}

/** Re-request the lock after the tab becomes visible again. */
function _onVisibility() {
  if (document.visibilityState === 'visible' && _holders.size > 0) _request();
}

if (_supported && typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', _onVisibility);
}

export default { acquire, release };