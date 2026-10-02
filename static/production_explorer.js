(() => {
  "use strict";

  const root = document.querySelector("[data-production-project]");
  const explorer = document.querySelector("#sar-explorer");
  const dataNode = document.querySelector("#productionVisualData");
  if (!root || !explorer || !dataNode) return;

  let data;
  try {
    data = JSON.parse(dataNode.textContent || "{}");
  } catch (_error) {
    return;
  }

  const projectId = root.dataset.productionProject || "production";
  const compounds = Array.isArray(data.compounds) ? data.compounds : [];
  const measurements = Array.isArray(data.measurements) ? data.measurements : [];
  const summaries = Array.isArray(data.summaries) ? data.summaries : [];
  const rgroupAssignments = Array.isArray(data.rgroupAssignments) ? data.rgroupAssignments : [];
  const activityCliffs = Array.isArray(data.activityCliffs) ? data.activityCliffs : [];
  const mmp = data.mmp && typeof data.mmp === "object" ? data.mmp : {};
  const selectivityObservations = Array.isArray(data.selectivity) ? data.selectivity : [];
  const translationObservations = Array.isArray(data.translation) ? data.translation : [];
  const admeObservations = Array.isArray(data.adme) ? data.adme : [];
  const propertyProfiles = Array.isArray(data.properties) ? data.properties : [];
  const paretoObservations = Array.isArray(data.pareto) ? data.pareto : [];
  const contradictionObservations = Array.isArray(data.contradictions) ? data.contradictions : [];
  const designCandidates = Array.isArray(data.designCandidates) ? data.designCandidates : [];
  const hypotheses = Array.isArray(data.hypotheses) ? data.hypotheses : [];
  const recommendations = Array.isArray(data.recommendations) ? data.recommendations : [];
  const scaffoldSvg = typeof data.scaffoldSvg === "string" ? data.scaffoldSvg : "";
  const storageKey = `sar-explorer:${projectId}`;
  const sessionsStorageKey = `${storageKey}:sessions`;

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const numeric = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  const F = window.SARFormat;
  const formatNumber = (value, digits = 2, unit = "") => {
    const number = numeric(value);
    if (number === null) return "—";
    if (F && unit) return F.number(number, unit);
    // Fixed decimals without a unit (similarities, effects) so columns line up: 0.80, not 0.8.
    return number.toFixed(digits);
  };
  const displayUnit = (unit) => (F ? F.unit(unit) : String(unit || ""));
  const SUBSCRIPT = { "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉" };
  // Compact formula for the changed atoms of a matched pair, e.g. ["F","F","F","C"] → "CF₃".
  const atomFormula = (symbols) => {
    const counts = new Map();
    (symbols || []).forEach((symbol) => { const key = String(symbol || "").trim(); if (key) counts.set(key, (counts.get(key) || 0) + 1); });
    if (!counts.size) return "H";
    const order = [...(counts.has("C") ? ["C"] : []), ...[...counts.keys()].filter((key) => key !== "C").sort()];
    return order.map((key) => `${key}${counts.get(key) > 1 ? String(counts.get(key)).split("").map((d) => SUBSCRIPT[d]).join("") : ""}`).join("");
  };

  const endpointName = (key) => String(key || "Unknown assay")
    .replace(/:import-v1$/, "")
    .replaceAll("_", " ");

  const endpointKey = (kind, name, unit) => `${kind}|${name || "unknown"}|${unit || ""}`;
  const endpointIdForSummary = (summary) => endpointKey(
    "summary",
    summary.compatibility_key || summary.assay_name || "unknown",
    summary.canonical_unit || summary.unit || "",
  );
  const endpointIdForMeasurement = (measurement) => endpointKey(
    "raw",
    measurement.assay_name || "unknown",
    measurement.canonical_unit || measurement.unit_ucum || "",
  );

  const statusForRecord = (record, kind) => {
    if (!record) return "missing";
    const state = String(kind === "summary" ? record.summary_state : "").toLowerCase();
    const qualifier = String(kind === "summary" ? record.summary_qualifier : record.qualifier || "=");
    if (state === "missing" || record.missing_reason) return "missing";
    if (state === "censored" || (qualifier && qualifier !== "=")) return "censored";
    if (numeric(kind === "summary" ? record.summary_value ?? record.value : record.canonical_value) !== null) return "observed";
    return "review";
  };

  const comparableRecord = (record, kind) => {
    const value = numeric(kind === "summary" ? record?.summary_value ?? record?.value : record?.canonical_value);
    return value !== null && statusForRecord(record, kind) === "observed";
  };

  const valueForRecord = (record, kind) => numeric(
    kind === "summary" ? record?.summary_value ?? record?.value : record?.canonical_value,
  );

  const unitForRecord = (record, kind) => String(
    kind === "summary" ? record?.canonical_unit || record?.unit : record?.canonical_unit || record?.unit_ucum,
  );

  const recordSourceIds = (record, kind) => {
    if (!record) return [];
    if (kind === "summary") return Array.isArray(record.source_measurement_ids) ? record.source_measurement_ids : [];
    return record.id ? [record.id] : [];
  };

  const resultStateLabel = (status) => ({
    observed: "Exact result",
    censored: "Threshold result",
    missing: "No result",
    review: "Needs review",
  }[status] || "Needs review");

  const rawLabel = (record) => {
    if (!record) return "No result";
    const status = statusForRecord(record, "raw");
    const raw = record.raw_value_text || record.missing_reason || "—";
    const qualifier = record.qualifier && record.qualifier !== "=" ? `${record.qualifier} ` : "";
    if (status === "missing") return `No result${record.missing_reason ? `: ${record.missing_reason}` : ""}`;
    return `${qualifier}${raw} ${displayUnit(record.unit_ucum)}`.trim();
  };

  const summaryLabel = (record) => {
    if (!record) return "—";
    const status = statusForRecord(record, "summary");
    if (status === "missing") return `No result${record.missing_reasons?.length ? `: ${record.missing_reasons.join(", ")}` : ""}`;
    const value = valueForRecord(record, "summary");
    const unit = unitForRecord(record, "summary");
    const qualifier = record.summary_qualifier && record.summary_qualifier !== "=" ? `${record.summary_qualifier} ` : "";
    if (value !== null) return `${qualifier}${formatNumber(value, 2, unit)} ${displayUnit(unit)}`.trim();
    if (record.lower_bound !== null && record.lower_bound !== undefined) return `At least ${formatNumber(record.lower_bound, 2, unit)} ${displayUnit(unit)}`.trim();
    if (record.upper_bound !== null && record.upper_bound !== undefined) return `At most ${formatNumber(record.upper_bound, 2, unit)} ${displayUnit(unit)}`.trim();
    return resultStateLabel(status);
  };

  const buildEndpoints = () => {
    const definitions = new Map();
    const add = (kind, name, unit) => {
      const id = endpointKey(kind, name, unit);
      if (!definitions.has(id)) {
        definitions.set(id, {
          id,
          kind,
          name: endpointName(name),
          rawName: name || "unknown",
          unit: unit || "",
          unitLabel: displayUnit(unit || ""),
        });
      }
      return definitions.get(id);
    };
    summaries.forEach((summary) => add(
      "summary",
      summary.compatibility_key || summary.assay_name,
      summary.canonical_unit || summary.unit,
    ));
    measurements.forEach((measurement) => add(
      "raw",
      measurement.assay_name,
      measurement.canonical_unit || measurement.unit_ucum,
    ));
    return [...definitions.values()].sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === "summary" ? -1 : 1;
      return `${left.name}|${left.unit}`.localeCompare(`${right.name}|${right.unit}`);
    });
  };

  const endpoints = buildEndpoints();
  const endpointById = new Map(endpoints.map((endpoint) => [endpoint.id, endpoint]));
  const summaryIndex = new Map();
  summaries.forEach((summary) => {
    const key = `${summary.registration_id}|${endpointIdForSummary(summary)}`;
    const current = summaryIndex.get(key);
    const currentRank = current ? (statusForRecord(current, "summary") === "observed" ? 2 : 1) : 0;
    const candidateRank = statusForRecord(summary, "summary") === "observed" ? 2 : 1;
    if (!current || candidateRank >= currentRank) summaryIndex.set(key, summary);
  });
  const measurementIndex = new Map();
  measurements.forEach((measurement) => {
    const key = `${measurement.registration_id}|${endpointIdForMeasurement(measurement)}`;
    if (!measurementIndex.has(key)) measurementIndex.set(key, measurement);
  });

  const endpointFor = (id) => id ? endpointById.get(id) || null : null;
  const recordFor = (registrationId, endpoint) => {
    if (!endpoint) return null;
    const key = `${registrationId}|${endpoint.id}`;
    return endpoint.kind === "summary" ? summaryIndex.get(key) || null : measurementIndex.get(key) || null;
  };

  const endpointStats = (endpoint) => {
    const records = compounds.map((compound) => recordFor(compound.registration_id, endpoint));
    return {
      observed: records.filter((record) => statusForRecord(record, endpoint?.kind) === "observed").length,
      censored: records.filter((record) => statusForRecord(record, endpoint?.kind) === "censored").length,
      missing: records.filter((record) => statusForRecord(record, endpoint?.kind) === "missing").length,
      review: records.filter((record) => statusForRecord(record, endpoint?.kind) === "review").length,
    };
  };

  const endpointLabel = (endpoint) => endpoint
    ? `${endpoint.name} · ${endpoint.kind === "summary" ? "calculated result" : "uploaded result"}${endpoint.unit ? ` · ${endpoint.unitLabel}` : ""}`
    : "No assay selected";

  const endpointCompatibilityKey = (endpoint) => {
    if (!endpoint) return "";
    return endpoint.kind === "summary" ? endpoint.rawName : `${endpoint.rawName}:import-v1`;
  };

  const selectionValues = (selection) => Object.values(selection || {})
    .flatMap((value) => Array.isArray(value) ? value : [value])
    .filter((value) => value !== null && value !== undefined)
    .map((value) => String(value));

  const analysisMatchesEndpoints = (record, endpointsToMatch) => {
    const keys = endpointsToMatch.filter(Boolean).map(endpointCompatibilityKey).filter(Boolean);
    if (!keys.length || !record?.input_selection) return true;
    const values = selectionValues(record.input_selection);
    return keys.every((key) => values.includes(key) || values.some((value) => value.startsWith(`${key}:`)));
  };

  const contextKeyMatchesEndpoint = (contextKey, endpoint) => {
    if (!endpoint || !contextKey) return true;
    const key = endpointCompatibilityKey(endpoint);
    return String(contextKey) === key || String(contextKey).startsWith(`${endpoint.rawName}:`);
  };

  const compoundEvidenceIds = (compound) => {
    if (!compound) return [];
    const registrationId = compound.registration_id;
    const ids = [
      ...measurements.filter((item) => item.registration_id === registrationId).map((item) => item.id),
      ...summaries.filter((item) => item.registration_id === registrationId).map((item) => item.id),
      ...rgroupAssignments.filter((item) => item.registration_id === registrationId).map((item) => item.id),
      ...activityCliffs.filter((item) => item.compound_a_registration_id === registrationId || item.compound_b_registration_id === registrationId).map((item) => item.id),
      ...selectivityObservations.filter((item) => item.registration_id === registrationId).map((item) => item.id),
      ...translationObservations.filter((item) => item.registration_id === registrationId).map((item) => item.id),
      ...admeObservations.filter((item) => item.registration_id === registrationId).map((item) => item.id),
      ...paretoObservations.filter((item) => item.registration_id === registrationId).map((item) => item.id),
      ...contradictionObservations.filter((item) => item.registration_id === registrationId).map((item) => item.id),
    ];
    (mmp.pairs || []).forEach((pair) => {
      if (pair.compound_a === registrationId || pair.compound_b === registrationId) ids.push(pair.id);
    });
    return [...new Set(ids.filter(Boolean).map(String))];
  };

  const displayJson = (value) => escapeHtml(JSON.stringify(value ?? {}, null, 0));
  const descriptorValue = (profile, key) => profile?.descriptors?.[key]?.value ?? profile?.descriptors?.[key] ?? null;

  const endpointMeta = (endpoint) => {
    if (!endpoint) return "Choose a assay";
    const stats = endpointStats(endpoint);
    return `${stats.observed} exact · ${stats.censored} threshold · ${stats.missing} no result · ${stats.review} review`;
  };

  const savedState = (() => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(storageKey) || "null");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  })();

  const savedSessions = (() => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(sessionsStorageKey) || "[]");
      return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item === "object" && item.state) : [];
    } catch (_error) {
      return [];
    }
  })();
  let sessions = savedSessions;

  const defaultA = endpoints.find((endpoint) => endpoint.kind === "summary" && endpoint.rawName.startsWith("IC50"))?.id
    || endpoints[0]?.id
    || "";
  const defaultB = endpoints.find((endpoint) => endpoint.kind === "summary" && endpoint.rawName.startsWith("Cellular"))?.id
    || endpoints.find((endpoint) => endpoint.id !== defaultA)?.id
    || "none";
  const comparisonSteps = ["import", "configure", "filter", "compare", "evidence"];
  const urlState = new URLSearchParams(window.location.search);
  const requestedA = urlState.get("endpoint_a") || "";
  const requestedB = urlState.get("endpoint_b") || "";
  const requestedStage = urlState.get("stage") || "";
  const requestedCoverage = urlState.get("coverage") || "";
  const requestedQuery = urlState.get("q");
  const requestedSelected = urlState.get("selected") || "";
  const initialA = endpointById.has(requestedA)
    ? requestedA
    : endpointById.has(savedState.endpointA) ? savedState.endpointA : defaultA;
  const initialB = requestedB === "none" || endpointById.has(requestedB)
    ? requestedB
    : savedState.endpointB === "none" || endpointById.has(savedState.endpointB)
      ? savedState.endpointB
      : defaultB;

  const state = {
    step: comparisonSteps.includes(requestedStage) ? requestedStage : (comparisonSteps.includes(savedState.step) ? savedState.step : "configure"),
    endpointA: initialA,
    endpointB: initialB || "none",
    coverage: ["all", "endpoint-a", "both", "review"].includes(requestedCoverage) ? requestedCoverage : (["all", "endpoint-a", "both", "review"].includes(savedState.coverage) ? savedState.coverage : "all"),
    query: requestedQuery !== null ? requestedQuery : (typeof savedState.query === "string" ? savedState.query : ""),
    selectedId: requestedSelected || (typeof savedState.selectedId === "string" ? savedState.selectedId : ""),
  };

  const elements = {
    status: document.querySelector("#productionExplorerStatus"),
    message: document.querySelector("#productionExplorerMessage"),
    canvas: document.querySelector("#productionExplorerCanvas"),
    selection: document.querySelector("#productionExplorerSelection"),
    endpointA: document.querySelector("#productionEndpointA"),
    endpointB: document.querySelector("#productionEndpointB"),
    endpointAMeta: document.querySelector("#productionEndpointAMeta"),
    endpointBMeta: document.querySelector("#productionEndpointBMeta"),
    coverage: document.querySelector("#productionExplorerCoverage"),
    search: document.querySelector("#productionExplorerSearch"),
    back: document.querySelector("#productionExplorerBack"),
    next: document.querySelector("#productionExplorerNext"),
    save: document.querySelector("#productionExplorerSave"),
    export: document.querySelector("#productionExplorerExport"),
    reset: document.querySelector("#productionExplorerReset"),
    sessionName: document.querySelector("#productionExplorerSessionName"),
    sessionList: document.querySelector("#productionExplorerSessionList"),
    exportBundle: document.querySelector("#productionExplorerExportBundle"),
  };

  const stepOrder = comparisonSteps;
  const stepLabels = {
    import: "Choose data",
    configure: "Choose tests",
    filter: "Narrow list",
    compare: "Compare results",
    evidence: "Check source",
  };

  const navigateToStep = (step) => {
    const params = new URLSearchParams();
    params.set("project_id", projectId);
    params.set("stage", step);
    if (state.endpointA) params.set("endpoint_a", state.endpointA);
    if (state.endpointB) params.set("endpoint_b", state.endpointB);
    if (state.coverage) params.set("coverage", state.coverage);
    if (state.query) params.set("q", state.query);
    if (state.selectedId) params.set("selected", state.selectedId);
    window.location.href = `/workspace/explore?${params.toString()}`;
  };

  const setStatus = (message, kind = "") => {
    if (!elements.status) return;
    elements.status.textContent = message;
    elements.status.dataset.status = kind;
  };

  const setMessage = (message, kind = "") => {
    if (!elements.message) return;
    elements.message.textContent = message;
    elements.message.dataset.status = kind;
  };

  const setOptions = (select, options, selected) => {
    if (!select) return;
    select.innerHTML = options.map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`).join("");
    if (options.some((option) => option.value === selected)) select.value = selected;
  };

  const refreshSessionList = () => {
    if (!elements.sessionList) return;
    const selected = elements.sessionList.value;
    setOptions(
      elements.sessionList,
      [{ value: "", label: "No saved session selected" }].concat(
        sessions.map((session) => ({
          value: session.id,
          label: `${session.name || "Unnamed session"} · ${new Date(session.saved_at || Date.now()).toLocaleString()}`,
        })),
      ),
      sessions.some((session) => session.id === selected) ? selected : "",
    );
  };

  const configureControls = () => {
    refreshSessionList();
    setOptions(
      elements.endpointA,
      endpoints.map((endpoint) => ({ value: endpoint.id, label: endpointLabel(endpoint) })),
      state.endpointA,
    );
    setOptions(
      elements.endpointB,
      [{ value: "none", label: "No comparison endpoint" }].concat(
        endpoints.map((endpoint) => ({ value: endpoint.id, label: endpointLabel(endpoint) })),
      ),
      state.endpointB,
    );
    if (elements.coverage) elements.coverage.value = state.coverage;
    if (elements.search) elements.search.value = state.query;
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    if (elements.endpointAMeta) elements.endpointAMeta.textContent = endpointMeta(endpointA);
    if (elements.endpointBMeta) elements.endpointBMeta.textContent = endpointMeta(endpointB);
  };

  const filteredCompounds = () => {
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const query = state.query.trim().toLowerCase();
    return compounds.filter((compound) => {
      if (query && !String(compound.registration_id || "").toLowerCase().includes(query)) return false;
      const recordA = recordFor(compound.registration_id, endpointA);
      const recordB = recordFor(compound.registration_id, endpointB);
      const measuredA = Boolean(recordA && statusForRecord(recordA, endpointA?.kind) !== "missing");
      const measuredB = Boolean(recordB && statusForRecord(recordB, endpointB?.kind) !== "missing");
      const review = statusForRecord(recordA, endpointA?.kind) !== "observed"
        || (endpointB && statusForRecord(recordB, endpointB.kind) !== "observed");
      if (state.coverage === "endpoint-a" && !measuredA) return false;
      if (state.coverage === "both" && (!measuredA || !measuredB)) return false;
      if (state.coverage === "review" && !review) return false;
      return true;
    });
  };

  const cellHtml = (record, endpoint) => {
    const status = statusForRecord(record, endpoint?.kind);
    const value = endpoint?.kind === "summary" ? summaryLabel(record) : rawLabel(record);
    const sources = recordSourceIds(record, endpoint?.kind);
    return `<div class="production-explorer-cell production-explorer-cell--${status}">
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(resultStateLabel(status))}${endpoint?.unit ? ` · ${escapeHtml(endpoint.unitLabel)}` : ""}${sources.length ? ` · ${sources.length} source${sources.length === 1 ? "" : "s"}` : ""}</small>
    </div>`;
  };

  const compoundStructure = (compound) => {
    if (typeof compound?.rendered_svg === "string" && compound.rendered_svg.trim().startsWith("<svg")) return compound.rendered_svg;
    return "<span class=\"compound-card__no-structure\">No rendered structure</span>";
  };

  const renderImport = () => `<div class="production-explorer-configure">
    <div class="production-explorer-endpoint-cards">
      <article class="production-explorer-endpoint-card production-explorer-endpoint-card--primary">
        <span class="production-explorer-card-kicker">STEP 1 · CHECK FILE</span>
        <h3>Choose your lab results file</h3>
        <p>Check the file, review rows that need attention, then save only accepted results. The original file remains available for review.</p>
        <button class="button button--primary button--small" type="button" data-explorer-jump-import>Open import workspace</button>
      </article>
      <article class="production-explorer-endpoint-card production-explorer-endpoint-card--secondary">
        <span class="production-explorer-card-kicker">CURRENT PROJECT SNAPSHOT</span>
        <h3>${compounds.length} chemical${compounds.length === 1 ? "" : "s"} · ${measurements.length} uploaded result${measurements.length === 1 ? "" : "s"}</h3>
        <p>${endpoints.length} assay view${endpoints.length === 1 ? "" : "s"} are available. Calculated summaries are preferred when they exist, while uploaded results remain traceable.</p>
        <span class="status-badge status-badge--good">project-scoped</span>
      </article>
    </div>
    <div class="production-explorer-configure-grid">
      <section class="production-explorer-subpanel">
        <div class="section-kicker">WHAT IS CHECKED</div>
        <h3>Files become source-linked results</h3>
        <ul class="production-explorer-checklist">
          <li>Compound ID and chemical structure are checked separately</li>
          <li>Test, result, unit, qualifier, and file row are retained</li>
          <li>Several test conditions can be compared after saving</li>
        </ul>
      </section>
      <section class="production-explorer-subpanel">
        <div class="section-kicker">NEXT</div>
        <h3>Choose tests after saving</h3>
        <p>After results are saved, choose a assay. Build calculated summaries when you need grouped values for a fair comparison.</p>
        <button class="button button--secondary button--small" type="button" data-explorer-go-step="configure">Continue to choose tests</button>
      </section>
    </div>
  </div>`;

  const renderConfigure = () => {
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const cards = [endpointA, endpointB].filter(Boolean).map((endpoint, index) => {
      const stats = endpointStats(endpoint);
      return `<article class="production-explorer-endpoint-card production-explorer-endpoint-card--${index === 0 ? "primary" : "secondary"}">
        <span class="production-explorer-card-kicker">Assay ${index === 0 ? "A" : "B"}</span>
        <h3>${escapeHtml(endpoint.name)}</h3>
        <p>${escapeHtml(endpoint.kind === "summary" ? "Calculated grouped result" : "Uploaded result")}${endpoint.unit ? ` · ${escapeHtml(endpoint.unitLabel)}` : ""}</p>
        <div class="production-explorer-card-stats"><strong>${stats.observed}</strong><span>exact</span><strong>${stats.censored}</strong><span>threshold</span><strong>${stats.missing}</strong><span>no result</span></div>
      </article>`;
    }).join("");
    const available = endpoints.map((endpoint) => {
      const stats = endpointStats(endpoint);
      return `<button class="production-explorer-endpoint-row" type="button" data-explorer-use-endpoint="${escapeHtml(endpoint.id)}" data-explorer-use-slot="${endpoint.id === state.endpointA ? "a" : endpoint.id === state.endpointB ? "b" : "a"}">
        <span><strong>${escapeHtml(endpoint.name)}</strong><small>${escapeHtml(endpoint.kind === "summary" ? "calculated" : "uploaded")}${endpoint.unit ? ` · ${escapeHtml(endpoint.unitLabel)}` : ""}</small></span>
        <span>${stats.observed}/${compounds.length} exact</span>
      </button>`;
    }).join("");
    return `<div class="production-explorer-configure">
      <div class="production-explorer-endpoint-cards">${cards || `<div class="production-explorer-empty">No endpoint records are available yet. Import measurements, then return here.</div>`}</div>
      <div class="production-explorer-configure-grid">
        <section class="production-explorer-subpanel">
          <div class="section-kicker">AVAILABLE TESTS</div>
          <h3>Choose from saved test conditions</h3>
          <p>Calculated grouped results are preferred when available. Uploaded results remain selectable and are labelled separately.</p>
          <div class="production-explorer-endpoint-list">${available}</div>
        </section>
        <section class="production-explorer-subpanel">
          <div class="section-kicker">WHAT THE COMPARISON PRESERVES</div>
          <h3>What this view will not hide</h3>
          <ul class="production-explorer-checklist">
            <li>Units and whether tests can be compared</li>
            <li>Threshold, missing, and review states</li>
            <li>Uploaded versus calculated origin</li>
            <li>Links back to the uploaded results</li>
            <li>Incomplete test comparisons</li>
          </ul>
        </section>
      </div>
    </div>`;
  };

  const renderMatrix = () => {
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const rows = filteredCompounds();
    if (!rows.length) return `<div class="production-explorer-empty">No compounds match this endpoint coverage and search filter.</div>`;
    const body = rows.map((compound) => {
      const selected = compound.registration_id === state.selectedId;
      const recordA = recordFor(compound.registration_id, endpointA);
      const recordB = recordFor(compound.registration_id, endpointB);
      return `<tr class="${selected ? "is-selected" : ""}" data-explorer-compound="${escapeHtml(compound.registration_id)}" tabindex="0">
        <td><strong>${escapeHtml(compound.registration_id)}</strong><small>${escapeHtml(compound.stereochemistry_status || "validated")}</small></td>
        <td><div class="production-explorer-mini-structure">${compoundStructure(compound)}</div></td>
        <td>${cellHtml(recordA, endpointA)}</td>
        ${endpointB ? `<td>${cellHtml(recordB, endpointB)}</td>` : ""}
        <td><span class="production-explorer-row-origin">${recordA?.data_origin === "derived" ? "derived" : "imported"}</span><small>${recordSourceIds(recordA, endpointA?.kind).length} source link${recordSourceIds(recordA, endpointA?.kind).length === 1 ? "" : "s"}</small></td>
      </tr>`;
    }).join("");
    return `<div class="production-explorer-table-wrap">
      <table class="production-explorer-table">
        <thead><tr><th>Compound</th><th>Structure</th><th>${escapeHtml(endpointLabel(endpointA))}</th>${endpointB ? `<th>${escapeHtml(endpointLabel(endpointB))}</th>` : ""}<th>Evidence</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
    <div class="production-explorer-table-note">${rows.length} chemical${rows.length === 1 ? "" : "s"} shown · exact values, threshold results, and no-result states remain distinct.</div>`;
  };

  const plotBounds = (values) => {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || Math.abs(max || 1) * 0.2 || 1;
    return { min: min - range * 0.08, max: max + range * 0.08 };
  };

  const renderComparison = () => {
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const rows = filteredCompounds();
    if (!endpointA || !endpointB) return `<div class="production-explorer-empty"><strong>Choose assay B to compare results.</strong><span>Keep one test selected for a single-test table, or add a compatible second test for the comparison view.</span></div>`;
    if (endpointA.unit && endpointB.unit && endpointA.unit !== endpointB.unit) {
      return `<div class="production-explorer-warning"><strong>Comparison paused: units differ.</strong><span>${escapeHtml(endpointA.unitLabel)} and ${escapeHtml(endpointB.unitLabel)} cannot be plotted directly together. Choose tests with matching units or inspect them separately in the table.</span></div>`;
    }
    const pairs = rows.map((compound) => ({
      compound,
      a: recordFor(compound.registration_id, endpointA),
      b: recordFor(compound.registration_id, endpointB),
    })).filter((pair) => comparableRecord(pair.a, endpointA.kind) && comparableRecord(pair.b, endpointB.kind));
    const excluded = rows.length - pairs.length;
    if (!pairs.length) return `<div class="production-explorer-empty"><strong>No exact comparable pairs to plot.</strong><span>${excluded} result${excluded === 1 ? " is" : "s are"} retained in the table but cannot be treated as exact plotted values.</span></div>`;

    const width = 820;
    const height = 390;
    const margin = { top: 24, right: 28, bottom: 66, left: 72 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const xBounds = plotBounds(pairs.map((pair) => valueForRecord(pair.a, endpointA.kind)));
    const yBounds = plotBounds(pairs.map((pair) => valueForRecord(pair.b, endpointB.kind)));
    const x = (value) => margin.left + ((value - xBounds.min) / (xBounds.max - xBounds.min)) * plotWidth;
    const y = (value) => margin.top + plotHeight - ((value - yBounds.min) / (yBounds.max - yBounds.min)) * plotHeight;
    const ticks = [0, 0.5, 1].map((fraction) => ({
      fraction,
      xValue: xBounds.min + fraction * (xBounds.max - xBounds.min),
      yValue: yBounds.min + fraction * (yBounds.max - yBounds.min),
    }));
    const grid = ticks.map((tick) => `<line x1="${x(tick.xValue)}" y1="${margin.top}" x2="${x(tick.xValue)}" y2="${margin.top + plotHeight}" class="production-explorer-plot-grid"/><line x1="${margin.left}" y1="${y(tick.yValue)}" x2="${margin.left + plotWidth}" y2="${y(tick.yValue)}" class="production-explorer-plot-grid"/><text x="${x(tick.xValue)}" y="${height - 42}" class="production-explorer-plot-tick" text-anchor="middle">${formatNumber(tick.xValue)}</text><text x="${margin.left - 10}" y="${y(tick.yValue) + 4}" class="production-explorer-plot-tick" text-anchor="end">${formatNumber(tick.yValue)}</text>`).join("");
    const points = pairs.map((pair) => {
      const selected = pair.compound.registration_id === state.selectedId;
      const px = x(valueForRecord(pair.a, endpointA.kind));
      const py = y(valueForRecord(pair.b, endpointB.kind));
      return `<circle cx="${px}" cy="${py}" r="${selected ? 8 : 6}" class="production-explorer-plot-point ${selected ? "is-selected" : ""}" data-explorer-compound="${escapeHtml(pair.compound.registration_id)}" tabindex="0" role="button" aria-label="${escapeHtml(pair.compound.registration_id)}: ${escapeHtml(formatNumber(valueForRecord(pair.a, endpointA.kind)))} by ${escapeHtml(formatNumber(valueForRecord(pair.b, endpointB.kind)))}"/>`;
    }).join("");
    return `<div class="production-explorer-comparison">
      <div class="production-explorer-plot-header"><div><strong>${escapeHtml(endpointA.name)} versus ${escapeHtml(endpointB.name)}</strong><span>${pairs.length} exact comparable pair${pairs.length === 1 ? "" : "s"} plotted</span></div><span class="production-explorer-plot-legend"><i></i>select a point to inspect its source</span></div>
      <svg class="production-explorer-plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="Comparison of ${escapeHtml(endpointA.name)} and ${escapeHtml(endpointB.name)}">
        ${grid}
        <line x1="${margin.left}" y1="${margin.top + plotHeight}" x2="${margin.left + plotWidth}" y2="${margin.top + plotHeight}" class="production-explorer-plot-axis"/><line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + plotHeight}" class="production-explorer-plot-axis"/>
        ${points}
        <text x="${margin.left + plotWidth / 2}" y="${height - 12}" class="production-explorer-plot-label" text-anchor="middle">${escapeHtml(endpointA.name)} (${escapeHtml(endpointA.unitLabel || "value")})</text>
        <text x="18" y="${margin.top + plotHeight / 2}" class="production-explorer-plot-label" text-anchor="middle" transform="rotate(-90 18 ${margin.top + plotHeight / 2})">${escapeHtml(endpointB.name)} (${escapeHtml(endpointB.unitLabel || "value")})</text>
      </svg>
      <div class="production-explorer-plot-note">${excluded} chemical${excluded === 1 ? "" : "s"} not plotted because the result is missing, threshold-only, or needs review. The result remains available in the table.</div>
    </div>`;
  };

  const renderAnalysisWorkspace = () => {
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const selectedCompound = compounds.find((item) => item.registration_id === state.selectedId);
    const selectedEndpoints = [endpointA, endpointB].filter(Boolean);
    const rgroup = selectedCompound
      ? rgroupAssignments.find((item) => item.registration_id === selectedCompound.registration_id)
      : null;
    const selectedCliffs = activityCliffs.filter((item) => contextKeyMatchesEndpoint(item.compatibility_key, endpointA)
      && selectedCompound
      && (item.compound_a_registration_id === selectedCompound.registration_id || item.compound_b_registration_id === selectedCompound.registration_id)).slice(0, 8);
    const selectedMmpPairs = (mmp.pairs || []).filter((pair) => contextKeyMatchesEndpoint(pair.compatibility_key, endpointA)
      && selectedCompound
      && (pair.compound_a === selectedCompound.registration_id || pair.compound_b === selectedCompound.registration_id)).slice(0, 8);
    const selectedSelectivity = selectedCompound
      ? selectivityObservations.find((item) => item.registration_id === selectedCompound.registration_id && analysisMatchesEndpoints(item, selectedEndpoints))
        || selectivityObservations.find((item) => item.registration_id === selectedCompound.registration_id && analysisMatchesEndpoints(item, [endpointA]))
      : null;
    const selectedTranslation = selectedCompound
      ? translationObservations.find((item) => item.registration_id === selectedCompound.registration_id && analysisMatchesEndpoints(item, selectedEndpoints))
        || translationObservations.find((item) => item.registration_id === selectedCompound.registration_id && analysisMatchesEndpoints(item, [endpointA]))
      : null;
    const selectedAdme = selectedCompound ? admeObservations.find((item) => item.registration_id === selectedCompound.registration_id) : null;
    const selectedProperty = selectedCompound ? propertyProfiles.find((item) => item.registration_id === selectedCompound.registration_id) : null;
    const selectedPareto = selectedCompound ? paretoObservations.find((item) => item.registration_id === selectedCompound.registration_id) : null;
    const selectedContradictions = selectedCompound ? contradictionObservations.filter((item) => item.registration_id === selectedCompound.registration_id).slice(0, 5) : [];
    const evidenceIds = compoundEvidenceIds(selectedCompound);
    const tile = (label, value, note, kind = "") => `<div class="production-explorer-analysis-tile"><span>${escapeHtml(label)}</span><strong class="${kind ? `production-explorer-analysis-value--${kind}` : ""}">${escapeHtml(value || "—")}</strong><small>${escapeHtml(note || "")}</small></div>`;
    const propertyTiles = selectedProperty
      ? [
        tile("MW", `${formatNumber(descriptorValue(selectedProperty, "molecular_weight"))} Da`, selectedProperty.status || "derived property"),
        tile("LogP", formatNumber(descriptorValue(selectedProperty, "logp")), "derived property"),
        tile("TPSA", `${formatNumber(descriptorValue(selectedProperty, "tpsa"))} Å²`, "derived property"),
        tile("HBD / HBA", `${formatNumber(descriptorValue(selectedProperty, "hbd"), 0)} / ${formatNumber(descriptorValue(selectedProperty, "hba"), 0)}`, selectedProperty.algorithm_version || "property profile"),
      ].join("")
      : tile("Properties", "No profile", "Run the persisted property analysis", "missing");
    const selectivityMarkup = selectedSelectivity
      ? `<div class="production-explorer-analysis-detail"><strong>${escapeHtml(selectedSelectivity.status || "selectivity")}</strong><span>${escapeHtml(`${formatNumber(selectedSelectivity.primary_value, 2, selectedSelectivity.canonical_unit)} vs ${formatNumber(selectedSelectivity.comparator_value, 2, selectedSelectivity.canonical_unit)} ${displayUnit(selectedSelectivity.canonical_unit)}`)}</span><small>Δ ${escapeHtml(formatNumber(selectedSelectivity.selectivity_delta))} · ${escapeHtml(selectedSelectivity.evidence_status || "evidence-linked")}</small></div>`
      : `<div class="production-explorer-analysis-empty">No selectivity observation matches Endpoint A context. Run or select a compatible comparator.</div>`;
    const translationMarkup = selectedTranslation
      ? `<div class="production-explorer-analysis-detail"><strong>${escapeHtml(selectedTranslation.status || "translation")}</strong><span>${escapeHtml(`${formatNumber(selectedTranslation.biochemical_value, 2, selectedTranslation.canonical_unit)} → ${formatNumber(selectedTranslation.cellular_value, 2, selectedTranslation.canonical_unit)} ${displayUnit(selectedTranslation.canonical_unit)}`)}</span><small>Loss ${escapeHtml(formatNumber(selectedTranslation.translation_loss))} · ${escapeHtml(selectedTranslation.evidence_status || "evidence-linked")}</small></div>`
      : `<div class="production-explorer-analysis-empty">No cellular-translation observation matches the selected endpoint context.</div>`;
    const admeMarkup = selectedAdme
      ? `<div class="production-explorer-analysis-detail"><strong>${escapeHtml(selectedAdme.status || "ADME")}</strong><span>${escapeHtml((selectedAdme.observed_contexts || []).join(", ") || "No exact observed contexts")}</span><small>Censored: ${escapeHtml((selectedAdme.censored_contexts || []).join(", ") || "none")} · Missing: ${escapeHtml((selectedAdme.missing_contexts || []).join(", ") || "none")}</small></div>`
      : `<div class="production-explorer-analysis-empty">No ADME evidence panel for this compound.</div>`;
    const rgroupMarkup = rgroup
      ? `<div class="production-explorer-analysis-rgroup"><div class="production-explorer-scaffold-preview">${scaffoldSvg || `<span class="compound-card__no-structure">No scaffold render</span>`}</div><div><strong>${escapeHtml(rgroup.status || "R-group assignment")}</strong><p>Endpoint A context: ${escapeHtml(endpointLabel(endpointA))}</p><code>${displayJson(rgroup.assignments)}</code><small>${escapeHtml(rgroup.algorithm_version || "RDKit R-group analysis")}</small></div></div>`
      : `<div class="production-explorer-analysis-empty">No persisted R-group assignment is available. Run the scaffold analysis from the analysis workspace.</div>`;
    const mmpMarkup = selectedMmpPairs.length
      ? `<div class="production-explorer-table-wrap"><table class="production-explorer-analysis-table"><thead><tr><th>Pair</th><th>Transformation</th><th>Effect</th><th>Similarity</th></tr></thead><tbody>${selectedMmpPairs.map((pair) => {
        const transformation = pair.transformation || {};
        return `<tr><td>${escapeHtml(`${pair.compound_a} ↔ ${pair.compound_b}`)}</td><td>${escapeHtml(`${atomFormula(transformation.a)} → ${atomFormula(transformation.b)}`)}</td><td>${escapeHtml(`${F ? F.delta(pair.effect_value, pair.effect_unit) : formatNumber(pair.effect_value)}`)}</td><td>${escapeHtml(formatNumber(pair.similarity))}</td></tr>`;
      }).join("")}</tbody></table></div>`
      : `<div class="production-explorer-analysis-empty">No endpoint-compatible MMP pair links this compound. Censored and missing measurements remain excluded from MMP effects.</div>`;
    const cliffMarkup = selectedCliffs.length
      ? `<div class="production-explorer-table-wrap"><table class="production-explorer-analysis-table"><thead><tr><th>Pair</th><th>Effect</th><th>Similarity</th><th>Evidence</th></tr></thead><tbody>${selectedCliffs.map((cliff) => `<tr><td>${escapeHtml(`${cliff.compound_a_registration_id} ↔ ${cliff.compound_b_registration_id}`)}</td><td>${escapeHtml(`${F ? F.delta(cliff.effect_value, cliff.effect_unit) : formatNumber(cliff.effect_value)}`)}</td><td>${escapeHtml(formatNumber(cliff.similarity))}</td><td>${escapeHtml(cliff.evidence_status || "eligible observed summary")}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="production-explorer-analysis-empty">No endpoint-compatible activity cliff is linked to this compound.</div>`;
    const contradictionsMarkup = selectedContradictions.length
      ? selectedContradictions.map((item) => `<div class="production-explorer-analysis-detail production-explorer-analysis-detail--warning"><strong>${escapeHtml(item.status || "review")}</strong><span>${escapeHtml(item.compatibility_key || "endpoint context")}</span><small>${escapeHtml(item.reason || item.reconciliation_status || "Unreconciled evidence")}</small></div>`).join("")
      : `<div class="production-explorer-analysis-empty">No persisted contradiction warning for this compound.</div>`;
    const recommendationMarkup = recommendations.length
      ? recommendations.slice(0, 4).map((item) => `<div class="production-explorer-analysis-detail"><strong>${escapeHtml(item.title || item.recommendation_type || item.id)}</strong><span>${escapeHtml(item.status || "generated_review")}</span><small>${escapeHtml(item.rationale || "Generated evidence-gap recommendation")} · ${escapeHtml((item.evidence_ids || []).join(", "))}</small></div>`).join("")
      : `<div class="production-explorer-analysis-empty">No generated recommendations are persisted yet. Run the information-gap workflow first.</div>`;
    const recentHypotheses = hypotheses.slice(0, 3).map((item) => `<div class="production-explorer-analysis-detail"><strong>${escapeHtml(item.status || "untested")}</strong><span>${escapeHtml(item.statement)}</span><small>${escapeHtml(item.rationale || "No rationale recorded")}</small></div>`).join("");
    const designCount = designCandidates.length;
    return `<section class="production-explorer-analysis-workspace" id="productionExplorerAnalysisWorkspace">
      <div class="production-explorer-analysis-heading"><div><div class="section-kicker">LINKED ANALYSIS WORKSPACE</div><h3>Endpoint-aware evidence overlays</h3><p>These panels reuse persisted, versioned analyses. They describe evidence and uncertainty; they do not infer causal SAR conclusions.</p></div><span class="status-badge status-badge--good">${escapeHtml(endpointLabel(endpointA))}</span></div>
      <div class="production-explorer-analysis-grid">
        <section class="production-explorer-analysis-panel production-explorer-analysis-panel--wide"><div class="section-kicker">SCAFFOLD / R-GROUP</div><h4>Structure context for the selected endpoint</h4>${rgroupMarkup}</section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">PROPERTY OVERLAY</div><h4>Deterministic descriptors</h4><div class="production-explorer-analysis-tiles">${propertyTiles}</div></section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">CELLULAR TRANSLATION</div><h4>Biochemical → cellular</h4>${translationMarkup}</section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">SELECTIVITY</div><h4>Primary versus comparator</h4>${selectivityMarkup}</section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">ADME</div><h4>Context-by-context evidence</h4>${admeMarkup}</section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">CONTRADICTIONS</div><h4>Review warnings</h4>${contradictionsMarkup}</section>
        <section class="production-explorer-analysis-panel production-explorer-analysis-panel--wide"><div class="section-kicker">MMP / ACTIVITY CLIFFS</div><h4>Localized changes linked to ${escapeHtml(endpointA?.name || "Endpoint A")}</h4>${mmpMarkup}${cliffMarkup}</section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">PARETO</div><h4>Multi-objective context</h4>${selectedPareto ? `<div class="production-explorer-analysis-detail"><strong>${escapeHtml(selectedPareto.status || "incomplete")}</strong><span>${selectedPareto.is_pareto ? "On the observed front" : "Not on the observed front"}</span><small>${escapeHtml(displayJson(selectedPareto.objective_values))}</small></div>` : `<div class="production-explorer-analysis-empty">No Pareto observation for this compound.</div>`}</section>
      </div>
      <div class="production-explorer-analysis-actions"><button class="button button--secondary button--small" type="button" data-explorer-scroll-target="production-actions">Open analysis runner</button><button class="button button--secondary button--small" type="button" data-explorer-scroll-target="productionEvidenceDetails">Review full evidence audit</button><span>${escapeHtml(`${designCount} curated design candidate${designCount === 1 ? "" : "s"} · ${recommendations.length} generated recommendation${recommendations.length === 1 ? "" : "s"}`)}</span></div>
      <div class="production-explorer-analysis-workflows">
        <section class="production-explorer-analysis-panel"><div class="section-kicker">OPTIONAL HYPOTHESIS</div><h4>Save an untested statement</h4><form data-explorer-hypothesis-form><label>Statement<input name="statement" minlength="8" required placeholder="A compatible change may…"></label><label>Rationale<textarea name="rationale" rows="3" placeholder="Cite the observed evidence and uncertainty."></textarea></label><button class="button button--primary button--small" type="submit">Save hypothesis</button></form>${recentHypotheses || `<div class="production-explorer-analysis-empty">No hypotheses saved in this project.</div>`}</section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">OPTIONAL DESIGN CANDIDATE</div><h4>Prepare a reviewable candidate</h4><p class="production-explorer-analysis-note">Requires a selected compound and persisted evidence IDs. Candidates remain curated hypotheses, never experimental confirmation.</p><form data-explorer-design-form><div class="production-explorer-form-grid"><label>Category<select name="category"><option value="exploit">Exploit</option><option value="test">Test</option><option value="resolve">Resolve</option><option value="fill">Fill</option><option value="explore">Explore</option></select></label><label>Feasibility<select name="feasibility_status"><option value="unreviewed">Unreviewed</option><option value="tractable">Tractable</option><option value="review">Review</option></select></label></div><label>Title<input name="title" minlength="8" required placeholder="Test a supported change"></label><label>Rationale<textarea name="rationale" rows="2" minlength="8" required placeholder="What evidence supports this candidate?"></textarea></label><label>Hypothesis<textarea name="hypothesis" rows="2" minlength="8" required placeholder="What remains untested?"></textarea></label><label>Expected outcome<textarea name="expected_outcome" rows="2" minlength="8" required placeholder="What would this experiment clarify?"></textarea></label><label>Uncertainty<textarea name="uncertainty" rows="2" minlength="8" required placeholder="What could make this wrong?"></textarea></label><label>Structure SMILES <em>optional</em><input name="structure_smiles" placeholder="Leave blank when not designed"></label><input type="hidden" name="evidence_ids" value="${escapeHtml(evidenceIds.join(", "))}"><button class="button button--primary button--small" type="submit" ${selectedCompound ? "" : "disabled"}>Save curated candidate</button></form></section>
        <section class="production-explorer-analysis-panel"><div class="section-kicker">GENERATED GAPS</div><h4>Recommendations awaiting review</h4>${recommendationMarkup}<button class="button button--secondary button--small" type="button" data-explorer-scroll-target="productionRecommendations">Open recommendation review</button></section>
      </div>
    </section>`;
  };

  const renderEvidence = () => {
    const compound = compounds.find((item) => item.registration_id === state.selectedId);
    if (!compound) return `<div class="production-explorer-empty"><strong>Select a compound first.</strong><span>Use the matrix or comparison plot, then return here to inspect source-linked evidence.</span></div>`;
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const selectedEndpoints = [endpointA, endpointB].filter(Boolean);
    const selectedValues = selectedEndpoints.map((endpoint) => {
      const record = recordFor(compound.registration_id, endpoint);
      return `<div class="production-explorer-evidence-value"><span>${escapeHtml(endpointLabel(endpoint))}</span><strong>${escapeHtml(endpoint.kind === "summary" ? summaryLabel(record) : rawLabel(record))}</strong><small>${escapeHtml(statusForRecord(record, endpoint.kind))} · ${recordSourceIds(record, endpoint.kind).length} source link${recordSourceIds(record, endpoint.kind).length === 1 ? "" : "s"}</small></div>`;
    }).join("");
    const rawRows = measurements.filter((measurement) => measurement.registration_id === compound.registration_id).map((measurement) => `<tr><td>${escapeHtml(measurement.assay_name)}</td><td>${escapeHtml(rawLabel(measurement))}</td><td>${escapeHtml(`${formatNumber(measurement.canonical_value, 2, measurement.canonical_unit)} ${displayUnit(measurement.canonical_unit)}`)}</td><td>${escapeHtml(measurement.qualifier || measurement.missing_reason || "—")}</td><td><code>${escapeHtml(measurement.source_row_id || measurement.id)}</code></td></tr>`).join("");
    const summaryRows = summaries.filter((summary) => summary.registration_id === compound.registration_id).map((summary) => `<tr><td>${escapeHtml(endpointName(summary.compatibility_key))}</td><td>${escapeHtml(summaryLabel(summary))}</td><td>${escapeHtml(summary.summary_state || "—")}</td><td>${escapeHtml(`${summary.eligible_measurement_count || 0} eligible · ${summary.censored_measurement_count || 0} censored · ${summary.missing_measurement_count || 0} missing`)}</td><td><code>${escapeHtml(summary.id)}</code></td></tr>`).join("");
    return `<div class="production-explorer-evidence">
      <div class="production-explorer-evidence-hero"><div class="production-explorer-evidence-structure">${compoundStructure(compound)}</div><div><span class="production-explorer-card-kicker">SELECTED COMPOUND</span><h3>${escapeHtml(compound.registration_id)}</h3><p>${escapeHtml(compound.isomeric_smiles || compound.canonical_smiles || "Validated structure")}</p><span class="status-badge status-badge--good">${escapeHtml(compound.data_origin || "imported")}</span></div></div>
      <div class="production-explorer-evidence-values">${selectedValues || `<div class="production-explorer-empty">No endpoint selected.</div>`}</div>
      <section class="production-explorer-subpanel"><div class="section-kicker">RAW OBSERVATIONS</div><h3>Source-linked measurements</h3><div class="production-explorer-table-wrap"><table class="production-explorer-table"><thead><tr><th>Assay</th><th>Raw</th><th>Canonical</th><th>Qualifier/state</th><th>Source row</th></tr></thead><tbody>${rawRows || `<tr><td colspan="5">No raw measurements for this compound.</td></tr>`}</tbody></table></div></section>
      <section class="production-explorer-subpanel"><div class="section-kicker">DERIVED SUMMARIES</div><h3>Versioned endpoint summaries</h3><div class="production-explorer-table-wrap"><table class="production-explorer-table"><thead><tr><th>Endpoint context</th><th>Summary</th><th>State</th><th>Replicates</th><th>Summary ID</th></tr></thead><tbody>${summaryRows || `<tr><td colspan="5">No derived summary for this compound.</td></tr>`}</tbody></table></div></section>
      ${renderAnalysisWorkspace()}
    </div>`;
  };

  const attachCompoundSelection = () => {
    elements.canvas?.querySelectorAll("[data-explorer-compound]").forEach((target) => {
      const select = () => {
        state.selectedId = target.dataset.explorerCompound || "";
        render();
      };
      target.addEventListener("click", select);
      target.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
    });
  };

  const attachEndpointShortcuts = () => {
    elements.canvas?.querySelectorAll("[data-explorer-use-endpoint]").forEach((button) => {
      button.addEventListener("click", () => {
        const slot = button.dataset.explorerUseSlot === "b" ? "endpointB" : "endpointA";
        state[slot] = button.dataset.explorerUseEndpoint || state[slot];
        render();
      });
    });
  };

  const postExplorerJson = async (url, payload) => {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
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

  const attachWorkflowShortcuts = () => {
    elements.canvas?.querySelectorAll("[data-explorer-go-step]").forEach((button) => {
      button.addEventListener("click", () => {
        navigateToStep(button.dataset.explorerGoStep || "configure");
      });
    });
    elements.canvas?.querySelectorAll("[data-explorer-jump-import]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = document.querySelector("#production-actions");
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        else window.location.href = `/workspace/overview?project_id=${encodeURIComponent(projectId)}#production-actions`;
      });
    });
    elements.canvas?.querySelectorAll("[data-explorer-scroll-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const targetId = button.dataset.explorerScrollTarget;
        const target = document.querySelector(`#${targetId}`);
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "start" });
          return;
        }
        const pageForTarget = {
          "production-actions": "overview",
          productionEvidenceDetails: "evidence",
          productionRecommendations: "designs",
        }[targetId] || "analysis";
        window.location.href = `/workspace/${pageForTarget}?project_id=${encodeURIComponent(projectId)}`;
      });
    });
    elements.canvas?.querySelectorAll("[data-explorer-hypothesis-form]").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submit = form.querySelector("button[type=submit]");
        if (submit) submit.disabled = true;
        const formData = new FormData(form);
        try {
          const result = await postExplorerJson("/api/v1/hypotheses", {
            project_id: projectId,
            statement: formData.get("statement"),
            rationale: formData.get("rationale"),
          });
          if (result.hypothesis) hypotheses.unshift(result.hypothesis);
          setStatus("Hypothesis saved as untested and project-scoped.", "success");
          render();
        } catch (error) {
          setStatus(`Hypothesis save failed: ${error.message}`, "error");
          if (submit) submit.disabled = false;
        }
      });
    });
    elements.canvas?.querySelectorAll("[data-explorer-design-form]").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const selectedCompound = compounds.find((item) => item.registration_id === state.selectedId);
        if (!selectedCompound) {
          setStatus("Select a compound before saving a design candidate.", "warning");
          return;
        }
        const formData = new FormData(form);
        const submit = form.querySelector("button[type=submit]");
        if (submit) submit.disabled = true;
        try {
          const result = await postExplorerJson("/api/v1/designs", {
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
              feasibility_reasons: ["Prepared from the endpoint-aware explorer; requires qualified review."],
              information_gain_score: 0.5,
              objective_alignment_score: 0.5,
              parent_compound_id: selectedCompound.id || null,
              structure_smiles: formData.get("structure_smiles") || null,
              status: "proposed",
            }],
          });
          if (Array.isArray(result.candidates)) designCandidates.unshift(...result.candidates);
          setStatus("Curated design candidate saved; experimental confirmation remains false.", "success");
          render();
        } catch (error) {
          setStatus(`Design candidate save failed: ${error.message}`, "error");
          if (submit) submit.disabled = false;
        }
      });
    });
  };

  const render = () => {
    configureControls();
    document.querySelectorAll("[data-explorer-step]").forEach((button) => {
      const active = button.dataset.explorerStep === state.step;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
    });
    if (elements.back) elements.back.disabled = state.step === "import";
    if (elements.next) {
      const nextStep = stepOrder[Math.min(stepOrder.indexOf(state.step) + 1, stepOrder.length - 1)];
      elements.next.textContent = state.step === "evidence" ? "Back to import" : `Next: ${stepLabels[nextStep].toLowerCase()}`;
    }
    if (elements.canvas) {
      if (state.step === "import") {
        elements.canvas.innerHTML = renderImport();
      } else if (!endpoints.length) {
        elements.canvas.innerHTML = `<div class="production-explorer-empty"><strong>No endpoint data is available yet.</strong><span>Use the import workflow above to preview and commit a file, then generate summaries when you are ready to compare derived endpoints.</span></div>`;
      } else if (state.step === "configure") {
        elements.canvas.innerHTML = renderConfigure();
      } else if (state.step === "filter") {
        elements.canvas.innerHTML = renderMatrix();
      } else if (state.step === "compare") {
        elements.canvas.innerHTML = renderComparison();
      } else {
        elements.canvas.innerHTML = renderEvidence();
      }
      attachCompoundSelection();
      attachEndpointShortcuts();
      attachWorkflowShortcuts();
    }
    if (elements.selection) {
      const selected = compounds.find((compound) => compound.registration_id === state.selectedId);
      elements.selection.innerHTML = selected
        ? `<span class="production-explorer-selection-label">Selected</span><strong>${escapeHtml(selected.registration_id)}</strong><button type="button" class="button button--secondary button--small" data-explorer-go-evidence>Inspect evidence</button>`
        : `<span class="production-explorer-selection-label">Selection</span><strong>No compound selected</strong><small>Select a row or plot point to pin evidence.</small>`;
      elements.selection.querySelector("[data-explorer-go-evidence]")?.addEventListener("click", () => {
        navigateToStep("evidence");
      });
    }
    setMessage(`${filteredCompounds().length} compound${filteredCompounds().length === 1 ? "" : "s"} match the current filter.`, "info");
  };

  const persist = (message = "Exploration session saved in this browser.") => {
    try {
      const name = elements.sessionName?.value.trim() || `Exploration ${new Date().toLocaleString()}`;
      const existing = sessions.find((session) => session.name === name);
      const session = {
        id: existing?.id || `session-${Date.now()}`,
        name,
        saved_at: new Date().toISOString(),
        state: { ...state },
      };
      sessions = [session, ...sessions.filter((item) => item.id !== session.id)].slice(0, 24);
      window.localStorage.setItem(sessionsStorageKey, JSON.stringify(sessions));
      window.localStorage.setItem(storageKey, JSON.stringify(state));
      refreshSessionList();
      if (elements.sessionList) elements.sessionList.value = session.id;
      setStatus(message, "success");
    } catch (_error) {
      setStatus("This browser blocked local session saving; the current exploration remains active.", "warning");
    }
  };

  const downloadFile = (filename, content, type) => {
    const blob = new Blob([content], { type });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  };

  const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const exportMatrix = () => {
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const rows = filteredCompounds();
    const header = ["compound_id", "smiles", "endpoint_a", "endpoint_a_value", "endpoint_a_status", "endpoint_a_sources"];
    if (endpointB) header.push("endpoint_b", "endpoint_b_value", "endpoint_b_status", "endpoint_b_sources");
    const lines = [header.map(csvCell).join(",")];
    rows.forEach((compound) => {
      const recordA = recordFor(compound.registration_id, endpointA);
      const values = [
        compound.registration_id,
        compound.isomeric_smiles || compound.canonical_smiles,
        endpointLabel(endpointA),
        endpointA?.kind === "summary" ? summaryLabel(recordA) : rawLabel(recordA),
        statusForRecord(recordA, endpointA?.kind),
        recordSourceIds(recordA, endpointA?.kind).join(";")
      ];
      if (endpointB) {
        const recordB = recordFor(compound.registration_id, endpointB);
        values.push(endpointLabel(endpointB), endpointB.kind === "summary" ? summaryLabel(recordB) : rawLabel(recordB), statusForRecord(recordB, endpointB.kind), recordSourceIds(recordB, endpointB.kind).join(";"));
      }
      lines.push(values.map(csvCell).join(","));
    });
    downloadFile(`sar-exploration-${projectId}.csv`, `${lines.join("\\n")}\\n`, "text/csv;charset=utf-8");
    setStatus("Current exploration matrix exported with endpoint and provenance columns.", "success");
  };

  const exportBundle = () => {
    const endpointA = endpointFor(state.endpointA);
    const endpointB = state.endpointB === "none" ? null : endpointFor(state.endpointB);
    const rows = filteredCompounds();
    const registrationIds = new Set(rows.map((compound) => compound.registration_id));
    const scopedMeasurements = measurements.filter((item) => registrationIds.has(item.registration_id));
    const scopedSummaries = summaries.filter((item) => registrationIds.has(item.registration_id));
    const sourceMeasurementIds = [...new Set([
      ...scopedMeasurements.map((item) => item.id),
      ...scopedSummaries.flatMap((item) => item.source_measurement_ids || []),
    ].filter(Boolean).map(String))];
    const analysisRunIds = [...new Set([
      ...rgroupAssignments,
      ...activityCliffs,
      ...selectivityObservations,
      ...translationObservations,
      ...admeObservations,
      ...propertyProfiles,
      ...paretoObservations,
      ...contradictionObservations,
    ].map((item) => item.analysis_run_id).concat(mmp.analysis_run_id || []).filter(Boolean).map(String))];
    const bundle = {
      schema_version: "sar-exploration-bundle-v1",
      project_id: projectId,
      exported_at: new Date().toISOString(),
      exploration: {
        state: { ...state },
        endpoint_a: endpointA ? { ...endpointA, compatibility_key: endpointCompatibilityKey(endpointA) } : null,
        endpoint_b: endpointB ? { ...endpointB, compatibility_key: endpointCompatibilityKey(endpointB) } : null,
        endpoint_catalog: endpoints.map((endpoint) => ({ ...endpoint, compatibility_key: endpointCompatibilityKey(endpoint) })),
        filtered_compound_ids: [...registrationIds],
      },
      records: {
        compounds: rows,
        measurements: scopedMeasurements,
        summaries: scopedSummaries,
      },
      analyses: {
        rgroup_assignments: rgroupAssignments,
        activity_cliffs: activityCliffs,
        mmp,
        selectivity: selectivityObservations,
        cellular_translation: translationObservations,
        adme: admeObservations,
        properties: propertyProfiles,
        pareto: paretoObservations,
        contradictions: contradictionObservations,
      },
      optional_workflows: {
        hypotheses,
        design_candidates: designCandidates,
        generated_recommendations: recommendations,
      },
      provenance: {
        source_measurement_ids: sourceMeasurementIds,
        analysis_run_ids: analysisRunIds,
        data_origin_boundary: "project-scoped persisted records; imported, derived, curated, and generated origins remain distinct",
        exact_plot_policy: "censored, missing, review, and non-exact records are excluded from exact comparison plots but retained in records",
      },
    };
    downloadFile(`sar-exploration-${projectId}-bundle.json`, `${JSON.stringify(bundle, null, 2)}\\n`, "application/json;charset=utf-8");
    setStatus("Reproducibility bundle exported with state, endpoint context, analysis versions, and provenance links.", "success");
  };

  elements.endpointA?.addEventListener("change", (event) => {
    state.endpointA = event.target.value;
    if (state.endpointA === state.endpointB) state.endpointB = "none";
    render();
  });
  elements.endpointB?.addEventListener("change", (event) => {
    state.endpointB = event.target.value;
    if (state.endpointA === state.endpointB) state.endpointB = "none";
    render();
  });
  elements.coverage?.addEventListener("change", (event) => {
    state.coverage = event.target.value;
    render();
  });
  elements.search?.addEventListener("input", (event) => {
    state.query = event.target.value;
    render();
  });
  document.querySelectorAll("[data-explorer-step]").forEach((button) => {
    button.addEventListener("click", () => {
      navigateToStep(button.dataset.explorerStep || "configure");
    });
  });
  elements.back?.addEventListener("click", () => {
    const index = Math.max(0, stepOrder.indexOf(state.step) - 1);
    navigateToStep(stepOrder[index]);
  });
  elements.next?.addEventListener("click", () => {
    const index = stepOrder.indexOf(state.step);
    navigateToStep(index === stepOrder.length - 1 ? stepOrder[0] : stepOrder[index + 1]);
  });
  elements.save?.addEventListener("click", () => persist());
  elements.sessionList?.addEventListener("change", (event) => {
    const session = sessions.find((item) => item.id === event.target.value);
    if (!session) return;
    state.step = ["import", "configure", "filter", "compare", "evidence"].includes(session.state.step) ? session.state.step : "import";
    state.endpointA = endpointById.has(session.state.endpointA) ? session.state.endpointA : defaultA;
    state.endpointB = session.state.endpointB === "none" || endpointById.has(session.state.endpointB) ? session.state.endpointB : defaultB;
    state.coverage = ["all", "endpoint-a", "both", "review"].includes(session.state.coverage) ? session.state.coverage : "all";
    state.query = typeof session.state.query === "string" ? session.state.query : "";
    state.selectedId = typeof session.state.selectedId === "string" ? session.state.selectedId : "";
    try { window.localStorage.setItem(storageKey, JSON.stringify(state)); } catch (_error) { /* ignore storage errors */ }
    setStatus(`Loaded exploration session: ${session.name}.`, "success");
    render();
  });
  elements.export?.addEventListener("click", exportMatrix);
  elements.exportBundle?.addEventListener("click", exportBundle);
  elements.reset?.addEventListener("click", () => {
    state.step = "import";
    state.endpointA = defaultA;
    state.endpointB = defaultB;
    state.coverage = "all";
    state.query = "";
    state.selectedId = "";
    try { window.localStorage.removeItem(storageKey); } catch (_error) { /* ignore storage errors */ }
    setStatus("Exploration reset to the project defaults.", "success");
    render();
  });

  if (!endpoints.length) setStatus("Waiting for imported endpoint data.", "warning");
  else setStatus(`${endpoints.length} endpoint views available.`, "success");
  render();
})();
