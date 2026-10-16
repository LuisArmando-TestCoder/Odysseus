// background.js — Odysseus Browser Bridge service worker (MV3).
//
// Roles:
//   1. Relay an eval payload {eval_token, eval_code} from the Odysseus page
//      (chrome.runtime.onMessageExternal) to a Google/YouTube tab.
//   2. Relay results back to Odysseus by POSTing to /api/browser/eval-result.
//   3. Handle popup ("ODY_ASK"), context-menu and keyboard "ask" actions by
//      delegating to the content script's walker-based askAI driver.
//   4. Auto-open a Google/YouTube tab when none is open, so Odysseus can
//      perform a search even without a pre-existing AI-mode window.

async function getBaseUrl() {
  const d = await chrome.storage.sync.get({ baseUrl: "http://127.0.0.1:5173" });
  return String(d.baseUrl || "").replace(/\/+$/, "");
}

const TARGET_URLS = {
  google: "https://www.google.com/",
  youtube: "https://www.youtube.com/",
};

function hostOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch (e) { return ""; }
}

function isTargetTab(tab) {
  const h = hostOf(tab.url || "");
  return h.includes("google.") || h.includes("youtube.com");
}

// Find an existing Google/YouTube tab, preferring the active one if it's a
// target site. Returns null if none exists.
async function findTargetTab(preferred) {
  const active = await chrome.tabs.query({ active: true, currentWindow: true });
  const act = active[0];
  if (act && isTargetTab(act)) {
    // If a specific target is requested and the active tab matches it, use it.
    if (!preferred || hostOf(act.url).includes(preferred)) return act;
  }
  // Otherwise find any target tab, preferring the requested host.
  const all = await chrome.tabs.query({});
  const targets = all.filter(isTargetTab);
  if (!targets.length) return null;
  if (preferred) {
    const match = targets.find((t) => hostOf(t.url).includes(preferred));
    if (match) return match;
  }
  return targets[0];
}

// Open a new tab for the requested target (default google) and wait for it to
// finish loading so the content script is injected.
async function openTargetTab(target) {
  const url = TARGET_URLS[target] || TARGET_URLS.google;
  const tab = await chrome.tabs.create({ url, active: true });
  // Wait for the tab to reach "complete" (content script injects at idle).
  await new Promise((resolve) => {
    const check = () => {
      chrome.tabs.get(tab.id, (t) => {
        if (chrome.runtime.lastError || !t) return resolve();
        if (t.status === "complete") return resolve();
        setTimeout(check, 300);
      });
    };
    setTimeout(check, 500);
  });
  // Small extra settle for SPA hydration.
  await new Promise((r) => setTimeout(r, 1500));
  return tab;
}

// Resolve a target tab, opening one if none exists.
async function ensureTargetTab(target) {
  let tab = await findTargetTab(target);
  if (!tab) tab = await openTargetTab(target);
  return tab;
}

// Relay {eval_token, eval_code} to a target tab's content script.
async function relayToTab(payload) {
  const tab = await ensureTargetTab(payload.target);
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

// Ask the target tab's AI mode directly: delegate to the content script's
// walker-based driver (which auto-detects Google vs YouTube by hostname).
async function askAI(prompt, target) {
  const tab = await ensureTargetTab(target);
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
      const resp = await askAI(prompt, msg.target);
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