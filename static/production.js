(() => {
  "use strict";

  const root = document.querySelector("[data-production-project]");
  const projectId = root?.dataset.productionProject;
  if (!projectId) return;

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const status = document.querySelector("#productionActionStatus");
  const preview = document.querySelector("#productionImportPreview");
  const previewText = document.querySelector("#productionImportPreviewText");
  const commitButton = document.querySelector("#productionCommitButton");
  const summaryButton = document.querySelector("#productionSummaryButton");
  const analysisButton = document.querySelector("#productionAnalysisButton");
  const summaryActionAvailable = Boolean(summaryButton && !summaryButton.disabled);
  const analysisActionAvailable = Boolean(analysisButton && !analysisButton.disabled);
  const fileInput = document.querySelector("#productionCsvFile");
  const workbookProfile = document.querySelector("#productionWorkbookProfile");
  const workbookProfileText = document.querySelector("#productionWorkbookProfileText");
  const workbookSheet = document.querySelector("#productionWorkbookSheet");
  const workbookHeaderRow = document.querySelector("#productionWorkbookHeaderRow");
  const workbookDataStartRow = document.querySelector("#productionWorkbookDataStartRow");
  const formulaAcknowledgement = document.querySelector("#productionFormulaAcknowledgement");
  const previewSelectedButton = document.querySelector("#productionPreviewSelected");
  const importProfile = document.querySelector("#productionImportProfile");
  const mappingReview = document.querySelector("#productionMappingReview");
  const mappingReviewText = document.querySelector("#productionMappingReviewText");
  const mappingControls = document.querySelector("#productionMappingControls");
  const previewWithMappingButton = document.querySelector("#productionPreviewWithMapping");
  const importRows = document.querySelector("#productionImportRows");
  const importRowsBody = document.querySelector("#productionImportRowsBody");
  const importRowsMore = document.querySelector("#productionImportRowsMore");
  let pendingImportId = null;
  let pendingFile = null;
  let pendingPreview = null;
  let visibleRowCount = 25;
  const pendingImportStorageKey = `sar-workbench:pending-import:${projectId}`;

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const setStatus = (message, kind = "") => {
    if (!status) return;
    status.textContent = message;
    status.dataset.status = kind;
  };

  const request = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers,
    });
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = { message: text };
      }
    }
    if (!response.ok) {
      const error = new Error(payload.message || payload.error || `Request failed (${response.status})`);
      error.payload = payload;
      throw error;
    }
    return payload;
  };

  const postJson = (url, payload) => request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const setButtonsBusy = (busy) => {
    document.querySelectorAll("#production-actions button").forEach((button) => {
      button.disabled = busy;
    });
    if (!busy) setPendingImportActionsDisabled(hasPendingImportState());
  };

  const rememberPendingImport = (importId) => {
    pendingImportId = importId;
    setPendingImportActionsDisabled(true);
    try {
      window.sessionStorage.setItem(pendingImportStorageKey, importId);
    } catch (_error) {
      // Session storage may be unavailable; the in-memory state still protects this page.
    }
  };

  const forgetPendingImport = () => {
    pendingImportId = null;
    try {
      window.sessionStorage.removeItem(pendingImportStorageKey);
    } catch (_error) {
      // Session storage may be unavailable.
    }
  };

  const hasPendingImportState = () => Boolean(pendingFile || pendingImportId || pendingPreview);

  const setPendingImportActionsDisabled = (disabled) => {
    if (summaryActionAvailable && summaryButton) summaryButton.disabled = disabled;
    if (analysisActionAvailable && analysisButton) analysisButton.disabled = disabled;
  };

  const requireNoPendingImport = (action) => {
    if (!hasPendingImportState()) return true;
    setStatus(`Save accepted results before ${action}. The checked file is still open above; nothing has been saved yet.`, "error");
    return false;
  };

  const resetActionButtonLabels = () => {
    const summaryButton = document.querySelector("#productionSummaryButton");
    const analysisButton = document.querySelector("#productionAnalysisButton");
    if (summaryButton) summaryButton.textContent = "Build result summaries";
    if (analysisButton) analysisButton.textContent = "Find patterns";
  };

  const reloadAfter = (message) => {
    const statusKind = message.includes(" Failed:") ? "error" : "success";
    if (hasPendingImportState()) {
      setButtonsBusy(false);
      resetActionButtonLabels();
      setStatus(`${message} Your selected or checked file is still open above. Save accepted results before refreshing this page.`, statusKind);
      return;
    }
    setStatus(`${message} Reloading the persisted read model…`, statusKind);
    window.setTimeout(() => window.location.reload(), 700);
  };

  const isBlockingMappingIssue = (issue) => issue?.severity ? issue.severity === "blocking" : true;

  const renderProfileSummary = (profile) => {
    if (!importProfile) return;
    importProfile.replaceChildren();
    const summary = document.createElement("div");
    const sheets = Array.isArray(profile?.sheets) ? profile.sheets : [];
    const sheetText = sheets.map((sheet) => {
      const dimensions = sheet.row_count != null && sheet.column_count != null
        ? ` ${sheet.row_count} rows × ${sheet.column_count} columns`
        : "";
      return `${sheet.name || "Unnamed worksheet"}${sheet.state === "hidden" ? " (hidden)" : ""}${dimensions}`;
    });
    summary.textContent = `${profile?.file_format || "tabular"} profile${profile?.byte_size ? ` · ${profile.byte_size} bytes` : ""}${sheetText.length ? ` · ${sheetText.join("; ")}` : ""}`;
    importProfile.append(summary);
    if (Array.isArray(profile?.warnings) && profile.warnings.length) {
      const warning = document.createElement("div");
      warning.className = "cleaning-warning";
      warning.textContent = `Warnings: ${profile.warnings.join("; ")}`;
      importProfile.append(warning);
    }
    importProfile.hidden = false;
  };

  const renderWorkbookProfile = (profile) => {
    if (!workbookProfile || !workbookSheet) return;
    const sheets = Array.isArray(profile.sheets) ? profile.sheets : [];
    workbookSheet.replaceChildren();
    sheets.forEach((sheet) => {
      const option = document.createElement("option");
      option.value = sheet.name || "";
      option.textContent = `${sheet.name || "Unnamed worksheet"}${sheet.state === "hidden" ? " (hidden)" : ""}`;
      workbookSheet.append(option);
    });
    const visible = sheets.filter((sheet) => sheet.state !== "hidden");
    if (visible.length === 1) workbookSheet.value = visible[0].name;
    const warningText = Array.isArray(profile.warnings) && profile.warnings.length
      ? ` Warnings: ${profile.warnings.join("; ")}.`
      : "";
    const sheetText = sheets.map((sheet) => {
      const dimensions = sheet.row_count != null && sheet.column_count != null
        ? ` ${sheet.row_count}×${sheet.column_count}`
        : "";
      return `${sheet.name || "Unnamed"}${dimensions}`;
    });
    if (workbookProfileText) {
      workbookProfileText.textContent = `${sheets.length} worksheet${sheets.length === 1 ? "" : "s"} found${sheetText.length ? ` (${sheetText.join(", ")})` : ""}. Select the source sheet and confirm the header/data rows.${warningText}`;
    }
    workbookProfile.hidden = false;
  };

  const renderMappingReview = (result) => {
    if (!mappingReview || !mappingControls || !previewWithMappingButton) return;
    const issues = Array.isArray(result.mapping_issues) ? result.mapping_issues : [];
    const options = result.mapping_options || {};
    mappingControls.replaceChildren();
    let selectableFields = 0;
    Object.entries(options).forEach(([field, fieldOptions]) => {
      const current = result.mapping?.[field];
      if (!Array.isArray(fieldOptions) || fieldOptions.length < 2 || current) return;
      selectableFields += 1;
      const label = document.createElement("label");
      label.textContent = `${field} source column`;
      const select = document.createElement("select");
      select.dataset.mappingField = field;
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "Select a column";
      select.append(empty);
      fieldOptions.forEach((optionData) => {
        const option = document.createElement("option");
        option.value = optionData.key;
        option.textContent = optionData.label;
        select.append(option);
      });
      label.append(select);
      mappingControls.append(label);
    });
    if (!issues.length && !selectableFields) {
      mappingReview.hidden = true;
      return;
    }
    const blockingCount = issues.filter(isBlockingMappingIssue).length;
    if (mappingReviewText) {
      mappingReviewText.textContent = `${blockingCount} column choice${blockingCount === 1 ? " needs" : "s need"} attention. Choose the intended source column; other warnings remain visible in the report.`;
    }
    previewWithMappingButton.hidden = selectableFields === 0;
    mappingReview.hidden = false;
  };

  const renderCleaningRows = (rows) => {
    if (!importRows || !importRowsBody || !importRowsMore) return;
    importRowsBody.replaceChildren();
    const visibleRows = rows.slice(0, visibleRowCount);
    visibleRows.forEach((row) => {
      const tableRow = document.createElement("tr");
      const sourceCell = document.createElement("td");
      sourceCell.textContent = row.source_row_id || "—";
      const statusCell = document.createElement("td");
      statusCell.className = row.status === "accepted" ? "cleaning-accepted" : "cleaning-rejected";
      statusCell.textContent = row.status === "accepted" ? "Ready to save" : row.status === "rejected" ? "Needs attention" : "Review";
      const locationCell = document.createElement("td");
      const location = row.source_location || {};
      const addresses = Object.values(location.cells || {}).slice(0, 8);
      locationCell.textContent = location.sheet
        ? `${location.sheet}!${addresses.join(", ") || `row ${location.row || row.source_row_id}`}`
        : addresses.join(", ") || `row ${location.row || row.source_row_id || "—"}`;
      const issuesCell = document.createElement("td");
      const issues = [
        ...(row.errors || []).map((error) => `Error: ${error.message || error}`),
        ...(row.warnings || []).map((warning) => `Warning: ${warning}`),
      ];
      if (!issues.length) issues.push("No issues in this row");
      const list = document.createElement("ul");
      issues.forEach((issue) => {
        const item = document.createElement("li");
        item.textContent = issue;
        list.append(item);
      });
      issuesCell.append(list);
      tableRow.append(sourceCell, statusCell, locationCell, issuesCell);
      importRowsBody.append(tableRow);
    });
    importRows.hidden = rows.length === 0;
    importRowsMore.hidden = rows.length <= visibleRowCount;
  };

  const renderCleaningReport = (result) => {
    pendingPreview = result;
    visibleRowCount = 25;
    renderProfileSummary(result.profile || {});
    renderMappingReview(result);
    renderCleaningRows(Array.isArray(result.rows) ? result.rows : []);
    const blockingMappingIssues = (result.mapping_issues || []).some(isBlockingMappingIssue);
    if (preview) preview.hidden = false;
    if (previewText) {
      const profileWarnings = Array.isArray(result.profile?.warnings) ? result.profile.warnings.length : 0;
      const issueCount = Array.isArray(result.mapping_issues) ? result.mapping_issues.length : 0;
      previewText.textContent = `${result.filename}: ${result.accepted_count} row${result.accepted_count === 1 ? " is" : "s are"} ready, ${result.rejected_count} need${result.rejected_count === 1 ? "s" : ""} attention, and ${result.warning_count || 0} warning${(result.warning_count || 0) === 1 ? "" : "s"}${issueCount ? `, plus ${issueCount} column choice${issueCount === 1 ? "" : "s"} to review` : ""}. Check the source locations before saving accepted results.`;
      if (profileWarnings) previewText.textContent += ` Workbook profile warnings: ${profileWarnings}.`;
    }
    if (commitButton) {
      commitButton.hidden = !(result.accepted_count > 0 && !blockingMappingIssues);
      commitButton.disabled = false;
    }
  };

  const loadExistingPreview = async (importId) => {
    const existing = await request(`/api/v1/imports/${encodeURIComponent(importId)}`);
    const rows = Array.isArray(existing.rows)
      ? existing.rows.map((row) => ({
        ...row,
        errors: row.validation?.errors || [],
        warnings: row.validation?.warnings || [],
      }))
      : [];
    rememberPendingImport(importId);
    renderCleaningReport({
      ...existing,
      filename: existing.source_document?.filename || "Existing staged upload",
      file_format: existing.source_document?.file_format || "unknown",
      rows,
    });
    setStatus("The existing checked file is loaded. No new import was created; review it or save the rows that are ready.", "success");
  };

  const submitPreview = async (file, selection = {}) => {
    const form = document.querySelector("#productionImportForm");
    if (!form || !file) return;
    setButtonsBusy(true);
    setStatus(`Checking ${file.name}…`);
    try {
      const body = new FormData(form);
      body.set("project_id", projectId);
      if (selection.sheetName) body.set("sheet_name", selection.sheetName);
      if (selection.headerRow) body.set("header_row", String(selection.headerRow));
      if (selection.dataStartRow) body.set("data_start_row", String(selection.dataStartRow));
      if (selection.mappingOverrides) body.set("mapping_overrides", JSON.stringify(selection.mappingOverrides));
      body.set("formula_acknowledged", selection.formulaAcknowledged ? "true" : "false");
      const result = await request("/api/v1/imports/preview", {
        method: "POST",
        body,
      });
      rememberPendingImport(result.import_id);
      renderCleaningReport(result);
      setStatus("File checked. No saved results were created yet.", "success");
    } catch (error) {
      const duplicate = error.payload?.error === "duplicate_import";
      if (duplicate && error.payload.import_id && error.payload.import_status !== "committed") {
        try {
          await loadExistingPreview(error.payload.import_id);
          return;
        } catch (loadError) {
          setStatus(`The existing checked file could not be loaded: ${loadError.message}`, "error");
          return;
        }
      }
      const profile = error.payload?.profile;
      if (profile) {
        renderWorkbookProfile(profile);
        renderProfileSummary(profile);
      }
      setStatus(`File check failed: ${error.message}`, "error");
    } finally {
      setButtonsBusy(false);
    }
  };

  fileInput?.addEventListener("change", () => {
    pendingFile = fileInput.files?.[0] || null;
    setPendingImportActionsDisabled(Boolean(pendingFile));
    forgetPendingImport();
    pendingPreview = null;
    if (formulaAcknowledgement) formulaAcknowledgement.checked = false;
    if (preview) preview.hidden = true;
    if (importProfile) importProfile.hidden = true;
    if (mappingReview) mappingReview.hidden = true;
    if (workbookProfile && pendingFile && !/\.xlsx$/i.test(pendingFile.name)) workbookProfile.hidden = true;
  });

  document.querySelector("#productionImportForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput?.files?.[0];
    if (!file) {
      setStatus("Choose a CSV, TSV, or XLSX file before previewing.", "error");
      return;
    }
    pendingFile = file;
    if (/\.xlsx$/i.test(file.name)) {
      setButtonsBusy(true);
      setStatus(`Looking at the sheet structure in ${file.name}…`);
      try {
        const result = await request("/api/v1/imports/profile", {
          method: "POST",
          body: new FormData(event.currentTarget),
        });
        renderWorkbookProfile(result.profile || {});
        setStatus("Sheet details loaded. Confirm the sheet and row numbers, then check it.", "success");
      } catch (error) {
        const profile = error.payload?.profile;
        if (profile) {
          renderWorkbookProfile(profile);
          renderProfileSummary(profile);
        }
        setStatus(`Sheet check failed: ${error.message}`, "error");
      } finally {
        setButtonsBusy(false);
      }
      return;
    }
    await submitPreview(file);
  });

  previewSelectedButton?.addEventListener("click", async () => {
    if (!pendingFile || !workbookSheet) {
      setStatus("Choose a sheet before checking the workbook.", "error");
      return;
    }
    await submitPreview(pendingFile, {
      sheetName: workbookSheet.value,
      headerRow: Number(workbookHeaderRow?.value || 1),
      dataStartRow: Number(workbookDataStartRow?.value || 2),
      formulaAcknowledged: Boolean(formulaAcknowledgement?.checked),
    });
  });

  previewWithMappingButton?.addEventListener("click", async () => {
    if (!pendingFile || !mappingControls) return;
    const mappingOverrides = {};
    mappingControls.querySelectorAll("select[data-mapping-field]").forEach((select) => {
      if (select.value) mappingOverrides[select.dataset.mappingField] = select.value;
    });
    if (!Object.keys(mappingOverrides).length) {
      setStatus("Choose a source column for each unclear field.", "error");
      return;
    }
    await submitPreview(pendingFile, {
      sheetName: workbookSheet?.value,
      headerRow: Number(workbookHeaderRow?.value || 1),
      dataStartRow: Number(workbookDataStartRow?.value || 2),
      mappingOverrides,
      formulaAcknowledged: Boolean(formulaAcknowledgement?.checked),
    });
  });

  importRowsMore?.addEventListener("click", () => {
    visibleRowCount += 25;
    renderCleaningRows(Array.isArray(pendingPreview?.rows) ? pendingPreview.rows : []);
  });

  commitButton?.addEventListener("click", async () => {
    if (!pendingImportId) return;
    setButtonsBusy(true);
    setStatus("Saving the rows that are ready…");
    try {
      const result = await postJson(`/api/v1/imports/${encodeURIComponent(pendingImportId)}/commit`, {
        project_id: projectId,
      });
      forgetPendingImport();
      pendingFile = null;
      pendingPreview = null;
      if (fileInput) fileInput.value = "";
      reloadAfter(`${result.inserted_measurements || 0} uploaded result${(result.inserted_measurements || 0) === 1 ? "" : "s"} saved.`);
    } catch (error) {
      setStatus(`Import commit failed: ${error.message}`, "error");
      setButtonsBusy(false);
    }
  });

  document.querySelector("#productionSummaryButton")?.addEventListener("click", async (event) => {
    if (!requireNoPendingImport("building result summaries")) return;
    setButtonsBusy(true);
    event.currentTarget.textContent = "Building…";
    setStatus("Building grouped results for comparison…");
    try {
      const result = await postJson("/api/v1/measurement-summaries/project", { project_id: projectId });
      reloadAfter(`Built ${result.count || 0} grouped result${(result.count || 0) === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(`Summary generation failed: ${error.message}`, "error");
      setButtonsBusy(false);
      event.currentTarget.textContent = "Build result summaries";
    }
  });

  const analysisRequests = [
    ["properties", "/api/v1/analysis/properties", { project_id: projectId }],
    ["MMP", "/api/v1/analysis/mmp", { project_id: projectId }],
    ["R-group", "/api/v1/analysis/rgroup", { project_id: projectId, scaffold_smarts: "c1ccccc1" }],
    ["activity cliffs", "/api/v1/analysis/activity-cliffs", { project_id: projectId, effect_threshold: 0.5, similarity_threshold: 0.8 }],
    ["selectivity", "/api/v1/analysis/selectivity", {
      project_id: projectId,
      primary_compatibility_key: "IC50:import-v1",
      comparator_compatibility_key: "OffTarget:import-v1",
      selectivity_threshold: 1.0,
    }],
    ["cellular translation", "/api/v1/analysis/cellular-translation", {
      project_id: projectId,
      biochemical_compatibility_key: "IC50:import-v1",
      cellular_compatibility_key: "Cellular:import-v1",
      translation_loss_threshold: 1.0,
    }],
    ["ADME", "/api/v1/analysis/adme", {
      project_id: projectId,
      compatibility_keys: ["CLint:import-v1", "Papp:import-v1", "Solubility:import-v1", "CYP3A4:import-v1"],
    }],
    ["Pareto", "/api/v1/analysis/pareto", {
      project_id: projectId,
      objectives: [
        { source: "summary", key: "IC50:import-v1", direction: "maximize", label: "Observed biochemical potency" },
        { source: "property", key: "logp", direction: "minimize", label: "Derived logP" },
      ],
    }],
    ["contradictions", "/api/v1/analysis/contradictions", { project_id: projectId, value_tolerance: 0.5 }],
    ["information-gap analysis", "/api/v1/analysis/information-gain", {
      project_id: projectId,
      contexts: [
        { compatibility_key: "Cellular:import-v1", priority: 1.0, minimum_replicates: 2 },
        { compatibility_key: "CYP3A4:import-v1", priority: 0.7, minimum_replicates: 2 },
      ],
    }],
  ];

  document.querySelector("#productionAnalysisButton")?.addEventListener("click", async (event) => {
    if (!requireNoPendingImport("finding patterns")) return;
    setButtonsBusy(true);
    event.currentTarget.textContent = "Checking…";
    const completed = [];
    const failed = [];
    let informationGainRunId = null;
    const analysisDisplayNames = {
      properties: "chemical properties",
      MMP: "compound-to-compound changes",
      "R-group": "structure changes",
      "activity cliffs": "unexpected differences",
      selectivity: "one-test versus another",
      "cellular translation": "lab-to-cell results",
      ADME: "other test results",
      Pareto: "trade-offs",
      contradictions: "conflicts",
      "information-gap analysis": "evidence gaps",
    };
    for (const [label, url, payload] of analysisRequests) {
      setStatus(`Checking ${analysisDisplayNames[label] || label}…`);
      try {
        const result = await postJson(url, payload);
        completed.push(label);
        if (label === "information-gap analysis") {
          informationGainRunId = result.analysis_run_id || result.run_id || null;
        }
      } catch (error) {
        failed.push(`${label}: ${error.message}`);
      }
    }
    if (informationGainRunId) {
      setStatus("Preparing suggested questions from evidence gaps; they will need review…");
      try {
        await postJson("/api/v1/recommendations/generate", {
          project_id: projectId,
          information_gain_run_id: informationGainRunId,
          limit: 10,
        });
        completed.push("generated recommendations");
      } catch (error) {
        failed.push(`generated recommendations: ${error.message}`);
      }
    }
    const suffix = failed.length ? ` Failed: ${failed.join(" | ")}` : "";
    reloadAfter(`Finished ${completed.length} pattern-check action${completed.length === 1 ? "" : "s"}.${suffix}`);
  });

  const renderRecommendations = (recommendations) => {
    const container = document.querySelector("#productionRecommendations");
    if (!container) return;
    if (!recommendations.length) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = `
      <div class="section-kicker">SUGGESTED QUESTIONS <span class="status-badge status-badge--warn">review required</span></div>
      <p class="panel-subtitle" style="margin-bottom: 9px;">These prompts come from calculated evidence-gap checks. Review the cited results; none is an experimentally confirmed answer.</p>
      <div style="display: grid; gap: 8px;">
        ${recommendations.map((recommendation) => `
          <div style="padding: 12px; border: 1px solid var(--line); border-radius: 9px; background: var(--bg-elevated);">
            <strong>${escapeHtml(recommendation.title || recommendation.recommendation_type || recommendation.id)}</strong>
            <div style="margin-top: 4px; color: var(--text-muted); font-size: 11px;">${escapeHtml(recommendation.rationale || recommendation.description || "Evidence-gap recommendation")}</div>
            <div style="display: flex; flex-wrap: wrap; gap: 7px; align-items: center; margin-top: 9px;">
              <span class="status-badge status-badge--warn">${escapeHtml(recommendation.review_status || recommendation.status || "pending_review")}</span>
              <button class="button button--secondary button--small" type="button" data-review-recommendation="${escapeHtml(recommendation.id)}" data-review-status="approved">Approve for review</button>
              <button class="button button--secondary button--small" type="button" data-review-recommendation="${escapeHtml(recommendation.id)}" data-review-status="rejected">Reject</button>
            </div>
          </div>
        `).join("")}
      </div>`;
  };

  const loadRecommendations = async () => {
    try {
      const result = await request(`/api/v1/recommendations?project_id=${encodeURIComponent(projectId)}`);
      renderRecommendations(result.recommendations || []);
    } catch (_error) {
      // A missing recommendation run is a normal initial state.
    }
  };

  document.querySelector("#productionRecommendations")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-review-recommendation]");
    if (!button) return;
    button.disabled = true;
    try {
      await postJson(`/api/v1/recommendations/${encodeURIComponent(button.dataset.reviewRecommendation)}/review`, {
        status: button.dataset.reviewStatus,
        review_note: "Reviewed in the local production-shaped workspace.",
      });
      await loadRecommendations();
      setStatus("Recommendation review saved; experimental confirmation remains false.", "success");
    } catch (error) {
      button.disabled = false;
      setStatus(`Recommendation review failed: ${error.message}`, "error");
    }
  });

  const restorePendingPreview = async () => {
    let importId = null;
    try {
      importId = window.sessionStorage.getItem(pendingImportStorageKey);
    } catch (_error) {
      // Session storage may be unavailable.
    }
    if (!importId) return;
    try {
      await loadExistingPreview(importId);
      setStatus("Your checked file was restored. Review it and save accepted results when ready.", "success");
    } catch (error) {
      forgetPendingImport();
      setStatus(`The previous checked file could not be restored: ${error.message}. Choose the file again.`, "error");
    }
  };

  restorePendingPreview();
  loadRecommendations();
})();
