# Odysseus Browser Bridge

A Manifest V3 browser extension that lets the Odysseus agent (or you, directly)
harness YouTube's "Ask" / AI mode — and, by extension, any JS-rendered page that
a server-side `httpx` provider cannot reach — through your own logged-in browser
session. No UA spoofing, no bot detection: it drives your real session.

## What it does

- Locates the YouTube AI-mode button by its **visible text label** (not a
  hardcoded selector), so it survives UI changes and localization.
- Types a prompt, submits it, waits for the answer to stream, and reads it back.
- Reports the answer back to Odysseus via the existing
  `POST /api/browser/eval-result` single-use-token bridge.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 manifest — content script on `youtube.com`, background worker, popup, options, context menu, keyboard shortcut |
| `background.js` | Service worker — relays eval payloads, posts results back, handles popup/context-menu/shortcut |
| `content.js` | Runs in YouTube tabs — the text-node walker + `askYouTubeAI` driver |
| `popup.html` / `popup.js` | Toolbar popup with a question box and a built-in enable tutorial |
| `options.html` / `options.js` | Set the Odysseus base URL |

## How to enable (step-by-step)

1. **Load the extension unpacked.**
   - **Chrome:** open `chrome://extensions` → toggle **Developer mode**
     (top-right) → click **Load unpacked** → select this `extension/` folder.
   - **Firefox:** open `about:debugging#/runtime/this-firefox` →
     **Load Temporary Add-on…** → select `manifest.json`.

2. **Confirm it's enabled.** The Odysseus Bridge icon appears in your toolbar.

3. **Open YouTube and log in.** Go to `youtube.com` and sign in — the AI-mode
   ("Ask") button only renders for logged-in users.

4. **(Optional) Set your Odysseus URL.** Right-click the extension → **Options**
   → set the base URL if it isn't `http://127.0.0.1:5173`.

5. **Use it three ways:**
   - Click the extension icon → type a question → **Ask YouTube AI**.
   - Highlight text on a video → right-click → **Ask YouTube AI (Odysseus)**.
   - Press `Cmd+Shift+Y` (Mac) / `Ctrl+Shift+Y` (Windows/Linux).

## How the AI-mode button is found (the key idea)

Instead of hardcoding `button[aria-label="Ask"]`, the content script walks every
text node in the page, matches its content against a set of labels
(`ask ai`, `ai mode`, `ask`, `search with ai`, `gemini`, `preguntar`, …), and
climbs up to the nearest clickable ancestor (`button`, `role=button`, `a`,
`tabindex`, or a container holding a button). This is robust to YouTube UI
changes and localization.

## Architecture note

The existing `run_javascript` bridge (`src/browser_eval.py` →
`static/js/browserEval.js` → `routes/browser_eval_routes.py`) only reaches the
Odysseus page itself. This extension closes the gap:

```
Odysseus page ──chrome.runtime.onMessageExternal──▶ background worker
                                                      │ (relays to YouTube tab)
                                                      ▼
                              content.js runs the code ON youtube.com
                                                      │
              POST /api/browser/eval-result {token, output} ──▶ Odysseus
```

## Caveats

- You must be logged in to YouTube in the browser where the extension runs.
- YouTube's exact DOM changes over time; the text-node walker is designed to
  tolerate this, but if the answer isn't captured, the extension reports a
  clear error rather than failing silently.