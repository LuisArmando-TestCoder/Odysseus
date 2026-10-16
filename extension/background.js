// background.js — Odysseus Browser Bridge service worker (MV3).
//
// Roles:
//   1. Relay an eval payload {eval_token, eval_code} from the Odysseus page
//      (chrome.runtime.onMessageExternal) to the active tab.
//   2. Relay results back to Odysseus by POSTing to /api/browser/eval-result.
//   3. Handle popup ("ODY_ASK"), context-menu and keyboard "ask" actions by
//      delegating to the content script's walker-based askAI driver.

async function getBaseUrl() {
  const d = await chrome.storage.sync.get({ baseUrl: "http://127.0.0.1:5173" });
  return String(d.baseUrl || "").replace(/\/+$/, "");
}

// Target the currently active tab in the current window (whatever site it is),
// rather than a hardcoded YouTube query. This is what fixes the
// "Receiving end does not exist" crash when the user is on a Google page.
async function findTargetTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs.length ? tabs[0] : null;
}

// Relay {eval_token, eval_code} to the active tab's content script.
async function relayToTab(payload) {
  const tab = await findTargetTab();
  if (!tab) return { ok: false, error: "No active tab." };
  try {
    return await chrome.tabs.sendMessage(tab.id, {
      type: "ODY_EVAL",
      eval_token: payload.eval_token,
      eval_code: payload.eval_code,
    });
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

// Ask the active tab's AI mode directly: delegate to the content script's
// walker-based driver (which auto-detects Google vs YouTube by hostname).
async function askAI(prompt) {
  const tab = await findTargetTab();
  if (!tab) return { ok: false, error: "No active tab." };
  try {
    return await chrome.tabs.sendMessage(tab.id, {
      type: "ODY_EVAL",
      eval_token: "__standalone__",
      eval_code: "",
      prompt: prompt,
    });
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

// Internal message router (popup/content-script -> worker).
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg.type === "ODY_ASK") {
      const prompt = String(msg.prompt || "").trim();
      if (!prompt) return sendResponse({ ok: false, error: "Empty prompt." });
      const resp = await askAI(prompt);
      return sendResponse(resp);
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
        return sendResponse({ ok: r.ok });
      } catch (e) {
        return sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    }
    sendResponse({ ok: false, error: "unknown message" });
  })();
  return true; // keep the async channel open
});

// External message from the Odysseus page (browserEval.js side).
chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "ODY_EVAL") return;
  relayToTab(msg).then((r) => sendResponse(r));
  return true;
});

// Context menu + keyboard shortcut.
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "ody-ask",
    title: "Ask AI (Odysseus)",
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