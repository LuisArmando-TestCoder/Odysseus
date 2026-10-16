// content.js — Odysseus Browser Bridge content script (runs inside youtube.com tabs).
//
// Two jobs:
//   1. Receive {eval_token, eval_code} from the background worker and execute
//      the code in the YouTube page, then hand the result back.
//   2. Provide a robust "ask YouTube AI" driver that locates the AI-mode
//      button by its *visible text label* (not a hardcoded selector), types a
//      prompt, submits, and reads the rendered answer.

// ── Text-node → nearest-clickable walker ────────────────────────────────────
// The core idea: instead of guessing button[aria-label="Ask"], walk every text
// node in the page, match its content against a set of labels (case-insensitive,
// localization-tolerant), then climb up to the nearest element that is actually
// clickable (button, role=button, link, tabindex, or a container that holds a
// button). This survives YouTube UI changes and localization.
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
  ];
  for (const s of sel) {
    const el = document.querySelector(s);
    if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
  }
  const blocks = [...document.querySelectorAll("yt-formatted-string, #content")]
    .filter((n) => n.innerText && n.innerText.length > 80)
    .sort((a, b) => b.innerText.length - a.innerText.length);
  return blocks[0] ? blocks[0].innerText.trim() : "";
}

// ── The "ask YouTube AI" driver ─────────────────────────────────────────────
// Locates the AI-mode button by its visible label, opens it, types the prompt,
// submits, waits for the answer to stream, and returns the text.
async function askYouTubeAI(prompt) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // 1. Find + click the AI-mode button by its text label.
  const aiLabels = [
    "ask ai", "ai mode", "ask", "search with ai", "ask youtube",
    "gemini", "using ai", "ask about this video", "tienes preguntas",
    "preguntar", "pregúntale", "ask anything",
  ];
  let btn = findClickableByText(aiLabels);
  if (!btn) {
    // Fallback: any button whose aria-label mentions AI/ask.
    btn = [...document.querySelectorAll("button, [role=button]")].find((b) => {
      const s = ((b.getAttribute("aria-label") || "") + " " + (b.textContent || "")).toLowerCase();
      return /ask|ai mode|gemini|pregunt/i.test(s);
    }) || null;
  }
  if (!btn) return "ERROR: AI-mode button not found (are you logged in to YouTube?)";
  btn.click();
  await sleep(1500);

  // 2. Find the input box (may have appeared after clicking).
  let box = findAskInput();
  if (!box) {
    await sleep(1000);
    box = findAskInput();
  }
  if (!box) return "ERROR: no ask input found after opening AI mode";

  // 3. Type the prompt.
  setInputValue(box, prompt);
  await sleep(400);

  // 4. Submit — try a send button by text, else press Enter (possibly twice).
  const sendBtn = findClickableByText(["send", "submit", "enviar", "ask"], box.closest("form") || document);
  if (sendBtn && sendBtn !== btn) {
    sendBtn.click();
  } else {
    pressEnter(box);
    await sleep(300);
    pressEnter(box);
  }

  // 5. Wait for the answer to stream in, polling until text stabilizes.
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

// ── Message handling ────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "ODY_EVAL") return;

  (async () => {
    const token = msg.eval_token;
    const code = msg.eval_code;

    // Standalone path: the background worker sent a prompt to ask directly.
    if (token === "__standalone__" && msg.prompt !== undefined) {
      try {
        const answer = await askYouTubeAI(String(msg.prompt));
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

    // Post the result back to Odysseus via the background worker (which knows
    // the base URL and can reach the same-origin endpoint).
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