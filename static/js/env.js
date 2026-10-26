// static/js/env.js — Environment variables (.env) management modal.
//
// Admin-gated. Reads the installer's credential registry from /api/env and
// renders a form of inputs. The modal only auto-opens when a REQUIRED variable
// is missing (the "gate"); once everything required is saved, it stays closed
// unless the user reopens it from Settings → System. Secrets are never echoed
// back — the input is blank with a "(saved)" hint when a value already exists.

(function () {
  "use strict";

  const API = "/api/env";
  const STATUS = "/api/env/status";

  let registry = null; // { entries: [...], groups: {...} }
  let modalEl = null;
  let formEl = null;
  let msgEl = null;
  let gateChecked = false;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&" + "amp;")
      .replace(/</g, "&" + "lt;")
      .replace(/>/g, "&" + "gt;")
      .replace(/"/g, "&" + "quot;");
  }

  function el(id) {
    return document.getElementById(id);
  }

  function showToast(text, ms) {
    try {
      if (window.uiModule && window.uiModule.showToast) {
        window.uiModule.showToast(text, ms || 4000);
        return;
      }
    } catch (_) {}
    const t = el("toast");
    if (t) {
      t.textContent = text;
      t.classList.remove("hidden");
      setTimeout(() => t.classList.add("hidden"), ms || 4000);
    }
  }

  function buildForm() {
    if (!registry || !formEl) return;
    const entries = registry.entries || [];
    const groups = registry.groups || {};

    // Group entries by category, preserving registry order.
    const byGroup = {};
    const order = [];
    for (const e of entries) {
      const g = e.group || e.category || "misc";
      if (!byGroup[g]) {
        byGroup[g] = [];
        order.push(g);
      }
      byGroup[g].push(e);
    }

    let html = "";
    for (const g of order) {
      const label = groups[g] || g;
      html +=
        '<div class="env-group">' +
        '<div class="env-group-label">' + esc(label) + "</div>";
      for (const e of byGroup[g]) {
        const isSet = !!e.is_set;
        const isSecret = !!e.secret;
        const required = !!e.required;
        const missing = required && !isSet;
        const inputType = isSecret ? "password" : "text";
        const placeholder = isSet
          ? isSecret
            ? "(saved — leave blank to keep)"
            : esc(e.value || "")
          : esc(e.default || "");
        const hint = e.help_text
          ? '<div class="env-help">' + esc(e.help_text) + "</div>"
          : "";
        const tutorials = (e.tutorial_urls || [])
          .map(
            (u, i) =>
              '<a class="env-tutorial" href="' +
              esc(u) +
              '" target="_blank" rel="noopener noreferrer">Tutorial ' +
              (i + 1) +
              "</a>"
          )
          .join(" ");

        html +=
          '<div class="env-row' +
          (missing ? " env-missing" : "") +
          '">' +
          '<div class="env-row-head">' +
          '<label class="env-label" for="env-' + esc(e.key) + '">' +
          esc(e.label) +
          (required ? ' <span class="env-req">required</span>' : "") +
          (missing ? ' <span class="env-missing-badge">missing</span>' : "") +
          "</label>" +
          '<code class="env-key">' + esc(e.key) + "</code>" +
          "</div>" +
          hint +
          '<input class="env-input" id="env-' + esc(e.key) + '" data-key="' +
          esc(e.key) +
          '" type="' + inputType + '" placeholder="' + esc(placeholder) +
          '" autocomplete="off" spellcheck="false">' +
          (tutorials ? '<div class="env-tutorials">' + tutorials + "</div>" : "") +
          "</div>";
      }
      html += "</div>";
    }

    formEl.innerHTML = html;
  }

  async function loadRegistry() {
    try {
      const r = await fetch(API, { credentials: "same-origin" });
      if (!r.ok) return false;
      registry = await r.json();
      buildForm();
      return true;
    } catch (_) {
      return false;
    }
  }

  async function checkStatus() {
    try {
      const r = await fetch(STATUS, { credentials: "same-origin" });
      if (!r.ok) return { complete: true, missing: [] };
      return await r.json();
    } catch (_) {
      return { complete: true, missing: [] };
    }
  }

  function openModal() {
    if (!modalEl) return;
    modalEl.classList.remove("hidden");
  }

  function closeModal() {
    if (!modalEl) return;
    modalEl.classList.add("hidden");
  }

  async function save() {
    if (!formEl) return;
    const inputs = formEl.querySelectorAll("input[data-key]");
    const payload = {};
    for (const inp of inputs) {
      const key = inp.dataset.key;
      const val = inp.value;
      // Only send non-empty values; empty means "leave unchanged".
      if (val !== "") payload[key] = val;
    }
    if (Object.keys(payload).length === 0) {
      if (msgEl) msgEl.textContent = "Nothing to save.";
      return;
    }
    try {
      const r = await fetch(API, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Save failed");
      if (msgEl) msgEl.textContent = "Saved.";
      showToast("Environment variables saved", 3000);
      // Refresh registry so the "missing" badges update.
      await loadRegistry();
      // If everything required is now set, close the gate.
      const st = await checkStatus();
      if (st.complete) {
        closeModal();
      } else if (msgEl) {
        msgEl.textContent =
          "Saved. Still missing: " + (st.missing || []).join(", ");
      }
    } catch (e) {
      if (msgEl) msgEl.textContent = "Error: " + e.message;
    }
  }

  // The gate: auto-open only when a required variable is missing.
  async function runGate() {
    if (gateChecked) return;
    gateChecked = true;
    const st = await checkStatus();
    if (st.complete) return;
    const ok = await loadRegistry();
    if (!ok) return;
    openModal();
  }

  function init() {
    modalEl = el("env-modal");
    formEl = el("env-form");
    msgEl = el("env-msg");
    if (!modalEl) return;

    const closeBtn = el("env-close");
    if (closeBtn) closeBtn.addEventListener("click", closeModal);

    const saveBtn = el("env-save");
    if (saveBtn) saveBtn.addEventListener("click", save);

    // Reopen from Settings → System.
    const openBtn = el("env-open-btn");
    if (openBtn) {
      openBtn.addEventListener("click", async () => {
        await loadRegistry();
        openModal();
      });
    }

    // Run the gate once the app is up.
    runGate();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();