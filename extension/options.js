// options.js — persist the Odysseus base URL.

const input = document.getElementById("baseUrl");
const saveBtn = document.getElementById("save");
const savedEl = document.getElementById("saved");

async function load() {
  const d = await chrome.storage.sync.get({ baseUrl: "http://127.0.0.1:5173" });
  input.value = d.baseUrl || "";
}

saveBtn.addEventListener("click", async () => {
  const baseUrl = input.value.trim().replace(/\/+$/, "");
  await chrome.storage.sync.set({ baseUrl });
  savedEl.textContent = "Saved.";
  setTimeout(() => (savedEl.textContent = ""), 1500);
});

load();