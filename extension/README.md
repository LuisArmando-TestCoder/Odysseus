# Odysseus Browser Bridge

A Manifest V3 browser extension that lets the Odysseus agent (or you, directly)
harness **Google's AI Mode** and **YouTube's "Ask"** — and, by extension, any
JS-rendered page that a server-side `httpx` provider cannot reach — through your
own logged-in browser session. No UA spoofing, no bot detection: it drives your
real session.

## What it does

- Targets the **currently active tab** (whatever site it is), not a hardcoded
  YouTube query.
- Auto-detects Google vs YouTube by hostname and picks the right driver.
- Locates the AI-mode button by its **visible text label** (not a hardcoded
  selector), so it survives UI changes and localization.
- Types a prompt, submits it, waits for the answer to stream, and reads it back.
- Reports the answer back to Odysseus via the existing
  `POST /api/browser/eval-result` single-use-token bridge.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 manifest — content script on Google + YouTube, background worker, popup, options, context menu, keyboard shortcut |
| `background.js` | Service worker — targets the active tab, relays eval payloads, posts results back, handles popup/context-menu/shortcut |
| `content.js` | Runs in the active tab — the text-node walker + `askGoogleAI` / `askYouTubeAI` drivers with host auto-detect |
| `popup.html` / `popup.js` | Toolbar popup with a question box and a built-in enable tutorial |
| `options.html` / `options.js` | Set the Odysseus base URL |

## How to enable (step-by-step)

1. **Load the extension unpacked.**
   - **Chrome:** open `chrome://extensions` → toggle **Developer mode**
     (top-right) → click **Load unpacked** → select this `extension/` folder.
   - **Firefox:** open `about:debugging#/runtime/this-firefox` →
     **Load Temporary Add-on…** → select `manifest.json`.

2. **Confirm it's enabled.** The Odysseus Bridge icon appears in your toolbar.

3. **Open Google or YouTube and log in.**
   - **Google AI Mode:** go to `google.com` and sign in — the "AI Mode" button
     only renders for logged-in users.
   - **YouTube "Ask":** go to `youtube.com` and sign in.

4. **(Optional) Set your Odysseus URL.** Right-click the extension → **Options**
   → set the base URL if it isn't `http://127.0.0.1:5173`.

5. **Use it three ways:**
   - Click the extension icon → type a question → **Ask AI**.
   - Highlight text → right-click → **Ask AI (Odysseus)**.
   - Press `Cmd+Shift+Y` (Mac) / `Ctrl+Shift+Y` (Windows/Linux).

## How the AI-mode button is found (the key idea)

Instead of hardcoding `button[aria-label="Ask"]`, the content script walks every
text node in the page, matches its content against a set of labels
(`ai mode`, `ask ai`, `ask anything`, `gemini`, `ai overviews`, `preguntar`, …),
and climbs up to the nearest clickable ancestor (`button`, `role=button`, `a`,
`tabindex`, or a container holding a button). This is robust to UI changes and
localization.

## Using it from Odysseus chat (as a tool)

The agent's `run_javascript` tool can drive Google AI mode / YouTube "Ask"
directly from chat. Prefix the code with the marker comment `/*@external*/`
and call the bridge:

```js
/*@external*/
return await window.__odyBridge.askAI("your question here");
```

When `/*@external*/` is present AND the extension is installed (its ID stored
in `localStorage.ody_ext_id` on the Odysseus page, or set as
`window.ODYSSEUS_EXT_ID`), `static/js/browserEval.js` forwards the payload to
the extension instead of running it in-page. The extension runs it in the
active Google/YouTube tab and POSTs the answer back to
`/api/browser/eval-result`, resolving the agent's tool call.

To set the extension ID (shown at `chrome://extensions`), run this once in the
Odysseus page's DevTools console:

```js
localStorage.setItem('ody_ext_id', '<your-32-char-extension-id>');
```

## Architecture note

The existing `run_javascript` bridge (`src/browser_eval.py` →
`static/js/browserEval.js` → `routes/browser_eval_routes.py`) only reaches the
Odysseus page itself. This extension closes the gap:

```
Odysseus page ──chrome.runtime.onMessageExternal──▶ background worker
                                                      │ (relays to active tab)
                                                      ▼
                              content.js runs the code ON the active tab
                                                      │
              POST /api/browser/eval-result {token, output} ──▶ Odysseus
```

## Fixing "Could not establish connection. Receiving end does not exist."

This error means the content script wasn't injected into the tab you were
targeting. The fix in this version:

- The content script now matches **Google** domains (not just YouTube).
- The background worker targets the **active tab** (`{ active: true,
  currentWindow: true }`) instead of querying for a YouTube tab.

If you still see it, reload the target tab after loading the extension (content
scripts only inject on pages loaded *after* the extension is installed), and
make sure the tab's URL matches one of the `content_scripts.matches` globs in
`manifest.json`.

## Caveats

- You must be logged in to Google/YouTube in the browser where the extension
  runs.
- Google's and YouTube's exact DOM changes over time; the text-node walker is
  designed to tolerate this, but if the answer isn't captured, the extension
  reports a clear error rather than failing silently.