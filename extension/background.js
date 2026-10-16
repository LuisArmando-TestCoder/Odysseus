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

// ── Logging ────────────────────────────────────────────────────────────────
// Every bridge step is logged to the extension's service-worker console
// (chrome://extensions → "Odysseus Browser Bridge" → "service worker").
// Prefix "ODY" so it's easy to filter.
//
// Each line is ALSO forwarded to the Odysseus server's /api/browser/log
// endpoint so the bridge's browser-side activity shows up in the SERVER's
// tool process (the terminal running Odysseus), not just the browser console.
function log(...args) {
  const line = args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");
  console.log("[ODY]", ...args);
  _forwardLog("info", line);
}
function logErr(...args) {
  const line = args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");
  console.error("[ODY]", ...args);
  _forwardLog("error", line);
}

// Fire-and-forget POST of a log line to the server. Never blocks or throws —
// logging must not break the bridge.
async function _forwardLog(level, message) {
  try {
    const base = await getBaseUrl();
    await fetch(base + "/api/browser/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level, message }),
    });
  } catch (e) {
    // Ignore — the server may be down or the endpoint unreachable.
  }
}

async function getBaseUrl() {
  const d = await chrome.storage.sync.get({ baseUrl: "http://127.0.0.1:7860" });
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

// Open a new tab for the requested target (default google) and wait for the
// content script to actually be ready (window.__odyBridge present), not just
// for the tab to reach "complete" — Google's SPA hydrates after load and the
// content script may not have injected yet.
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
  // Poll for the content script's bridge to be ready (survives SPA hydration).
  for (let i = 0; i < 20; i++) {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => !!(window.__odyBridge && typeof window.__odyBridge.askAI === "function"),
      });
      if (results && results[0] && results[0].result) return tab;
    } catch (e) {
      // content script not injected yet — keep polling
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return tab;
}

// Resolve a target tab, opening one if none exists.
async function ensureTargetTab(target) {
  let tab = await findTargetTab(target);
  if (!tab) tab = await openTargetTab(target);
  return tab;
}

// Relay {eval_token, eval_code} to a target tab's content script. Retries the
// sendMessage (the content script may not be injected yet on a freshly-opened
// tab) and falls back to injecting content.js directly if the message channel
// never comes up.
async function relayToTab(payload) {
  const tab = await ensureTargetTab(payload.target);
  const msg = {
    type: "ODY_EVAL",
    eval_token: payload.eval_token,
    eval_code: payload.eval_code,
  };

  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const resp = await chrome.tabs.sendMessage(tab.id, msg);
      if (resp && resp.ok) return resp;
      // Non-ok response — surface it rather than retrying blindly.
      return resp || { ok: false, error: "empty response from content script" };
    } catch (e) {
      // "Receiving end does not exist" — content script not injected yet.
      if (attempt < 4) {
        await new Promise((r) => setTimeout(r, 500));
        continue;
      }
      // Last resort: inject content.js directly and retry once.
      try {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ["content.js"],
        });
        const resp = await chrome.tabs.sendMessage(tab.id, msg);
        if (resp && resp.ok) return resp;
        return resp || { ok: false, error: "content script injection failed" };
      } catch (e2) {
        return { ok: false, error: String((e2 && e2.message) || e2) };
      }
    }
  }
  return { ok: false, error: "relay failed after retries" };
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
  log("askAI start", { target, prompt: prompt.slice(0, 80) });
  const tab = await ensureTargetTab(target);
  log("target tab", { id: tab.id, url: tab.url });

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
    logErr("submit sendMessage failed", e);
    return { ok: false, error: String((e && e.message) || e) };
  }
  log("submit response", submitResp);
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
    if (i % 5 === 0) log("poll", { i, len: cur.length, stable });
  }
  if (last) {
    log("answer captured", { len: last.length });
    return { ok: true, output: last };
  }
  logErr("no answer captured after polling");
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
    if (msg.type === "ODY_LOG") {
      // content.js forwards its console lines here so they reach the server.
      _forwardLog(msg.level || "info", String(msg.message || ""));
      return sendResponse({ ok: true });
    }
    sendResponse({ ok: false, error: "unknown message" });
  })();
  return true; // keep the async channel open
});

// External message from the Odysseus page (browserEval.js side).
chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "ODY_EVAL") return;
  // Always respond — even on failure — so the page's sendMessage promise
  // settles and its watchdog can resolve the server Future. A hung
  // sendResponse is what left the agent waiting 120s.
  let responded = false;
  const finish = (r) => {
    if (responded) return;
    responded = true;
    try { sendResponse(r); } catch (_) {}
  };
  relayToTab(msg).then(finish).catch((e) => finish({ ok: false, error: String((e && e.message) || e) }));
  // Hard timeout: if relayToTab somehow hangs, still answer.
  setTimeout(() => finish({ ok: false, error: "relay timed out" }), 60000);
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