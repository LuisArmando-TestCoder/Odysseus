# Procedure: Harnessing Google AI Mode & YouTube "Ask" via the Odysseus Browser Bridge

This document is the complete, step-by-step implementation record for connecting
Odysseus to the **public, free Google AI Mode** (and YouTube's "Ask") through a
browser extension that drives the user's own logged-in session.

It answers, with absolute paths, the two questions that drove this work:

1. **How do we connect a plugin to search directly from public Google search?**
2. **How do we enable the internal browser extension, and what does it do?**

---

## 1. The core architectural insight

Google AI Mode and YouTube "Ask" are **JS-rendered single-page apps with no
public API**. A server-side `httpx` call — even with a full desktop-Chrome
`User-Agent` — gets blocked or captcha'd, and would violate Google's ToS.

The legitimate path is to **drive the user's own already-open, logged-in
browser tab** and read the rendered answer back. This is exactly what the
extension does. No UA spoofing, no bot detection: it is the user's real session.

The existing `run_javascript` bridge only reaches the **Odysseus page itself**.
The extension closes the gap so the agent can reach **Google / YouTube tabs**.

---

## 2. The end-to-end flow (what actually happens)

```
Agent calls run_javascript with:
  /*@external*/
  return await window.__odyBridge.askAI("your question");

        │  (1) server registers a single-use token
        ▼
src/browser_eval.py  do_run_javascript()
  → register_eval() → token
  → progress_cb({eval_request, eval_token, eval_code})   [SSE tool_progress]

        │  (2) the Odysseus page receives the SSE payload
        ▼
static/js/browserEval.js  handleEvalRequest()
  → sees /*@external*/ marker + extension ID
  → chrome.runtime.sendMessage(extId, {type:'ODY_EVAL', eval_token, eval_code})

        │  (3) the extension background worker relays to the active tab
        ▼
extension/background.js  onMessageExternal → relayToTab()
  → chrome.tabs.sendMessage(tabId, {type:'ODY_EVAL', eval_token, eval_code})

        │  (4) the content script runs the code IN the Google/YouTube page
        ▼
extension/content.js  onMessage → executes code (async IIFE)
  → window.__odyBridge.askAI(prompt)
      → findClickableByText()  locates the AI-mode button by visible label
      → setInputValue()        types the prompt (native setter + input event)
      → submitInput()          form.requestSubmit / send button / Enter
      → readAnswer()           polls until the answer stabilizes

        │  (5) the result is posted back to Odysseus
        ▼
extension/content.js → chrome.runtime.sendMessage({type:'ODY_POST_RESULT', ...})
extension/background.js → POST /api/browser/eval-result {token, output, error}

        │  (6) the server resolves the agent's pending Future
        ▼
routes/browser_eval_routes.py  resolve_eval(token, result)
src/browser_eval.py  do_run_javascript() returns the answer to the agent
```

---

## 3. Files and their absolute paths

All paths are relative to the project root:

```
/Users/luisarmandooriens/Desktop/projects/odysseus/
```

### Extension (new — the deliverable)

| Absolute path | Purpose |
|---|---|
| `extension/manifest.json` | MV3 manifest. Registers the content script on Google + YouTube, the background worker, popup, options, context menu, keyboard shortcut, and **`externally_connectable`** (required so the Odysseus page can message the extension). |
| `extension/background.js` | Service worker. Relays eval payloads to the active tab, posts results back to `/api/browser/eval-result`, handles popup/context-menu/shortcut "ask" actions, auto-opens a Google/YouTube tab when none exists. |
| `extension/content.js` | Runs inside Google/YouTube tabs. The text-node walker (`findClickableByText`) + `askAI` driver with host auto-detect. Executes agent code in an **async IIFE** so `return await ...` works. |
| `extension/popup.html` / `popup.js` | Toolbar popup with a question box and a built-in enable tutorial. |
| `extension/options.html` / `options.js` | Set the Odysseus base URL (default `http://127.0.0.1:5173`). |
| `extension/README.md` | User-facing enable instructions. |
| `extension/PROCEDURE.md` | This document. |

### Existing backend (reused, not rebuilt)

| Absolute path | Role |
|---|---|
| `src/browser_eval.py` | `do_run_javascript()` — registers the single-use token, dispatches via SSE, awaits the result. |
| `routes/browser_eval_routes.py` | `POST /api/browser/eval-result` — resolves the token → Future. |
| `static/js/browserEval.js` | `handleEvalRequest()` — detects `/*@external*/`, forwards to the extension, otherwise runs in-page. |
| `src/tool_schemas.py` | `run_javascript` function schema (documents the `/*@external*/` path). |
| `src/agent_loop.py` | `run_javascript` tool doc string (documents the `/*@external*/` path). |
| `src/tool_index.py` | `run_javascript` is in `ALWAYS_AVAILABLE`, so the agent can always reach it. |

---

## 4. The two critical bugs that were fixed

These were the blockers that made the whole `/*@external*/` path silently fail.
They are now fixed in the files on disk.

### Bug 1 — Missing `externally_connectable` (extension/manifest.json)

`static/js/browserEval.js` forwards to the extension via
`chrome.runtime.sendMessage(extId, ...)`. But a web page can only message an
extension if the extension's manifest declares `externally_connectable` with a
`matches` glob covering the page's origin. Without it, every forward fails with
**"Could not establish connection. Receiving end does not exist."**

**Fix:** added to `extension/manifest.json`:

```json
"externally_connectable": {
  "matches": [
    "http://127.0.0.1/*",
    "http://localhost/*"
  ]
}
```

### Bug 2 — `await` at the top level of a non-async function (extension/content.js)

The agent's external code is `return await window.__odyBridge.askAI(...)`. The
content script executed it with `new Function('"use strict";\n' + code)`, whose
body is **not async** — so the top-level `await` was a `SyntaxError` on every
call.

**Fix:** wrapped the code in an async IIFE in `extension/content.js`:

```js
const fn = new Function('"use strict";\nreturn (async () => {\n' + code + '\n})();');
```

---

## 5. How to enable the extension (step-by-step)

1. **Load the extension unpacked.**
   - **Chrome:** `chrome://extensions` → toggle **Developer mode** (top-right)
     → **Load unpacked** → select the `extension/` folder.
   - **Firefox:** `about:debugging#/runtime/this-firefox` → **Load Temporary
     Add-on…** → select `extension/manifest.json`.

2. **Copy the extension ID.** At `chrome://extensions`, the loaded extension
   shows a 32-character ID.

3. **Register the ID on the Odysseus page.** Open the Odysseus app, open its
   DevTools console, and run:

   ```js
   localStorage.setItem('ody_ext_id', '<your-32-char-extension-id>');
   ```

   (Alternatively set `window.ODYSSEUS_EXT_ID` before the app loads.)

4. **Reload the Odysseus page** so `browserEval.js` picks up the ID.

5. **Open Google or YouTube and log in.**
   - **Google AI Mode:** `google.com` → sign in → the "AI Mode" button renders.
   - **YouTube "Ask":** `youtube.com` → sign in.

6. **(Optional) Set the Odysseus URL.** Right-click the extension → **Options**
   → set the base URL if it isn't `http://127.0.0.1:5173`.

7. **Use it three ways:**
   - Click the extension icon → type a question → **Ask AI**.
   - Highlight text → right-click → **Ask AI (Odysseus)**.
   - Press `Cmd+Shift+Y` (Mac) / `Ctrl+Shift+Y` (Windows/Linux).

---

## 6. Using it from Odysseus chat (the agent path)

In chat, the agent can ask Google AI Mode / YouTube "Ask" directly:

```js
/*@external*/
return await window.__odyBridge.askAI("your question here");
```

The `/*@external*/` marker tells `browserEval.js` to forward to the extension
instead of running in-page. The extension runs it in the active Google/YouTube
tab and posts the answer back, resolving the agent's tool call.

---

## 7. How the AI-mode button is found (the key idea)

Instead of hardcoding `button[aria-label="Ask"]`, the content script walks every
**text node** in the page, matches its content against a set of labels
(`ai mode`, `ask ai`, `ask anything`, `gemini`, `ai overviews`, `preguntar`, …),
and climbs up to the nearest clickable ancestor (`button`, `role=button`, `a`,
`tabindex`, or a container holding a button).

This is robust to UI changes and localization — it keys off the **visible label
text** the user actually sees, in whatever UI version they have.

---

## 8. Caveats

- You must be logged in to Google/YouTube in the browser where the extension
  runs.
- Google's and YouTube's exact DOM changes over time; the text-node walker
  tolerates this, but if the answer isn't captured, the extension reports a
  clear error rather than failing silently.
- The answer streams; the driver polls until the text stabilizes (3 consecutive
  unchanged reads) rather than using a fixed sleep.

---

## 9. What this unlocks (beyond the Yoko case)

This is not specific to any one investigation. Any JS-rendered page that a
server-side `httpx` provider cannot reach — Google AI Mode, YouTube "Ask", or
any SPA — becomes reachable through the user's own logged-in browser session.
The extension is a general-purpose "drive my real browser and read the result
back" bridge, not a single-purpose scraper.