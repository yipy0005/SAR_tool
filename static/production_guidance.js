"use strict";

(() => {
  const toggle = document.querySelector("#productionModeToggle");
  if (!toggle) return;

  const storageKey = "sar-workbench-workspace-mode";
  let mode = "guided";
  try {
    mode = window.localStorage.getItem(storageKey) === "expert" ? "expert" : "guided";
  } catch (_error) {
    mode = "guided";
  }

  const render = () => {
    document.body.dataset.workspaceMode = mode;
    const expert = mode === "expert";
    toggle.textContent = expert ? "Use guided view" : "Use expert view";
    toggle.setAttribute("aria-pressed", String(expert));
    toggle.setAttribute("aria-label", expert ? "Switch to guided view" : "Switch to expert view");
    document.querySelectorAll("details.production-guided-advanced").forEach((details) => {
      if (expert) details.open = true;
    });
  };

  toggle.addEventListener("click", () => {
    mode = mode === "expert" ? "guided" : "expert";
    try {
      window.localStorage.setItem(storageKey, mode);
    } catch (_error) {
      // The view remains usable when local storage is unavailable.
    }
    render();
  });
  render();
})();

// Evidence links (#ev-<id>) may point into a collapsed <details>; open its ancestors and highlight the row.
(() => {
  const reveal = () => {
    const hash = decodeURIComponent(window.location.hash || "");
    if (!hash.startsWith("#ev-")) return;
    const target = document.getElementById(hash.slice(1));
    if (!target) return;
    for (let node = target.parentElement; node; node = node.parentElement) {
      if (node.tagName === "DETAILS") node.open = true;
    }
    target.classList.add("is-linked-evidence");
    target.scrollIntoView({ block: "center" });
  };
  window.addEventListener("hashchange", reveal);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", reveal);
  else reveal();
})();
