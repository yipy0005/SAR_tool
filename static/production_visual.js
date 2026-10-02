(() => {
  "use strict";

  const dataNode = document.querySelector("#productionVisualData");
  const board = document.querySelector("#productionSarDiagram");
  const grid = document.querySelector("#productionCompoundGrid");
  if (!dataNode) return;

  let data;
  try {
    data = JSON.parse(dataNode.textContent || "{}");
  } catch (_error) {
    grid.textContent = "The visual compound map could not be loaded.";
    return;
  }

  const compounds = Array.isArray(data.compounds) ? data.compounds : [];
  const measurements = Array.isArray(data.measurements) ? data.measurements : [];
  const summaries = Array.isArray(data.summaries) ? data.summaries : [];
  const selectivity = Array.isArray(data.selectivity) ? data.selectivity : [];
  const translation = Array.isArray(data.translation) ? data.translation : [];
  const adme = Array.isArray(data.adme) ? data.adme : [];
  const properties = Array.isArray(data.properties) ? data.properties : [];

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const forCompound = (items, compound) => items.find((item) => item.compound_id === compound.id);
  const summaryFor = (compound, compatibilityKey) => summaries.find(
    (summary) => summary.compound_id === compound.id && summary.compatibility_key === compatibilityKey,
  );
  const numericValue = (summary) => {
    const value = summary?.value ?? summary?.summary_value;
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  const formatValue = (value, digits = 2) => value === null ? "—" : Number(value).toFixed(digits);
  const displayUnit = (unit) => (window.SARFormat ? window.SARFormat.unit(unit) : String(unit || ""));
  const summaryLabel = (summary) => {
    if (!summary) return "No result";
    if (summary.summary_state && summary.summary_state !== "observed") return summary.summary_state.replaceAll("_", " ");
    const qualifier = summary.summary_qualifier && summary.summary_qualifier !== "=" ? `${summary.summary_qualifier} ` : "";
    return `${qualifier}${formatValue(numericValue(summary))}`;
  };
  const rawIc50For = (compound) => measurements.find(
    (measurement) => measurement.registration_id === compound.registration_id && measurement.assay_name === "IC50" && measurement.raw_value_text,
  );
  const rawIc50Label = (compound, summary) => {
    const measurement = rawIc50For(compound);
    if (!measurement) return `${summaryLabel(summary)} ${displayUnit(summary?.unit)}`.trim();
    const qualifier = measurement.qualifier && measurement.qualifier !== "=" ? `${measurement.qualifier} ` : "";
    return `${qualifier}${measurement.raw_value_text} ${displayUnit(measurement.unit_ucum)}`.trim();
  };
  const series = Array.isArray(data.series) ? data.series : [];
  const compoundSeries = data.compoundSeries && typeof data.compoundSeries === "object" ? data.compoundSeries : {};
  const membershipsFor = (compound) => Array.isArray(compoundSeries[compound.id]) ? compoundSeries[compound.id] : [];
  const seriesLabel = (compound) => {
    const memberships = membershipsFor(compound);
    if (!memberships.length) return "Unassigned · review series context";
    return memberships.map((membership) => {
      const state = membership.membership_status === "needs_review" ? " · needs review" : "";
      return `${membership.series_name}${state}`;
    }).join(" · ");
  };
  const seriesMemberRationale = (compound) => membershipsFor(compound)
    .map((membership) => membership.rationale)
    .find((rationale) => rationale) || "Membership rationale not recorded";
  const potencyBar = (summary) => {
    const value = numericValue(summary);
    if (value === null || summary?.summary_state !== "observed") return "<span class=\"compound-signal__bar compound-signal__bar--empty\"></span>";
    const width = Math.max(8, Math.min(100, value * 10));
    return `<span class="compound-signal__bar"><span style="width:${width}%"></span></span>`;
  };
  const statusClass = (value) => {
    const normalized = String(value || "").toLowerCase();
    if (["observed", "translated", "selective", "complete_observed", "computed", "pareto"].some((token) => normalized.includes(token))) return "compound-pill--good";
    if (["missing", "incomplete", "censored", "conflict", "not_run"].some((token) => normalized.includes(token))) return "compound-pill--warn";
    return "compound-pill--neutral";
  };
  const signal = (label, summary) => `
    <div class="compound-signal" title="${escapeHtml(label)}">
      <div class="compound-signal__label">${escapeHtml(label)}</div>
      <strong>${escapeHtml(summaryLabel(summary))}</strong>
      ${potencyBar(summary)}
      <small>${summary ? escapeHtml(displayUnit(summary.unit || summary.canonical_unit || "")) : "awaiting assay"}</small>
    </div>`;

  const structureHtml = (compound) => {
    if (typeof compound?.rendered_svg === "string" && compound.rendered_svg.trim().startsWith("<svg")) return compound.rendered_svg;
    return "<span class=\"compound-card__no-structure\">No rendered structure</span>";
  };

  const renderSarDiagram = () => {
    if (!board) return;
    if (!compounds.length) {
      board.innerHTML = `<div class="compound-map-empty">No validated structures yet. Preview and commit a CSV above to build the SAR view.</div>`;
      return;
    }
    const ordered = compounds.slice().sort((left, right) => String(left.registration_id).localeCompare(String(right.registration_id), undefined, { numeric: true }));
    const ranked = ordered.map((compound) => ({
      compound,
      summary: summaryFor(compound, "IC50:import-v1"),
      value: numericValue(summaryFor(compound, "IC50:import-v1")),
    })).sort((left, right) => {
      if (left.value === null && right.value === null) return String(left.compound.registration_id).localeCompare(String(right.compound.registration_id), undefined, { numeric: true });
      if (left.value === null) return 1;
      if (right.value === null) return -1;
      return right.value - left.value;
    });
    const lead = ordered[0];
    const biochemical = summaryFor(lead, "IC50:import-v1");
    const cellular = summaryFor(lead, "Cellular:import-v1");
    const offTarget = summaryFor(lead, "OffTarget:import-v1");
    const selectivityResult = forCompound(selectivity, lead);
    const translationResult = forCompound(translation, lead);
    const admeResult = forCompound(adme, lead);
    const propertyResult = forCompound(properties, lead);
    const leadSvg = structureHtml(lead);
    const scaffoldSvg = typeof data.scaffoldSvg === "string" && data.scaffoldSvg.trim().startsWith("<svg")
      ? data.scaffoldSvg
      : leadSvg;
    const primarySeries = series
      .filter((item) => Array.isArray(item.members) && item.members.length > 0)
      .sort((left, right) => (right.member_count || 0) - (left.member_count || 0))[0];
    const scaffoldMemberIds = new Set((primarySeries?.members || [])
      .filter((member) => member.membership_status !== "excluded")
      .map((member) => member.compound_id));
    const scaffoldCore = ordered
      .map((compound) => ranked.find(({ compound: rankedCompound }) => rankedCompound.id === compound.id))
      .filter((entry) => entry && scaffoldMemberIds.has(entry.compound.id));
    const scaffoldExtensions = ranked
      .filter(({ compound }) => !scaffoldMemberIds.has(compound.id));
    const potencyText = ({ compound, summary }) => {
      const value = numericValue(summary);
      const unit = summary?.unit || summary?.canonical_unit || "nM";
      if (value !== null) return `${formatValue(value)} ${unit}`;
      const state = summary?.summary_state && summary.summary_state !== "observed"
        ? ` · ${summary.summary_state.replaceAll("_", " ")}`
        : "";
      return `${rawIc50Label(compound, summary)}${state}`.trim();
    };

    if (scaffoldCore.length >= 4) {
      const reference = scaffoldCore[0];
      const coreValues = scaffoldCore.map(({ value }) => value).filter((value) => value !== null);
      const coreRange = coreValues.length
        ? `${formatValue(Math.min(...coreValues))}–${formatValue(Math.max(...coreValues))} nM`
        : "not available";
      const scaffoldPositions = {
        r1: { label: "MEMBERS", tone: "blue", title: "Series membership" },
        linker: { label: "REVIEW", tone: "violet", title: "Membership rationale" },
        r3: { label: "EVIDENCE", tone: "green", title: "Evidence state" },
        amide: { label: "CORE", tone: "red", title: "Shared context" },
      };
      const scaffoldPositionFor = () => "r1";
      const scaffoldExtensionLabel = (compound) => `${seriesLabel(compound)} · ${seriesMemberRationale(compound)}`;
      const scaffoldCallout = (positionKey, lines) => {
        const position = scaffoldPositions[positionKey];
        return `
          <article class="production-scaffold-callout production-scaffold-callout--${position.tone} production-scaffold-callout--${positionKey}" data-scaffold-position="${escapeHtml(position.label)}" aria-label="${escapeHtml(position.label)} ${escapeHtml(position.title)}">
            <span class="production-scaffold-callout__eyebrow">${escapeHtml(position.label)} · SCAFFOLD POSITION</span>
            <h3>${escapeHtml(position.title)}</h3>
            <ul>${lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
          </article>`;
      };
      const coreCards = scaffoldCore.map(({ compound, summary }) => {
        const memberLabel = seriesLabel(compound);
        return `
          <article class="production-scaffold-series-card production-scaffold-series-card--r1" data-scaffold-position="MEMBERS" aria-label="Series member ${escapeHtml(compound.registration_id)}">
            <div class="production-scaffold-series__structure structure-hover-target" data-structure-name="${escapeHtml(compound.registration_id)}">${structureHtml(compound)}</div>
            <strong>${escapeHtml(compound.registration_id)}</strong>
            <span>${escapeHtml(memberLabel)}</span>
            <small title="${escapeHtml(seriesMemberRationale(compound))}">IC<sub>50</sub> ${escapeHtml(potencyText({ compound, summary }))}</small>
          </article>`;
      }).join("");
      const extensionCards = scaffoldExtensions.map(({ compound, summary }) => {
        const positionKey = scaffoldPositionFor(compound.registration_id);
        const position = scaffoldPositions[positionKey];
        return `
          <article class="production-scaffold-extension-card production-scaffold-extension-card--${positionKey}" data-scaffold-position="${escapeHtml(position.label)}" aria-label="Project compound ${escapeHtml(compound.registration_id)} outside the current series">
            <div class="production-scaffold-extension__structure structure-hover-target" data-structure-name="${escapeHtml(compound.registration_id)}">${structureHtml(compound)}</div>
            <strong>${escapeHtml(compound.registration_id)}</strong>
            <span>${escapeHtml(scaffoldExtensionLabel(compound))}</span>
            <small>IC<sub>50</sub> ${escapeHtml(potencyText({ compound, summary }))}</small>
          </article>`;
      }).join("");
      const referenceId = reference.compound.registration_id;
      const seriesName = primarySeries?.name || "Curated series";
      const seriesLabels = scaffoldCore.map(({ compound }) => seriesLabel(compound)).join(" · ");
      const rgroupNote = primarySeries
        ? `Series version ${primarySeries.current_version} · ${primarySeries.membership_source.replaceAll("_", " ")} · ${primarySeries.version_rationale || "rationale recorded in series context"}.`
        : "No persisted series membership is available for this project.";

      board.innerHTML = `
        <div class="production-scaffold-note">
          <strong>Series-aware SAR view</strong>
          <span>${escapeHtml(seriesName)} · ${escapeHtml(rgroupNote)}</span>
        </div>
        <div class="production-scaffold-layout">
          ${scaffoldCallout("r1", [
            `${scaffoldCore.length} included members`,
            `Series: ${seriesName}`,
            `Observed IC50 range: ${coreRange}`,
          ])}
          ${scaffoldCallout("r3", [
            `Version ${primarySeries.current_version} membership`,
            `${scaffoldCore.length} compounds included in this view`,
            "Membership is reviewable and does not alter measurements",
          ])}
          ${scaffoldCallout("linker", [
            `${scaffoldExtensions.length} compounds outside this series`,
            "Unassigned or excluded compounds stay separate",
            "Censoring and mixed evidence remain visible",
          ])}
          ${scaffoldCallout("amide", [
            "Shared comparison context",
            primarySeries.version_rationale || "Series rationale recorded by the project team",
            "Structural interpretation remains separate from observation",
          ])}
          <div class="production-scaffold-core">
            <span class="production-scaffold-core__eyebrow">REFERENCE STRUCTURE · SHARED CORE</span>
            <div class="production-scaffold-core__structure structure-hover-target" data-structure-name="shared scaffold">
              ${scaffoldSvg}
            </div>
            <strong>Lead compound (${escapeHtml(referenceId)})</strong>
            <span>IC<sub>50</sub> ${escapeHtml(potencyText(reference))}</span>
            <small>Reference structure rendered by RDKit; member positions are not inferred without a persisted analysis result.</small>
          </div>
        </div>
        <div class="production-scaffold-series-heading">
          <span>${escapeHtml(seriesName)} · current membership</span>
          <small>Version ${escapeHtml(primarySeries.current_version)} · values remain source-linked · ${escapeHtml(seriesLabels)}</small>
        </div>
        <div class="production-scaffold-series-rail"><div class="production-scaffold-series">${coreCards}</div></div>
        <div class="production-scaffold-potency-arrow"><span>Potency key · lower IC<sub>50</sub> = higher potency</span><i aria-hidden="true"></i></div>
        ${scaffoldExtensions.length ? `
          <div class="production-scaffold-extension-heading">
            <span>Other project compounds</span>
            <small>Not included in the current series version · review membership before interpretation</small>
          </div>
          <div class="production-scaffold-extension-rail"><div class="production-scaffold-extensions">${extensionCards}</div></div>` : ""}`;
      return;
    }
    const callout = (tone, label, value, note) => `
      <article class="production-sar-callout production-sar-callout--${tone}">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
        <small>${escapeHtml(note)}</small>
      </article>`;
    const analogueCards = ranked.map(({ compound, summary }) => `
      <article class="production-sar-analogue" title="${escapeHtml(compound.registration_id)}">
        <div class="production-sar-analogue__structure">${structureHtml(compound)}</div>
        <strong>${escapeHtml(compound.registration_id)}</strong>
        <span>${escapeHtml(seriesLabel(compound))} · IC50 ${escapeHtml(rawIc50Label(compound, summary))}</span>
      </article>`).join("");

    board.innerHTML = `
      <div class="production-sar-callouts">
        ${callout("blue", "Observed biochemical potency", `IC50 ${rawIc50Label(lead, biochemical)}`, "Reference compound result")}
        ${callout("violet", "Cellular translation", translationResult?.status || "not run", translationResult?.reason || "Compare biochemical and cellular contexts")}
        <div class="production-sar-lead">
          <div class="production-sar-lead__structure">${leadSvg}</div>
          <div class="production-sar-lead__label">Lead / reference compound</div>
          <strong>${escapeHtml(lead.registration_id)}</strong>
          <span>${escapeHtml(lead.isomeric_smiles || lead.canonical_smiles || "Validated structure")}</span>
        </div>
        ${callout("green", "Selectivity", selectivityResult?.status || "not run", selectivityResult?.reason || `Off-target summary ${summaryLabel(offTarget)}`)}
        ${callout("yellow", "Developability snapshot", admeResult?.status || "not run", propertyResult?.descriptors?.logp?.value === undefined ? "ADME/property analyses not run" : `LogP ${formatValue(propertyResult.descriptors.logp.value)}`)}
      </div>
      <div class="production-sar-analogue-heading">
        <span>Observed analogue/order view</span>
        <small>Sorted from lower to higher observed potency; censored and missing values are not treated as exact potency.</small>
      </div>
      <div class="production-sar-analogue-rail">
        <div class="production-sar-analogue-strip">${analogueCards}</div>
        <div class="production-sar-potency-arrow"><span>Less potent → more potent (lower IC50)</span><i aria-hidden="true"></i></div>
      </div>`;
  };

  renderSarDiagram();

  if (!grid) return;

  if (!compounds.length) {
    grid.innerHTML = `<div class="compound-map-empty">No validated structures yet. Preview and commit a CSV above to build the compound map.</div>`;
    return;
  }

  grid.innerHTML = compounds.map((compound) => {
    const biochemical = summaryFor(compound, "IC50:import-v1");
    const cellular = summaryFor(compound, "Cellular:import-v1");
    const offTarget = summaryFor(compound, "OffTarget:import-v1");
    const selectivityResult = forCompound(selectivity, compound);
    const translationResult = forCompound(translation, compound);
    const admeResult = forCompound(adme, compound);
    const propertyResult = forCompound(properties, compound);
    const logp = propertyResult?.descriptors?.logp?.value;
    const svg = typeof compound.rendered_svg === "string" && compound.rendered_svg.trim().startsWith("<svg")
      ? compound.rendered_svg
      : "<span class=\"compound-card__no-structure\">No rendered structure</span>";
    const identityStatus = compound.stereochemistry_status || "validated";
    const admeStatus = admeResult?.status || "not run";
    const selectivityStatus = selectivityResult?.status || "not run";
    const translationStatus = translationResult?.status || "not run";
    return `
      <article class="compound-card" aria-label="Compound ${escapeHtml(compound.registration_id)}">
        <div class="compound-card__topline">
          <div>
            <span class="compound-card__id">${escapeHtml(compound.registration_id)}</span>
            <span class="compound-pill ${statusClass(identityStatus)}">${escapeHtml(identityStatus.replaceAll("_", " "))}</span>
          </div>
          <button class="button button--secondary button--small" type="button" data-open-evidence="${escapeHtml(compound.registration_id)}">Evidence</button>
        </div>
        <div class="compound-card__body">
          <div class="compound-card__structure" role="img" aria-label="Chemical structure for ${escapeHtml(compound.registration_id)}">${svg}</div>
          <div class="compound-card__signals">
            ${signal("Biochemical pIC50", biochemical)}
            ${signal("Cellular pIC50", cellular)}
          </div>
        </div>
        <div class="compound-card__metrics">
          <div><span>Off-target</span><strong>${escapeHtml(summaryLabel(offTarget))}</strong></div>
          <div><span>Selectivity</span><strong class="compound-pill ${statusClass(selectivityStatus)}">${escapeHtml(selectivityStatus.replaceAll("_", " "))}</strong></div>
          <div><span>Translation</span><strong class="compound-pill ${statusClass(translationStatus)}">${escapeHtml(translationStatus.replaceAll("_", " "))}</strong></div>
          <div><span>ADME panel</span><strong class="compound-pill ${statusClass(admeStatus)}">${escapeHtml(admeStatus.replaceAll("_", " "))}</strong></div>
          <div><span>LogP</span><strong>${escapeHtml(formatValue(logp))}</strong></div>
        </div>
      </article>`;
  }).join("");

  grid.querySelectorAll("[data-open-evidence]").forEach((button) => {
    button.addEventListener("click", () => {
      const details = document.querySelector("#productionEvidenceDetails");
      if (details) details.open = true;
      const target = document.querySelector("#productionEvidenceTables");
      if (!target) return;
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      target.querySelectorAll("tr[data-registration-id]").forEach((row) => {
        const active = row.dataset.registrationId === button.dataset.openEvidence;
        row.classList.toggle("production-row-highlight", active);
      });
    });
  });
})();

  const setupStructureHover = () => {
    const targets = document.querySelectorAll(
      ".production-sar-lead__structure, .production-sar-analogue__structure, " +
      ".compound-card__structure, .production-structure-preview, " +
      ".production-scaffold-core__structure, .production-scaffold-series__structure, " +
      ".production-scaffold-extension__structure",
    );
    if (!targets.length) return;

    const preview = document.createElement("div");
    preview.className = "structure-hover-preview";
    preview.setAttribute("role", "tooltip");
    const previewLabel = document.createElement("div");
    previewLabel.className = "structure-hover-preview__label";
    const previewBody = document.createElement("div");
    previewBody.className = "structure-hover-preview__body";
    preview.append(previewLabel, previewBody);
    document.body.appendChild(preview);

    let activeTarget = null;
    const targetName = (target) => {
      const rowName = target.closest("tr")?.querySelector("td")?.textContent?.trim();
      const cardName = target.closest(".production-sar-analogue, .compound-card, .production-sar-lead")
        ?.querySelector("strong")?.textContent?.trim();
      return target.dataset.structureName || rowName || cardName || "chemical structure";
    };
    const placePreview = (target, event) => {
      const rect = target.getBoundingClientRect();
      const pointerX = Number.isFinite(event?.clientX) && event.clientX ? event.clientX : rect.right;
      const pointerY = Number.isFinite(event?.clientY) && event.clientY ? event.clientY : rect.top;
      const gap = 14;
      let left = pointerX + gap;
      let top = pointerY + gap;
      const maxLeft = window.innerWidth - preview.offsetWidth - 12;
      const maxTop = window.innerHeight - preview.offsetHeight - 12;
      if (left > maxLeft) left = Math.max(12, rect.left - preview.offsetWidth - gap);
      if (top > maxTop) top = Math.max(12, rect.top - preview.offsetHeight - gap);
      preview.style.left = `${Math.max(12, left)}px`;
      preview.style.top = `${Math.max(12, top)}px`;
    };
    const hidePreview = () => {
      activeTarget = null;
      preview.classList.remove("is-visible");
    };
    const showPreview = (target, event) => {
      const svg = target.querySelector("svg");
      if (!svg || event?.pointerType === "touch") return;
      activeTarget = target;
      const name = targetName(target);
      target.setAttribute("tabindex", "0");
      target.setAttribute("role", "img");
      target.setAttribute("aria-label", `Enlarge structure for ${name}`);
      previewLabel.textContent = `Structure · ${name}`;
      previewBody.replaceChildren(svg.cloneNode(true));
      preview.classList.add("is-visible");
      requestAnimationFrame(() => placePreview(target, event));
    };

    targets.forEach((target) => {
      const name = targetName(target);
      target.setAttribute("tabindex", "0");
      target.setAttribute("role", "img");
      target.setAttribute("aria-label", `Enlarge structure for ${name}`);
      target.addEventListener("pointerenter", (event) => showPreview(target, event));
      target.addEventListener("pointermove", (event) => {
        if (activeTarget === target) placePreview(target, event);
      });
      target.addEventListener("pointerleave", hidePreview);
      target.addEventListener("focus", (event) => showPreview(target, event));
      target.addEventListener("blur", hidePreview);
    });
    window.addEventListener("scroll", hidePreview, { passive: true });
    window.addEventListener("resize", hidePreview);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hidePreview();
    });
  };

  setupStructureHover();
