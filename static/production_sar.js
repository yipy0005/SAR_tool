(() => {
  "use strict";

  const root = document.querySelector("[data-production-project]");
  const projectId = root?.dataset.productionProject || "";
  const form = document.querySelector("#productionSarForm");
  if (!projectId || !form) return;

  const endpoint = document.querySelector("#productionSarEndpoint");
  const compoundField = document.querySelector("#productionSarCompoundField");
  const compound = document.querySelector("#productionSarCompound");
  const pairField = document.querySelector("#productionSarPairField");
  const pairFile = document.querySelector("#productionSarPairFile");
  const pairText = document.querySelector("#productionSarPairText");
  const pairCount = document.querySelector("#productionSarPairCount");
  const pairSaved = document.querySelector("#productionSarPairSaved");
  const runButton = document.querySelector("#productionSarRun");
  const status = document.querySelector("#productionSarStatus");
  const results = document.querySelector("#productionSarResults");
  const summary = document.querySelector("#productionSarResultsSummary");
  const guide = document.querySelector("#productionSarResultsGuide");
  const badge = document.querySelector("#productionSarResultsBadge");
  const grid = document.querySelector("#productionSarSeriesGrid");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  let latestSeries = [];
  let savedRelationships = [];
  let pendingRelationshipText = "";

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const endpointLabel = (key) => String(key || "endpoint").split(":", 1)[0] || "endpoint";

  const F = window.SARFormat;
  const formatValue = (value, unit, qualifier = "") => F.measure(value, unit, qualifier);
  const formatDelta = (value, unit) => F.delta(value, unit);

  const setStatus = (message, kind = "") => {
    if (!status) return;
    status.textContent = message;
    status.dataset.status = kind;
  };

  const selectedMode = () => form.querySelector('input[name="mode"]:checked')?.value || "discover";

  const syncMode = () => {
    const mode = selectedMode();
    const selected = mode === "selected";
    const prodrug = mode === "prodrug";
    if (compoundField) compoundField.hidden = !selected;
    if (pairField) pairField.hidden = !prodrug;
    if (compound) compound.required = selected;
    if (prodrug && !savedRelationships.length) loadSavedRelationships();
  };

  const effectFor = (member, item) => {
    if (member.is_reference) return { title: "Reference", detail: "baseline", tone: "reference" };
    if (member.endpoint_delta === null || member.endpoint_delta === undefined) {
      return { title: "No delta", detail: F.humanize(member.summary_state || "incomplete"), tone: "unavailable" };
    }
    const delta = formatDelta(member.endpoint_delta, item.unit);
    if (member.endpoint_effect === "better") return { title: delta, detail: "better in this direction", tone: "better" };
    if (member.endpoint_effect === "worse") return { title: delta, detail: "worse in this direction", tone: "worse" };
    if (member.endpoint_effect === "no_change") return { title: delta, detail: "no observed change", tone: "neutral" };
    return { title: delta, detail: "observed change", tone: "neutral" };
  };

  const positionCell = (member, position) => {
    const fragment = member.structural_change?.by_position?.[position.label];
    const status = member.structural_change?.status;
    if (status === "unmatched") return `<td class="sar-rtable__r sar-rtable__r--na" title="Structure could not be mapped onto the scaffold">?</td>`;
    if (!fragment) return `<td class="sar-rtable__r sar-rtable__r--h">H</td>`;
    const smiles = fragment.attached_smiles || fragment.smiles;
    // Named groups read best as text (CF₃, OMe); unnamed ones are drawn, with the SMILES on hover.
    const body = fragment.name
      ? `<strong>${escapeHtml(fragment.name)}</strong>`
      : fragment.svg
        ? `<span class="sar-rtable__fragment" role="img" aria-label="${escapeHtml(smiles)}">${fragment.svg}</span>`
        : `<code>${escapeHtml(fragment.smiles)}</code>`;
    return `<td class="sar-rtable__r" title="${escapeHtml(smiles)}">${body}</td>`;
  };

  const memberMarkup = (member, item) => {
    const endpointName = endpointLabel(item.endpoint_key);
    const state = F.humanize(member.summary_state || "missing");
    const value = formatValue(member.summary_value, member.canonical_unit || item.unit, member.summary_qualifier);
    const selected = member.compound_id === compound?.value;
    const effect = effectFor(member, item);
    const structure = member.rendered_svg
      ? `<span class="sar-rtable__structure" role="img" aria-label="Structure of ${escapeHtml(member.registration_id)}" title="${escapeHtml(member.isomeric_smiles)}">${member.rendered_svg}</span>`
      : `<span class="sar-rtable__structure sar-rtable__structure--empty" aria-hidden="true">—</span>`;
    const positions = (item.positions || []).map((position) => positionCell(member, position)).join("");
    return `<tr class="sar-rtable__row sar-rtable__row--${escapeHtml(effect.tone)}${selected ? " is-selected" : ""}"><th scope="row"><strong>${escapeHtml(member.registration_id)}</strong><small>${member.is_reference ? "reference" : escapeHtml(state)}</small></th><td class="sar-rtable__structure-cell">${structure}</td>${positions}<td class="numeric sar-rtable__value">${escapeHtml(value)}</td><td class="numeric sar-rtable__effect sar-rtable__effect--${escapeHtml(effect.tone)}"><strong>${escapeHtml(effect.title)}</strong><small>${escapeHtml(effect.detail)}</small></td><td><button class="button button--secondary button--small sar-rtable__reference-action" type="button" data-sar-selected-compound="${escapeHtml(member.compound_id)}" aria-label="Set ${escapeHtml(member.registration_id)} as the SAR reference and rerun the local series analysis">Set as SAR reference</button></td></tr>`;
  };

  const bindSeriesActions = () => {
    grid.querySelectorAll("[data-sar-selected-compound]").forEach((button) => {
      button.addEventListener("click", () => {
        const registrationId = button.closest("tr")?.querySelector("th strong")?.textContent || "selected compound";
        if (compound) compound.value = button.dataset.sarSelectedCompound || "";
        const selectedInput = form.querySelector('input[name="mode"][value="selected"]');
        if (selectedInput) selectedInput.checked = true;
        syncMode();
        setStatus(`Setting ${registrationId} as the SAR reference and rerunning the local series…`);
        form.requestSubmit();
      });
    });

    grid.querySelectorAll("[data-sar-save-series]").forEach((button, index) => {
      button.addEventListener("click", async () => {
        const item = latestSeries[index];
        const card = button.closest(".production-sar-series-card");
        const saveStatus = card?.querySelector(".production-sar-series-card__save-status");
        if (!item || !Array.isArray(item.members) || !item.members.length) return;
        button.disabled = true;
        if (saveStatus) saveStatus.textContent = "Saving reviewed membership…";
        try {
          const headers = { "Content-Type": "application/json" };
          if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
          const endpointName = endpointLabel(item.endpoint_key || "SAR");
          const response = await fetch("/api/v1/series", {
            method: "POST",
            headers,
            body: JSON.stringify({
              project_id: projectId,
              name: `SAR · ${endpointName} · ${item.series_id}`,
              description: `Observed scaffold series discovered from ${item.endpoint_key}.`,
              rationale: `Generated by ${item.membership_source} using ${item.evidence_boundary}`,
              membership_source: "analysis_derived",
              membership_status: "included",
              compound_ids: item.members.map((member) => member.compound_id),
            }),
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
          button.textContent = "Series saved";
          if (saveStatus) saveStatus.textContent = "Persisted as an analysis-derived membership.";
        } catch (error) {
          button.disabled = false;
          if (saveStatus) saveStatus.textContent = `Could not save series: ${error.message}`;
        }
      });
    });
  };

  const apiJson = async (url, body = null) => {
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch(url, {
      method: body === null ? "GET" : "POST",
      credentials: "same-origin",
      headers,
      ...(body === null ? {} : { body: JSON.stringify(body) }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
    return payload;
  };

  const pairStructure = (compound, role) => {
    const svg = compound?.rendered_svg
      ? `<span class="production-sar-pair__structure" aria-hidden="true">${compound.rendered_svg}</span>`
      : `<span class="production-sar-pair__structure production-sar-pair__structure--empty" aria-hidden="true">—</span>`;
    return `<div class="production-sar-pair__form"><span class="production-sar-pair__role">${escapeHtml(role)}</span>${svg}<strong>${escapeHtml(compound?.registration_id || "Unknown compound")}</strong></div>`;
  };

  const relationshipStatusLabel = (status) => ({
    complete: "Exact pair",
    missing_prodrug: "Prodrug result missing",
    missing_active: "Active-form result missing",
    prodrug_non_exact: "Prodrug result is non-exact",
    active_non_exact: "Active-form result is non-exact",
    unit_mismatch: "Units cannot be compared",
  }[status] || "Needs review");

  const renderPairPreview = (preview, endpointName) => {
    if (!results || !grid) return;
    results.hidden = false;
    if (badge) badge.textContent = `${preview.valid_count || 0} valid · ${preview.invalid_count || 0} to fix`;
    if (summary) summary.textContent = "Review the full pair mapping once, then compare every valid prodrug–active relationship together.";
    if (guide) {
      guide.hidden = false;
      guide.innerHTML = `<strong>Bulk pair review</strong><span>Each row becomes one project-scoped <b>prodrug → active form</b> relationship for the ${escapeHtml(endpointName)} comparison.</span><small>Nothing is saved until you choose “Save mapping and compare”.</small>`;
    }
    const validRows = (preview.valid || []).map((item) => `<tr><td>${escapeHtml(item.prodrug.registration_id)}</td><td>→</td><td>${escapeHtml(item.active.registration_id)}</td><td><span class="compound-pill compound-pill--good">ready</span></td></tr>`).join("");
    const invalidRows = (preview.invalid || []).map((item) => `<tr class="production-sar-pair-preview__invalid"><td>${escapeHtml(item.prodrug_token || "—")}</td><td>→</td><td>${escapeHtml(item.active_token || "—")}</td><td>${escapeHtml((item.errors || []).join(" "))}</td></tr>`).join("");
    const canSave = Number(preview.valid_count || 0) > 0 && Number(preview.invalid_count || 0) === 0;
    grid.innerHTML = `<section class="panel production-sar-pair-preview"><div class="panel-header"><div><div class="section-kicker">PAIR MAPPING REVIEW</div><h3>Review ${preview.total_count || 0} relationships before comparing ${escapeHtml(endpointName)}</h3><p class="panel-subtitle">${preview.valid_count || 0} valid rows · ${preview.invalid_count || 0} rows need correction.</p></div></div><div class="production-sar-pair-preview__table-wrap"><table class="production-sar-pair-preview__table"><thead><tr><th>Prodrug</th><th></th><th>Active form</th><th>Mapping status</th></tr></thead><tbody>${validRows}${invalidRows || ""}</tbody></table></div><div class="production-sar-pair-preview__actions"><button class="button button--primary button--small" type="button" data-sar-save-pairs${canSave ? "" : " disabled"}>Save mapping and compare all pairs</button><span class="production-sar-pair-preview__status" role="status" aria-live="polite">${canSave ? "All rows are ready for review." : "Fix the highlighted rows, then review again."}</span></div></section>`;
    grid.querySelector("[data-sar-save-pairs]")?.addEventListener("click", async (buttonEvent) => {
      const button = buttonEvent.currentTarget;
      const saveStatus = grid.querySelector(".production-sar-pair-preview__status");
      button.disabled = true;
      if (saveStatus) saveStatus.textContent = "Saving the project-scoped mapping…";
      try {
        const saved = await apiJson("/api/v1/compound-relationships", { project_id: projectId, pair_text: pendingRelationshipText });
        savedRelationships = Array.isArray(saved.relationships) ? saved.relationships : [];
        if (pairCount) pairCount.textContent = `${savedRelationships.length} saved pair${savedRelationships.length === 1 ? "" : "s"}`;
        if (pairSaved) pairSaved.textContent = "Mapping saved with review status: needs review.";
        const comparison = await apiJson("/api/v1/analysis/prodrug-comparison", { project_id: projectId, endpoint_key: endpoint?.value || "" });
        renderProdrugComparison(comparison);
        setStatus(`Compared ${comparison.pair_count || 0} linked prodrug–active pairs.`, "success");
      } catch (error) {
        button.disabled = false;
        if (saveStatus) saveStatus.textContent = `Could not save the mapping: ${error.message}`;
      }
    });
  };

  const renderProdrugComparison = (payload) => {
    const pairs = Array.isArray(payload.pairs) ? payload.pairs : [];
    if (!results || !grid) return;
    results.hidden = false;
    if (badge) badge.textContent = `${payload.complete_count || 0}/${payload.pair_count || 0} exact pairs`;
    if (summary) summary.textContent = `${payload.complete_count || 0} pairs have exact compatible ${endpointLabel(payload.endpoint_key)} results; incomplete evidence remains visible below.`;
    if (guide) {
      guide.hidden = false;
      guide.innerHTML = `<strong>How to read this comparison</strong><span>Each row is one saved relationship: <b>prodrug → active form → observed endpoint difference</b>.</span><small>Exact deltas require matching units and exact observed summaries. The relationship mapping remains flagged for review.</small>`;
    }
    if (!pairs.length) {
      grid.innerHTML = `<div class="empty-state production-sar-empty"><strong>No prodrug–active pairs are saved yet.</strong><span>Choose Compare prodrug–active pairs, paste or upload the two-column mapping, and review it before saving.</span></div>`;
      return;
    }
    const endpointName = endpointLabel(payload.endpoint_key);
    const rows = pairs.map((pair) => {
      const comparison = pair.comparison || {};
      const status = relationshipStatusLabel(comparison.status);
      const prodrugValue = formatValue(pair.prodrug?.result?.value, pair.prodrug?.result?.unit);
      const activeValue = formatValue(pair.active?.result?.value, pair.active?.result?.unit);
      const delta = comparison.delta === null || comparison.delta === undefined ? "—" : formatDelta(comparison.delta, comparison.unit);
      const effect = comparison.effect === "active_better" ? "active form better in this direction" : comparison.effect === "active_lower" ? "active form lower in this direction" : comparison.effect === "no_change" ? "no observed difference" : comparison.effect === "observed_change" ? "observed change" : "not comparable";
      const tone = comparison.status === "complete" ? (comparison.effect === "active_better" ? "better" : "neutral") : "unavailable";
      const sourceCount = (pair.prodrug?.result?.source_measurement_ids?.length || 0) + (pair.active?.result?.source_measurement_ids?.length || 0);
      return `<article class="production-sar-pair-row production-sar-pair-row--${tone}"><div class="production-sar-pair__forms">${pairStructure(pair.prodrug, "PRODRUG")}<span class="production-sar-pair__arrow" aria-hidden="true">→</span>${pairStructure(pair.active, "ACTIVE FORM")}</div><div class="production-sar-pair__values"><div><span>${escapeHtml(endpointName)} · prodrug</span><strong>${escapeHtml(prodrugValue)}</strong><small>${escapeHtml(F.humanize(pair.prodrug?.result?.state || "missing"))}</small></div><div><span>${escapeHtml(endpointName)} · active form</span><strong>${escapeHtml(activeValue)}</strong><small>${escapeHtml(F.humanize(pair.active?.result?.state || "missing"))}</small></div></div><div class="production-sar-pair__effect production-sar-pair__effect--${tone}"><span>Effect vs prodrug</span><strong>${escapeHtml(delta)}</strong><small>${escapeHtml(effect)}</small></div><div class="production-sar-pair__status"><span class="status-badge status-badge--${comparison.status === "complete" ? "good" : "neutral"}">${escapeHtml(status)}</span><small>${escapeHtml(pair.review_status || "needs_review").replaceAll("_", " ")} · ${sourceCount} source links</small></div></article>`;
    }).join("");
    grid.innerHTML = `<section class="production-sar-prodrug-results"><div class="production-sar-prodrug-results__heading"><div><div class="section-kicker">BULK PRODRUG–ACTIVE COMPARISON</div><h3>${escapeHtml(endpointName)} across ${pairs.length} linked pairs</h3></div><span class="status-badge status-badge--neutral" title="${escapeHtml(payload.algorithm_version || "")}">derived comparison</span></div><div class="production-sar-prodrug-results__legend"><span><b>PRODRUG</b> supplied form</span><span><b>ACTIVE FORM</b> linked active compound</span><span><b>Effect</b> active value minus prodrug value</span></div><div class="production-sar-pair-list">${rows}</div><p class="production-sar-series-card__boundary">${escapeHtml(payload.evidence_boundary || "Exact compatible summaries only; incomplete evidence remains visible.")}</p></section>`;
  };

  const loadSavedRelationships = async () => {
    if (!pairSaved) return;
    try {
      const payload = await apiJson(`/api/v1/compound-relationships?project_id=${encodeURIComponent(projectId)}`);
      savedRelationships = Array.isArray(payload.relationships) ? payload.relationships : [];
      if (pairCount) pairCount.textContent = savedRelationships.length ? `${savedRelationships.length} saved pair${savedRelationships.length === 1 ? "" : "s"}` : "No pair list loaded";
      pairSaved.textContent = savedRelationships.length ? `${savedRelationships.length} saved pair${savedRelationships.length === 1 ? "" : "s"} available. Paste a new list to replace or extend the mapping.` : "No saved mapping yet. Paste or upload a two-column pair list.";
    } catch (error) {
      pairSaved.textContent = `Saved pair mappings could not be loaded: ${error.message}`;
    }
  };

  const runProdrugAnalysis = async (endpointKey) => {
    const text = pairText?.value.trim() || "";
    if (text) {
      pendingRelationshipText = text;
      const preview = await apiJson("/api/v1/compound-relationships/preview", { project_id: projectId, pair_text: text });
      renderPairPreview(preview, endpointLabel(endpointKey));
      return;
    }
    if (!savedRelationships.length) {
      await loadSavedRelationships();
    }
    if (!savedRelationships.length) {
      setStatus("Paste or upload a prodrug–active pair list before comparing.", "error");
      return;
    }
    const comparison = await apiJson("/api/v1/analysis/prodrug-comparison", { project_id: projectId, endpoint_key: endpointKey });
    renderProdrugComparison(comparison);
  };

  const renderSeries = (payload) => {
    const series = Array.isArray(payload.series) ? payload.series : [];
    latestSeries = series;
    if (!results || !grid) return;
    results.hidden = false;
    if (badge) badge.textContent = `${series.length} series found`;
    if (!series.length) {
      if (summary) summary.textContent = "No shared scaffold has at least two compounds for this endpoint.";
      if (guide) {
        guide.hidden = false;
        guide.textContent = "Try another compatible endpoint or start from a validated compound. No unmeasured structures were inferred.";
      }
      grid.innerHTML = `<div class="empty-state production-sar-empty"><strong>No multi-compound series found.</strong><span>Try another endpoint or start from a compound with a validated structure. No unmeasured structures were inferred.</span></div>`;
      return;
    }

    const modeText = payload.mode === "selected" ? "using the selected SAR reference" : "across the project";
    const firstReference = series.find((item) => item.reference_registration_id)?.reference_registration_id || "the first exact observed member";
    const referenceNote = payload.mode === "selected"
      ? (series.some((item) => item.reference_selection === "selected_compound") ? "Deltas use the selected exact SAR reference." : "The selected compound has no exact result; deltas use the first exact observed member.")
      : "Deltas use each series’ first exact observed member.";
    if (summary) summary.textContent = `Endpoint-first view ${modeText} · ${endpointLabel(payload.endpoint_key)}. Each row makes the scaffold-relative structural change visible beside its observed result.`;
    if (guide) {
      guide.hidden = false;
      guide.innerHTML = `<strong>How to read this result</strong><span>Keep the shared scaffold fixed, then read each row as <b>substituent change → observed ${escapeHtml(endpointLabel(payload.endpoint_key))} → delta vs reference</b>.</span><small>Reference: ${escapeHtml(firstReference)} · ${escapeHtml(referenceNote)} “associated with” describes an observed comparison, not causal proof.</small>`;
    }

    grid.innerHTML = series.map((item, index) => {
      const best = item.best_member;
      const endpointName = endpointLabel(item.endpoint_key);
      const unitLabel = F.unit(item.unit);
      const directionText = item.direction === "lower" ? "lower is better" : item.direction === "higher" ? "higher is better" : "direction not declared";
      const bestText = best ? `${best.registration_id} · ${formatValue(best.summary_value, item.unit)}` : "No observed value";
      const members = (item.members || []).map((member) => memberMarkup(member, item)).join("");
      const reference = item.reference_registration_id || "first exact observed member";
      const positionHeads = (item.positions || []).map((position) => `<th scope="col" class="sar-rtable__r-head">${escapeHtml(position.label)}<small>${position.member_count} varied</small></th>`).join("");
      return `<article class="panel production-sar-series-card"><div class="production-sar-series-card__visual"><span class="production-sar-series-card__visual-label">SHARED SCAFFOLD</span>${item.scaffold_svg || `<code>${escapeHtml(item.scaffold_smiles)}</code>`}</div><div class="production-sar-series-card__body"><div class="section-kicker">SERIES ${String(index + 1).padStart(2, "0")} · ${escapeHtml(directionText.toUpperCase())}</div><h3>${escapeHtml(item.name)} · ${item.member_count} compounds</h3><p>${item.observed_count} observed · ${item.missing_count} missing or incomplete · ${escapeHtml(endpointName)}${unitLabel ? ` (${escapeHtml(unitLabel)})` : ""}</p><div class="production-sar-series-card__metric"><span>Observed range</span><strong>${escapeHtml(formatValue(item.min_value, item.unit))} – ${escapeHtml(formatValue(item.max_value, item.unit))}</strong></div><div class="production-sar-series-card__metric"><span>Best observed</span><strong>${escapeHtml(bestText)}</strong></div><div class="production-sar-series-card__metric"><span>Reference for Δ</span><strong>${escapeHtml(reference)}</strong></div></div><section class="production-sar-series-card__ladder" aria-labelledby="productionSarLadder${index}"><div class="production-sar-ladder__heading"><div><span id="productionSarLadder${index}">R-GROUP TABLE · ${escapeHtml(endpointName.toUpperCase())}</span><small>Positions are numbered on the scaffold drawing. H means no substituent at that position.</small></div><span class="status-badge status-badge--neutral">${item.observed_count} observed</span></div><div class="sar-rtable-wrap"><table class="sar-rtable"><thead><tr><th scope="col">Compound</th><th scope="col">Structure</th>${positionHeads}<th scope="col" class="numeric">${escapeHtml(endpointName)}${unitLabel ? `<small>${escapeHtml(unitLabel)}</small>` : ""}</th><th scope="col" class="numeric">Δ vs ${escapeHtml(reference)}<small>${escapeHtml(directionText)}</small></th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead><tbody>${members}</tbody></table></div></section><div class="production-sar-series-card__actions"><button class="button button--secondary button--small" type="button" data-sar-save-series>Save reviewed series</button><span class="production-sar-series-card__save-status" role="status" aria-live="polite"></span></div><small class="production-sar-series-card__boundary">${escapeHtml(item.evidence_boundary)}</small></article>`;
    }).join("");
    const unassigned = Array.isArray(payload.unassigned_compounds) ? payload.unassigned_compounds : [];
    if (unassigned.length) {
      grid.insertAdjacentHTML("beforeend", `<p class="production-sar-unassigned"><strong>${unassigned.length} compound${unassigned.length === 1 ? " is" : "s are"} not in a series:</strong> ${escapeHtml(unassigned.join(", "))}. Their Murcko scaffold (rings plus linkers) is not shared with another compound. Choose “Start from a selected SAR reference” to see their nearest neighbours.</p>`);
    }
    bindSeriesActions();
  };

  pairFile?.addEventListener("change", () => {
    const file = pairFile.files?.[0];
    if (!file || !pairText) return;
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      pairText.value = String(reader.result || "");
      if (pairSaved) pairSaved.textContent = `${file.name} loaded. Review the pair list, then run the bulk comparison.`;
    });
    reader.addEventListener("error", () => {
      if (pairSaved) pairSaved.textContent = "The pair-list file could not be read.";
    });
    reader.readAsText(file);
  });

  form.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener("change", syncMode));
  syncMode();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const endpointKey = endpoint?.value || "";
    const mode = selectedMode();
    const selectedCompoundId = compound?.value || "";
    if (!endpointKey) {
      setStatus("Choose an endpoint before running SAR analysis.", "error");
      return;
    }
    if (mode === "selected" && !selectedCompoundId) {
      setStatus("Choose a compound for the local series view.", "error");
      return;
    }
    if (runButton) runButton.disabled = true;
    setStatus(mode === "prodrug" ? "Reviewing the bulk prodrug–active mapping…" : "Running structure-based SAR discovery…");
    try {
      if (mode === "prodrug") {
        await runProdrugAnalysis(endpointKey);
        setStatus(pairText?.value.trim() ? "Pair mapping preview ready. Review it before saving." : "Bulk prodrug–active comparison complete.", "success");
        results?.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      const headers = { "Content-Type": "application/json" };
      if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
      const response = await fetch("/api/v1/sar/discover", {
        method: "POST",
        headers,
        body: JSON.stringify({ project_id: projectId, endpoint_key: endpointKey, mode, selected_compound_id: selectedCompoundId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
      renderSeries(payload);
      setStatus(`SAR analysis complete · ${payload.series_count || 0} series found.`, "success");
      results?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      setStatus(`SAR analysis could not run: ${error.message}`, "error");
    } finally {
      if (runButton) runButton.disabled = false;
    }
  });

  // Find by structure (static/structure_search.js) can make a hit the SAR reference, here or
  // from another page via ?reference=<compound id>. Same behaviour as the row button: rerun at once.
  const useAsReference = (compoundId) => {
    if (!compound || !compoundId || ![...compound.options].some((option) => option.value === compoundId)) return false;
    compound.value = compoundId;
    const selectedInput = form.querySelector('input[name="mode"][value="selected"]');
    if (selectedInput) selectedInput.checked = true;
    syncMode();
    const registrationId = (compound.selectedOptions[0]?.textContent || "the selected compound").split(" · ")[0];
    setStatus(`Setting ${registrationId} as the SAR reference and rerunning the local series…`);
    form.requestSubmit();
    return true;
  };
  (window.SARStructureActions = window.SARStructureActions || []).push({
    id: "sar-reference",
    scope: "compound",
    label: "Set as SAR reference",
    run: ({ compoundId }) => useAsReference(compoundId),
  });
  const requestedReference = new URLSearchParams(window.location.search).get("reference");
  if (requestedReference && !useAsReference(requestedReference)) setStatus("That compound is not in this project, so no reference was set.", "error");
})();
