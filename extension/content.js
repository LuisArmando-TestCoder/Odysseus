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
    // contenteditable: use execCommand so the framework sees a real edit.
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
  // Prefer a button whose label/title/aria mentions send/submit (NOT "search",
  // which matches the mic's "Search by voice" label).
  const labeled = candidates.find((b) => {
    const s = ((b.getAttribute("aria-label") || "") + " " + (b.getAttribute("title") || "") + " " + (b.textContent || "")).toLowerCase();
    return /send|submit|enviar|ask/i.test(s);
  });
  if (labeled) return labeled;
  // Fallback: the last non-voice button in the form (often the send button).
  const buttons = candidates.filter((b) => b.tagName === "BUTTON" || b.getAttribute("role") === "button");
  return buttons.length ? buttons[buttons.length - 1] : null;
}

// Submit the input. Order of preference:
//   1. Native form submission (form.requestSubmit / form.submit) — this is what
//      a REAL Enter keypress triggers, and it works for Google's standard
//      search box where synthetic KeyboardEvents are ignored (untrusted).
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

  // Fallback: clone the body, strip non-content elements, read the longest
  // visible text block.
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll("style, script, noscript, link, svg, iframe, [aria-hidden='true']").forEach((n) => n.remove());
  const blocks = [...clone.querySelectorAll("div, section, article, main, yt-formatted-string, #content")]
    .map((n) => n.innerText || "")
    .filter((t) => t.trim().length > 80)
    .sort((a, b) => b.length - a.length);
  return blocks[0] ? blocks[0].trim() : "";
}

// ── Google AI mode driver ───────────────────────────────────────────────────
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

  // 4. Submit.
  submitInput(box);
  await sleep(500);

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
  return last || "ERROR: no answer captured";
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

  submitInput(box);
  await sleep(500);

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
  // Generic fallback: just find an input, type, submit, and read the page.
  const box = findAskInput();
  if (!box) return "ERROR: no input found on this page";
  setInputValue(box, prompt);
  submitInput(box);
  await new Promise((r) => setTimeout(r, 5000));
  return readAnswer() || "ERROR: no answer captured";
}

// ── Expose the driver to agent-injected code ────────────────────────────────
window.__odyBridge = {
  askAI,
  askGoogleAI,
  askYouTubeAI,
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