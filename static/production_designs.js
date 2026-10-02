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

  const designForm = document.querySelector("#productionDesignForm");
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  let designData = { evidence: [] };
  try {
    designData = JSON.parse(document.querySelector("#productionDesignData")?.textContent || "{}");
  } catch (_error) {
    designData = { evidence: [] };
  }

  // Structure: start from the parent, edit SMILES, preview through the RDKit standardizer.
  const parentSelect = designForm?.querySelector("[data-design-parent]");
  const smilesField = designForm?.querySelector("[data-design-smiles]");
  const copyParent = designForm?.querySelector("[data-design-copy-parent]");
  const clearStructure = designForm?.querySelector("[data-design-clear-structure]");
  const parentPreview = designForm?.querySelector("[data-design-parent-preview]");
  const preview = designForm?.querySelector("[data-design-preview]");
  const structureStatus = designForm?.querySelector("[data-design-structure-status]");
  let structureValid = true;
  let previewTimer = null;
  let previewToken = 0;

  const renderStructure = async (smiles, target) => {
    const text = String(smiles || "").trim();
    if (!text) return { ok: true, empty: true };
    try {
      const result = await request("/api/v1/structure/standardize", { structure: text, input_format: "smiles" });
      if (target) target.innerHTML = result.result?.rendered_svg || "<span>No drawing available</span>";
      return { ok: true, result: result.result };
    } catch (error) {
      return { ok: false, message: error.message };
    }
  };

  const parentSmiles = () => parentSelect?.selectedOptions?.[0]?.dataset.smiles || "";

  const updatePreview = async () => {
    const token = ++previewToken;
    const text = String(smilesField?.value || "").trim();
    if (!text) {
      structureValid = true;
      if (preview) preview.innerHTML = "<span>No structure yet</span>";
      if (structureStatus) { structureStatus.textContent = ""; structureStatus.dataset.status = ""; }
      return;
    }
    if (structureStatus) { structureStatus.textContent = "Checking structure…"; structureStatus.dataset.status = ""; }
    const outcome = await renderStructure(text, null);
    if (token !== previewToken) return;
    structureValid = outcome.ok;
    if (!outcome.ok) {
      if (preview) preview.innerHTML = "<span>Not a valid structure</span>";
      if (structureStatus) { structureStatus.textContent = `RDKit could not read this SMILES: ${outcome.message}`; structureStatus.dataset.status = "error"; }
      return;
    }
    if (preview) preview.innerHTML = outcome.result?.rendered_svg || "<span>No drawing available</span>";
    const same = outcome.result?.canonical_smiles && parentSmiles() && text === parentSmiles();
    const warnings = (outcome.result?.warnings || []).map((warning) => String(warning).replaceAll("_", " "));
    if (structureStatus) {
      structureStatus.textContent = same
        ? "Identical to the parent. Edit the SMILES to propose a change."
        : `Valid structure · ${outcome.result?.stereochemistry_status ? (window.SARFormat ? window.SARFormat.humanize(outcome.result.stereochemistry_status) : outcome.result.stereochemistry_status) : "stereo not assessed"}${warnings.length ? ` · ${warnings.join(", ")}` : ""}`;
      structureStatus.dataset.status = same ? "" : "success";
    }
  };

  smilesField?.addEventListener("input", () => {
    window.clearTimeout(previewTimer);
    previewTimer = window.setTimeout(updatePreview, 450);
  });

  // Substituent swap: pick an R-site and a common group; the server edits the structure with RDKit.
  const swap = designForm?.querySelector("[data-design-swap]");
  const siteSelect = swap?.querySelector("[data-design-site]");
  const replacementSelect = swap?.querySelector("[data-design-replacement]");
  const customWrap = swap?.querySelector("[data-design-replacement-custom-wrap]");
  const customField = swap?.querySelector("[data-design-replacement-custom]");
  const applySwap = swap?.querySelector("[data-design-apply-swap]");
  const siteCurrent = swap?.querySelector("[data-design-site-current]");
  const siteHint = siteCurrent?.textContent || "";
  const showCurrentGroup = () => {
    if (!siteCurrent) return;
    const groups = (designData.compoundGroups || {})[parentSelect?.value || ""];
    const site = siteSelect?.value || "";
    siteCurrent.textContent = groups
      ? `${site} on the parent is ${groups[site] || "H"}. ${siteHint}`
      : siteHint;
  };
  replacementSelect?.addEventListener("change", () => {
    const custom = replacementSelect.value === "custom";
    if (customWrap) customWrap.hidden = !custom;
    if (custom) customField?.focus();
  });
  siteSelect?.addEventListener("change", showCurrentGroup);
  applySwap?.addEventListener("click", async () => {
    const replacement = replacementSelect?.value === "custom" ? String(customField?.value || "").trim() : replacementSelect?.value || "";
    const current = String(smilesField?.value || "").trim();
    const parentId = parentSelect?.value || "";
    if (!current && !parentId) {
      if (structureStatus) { structureStatus.textContent = "Choose a parent compound first."; structureStatus.dataset.status = "error"; }
      return;
    }
    if (!replacement) {
      if (structureStatus) { structureStatus.textContent = "Enter the group SMILES with one * attachment, e.g. *C1CC1."; structureStatus.dataset.status = "error"; }
      return;
    }
    applySwap.disabled = true;
    if (structureStatus) { structureStatus.textContent = `Swapping ${siteSelect?.value}…`; structureStatus.dataset.status = ""; }
    try {
      const result = await request("/api/v1/structure/replace-substituent", {
        project_id: projectId,
        compound_id: parentId,
        smiles: current,
        site: siteSelect?.value || "",
        replacement,
      });
      if (smilesField) smilesField.value = result.result?.isomeric_smiles || current;
      await updatePreview();
      const label = replacementSelect?.value === "custom" ? replacement : replacementSelect?.selectedOptions?.[0]?.textContent || replacement;
      if (structureStatus && structureStatus.dataset.status !== "error") structureStatus.textContent = `${siteSelect?.value} → ${label}. ${structureStatus.textContent}`;
    } catch (error) {
      if (structureStatus) { structureStatus.textContent = error.message; structureStatus.dataset.status = "error"; }
    } finally {
      applySwap.disabled = false;
    }
  });

  // Evidence picker: tick saved records instead of typing identifiers.
  const evidenceList = designForm?.querySelector("[data-design-evidence-list]");
  const evidenceAll = designForm?.querySelector("[data-design-evidence-all]");
  const evidenceCount = designForm?.querySelector("[data-design-evidence-count]");
  const selectedEvidence = new Set();
  const syncEvidenceCount = () => {
    if (evidenceCount) evidenceCount.textContent = `${selectedEvidence.size} selected`;
  };
  const renderEvidence = () => {
    if (!evidenceList) return;
    const parentId = parentSelect?.value || "";
    const showAll = Boolean(evidenceAll?.checked);
    const options = (designData.evidence || []).filter((item) => showAll || (parentId && (item.compound_id === parentId || item.other_compound_id === parentId)));
    if (!options.length) {
      evidenceList.innerHTML = `<p class="design-evidence__empty">${parentId || showAll ? "No saved summaries or analysis observations match." : "Choose a parent compound to list its saved results, or show evidence for all compounds."}</p>`;
      return;
    }
    const groups = new Map();
    options.forEach((item) => {
      const key = showAll ? `${item.compound} · ${item.group}` : item.group;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    evidenceList.innerHTML = [...groups.entries()].map(([group, items]) => `<div class="design-evidence__group"><strong>${escapeHtml(group)}</strong>${items.map((item) => `<label class="design-evidence__item"><input type="checkbox" value="${escapeHtml(item.id)}"${selectedEvidence.has(item.id) ? " checked" : ""}> <span>${escapeHtml(item.label)}</span></label>`).join("")}</div>`).join("");
  };
  evidenceList?.addEventListener("change", (event) => {
    const input = event.target.closest("input[type=checkbox]");
    if (!input) return;
    if (input.checked) selectedEvidence.add(input.value); else selectedEvidence.delete(input.value);
    syncEvidenceCount();
  });
  evidenceAll?.addEventListener("change", renderEvidence);

  parentSelect?.addEventListener("change", async () => {
    const smiles = parentSmiles();
    if (copyParent) copyParent.disabled = !smiles;
    if (parentPreview) {
      parentPreview.innerHTML = smiles ? "<span>Loading…</span>" : "<span>No parent selected</span>";
      if (smiles) {
        const outcome = await renderStructure(smiles, parentPreview);
        if (!outcome.ok) parentPreview.innerHTML = "<span>Parent structure unavailable</span>";
      }
    }
    if (smilesField && !smilesField.value.trim() && smiles) {
      smilesField.value = smiles;
      updatePreview();
    }
    showCurrentGroup();
    renderEvidence();
  });
  copyParent?.addEventListener("click", () => {
    if (!smilesField) return;
    smilesField.value = parentSmiles();
    smilesField.focus();
    updatePreview();
  });
  clearStructure?.addEventListener("click", () => {
    if (!smilesField) return;
    smilesField.value = "";
    updatePreview();
  });

  designForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector("button[type=submit]");
    const formData = new FormData(form);
    const manualIds = String(formData.get("evidence_ids") || "").split(",").map((item) => item.trim()).filter(Boolean);
    if (!selectedEvidence.size && !manualIds.length) {
      setStatus("#productionDesignStatus", "Tick at least one supporting result under Supporting evidence.", "error");
      designForm?.querySelector("[data-design-evidence]")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (!structureValid) {
      setStatus("#productionDesignStatus", "Fix or clear the proposed SMILES before saving.", "error");
      return;
    }
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
          evidence_ids: [...new Set([...selectedEvidence, ...String(formData.get("evidence_ids") || "").split(",").map((item) => item.trim()).filter(Boolean)])],
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
