// content.js — Odysseus Browser Bridge content script.
//
// Runs inside the user's active tab (Google, YouTube, or any matched page).
// Two jobs:
//   1. Receive {eval_token, eval_code} from the background worker and execute
//      the code in the page, then hand the result back.
//   2. Provide a robust "ask AI" driver that locates the AI-mode button by its
//      *visible text label* (not a hardcoded selector), types a prompt,
//      submits, and reads the rendered answer. Auto-detects Google vs YouTube
//      by hostname.

// ── Text-node → nearest-clickable walker ────────────────────────────────────
// Walk every text node, match its content against a set of labels
// (case-insensitive, localization-tolerant), then climb up to the nearest
// element that is actually clickable. Survives UI changes and localization.
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
    el.textContent = s;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: s }));
  }
}

// Press Enter on an element (keydown + keyup), which is how the AI box submits.
function pressEnter(el) {
  const opts = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
  el.dispatchEvent(new KeyboardEvent("keydown", opts));
  el.dispatchEvent(new KeyboardEvent("keyup", opts));
}

// Read the rendered answer. Prefer a scoped container; fall back to the
// longest text block on the page.
function readAnswer() {
  const sel = [
    "#ai-feature-content",
    '[data-testid*="ai-response" i]',
    "ytd-ai-ask-response-renderer",
    "#content #answer",
    "ytd-transcript-search-panel-renderer",
    // Google AI mode / AI Overviews containers
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
  const blocks = [...document.querySelectorAll("yt-formatted-string, #content, [jsname]")]
    .filter((n) => n.innerText && n.innerText.length > 80)
    .sort((a, b) => b.innerText.length - a.innerText.length);
  return blocks[0] ? blocks[0].innerText.trim() : "";
}

// ── Google AI mode driver ───────────────────────────────────────────────────
// Google's AI mode: click the "AI Mode" button (or the Gemini sparkle), a
// textarea appears, type the prompt, press Enter (possibly twice), and read
// the rendered answer.
async function askGoogleAI(prompt) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // 1. Find + click the AI-mode button by its text label.
  const aiLabels = [
    "ai mode", "ask ai", "ask anything", "gemini", "search labs",
    "ai overviews", "ai overview", "generative ai", "ask google",
    "modo ia", "preguntar", "pregúntale",
  ];
  let btn = findClickableByText(aiLabels);
  if (!btn) {
    // Fallback: any button whose aria-label/title mentions AI.
    btn = [...document.querySelectorAll("button, [role=button]")].find((b) => {
      const s = ((b.getAttribute("aria-label") || "") + " " + (b.getAttribute("title") || "") + " " + (b.textContent || "")).toLowerCase();
      return /ai mode|ask ai|gemini|ai overview|pregunt/i.test(s);
    }) || null;
  }
  if (!btn) return "ERROR: AI-mode button not found (are you logged in to Google?)";
  btn.click();
  await sleep(2000);

  // 2. Find the input box (may have appeared after clicking).
  let box = findAskInput();
  if (!box) {
    await sleep(1500);
    box = findAskInput();
  }
  if (!box) return "ERROR: no ask input found after opening AI mode";

  // 3. Type the prompt.
  setInputValue(box, prompt);
  await sleep(500);

  // 4. Submit — try a send button by text, else press Enter (possibly twice).
  const sendBtn = findClickableByText(["send", "submit", "enviar", "ask"], box.closest("form") || document);
  if (sendBtn && sendBtn !== btn) {
    sendBtn.click();
  } else {
    pressEnter(box);
    await sleep(400);
    pressEnter(box);
  }

  // 5. Wait for the answer to stream in, polling until text stabilizes.
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
  if (last) return last;
  // Last resort: the user's original approach — read the whole page text.
  return document.body.innerText.trim() || "ERROR: no answer captured";
}

// ── YouTube AI mode driver ──────────────────────────────────────────────────
async function askYouTubeAI(prompt) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const aiLabels = [
    "ask ai", "ai mode", "ask", "search with ai", "ask youtube",
    "gemini", "using ai", "ask about this video", "tienes preguntas",
    "preguntar", "pregúntale", "ask anything",
  ];
  let btn = findClickableByText(aiLabels);
  if (!btn) {
    btn = [...document.querySelectorAll("button, [role=button]")].find((b) => {
      const s = ((b.getAttribute("aria-label") || "") + " " + (b.textContent || "")).toLowerCase();
      return /ask|ai mode|gemini|pregunt/i.test(s);
    }) || null;
  }
  if (!btn) return "ERROR: AI-mode button not found (are you logged in to YouTube?)";
  btn.click();
  await sleep(1500);

  let box = findAskInput();
  if (!box) {
    await sleep(1000);
    box = findAskInput();
  }
  if (!box) return "ERROR: no ask input found after opening AI mode";

  setInputValue(box, prompt);
  await sleep(400);

  const sendBtn = findClickableByText(["send", "submit", "enviar", "ask"], box.closest("form") || document);
  if (sendBtn && sendBtn !== btn) {
    sendBtn.click();
  } else {
    pressEnter(box);
    await sleep(300);
    pressEnter(box);
  }

  let last = "";
  let stable = 0;
  for (let i = 0; i < 30; i++) {
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

// ── Auto-detect host and dispatch ───────────────────────────────────────────
function detectHost() {
  const h = location.hostname.toLowerCase();
  if (h.includes("youtube.com")) return "youtube";
  if (h.includes("google.")) return "google";
  return "generic";
}

async function askAI(prompt) {
  const host = detectHost();
  if (host === "youtube") return askYouTubeAI(prompt);
  if (host === "google") return askGoogleAI(prompt);
  // Generic fallback: just find an input and type, then read the page.
  const box = findAskInput();
  if (!box) return "ERROR: no input found on this page";
  setInputValue(box, prompt);
  pressEnter(box);
  await new Promise((r) => setTimeout(r, 5000));
  return readAnswer() || document.body.innerText.trim();
}

// ── Expose the driver to agent-injected code ────────────────────────────────
// The agent's run_javascript tool injects code into THIS page. Exposing these
// on `window` lets the agent drive Google/YouTube AI mode with one line:
//   return await window.__odyBridge.askAI("your question here");
window.__odyBridge = {
  askAI,
  askGoogleAI,
  askYouTubeAI,
  findClickableByText,
  findAskInput,
  setInputValue,
  pressEnter,
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
        const answer = await askAI(String(msg.prompt));
        sendResponse({ ok: true, output: answer });
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

    // Post the result back to Odysseus via the background worker.
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