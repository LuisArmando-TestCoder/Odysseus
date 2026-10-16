// background.js — Odysseus Browser Bridge service worker (MV3).
//
// Roles:
//   1. Relay an eval payload {eval_token, eval_code} from the Odysseus page
//      (chrome.runtime.onMessageExternal) to the active YouTube tab.
//   2. Relay results back to Odysseus by POSTing to /api/browser/eval-result.
//   3. Handle popup ("ODY_ASK"), context-menu and keyboard "ask" actions.

async function getBaseUrl() {
  const d = await chrome.storage.sync.get({ baseUrl: "http://127.0.0.1:5173" });
  return String(d.baseUrl || "").replace(/\/+$/, "");
}

async function findYouTubeTab() {
  const tabs = await chrome.tabs.query({ url: "*://*.youtube.com/*" });
  return tabs.length ? tabs[0] : null;
}

// Relay {eval_token, eval_code} to the YouTube tab content script.
async function relayToYouTube(payload) {
  const tab = await findYouTubeTab();
  if (!tab) return { ok: false, error: "No YouTube tab open." };
  try {
    return await chrome.tabs.sendMessage(tab.id, {
      type: "ODY_EVAL",
      eval_token: payload.eval_token,
      eval_code: payload.eval_code,
    });
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
}

// Canonical YouTube "Ask"/AI-mode driver (standalone popup/shortcut path).
function youtubeAskJs(prompt) {
  const p = JSON.stringify(prompt);
  return `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const askBtn = [
    document.querySelector('button[aria-label*="Ask" i]'),
    document.querySelector('[data-testid*="ai" i] button'),
    document.querySelector('ytd-ai-ask-button button'),
    document.querySelector('button.yt-spec-button-shape-next--mono'),
  ].find(Boolean);
  if (askBtn) { askBtn.click(); await sleep(1200); }
  const box = document.querySelector('textarea, div[contenteditable="true"], input');
  if (!box) return "ERROR: no ask input found (are you logged in?)";
  const setVal = (el, s) => {
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, s); else el.value = s;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
      el.textContent = s;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: s }));
    }
  };
  setVal(box, ${p});
  await sleep(300);
  const submit = document.querySelector('button[aria-label*="send" i], #submit-button, ytd-ai-ask-button button');
  if (submit) { submit.click(); }
  else {
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
    box.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
  }
  await sleep(6000);
  const sel = ['#ai-feature-content','[data-testid*="ai-response" i]','ytd-ai-ask-response-renderer','#content #answer','ytd-transcript-search-panel-renderer'];
  let el = null;
  for (const s of sel) { el = document.querySelector(s); if (el && el.innerText.trim()) break; }
  if (!el) {
    const blocks = [...document.querySelectorAll('yt-formatted-string, #content')]
      .filter(n => n.innerText && n.innerText.length > 80)
      .sort((a, b) => b.innerText.length - a.innerText.length);
    el = blocks[0] || null;
  }
  return el ? el.innerText.trim() : "ERROR: no answer captured";
})()`;
}

// Internal message router (popup/content-script -> worker).
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg.type === "ODY_ASK") {
      // Popup asks: drive the YouTube tab standalone.
      const prompt = String(msg.prompt || "").trim();
      if (!prompt) return sendResponse({ ok: false, error: "Empty prompt." });
      const tab = await findYouTubeTab();
      if (!tab) return sendResponse({ ok: false, error: "No YouTube tab open. Open youtube.com first." });
      try {
        const resp = await chrome.tabs.sendMessage(tab.id, { type: "ODY_EVAL", eval_token: "__standalone__", eval_code: youtubeAskJs(prompt) });
        return sendResponse({ ok: true, ...resp });
      } catch (e) {
        return sendResponse({ ok: false, error: String(e && e.message || e) });
      }
    }
    if (msg.type === "ODY_POST_RESULT") {
      // content.js asks the worker to post the result back to Odysseus.
      try {
        const base = await getBaseUrl();
        const url = base + "/api/browser/eval-result";
        const r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: msg.token, output: msg.output || "", error: msg.error || "" }),
        });
        const ok = r.ok;
        sendResponse({ ok });
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message || e) });
      }
      return;
    }
    // no match
    sendResponse({ ok: false, error: "unknown message" });
  })();
  return true; // keep the async channel open
});

// External message from the Odysseus page (browserEval.js side).
chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "ODY_EVAL") return;
  relayToYouTube(msg).then((r) => sendResponse(r));
  return true;
});

// Context menu + keyboard shortcut.
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "ody-ask",
    title: "Ask YouTube AI (Odysseus)",
    contexts: ["selection"],
  });
});
chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === "ody-ask") {
    chrome.runtime.sendMessage({ type: "ODY_ASK", prompt: info.selectionText || "" });
  }
});
chrome.commands.onCommand.addListener((cmd) => {
  if (cmd === "run-ai-ask") {
    chrome.runtime.sendMessage({ type: "ODY_ASK", prompt: "" });
  }
});
