// content.js — Odysseus Browser Bridge content script.
//
// Runs inside the user's active tab (Google, YouTube, or any matched page).
// Two jobs:
//   1. Receive {eval_token, eval_code} from the background worker and execute
//      the code in the page, then hand the result back.
//   2. Provide a robust "ask AI" driver that locates the AI-mode button by its
//      *visible text label* (not a hardcoded selector), types a prompt,
//      submits it, and reads the rendered answer. Auto-detects Google vs
//      YouTube by hostname.
//
// The submit phase and the read phase are SEPARATE so the background worker
// can own the polling (via chrome.scripting.executeScript) and survive the
// SPA navigation/reload that Google AI mode performs after submit.

// ── Logging ────────────────────────────────────────────────────────────────
// Logs to the page's DevTools console (F12 on the Google/YouTube tab).
// Prefix "ODY" so it's easy to filter.
function log(...args) {
  console.log("[ODY]", ...args);
}
function logErr(...args) {
  console.error("[ODY]", ...args);
}

// ── Text-node → nearest-clickable walker ────────────────────────────────────
function findClickableByText(labels, root) {
  root = root || document;
  const needles = labels.map((s) => String(s).toLowerCase());
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const t = (node.textContent || "").trim().toLowerCase();
    if (!t) continue;
    if (needles.some((n) => t.includes(n) || n.includes(t))) {
      let el = node.parentElement;
      let guard = 0;
      while (el && el !== document.body && guard++ < 14) {
        const tag = el.tagName;
        const role = el.getAttribute("role");
        const clickable =
          tag === "BUTTON" ||
          tag === "A" ||
          role === "button" ||
          role === "link" ||
          el.hasAttribute("tabindex") ||
          el.hasAttribute("data-command") ||
          el.hasAttribute("data-testid") ||
          (tag === "DIV" && el.querySelector("button, [role=button]"));
        if (clickable) return el;
        el = el.parentElement;
      }
    }
  }
  return null;
}

// Find the AI-mode input box: a textarea, a text input, or a contenteditable
// that is visible and enabled.
function findAskInput(root) {
  root = root || document;
  const candidates = [
    ...root.querySelectorAll('textarea, input[type="text"], input:not([type]), [contenteditable="true"]'),
  ];
  return candidates.find((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !el.disabled && !el.readOnly;
  }) || null;
}

// Set a value on a textarea/input/contenteditable in a way that fires the
// framework's own handlers (React/Google don't react to plain .value = ...).
function setInputValue(el, s) {
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "INPUT") {
    const proto = tag === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, s);
    else el.value = s;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    el.focus();
    try {
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, s);
    } catch (e) {
      el.textContent = s;
    }
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: s }));
  }
}

// A button is a voice/microphone control if its label mentions voice/mic/audio.
function isVoiceButton(b) {
  const s = ((b.getAttribute("aria-label") || "") + " " + (b.getAttribute("title") || "") + " " + (b.textContent || "")).toLowerCase();
  return /voice|microphone|mic|audio|listen|speak/i.test(s);
}

// Find a submit/send button near the input box, EXCLUDING voice/mic buttons.
function findSubmitButton(box) {
  const scope = box.closest("form") || box.parentElement || document;
  const candidates = [
    ...scope.querySelectorAll('button, [role="button"], input[type="submit"]'),
  ].filter((b) => !isVoiceButton(b));
  const labeled = candidates.find((b) => {
    const s = ((b.getAttribute("aria-label") || "") + " " + (b.getAttribute("title") || "") + " " + (b.textContent || "")).toLowerCase();
    return /send|submit|enviar|ask/i.test(s);
  });
  if (labeled) return labeled;
  const buttons = candidates.filter((b) => b.tagName === "BUTTON" || b.getAttribute("role") === "button");
  return buttons.length ? buttons[buttons.length - 1] : null;
}

// Submit the input. Order of preference:
//   1. Native form submission (form.requestSubmit / form.submit) — what a REAL
//      Enter keypress triggers; works where synthetic KeyboardEvents are ignored.
//   2. Click the send button (excluding voice/mic).
//   3. Dispatch an Enter key sequence as a last resort.
function submitInput(box) {
  const form = box.closest("form");
  if (form) {
    try {
      form.requestSubmit();
      return true;
    } catch (e) {
      try { form.submit(); return true; } catch (e2) { /* ignore */ }
    }
  }
  const btn = findSubmitButton(box);
  if (btn) {
    btn.click();
    return true;
  }
  const opts = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
  box.dispatchEvent(new KeyboardEvent("keydown", opts));
  box.dispatchEvent(new KeyboardEvent("keypress", opts));
  box.dispatchEvent(new KeyboardEvent("keyup", opts));
  return false;
}

