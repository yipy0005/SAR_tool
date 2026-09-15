(() => {
  "use strict";

  const form = document.querySelector("#productionSeriesForm");
  const status = document.querySelector("#productionSeriesStatus");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  if (!form || !status || !projectId) return;

  const setStatus = (message, kind = "") => {
    status.textContent = message;
    status.dataset.status = kind;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    const compoundIds = formData.getAll("compound_id");
    if (!compoundIds.length) {
      setStatus("Choose at least one compound for the series.", "error");
      return;
    }
    const submit = form.querySelector("button[type=submit]");
    if (submit) submit.disabled = true;
    setStatus("Saving the reviewed series membership…");
    try {
      const headers = { "Content-Type": "application/json" };
      if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
      const response = await fetch("/api/v1/series", {
        method: "POST",
        headers,
        body: JSON.stringify({
          project_id: projectId,
          name: String(formData.get("name") || "").trim(),
          description: String(formData.get("description") || "").trim(),
          rationale: String(formData.get("rationale") || "").trim(),
          membership_source: "curated",
          membership_status: "included",
          compound_ids: compoundIds,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
      setStatus("Series saved. Reloading the dynamic SAR labels…", "success");
      window.setTimeout(() => window.location.reload(), 250);
    } catch (error) {
      setStatus(`Series could not be saved: ${error.message}`, "error");
      if (submit) submit.disabled = false;
    }
  });
})();
