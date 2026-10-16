// popup.js — toolbar popup logic.

const promptEl = document.getElementById("prompt");
const askBtn = document.getElementById("ask");
const statusEl = document.getElementById("status");

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || "";
}

function selectedTarget() {
  const el = document.querySelector('input[name="target"]:checked');
  return el ? el.value : "google";
}

askBtn.addEventListener("click", async () => {
  const prompt = promptEl.value.trim();
  if (!prompt) {
    setStatus("Please enter a question first.", "err");
    return;
  }
  const target = selectedTarget();
  askBtn.disabled = true;
  setStatus(`Asking ${target === "youtube" ? "YouTube" : "Google"} AI…`, "");

  try {
    const resp = await chrome.runtime.sendMessage({ type: "ODY_ASK", prompt, target });
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