// Read the rendered answer, stripping <style>/<script>/<noscript> so we never
// return CSS/JS garbage. Prefer a scoped container; fall back to the longest
// visible text block.
function readAnswer() {
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
}

// ── AI-mode button labels per host ──────────────────────────────────────────
const AI_LABELS = {
  google: [
    "ai mode", "ask ai", "ask anything", "gemini", "search labs",
    "ai overviews", "ai overview", "generative ai", "ask google",
    "modo ia", "preguntar", "pregúntale",
  ],
  youtube: [
    "ask ai", "ai mode", "ask", "search with ai", "ask youtube",
    "gemini", "using ai", "ask about this video", "tienes preguntas",
    "preguntar", "pregúntale", "ask anything",
  ],
};

// Click the AI-mode button and return the input box (or null).
async function openAIInput(host) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const labels = AI_LABELS[host] || AI_LABELS.google;
  let btn = findClickableByText(labels);
  if (!btn) {
    btn = [...document.querySelectorAll("button, [role=button]")].find((b) => {
      const s = ((b.getAttribute("aria-label") || "") + " " + (b.getAttribute("title") || "") + " " + (b.textContent || "")).toLowerCase();
      return /ai mode|ask ai|gemini|ai overview|pregunt|ask/i.test(s);
    }) || null;
  }
  if (!btn) return null;
  btn.click();
  await sleep(host === "youtube" ? 1500 : 2000);
  let box = findAskInput();
  if (!box) {
    await sleep(1500);
    box = findAskInput();
  }
  return box;
}

// Submit a prompt: open AI mode, type, submit. Returns {ok, error} WITHOUT
// polling for the answer — the background worker owns the read phase so it
// survives the SPA navigation/reload.
async function submitAsk(prompt) {
  const host = detectHost();
  log("submitAsk", { host, prompt: prompt.slice(0, 80) });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  if (host === "google" || host === "youtube") {
    const box = await openAIInput(host);
    if (!box) {
      logErr("no ask input found", { host });
      return { ok: false, error: "ERROR: no ask input found (are you logged in?)" };
    }
    log("input found", { tag: box.tagName });
    setInputValue(box, prompt);
    await sleep(500);
    const submitted = submitInput(box);
    log("submitted", { viaForm: submitted });
    return { ok: true };
  }
  const box = findAskInput();
  if (!box) return { ok: false, error: "ERROR: no input found on this page" };
  setInputValue(box, prompt);
  submitInput(box);
  return { ok: true };
}

// In-page convenience: submit + poll (used by the agent's /*@external*/ path).
async function askAI(prompt) {
  const r = await submitAsk(prompt);
  if (!r.ok) return r.error;
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  let last = "";
  let stable = 0;
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    const cur = readAnswer();
    if (cur && cur === last) {
      if (++stable >= 3) break;
    } else {
      stable = 0;
      last = cur;
    }
  }
  return last || "ERROR: no answer captured";
}

// ── Auto-detect host ────────────────────────────────────────────────────────
function detectHost() {
  const h = location.hostname.toLowerCase();
  if (h.includes("youtube.com")) return "youtube";
  if (h.includes("google.")) return "google";
  return "generic";
}

// ── Expose the driver to agent-injected code ────────────────────────────────
window.__odyBridge = {
  askAI,
  submitAsk,
  openAIInput,
  findClickableByText,
  findAskInput,
  setInputValue,
  submitInput,
  readAnswer,
  detectHost,
};

// ── Message handling ─────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "ODY_EVAL") return;

  (async () => {
    const token = msg.eval_token;
    const code = msg.eval_code;

    // Standalone path: the background worker sent a prompt to ask directly.
    if (token === "__standalone__" && msg.prompt !== undefined) {
      try {
        if (msg.submit_only) {
          // Submit only — the background worker will poll for the answer.
          const r = await submitAsk(String(msg.prompt));
          sendResponse(r);
        } else {
          const answer = await askAI(String(msg.prompt));
          sendResponse({ ok: true, output: answer });
        }
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
      return;
    }

    // Generic path: execute arbitrary eval_code in the page.
    let result;
    try {
      const fn = new Function('"use strict";\n' + code);
      let ret = fn();
      if (ret && typeof ret.then === "function") ret = await ret;
      result = { output: ret === undefined ? "" : String(ret) };
    } catch (err) {
      result = { error: String((err && err.message) || err) };
    }

    try {
      const posted = await chrome.runtime.sendMessage({
        type: "ODY_POST_RESULT",
        token: token,
        output: result.output || "",
        error: result.error || "",
      });
      sendResponse({ ok: true, ...posted });
    } catch (e) {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    }
  })();

  return true; // keep the async channel open
});