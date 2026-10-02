// Applies the saved light/dark theme before first paint and wires the header toggle.
// Loaded synchronously in <head>; the production CSP forbids inline scripts.
(() => {
  "use strict";
  const storageKey = "sar-workbench-theme";
  const root = document.documentElement;
  const read = () => {
    try {
      const saved = window.localStorage.getItem(storageKey);
      if (saved === "light" || saved === "dark") return saved;
    } catch (_error) {
      // Storage may be unavailable; fall back to the OS preference.
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  };
  let theme = read();
  root.dataset.theme = theme;

  const sync = (button) => {
    const light = theme === "light";
    button.textContent = light ? "Dark theme" : "Light theme";
    button.setAttribute("aria-pressed", String(light));
    button.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
  };

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.querySelector("[data-theme-toggle]");
    if (!button) return;
    sync(button);
    button.addEventListener("click", () => {
      theme = theme === "light" ? "dark" : "light";
      root.dataset.theme = theme;
      try {
        window.localStorage.setItem(storageKey, theme);
      } catch (_error) {
        // The toggle still works for this page view.
      }
      sync(button);
    });
  });
})();
