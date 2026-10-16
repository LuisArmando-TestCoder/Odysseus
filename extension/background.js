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

// Read the answer from a tab by injecting a small script. This survives SPA
// navigation/reload because it re-injects on every poll.
async function readAnswerFromTab(tabId) {
  const READ_FN = `(() => {
    const sel = [
      "#ai-feature-content",
      '[data-testid*="ai-response" i]',
      "ytd-ai-ask-response-renderer",
      "#content #answer",
      "ytd-transcript-search-panel-renderer",
      '[data-testid*="ai-overview" i]',
      '[data-testid*="ai-mode" i]',
      '[class*="ai-mode" i]',
      '[class*="ai-overview" i]',
      "g-ai-overview",
      "div[jsname*='ai']",
    ];
    for (const s of sel) {
      const el = document.querySelector(s);
      if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
    }
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll("style, script, noscript, link, svg, iframe, [aria-hidden='true']").forEach((n) => n.remove());
    const blocks = [...clone.querySelectorAll("div, section, article, main, yt-formatted-string, #content")]
      .map((n) => n.innerText || "")
      .filter((t) => t.trim().length > 80)
      .sort((a, b) => b.length - a.length);
    return blocks[0] ? blocks[0].trim() : "";
  })()`;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: new Function(READ_FN),
    });
    const out = results && results[0] && results[0].result;
    return typeof out === "string" ? out : "";
  } catch (e) {
    return "";
  }
}

// Ask the target tab's AI mode: submit via the content script, then poll the
// tab (via chrome.scripting.executeScript) until the answer stabilizes. The
// polling survives the SPA navigation/reload Google AI mode performs.
async function askAI(prompt, target) {
  const tab = await ensureTargetTab(target);

  // 1. Submit (content script opens AI mode, types, submits, returns).
  let submitResp;
  try {
    submitResp = await chrome.tabs.sendMessage(tab.id, {
      type: "ODY_EVAL",
      eval_token: "__standalone__",
      eval_code: "",
      prompt: prompt,
      submit_only: true,
    });
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
  if (!submitResp || !submitResp.ok) {
    return { ok: false, error: (submitResp && submitResp.error) || "submit failed" };
  }

  // 2. Poll for the answer, tolerating navigation/reload.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let last = "";
  let stable = 0;
  for (let i = 0; i < 60; i++) {
    await sleep(1000);
    const cur = await readAnswerFromTab(tab.id);
    if (cur && cur === last) {
      if (++stable >= 3) break;
    } else {
      stable = 0;
      last = cur;
    }
  }
  if (last) return { ok: true, output: last };
  return { ok: false, error: "ERROR: no answer captured" };
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