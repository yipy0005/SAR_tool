(() => {
  "use strict";

  const root = document.querySelector("[data-production-project]");
  const button = document.querySelector("#productionSummaryButton");
  const status = document.querySelector("#productionSummaryStatus");
  if (!root || !button || !status) return;

  const projectId = root.dataset.productionProject;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const pendingImportStorageKey = `sar-workbench:pending-import:${projectId}`;

  const setStatus = (message, kind = "") => {
    status.textContent = message;
    status.dataset.status = kind;
  };

  const hasPendingImport = () => {
    try {
      return Boolean(window.sessionStorage.getItem(pendingImportStorageKey));
    } catch (_error) {
      return false;
    }
  };

  const request = async () => {
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch("/api/v1/measurement-summaries/project", {
      method: "POST",
      credentials: "same-origin",
      headers,
      body: JSON.stringify({ project_id: projectId }),
    });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch (_error) { payload = { message: text }; }
    if (!response.ok) throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
    return payload;
  };

  if (hasPendingImport()) {
    button.disabled = true;
    setStatus("Save the checked file on Start before building summaries.", "error");
  }

  button.addEventListener("click", async () => {
    if (hasPendingImport()) {
      setStatus("Save the checked file on Start before building summaries.", "error");
      return;
    }
    button.disabled = true;
    button.textContent = "Building…";
    setStatus("Grouping compatible results and preserving source links…");
    try {
      const result = await request();
      setStatus(`Built ${result.count || 0} grouped result${(result.count || 0) === 1 ? "" : "s"}. Reloading the saved read model…`, "success");
      window.setTimeout(() => window.location.reload(), 650);
    } catch (error) {
      button.disabled = false;
      button.textContent = "Build result summaries";
      setStatus(`Summary generation failed: ${error.message}`, "error");
    }
  });
})();
