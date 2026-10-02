(() => {
  "use strict";

  const form = document.querySelector("#predictionQualificationForm");
  const status = document.querySelector("#predictionQualificationStatus");
  const projectId = document.querySelector("[data-production-project]")?.dataset.productionProject;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  if (!form || !status || !projectId) return;

  const setStatus = (message, kind = "") => {
    status.textContent = message;
    status.dataset.status = kind;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const submit = form.querySelector("button[type=submit]");
    if (submit) submit.disabled = true;
    setStatus("Running leakage-resistant internal validation…");
    try {
      const response = await fetch("/api/v1/predictions/train", {
        method: "POST",
        headers,
        body: JSON.stringify({
          project_id: projectId,
          compatibility_key: String(formData.get("compatibility_key") || "").trim(),
          min_training_compounds: Number(formData.get("min_training_compounds") || 8),
          min_validation_compounds: Number(formData.get("min_validation_compounds") || 8),
          min_coverage: Number(formData.get("min_coverage") || 0.8),
          max_mae: Number(formData.get("max_mae") || 1.0),
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
      const model = payload.model || {};
      setStatus(`Qualification complete: ${window.SARFormat ? window.SARFormat.humanize(model.model_status || "candidate") : (model.model_status || "candidate")}. Predictions remain scoped to this endpoint and applicability domain. Reloading…`, model.model_status === "internally_validated" ? "success" : "warning");
      window.setTimeout(() => window.location.reload(), 400);
    } catch (error) {
      setStatus(`Qualification failed: ${error.message}`, "error");
      if (submit) submit.disabled = false;
    }
  });
})();
