(() => {
  "use strict";

  const root = document.querySelector("[data-production-project]");
  if (!root) return;
  const projectId = root.dataset.productionProject || "";
  if (!projectId) return;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  const request = async (url, payload) => {
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers,
      body: JSON.stringify(payload),
    });
    const text = await response.text();
    let result = {};
    try { result = text ? JSON.parse(text) : {}; } catch (_error) { result = { message: text }; }
    if (!response.ok) throw new Error(result.message || result.error || `Request failed (${response.status})`);
    return result;
  };

  const setStatus = (selector, message, kind = "") => {
    const element = document.querySelector(selector);
    if (!element) return;
    element.textContent = message;
    element.dataset.status = kind;
  };

  document.querySelector("#productionHypothesisForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector("button[type=submit]");
    const formData = new FormData(form);
    if (submit) submit.disabled = true;
    setStatus("#productionHypothesisStatus", "Saving untested hypothesis…");
    try {
      await request("/api/v1/hypotheses", {
        project_id: projectId,
        statement: formData.get("statement"),
        rationale: formData.get("rationale"),
      });
      setStatus("#productionHypothesisStatus", "Hypothesis saved as untested.", "success");
      form.reset();
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      setStatus("#productionHypothesisStatus", `Hypothesis save failed: ${error.message}`, "error");
      if (submit) submit.disabled = false;
    }
  });

  document.querySelector("#productionDesignForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector("button[type=submit]");
    const formData = new FormData(form);
    if (submit) submit.disabled = true;
    setStatus("#productionDesignStatus", "Saving curated candidate…");
    try {
      await request("/api/v1/designs", {
        project_id: projectId,
        candidates: [{
          category: formData.get("category"),
          title: formData.get("title"),
          rationale: formData.get("rationale"),
          hypothesis: formData.get("hypothesis"),
          expected_outcome: formData.get("expected_outcome"),
          uncertainty: formData.get("uncertainty"),
          evidence_ids: String(formData.get("evidence_ids") || "").split(",").map((item) => item.trim()).filter(Boolean),
          novelty_score: 0.5,
          feasibility_status: formData.get("feasibility_status"),
          feasibility_reasons: ["Prepared in the focused Design & Review workspace; qualified review required."],
          information_gain_score: 0.5,
          objective_alignment_score: 0.5,
          parent_compound_id: formData.get("parent_compound_id") || null,
          structure_smiles: formData.get("structure_smiles") || null,
          status: "proposed",
        }],
      });
      setStatus("#productionDesignStatus", "Curated candidate saved; experimental confirmation remains false.", "success");
      form.reset();
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      setStatus("#productionDesignStatus", `Design save failed: ${error.message}`, "error");
      if (submit) submit.disabled = false;
    }
  });
})();
