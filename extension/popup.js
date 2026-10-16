// popup.js — toolbar popup logic.

const promptEl = document.getElementById("prompt");
const askBtn = document.getElementById("ask");
const statusEl = document.getElementById("status");

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || "";
}

askBtn.addEventListener("click", async () => {
  const prompt = promptEl.value.trim();
  if (!prompt) {
    setStatus("Please enter a question first.", "err");
    return;
  }
  askBtn.disabled = true;
  setStatus("Asking YouTube AI…", "");

  try {
    const resp = await chrome.runtime.sendMessage({ type: "ODY_ASK", prompt });
    if (resp && resp.ok) {
      setStatus(resp.output || "(no answer returned)", "ok");
    } else {
      setStatus((resp && resp.error) || "Unknown error", "err");
    }
  } catch (e) {
    setStatus(String((e && e.message) || e), "err");
  } finally {
    askBtn.disabled = false;
  }
});