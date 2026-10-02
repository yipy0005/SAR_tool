// Interactive Find patterns explorer.
//
// Three linked views over data the page has already loaded: the latest saved
// summary per compound and result, the saved pharmacophore R-group run,
// calculated properties, and saved matched-pair / activity-cliff records.
//   · R-group effects     one R-site at a time; results grouped by what sits there
//   · Activity landscape  every compound pair: structural similarity vs result difference
//   · Property space      compounds on chosen property or result axes, incl. LipE and LE
// A compound or pair selected in one view stays selected in the others and is
// described in the inspector. Presentation-only: nothing is written to the
// project and no stored value changes. Vanilla JS + SVG, so it runs under the
// page's script-src 'self' policy (no inline handlers, no external code).
(() => {
  "use strict";

  const root = document.querySelector("[data-pattern-explorer]");
  const source = document.getElementById("patternExplorerData");
  if (!root || !source) return;

  let data;
  try {
    data = JSON.parse(source.textContent || "{}");
  } catch (_error) {
    return;
  }
  const compounds = Array.isArray(data.compounds) ? data.compounds : [];
  const endpoints = Array.isArray(data.endpoints) ? data.endpoints : [];
  if (compounds.length < 2 || !endpoints.some((endpoint) => endpoint.exact > 0)) return;

  // Shared number/unit formatting (static/sar_format.js); a small fallback keeps the view usable.
  const F = window.SARFormat || {
    number: (value, _unit = "", digits = null) => {
      const numeric = Number(value);
      if (value === null || value === undefined || value === "" || !Number.isFinite(numeric)) return "—";
      return digits === null ? String(Number(numeric.toPrecision(3))) : numeric.toFixed(digits);
    },
    measure: (value, unit = "", qualifier = "") => `${qualifier && qualifier !== "=" ? `${qualifier} ` : ""}${Number(Number(value).toPrecision(3))}${unit ? ` ${unit}` : ""}`,
    delta: (value, unit = "") => `${value > 0 ? "+" : value < 0 ? "−" : "±"}${Math.abs(value).toFixed(2)}${unit ? ` ${unit}` : ""}`,
  };

  // ------------------------------------------------------------------ setup
  const NOISE = 0.3; // log units (≈ 2-fold): smaller differences read as "similar"
  const MAX_LANDSCAPE_POINTS = 4000;
  const LABEL_LIMIT = 30; // print compound IDs beside dots up to this many compounds
  const CLIFF_LIST_LIMIT = 12;
  const TABS = ["sites", "landscape", "properties"];
  const GROUPS = ["substituent", "feature", "ring", "rb"];
  const OUTSIDE = "Outside shared core";
  const NOT_DECOMPOSED = "Not decomposed";
  const PROPERTY_AXES = [
    { key: "clogp", label: "cLogP", short: "cLogP", digits: 2 },
    { key: "mw", label: "MW (Da)", short: "MW", digits: 0 },
    { key: "tpsa", label: "TPSA (Å²)", short: "TPSA", digits: 0 },
    { key: "hbd", label: "H-bond donors", short: "HBD", digits: 0 },
    { key: "hba", label: "H-bond acceptors", short: "HBA", digits: 0 },
    { key: "rb", label: "Rotatable bonds", short: "RB", digits: 0 },
    { key: "rings", label: "Rings", short: "Rings", digits: 0 },
    { key: "hac", label: "Heavy atoms", short: "HAC", digits: 0 },
    { key: "fsp3", label: "Fsp³", short: "Fsp³", digits: 2 },
  ];

  const sites = (Array.isArray(data.sites) ? data.sites : []).filter((site) => site && site.label);
  const siteLabels = sites.map((site) => site.label);
  const links = data.links || {};
  const referenceIndex = Number.isInteger(data.reference) && compounds[data.reference] ? data.reference : null;
  const pairKey = (i, j) => (i < j ? `${i}-${j}` : `${j}-${i}`);
  const tanimoto = new Map((data.pairs || []).map(([i, j, similarity]) => [pairKey(i, j), similarity]));
  const mcsOverlap = new Map((data.mcs || []).map(([i, j, similarity]) => [pairKey(i, j), similarity]));
  const savedCliffs = new Map();
  (data.saved_cliffs || []).forEach((record) => {
    const key = pairKey(record.i, record.j);
    if (!savedCliffs.has(key)) savedCliffs.set(key, []);
    savedCliffs.get(key).push(record);
  });
  const endpointByKey = new Map(endpoints.map((endpoint, index) => [endpoint.key, index]));
  const compoundById = new Map(compounds.map((compound, index) => [compound.id, index]));
  const everyIndex = compounds.map((_compound, index) => index);

  const find = (selector) => root.querySelector(selector);
  const el = {
    endpoint: find("[data-pattern-endpoint]"),
    compare: find("[data-pattern-compare]"),
    tablist: find("[role='tablist']"),
    tabs: [...root.querySelectorAll("[data-pattern-tab]")],
    panels: [...root.querySelectorAll("[data-pattern-panel]")],
    main: find(".pattern-explorer__main"),
    siteMap: find("[data-pattern-site-map]"),
    siteSummary: find("[data-pattern-site-summary]"),
    group: find("[data-pattern-group]"),
    strip: find("[data-pattern-strip]"),
    siteMode: find("[data-pattern-site-mode]"),
    comboControls: find("[data-pattern-combo-controls]"),
    comboRows: find("[data-pattern-combo-rows]"),
    comboCols: find("[data-pattern-combo-cols]"),
    comboSplit: find("[data-pattern-combo-split]"),
    comboLoose: find("[data-pattern-combo-loose]"),
    oneLegend: find("[data-pattern-one-legend]"),
    matrix: find("[data-pattern-matrix]"),
    sim: find("[data-pattern-sim]"),
    simOut: find("[data-pattern-sim-out]"),
    delta: find("[data-pattern-delta]"),
    deltaOut: find("[data-pattern-delta-out]"),
    deltaLabel: find("[data-pattern-delta-label]"),
    single: find("[data-pattern-single]"),
    landscape: find("[data-pattern-landscape]"),
    cliffs: find("[data-pattern-cliffs]"),
    x: find("[data-pattern-x]"),
    y: find("[data-pattern-y]"),
    colour: find("[data-pattern-colour]"),
    propertyHelp: find("[data-pattern-property-help]"),
    properties: find("[data-pattern-properties]"),
    legend: find("[data-pattern-property-legend]"),
    inspector: find("[data-pattern-inspector]"),
    live: find("[data-pattern-live]"),
    note: find("[data-pattern-note]"),
    highlight: find("[data-pattern-highlight]"),
  };
  if (el.inspector) el.inspector.tabIndex = -1;

  // ------------------------------------------------------------- formatting
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const cssEscape = (value) => (window.CSS && typeof window.CSS.escape === "function" ? window.CSS.escape(String(value)) : String(value).replace(/["\\]/g, "\\$&"));
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const at = (value) => Math.round(value * 10) / 10;
  const reg = (index) => (index === null || index === undefined || !compounds[index] ? "—" : compounds[index].reg);
  const plural = (count, word, many = `${word}s`) => `${count} ${count === 1 ? word : many}`;
  const joinList = (items) => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`);
  const truncate = (text, length) => {
    const value = String(text ?? "");
    return value.length > length ? `${value.slice(0, Math.max(1, length - 1))}…` : value;
  };
  const median = (values) => {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  };
  const extent = (values) => {
    let low = Infinity;
    let high = -Infinity;
    values.forEach((value) => {
      if (value === null || !Number.isFinite(value)) return;
      low = Math.min(low, value);
      high = Math.max(high, value);
    });
    return low <= high ? [low, high] : [0, 1];
  };
  const tickLabel = (value) => {
    const magnitude = Math.abs(value);
    if (magnitude >= 1000) return F.number(value);
    if (magnitude > 0 && magnitude < 0.01) return value.toExponential(0);
    return String(Number(value.toPrecision(3)));
  };

  // ------------------------------------------------------ values and deltas
  // Log-scale results subtract; positive linear results compare as log10 fold change.
  const deltaOf = (from, to, endpoint) => {
    if (from === null || to === null || from === undefined || to === undefined) return null;
    if (endpoint.scale === "fold") return from > 0 && to > 0 ? Math.log10(to / from) : null;
    return to - from;
  };
  const betterSign = (endpoint) => (endpoint.direction === "higher" ? 1 : endpoint.direction === "lower" ? -1 : 0);
  const tone = (delta, endpoint) => {
    if (delta === null || delta === undefined || !Number.isFinite(delta)) return "none";
    const sign = betterSign(endpoint);
    if (!sign) return "neutral";
    if (Math.abs(delta) < (endpoint.scale === "linear" ? 1e-12 : NOISE)) return "similar";
    return delta * sign > 0 ? "better" : "worse";
  };
  const fmtValue = (value, endpoint, withUnit = true) => {
    if (!value) return "—";
    const qualifier = value.s === "c" ? value.q || "" : "";
    if (withUnit) return F.measure(value.v, endpoint.unit, qualifier);
    return `${qualifier ? `${qualifier} ` : ""}${F.number(value.v, endpoint.unit)}`;
  };
  const fmtDelta = (delta, endpoint, compact = false) => {
    if (delta === null || delta === undefined || !Number.isFinite(delta)) return "—";
    if (endpoint.scale === "fold") {
      const fold = Math.pow(10, Math.abs(delta));
      if (fold < 1.05) return compact ? "≈ 1×" : "no fold change";
      const text = `${F.number(fold)}×`;
      return compact ? `${delta > 0 ? "↑" : "↓"} ${text}` : `${text} ${delta > 0 ? "higher" : "lower"}`;
    }
    const text = F.delta(delta, endpoint.unit);
    return compact ? text.split(" ")[0] : text;
  };
  const thresholdText = (value, endpoint) => {
    if (endpoint.scale === "fold") return `${F.number(Math.pow(10, value))}×`;
    if (endpoint.scale === "log") return `${value.toFixed(1)} ${endpoint.unit_label || ""}`.trim();
    return F.measure(value, endpoint.unit);
  };
  const directionText = (endpoint) => (endpoint.direction === "higher" ? "higher is better →" : endpoint.direction === "lower" ? "← lower is better" : "no better/worse direction declared");
  const endpointTitle = (endpoint) => `${endpoint.name}${endpoint.unit_label ? ` (${endpoint.unit_label}${endpoint.scale === "fold" ? ", log scale" : ""})` : ""}`;

  // ------------------------------------------------------- structure helpers
  const onCore = (index) => compounds[index]?.status === "assigned";
  const siteOf = (index, label) => compounds[index]?.sites?.[label] || null;
  const isSubstituted = (site) => Boolean(site && (site.smiles || (site.name && site.name !== "H")));
  const siteKey = (site) => (!site ? "" : site.smiles || (site.name && site.name !== "H" ? `name:${site.name}` : ""));
  const siteDetail = (site) => (isSubstituted(site)
    ? [site.features.length ? site.features.join(", ") : "no classified feature", `RB ${site.rb}`, site.rings ? plural(site.rings, "ring") : "acyclic"].join(" · ")
    : "unsubstituted");
  // Strip rows keep the label short: the feature, plus ring / rotatable-bond counts when non-zero.
  const compactDetail = (site) => {
    if (!isSubstituted(site)) return "unsubstituted";
    const parts = [site.features.length ? site.features.join(", ") : "no classified feature"];
    if (site.rings) parts.push(plural(site.rings, "ring"));
    if (site.rb) parts.push(`${site.rb} RB`);
    return parts.join(" · ");
  };
  // True when two compounds share the core and every R-group except the one at `label`.
  const differsOnlyAt = (index, other, label) => {
    if (!onCore(index) || !onCore(other) || compounds[index].core !== compounds[other].core) return false;
    return siteLabels.every((site) => site === label || siteKey(siteOf(index, site)) === siteKey(siteOf(other, site)));
  };
  const pairChange = (i, j) => {
    if (!onCore(i) || !onCore(j)) return { known: false, sites: [], core: false, single: false };
    const changed = siteLabels.filter((label) => siteKey(siteOf(i, label)) !== siteKey(siteOf(j, label)));
    const core = compounds[i].core !== compounds[j].core;
    return { known: true, sites: changed, core, single: (changed.length === 1 && !core) || (changed.length === 0 && core) };
  };
  const coreText = (from, to) => {
    const note = compounds[to].core_note || compounds[from].core_note;
    return note || "Core atoms differ";
  };
  const changeText = (from, to) => {
    const change = pairChange(from, to);
    if (!change.known) {
      const outside = [from, to].filter((index) => !onCore(index)).map(reg);
      return `${joinList(outside)} ${outside.length === 1 ? "is" : "are"} outside the shared core`;
    }
    const parts = change.sites.map((label) => `${label}: ${siteOf(from, label)?.name || "H"} → ${siteOf(to, label)?.name || "H"}`);
    if (change.core) parts.push(`Core: ${coreText(from, to)}`);
    return parts.join(" · ") || "Same R-groups and core";
  };
  const statusText = (status) => ({
    unmatched: "Outside the shared core, so it has no R-site assignment.",
    invalid: "The structure could not be decomposed onto the shared core.",
    not_in_run: "Added after the last pattern check. Refresh pattern checks to place it on R-sites.",
    no_run: "No pharmacophore R-group run yet.",
  })[status] || "No R-site assignment.";

  const structureSvg = (index, className = "pattern-structure") => `<span class="${className}"><svg viewBox="0 0 320 180" role="img" aria-label="Structure of ${esc(reg(index))}"><use href="#${esc(compounds[index].symbol)}"></use></svg></span>`;
  const fragmentSvg = (site) => (site && site.frag
    ? `<span class="pattern-frag"><svg viewBox="0 0 130 80" role="img" aria-label="${esc(site.name)}"><use href="#${esc(site.frag)}"></use></svg></span>`
    : `<span class="pattern-frag" aria-hidden="true">${esc(truncate(site?.name || "H", 7))}</span>`);
  const svgOpen = (width, height, label) => `<svg class="pattern-svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="group" aria-label="${esc(label)}">`;
  const pointGroup = (key, label, inner, extraClass = "") => `<g class="pe-point${extraClass ? ` ${extraClass}` : ""}" data-pe-key="${key}" role="button" tabindex="-1" aria-pressed="false" aria-label="${esc(label)}">${inner}</g>`;
  const chartWidth = (container) => Math.max(300, Math.round(container.getBoundingClientRect().width || container.clientWidth || 640));

  // ------------------------------------------------------------------ state
  const projectId = document.querySelector("[data-production-project]")?.dataset.productionProject || "";
  const storageKey = `sar-pattern-explorer:${projectId}`;
  const saved = (() => {
    try {
      return JSON.parse(window.sessionStorage.getItem(storageKey) || "{}") || {};
    } catch (_error) {
      return {};
    }
  })();
  const usableEndpoint = (key) => {
    const index = endpointByKey.get(key);
    return index !== undefined && endpoints[index].exact > 0 ? index : null;
  };
  const savedTabUsable = TABS.includes(saved.tab) && (saved.tab !== "sites" || siteLabels.length > 0);
  const state = {
    endpoint: usableEndpoint(saved.endpoint) ?? usableEndpoint(data.default_endpoint) ?? endpoints.findIndex((endpoint) => endpoint.exact > 0),
    compare: compoundById.get(saved.compare) ?? referenceIndex,
    tab: savedTabUsable ? saved.tab : siteLabels.length ? "sites" : "landscape",
    site: siteLabels.includes(saved.site) ? saved.site : siteLabels.includes(data.default_site) ? data.default_site : siteLabels[0] || null,
    group: GROUPS.includes(saved.group) ? saved.group : "substituent",
    // Combine sites: rows × columns, optionally one grid per group at a third site.
    siteMode: saved.siteMode === "combine" && siteLabels.length > 1 ? "combine" : "one",
    rows: siteLabels.includes(saved.rows) ? saved.rows : null,
    cols: siteLabels.includes(saved.cols) ? saved.cols : null,
    split: siteLabels.includes(saved.split) ? saved.split : "",
    loose: Boolean(saved.loose),
    sim: Number.isFinite(saved.sim) ? clamp(saved.sim, 0.3, 0.95) : 0.5,
    delta: Number.isFinite(saved.delta) ? saved.delta : null,
    deltaFor: saved.deltaFor && typeof saved.deltaFor === "object" ? saved.deltaFor : null,
    single: Boolean(saved.single),
    x: typeof saved.x === "string" ? saved.x : "p:clogp",
    y: typeof saved.y === "string" ? saved.y : null,
    colour: typeof saved.colour === "string" ? saved.colour : null,
    selection: null,
  };
  if (saved.selection && Array.isArray(saved.selection.ids)) {
    const picked = saved.selection.ids.map((id) => compoundById.get(id));
    if (picked.length === 1 && picked[0] !== undefined) state.selection = { type: "compound", i: picked[0] };
    if (picked.length === 2 && picked.every((index) => index !== undefined)) state.selection = { type: "pair", i: Math.min(...picked), j: Math.max(...picked) };
  }
  // Find by structure on another page links here with ?compare=<compound id>.
  const requestedCompare = compoundById.get(new URLSearchParams(window.location.search).get("compare") || "");
  if (requestedCompare !== undefined) state.compare = requestedCompare;
  // Substructure matches shared by Find by structure (static/structure_search.js) through session storage.
  const highlightKey = `sar-structure-highlight:${projectId}`;
  state.highlight = (() => {
    try {
      const stored = JSON.parse(window.sessionStorage.getItem(highlightKey) || "null");
      if (!stored || !Array.isArray(stored.ids)) return null;
      const ids = new Set(stored.ids.map((id) => compoundById.get(id)).filter((index) => index !== undefined));
      return { ids, smarts: String(stored.smarts || ""), label: String(stored.label || "substructure") };
    } catch (_error) {
      return null;
    }
  })();

  const save = () => {
    try {
      const chosen = state.selection ? (state.selection.type === "compound" ? [state.selection.i] : [state.selection.i, state.selection.j]) : null;
      window.sessionStorage.setItem(storageKey, JSON.stringify({
        endpoint: endpoints[state.endpoint]?.key,
        compare: state.compare !== null ? compounds[state.compare]?.id : null,
        tab: state.tab, site: state.site, group: state.group, sim: state.sim, delta: state.delta, deltaFor: state.deltaFor,
        single: state.single, x: state.x, y: state.y, colour: state.colour,
        siteMode: state.siteMode, rows: state.rows, cols: state.cols, split: state.split, loose: state.loose,
        selection: chosen ? { ids: chosen.map((index) => compounds[index].id) } : null,
      }));
    } catch (_error) {
      // Session storage can be unavailable (private browsing); the explorer still works.
    }
  };

  const valueOf = (index, e = state.endpoint) => {
    const values = compounds[index] && Array.isArray(compounds[index].values) ? compounds[index].values : [];
    return values[e] || null;
  };
  const exactOf = (index, e = state.endpoint) => {
    const value = valueOf(index, e);
    return value && value.s === "e" && Number.isFinite(value.v) ? value.v : null;
  };
  // The chosen comparison compound, or the pharmacophore reference / first exact compound
  // when the chosen one has no exact result for the current endpoint.
  const compareIndex = () => {
    if (state.compare !== null && exactOf(state.compare) !== null) return state.compare;
    if (referenceIndex !== null && exactOf(referenceIndex) !== null) return referenceIndex;
    const first = everyIndex.find((index) => exactOf(index) !== null);
    return first === undefined ? null : first;
  };
  // Pairs read "from → to": from the comparison compound when it is in the pair,
  // otherwise from the less favourable result to the more favourable one.
  const orient = (i, j) => {
    const c = compareIndex();
    if (i === c) return [i, j];
    if (j === c) return [j, i];
    const sign = betterSign(endpoints[state.endpoint]) || 1;
    const a = exactOf(i);
    const b = exactOf(j);
    return a !== null && b !== null && (b - a) * sign < 0 ? [j, i] : [i, j];
  };

  // Compounds that share the core and every R-group except the one at `label`, bucketed.
  const siteBuckets = (label) => {
    const buckets = new Map();
    everyIndex.forEach((index) => {
      if (!onCore(index) || exactOf(index) === null) return;
      const signature = [compounds[index].core, ...siteLabels.filter((site) => site !== label).map((site) => siteKey(siteOf(index, site)))].join("|");
      if (!buckets.has(signature)) buckets.set(signature, []);
      buckets.get(signature).push(index);
    });
    return buckets;
  };
  // When the comparison compound has no single-site partner at `label`, suggest one that does.
  const comparisonHint = (label) => {
    const c = compareIndex();
    let current = false;
    let best = null;
    siteBuckets(label).forEach((members) => {
      const distinct = new Set(members.map((index) => siteKey(siteOf(index, label)))).size;
      if (distinct < 2) return;
      if (members.includes(c)) current = true;
      if (!best || distinct > best.distinct) best = { members, distinct };
    });
    if (current || !best) return null;
    const parent = best.members.find((index) => !isSubstituted(siteOf(index, label))) ?? best.members[0];
    return { candidate: parent, partners: best.members.filter((index) => index !== parent) };
  };

  // ------------------------------------------------------------------ axes
  const niceTicks = (low, high, count = 5) => {
    const span = high - low;
    if (!(span > 0)) return [low];
    const rough = span / Math.max(1, count);
    const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    const residual = rough / magnitude;
    const step = (residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10) * magnitude;
    const ticks = [];
    for (let value = Math.ceil(low / step - 1e-9) * step; value <= high + step * 1e-9; value += step) {
      ticks.push(Math.abs(value) < step * 1e-9 ? 0 : Number(value.toPrecision(12)));
    }
    return ticks;
  };
  // 1-2-5 ticks for a log axis; `low` and `high` are log10 values.
  const logTicks = (low, high) => {
    const ticks = [];
    for (let power = Math.floor(low); power <= Math.ceil(high); power += 1) {
      [1, 2, 5].forEach((mantissa) => {
        const value = mantissa * Math.pow(10, power);
        const position = Math.log10(value);
        if (position >= low - 1e-9 && position <= high + 1e-9) ticks.push(Number(value.toPrecision(12)));
      });
    }
    if (ticks.length > 8) return ticks.filter((value) => Math.abs(Math.log10(value) - Math.round(Math.log10(value))) < 1e-9);
    if (ticks.length < 2) return [Number(Math.pow(10, low).toPrecision(2)), Number(Math.pow(10, high).toPrecision(2))];
    return ticks;
  };
  const makeAxis = (values, log, range, pad = 0.07) => {
    const transform = log ? (value) => (value > 0 ? Math.log10(value) : null) : (value) => value;
    let [low, high] = extent(values.map(transform));
    if (high - low < 1e-9) {
      const widen = log ? 0.5 : Math.max(Math.abs(low) * 0.1, 0.5);
      low -= widen;
      high += widen;
    }
    const span = high - low;
    low -= span * pad;
    high += span * pad;
    const scale = (value) => {
      const position = transform(value);
      return position === null || !Number.isFinite(position) ? null : range[0] + ((position - low) / (high - low)) * (range[1] - range[0]);
    };
    const ticks = log ? logTicks(low, high) : niceTicks(low, high, clamp(Math.round(Math.abs(range[1] - range[0]) / 90), 3, 7));
    return { log, low, high, scale, ticks, range };
  };

  // Theme colours are read from CSS so continuous colour scales follow light/dark mode.
  const cssColour = (name, fallback) => {
    const text = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
    if (!match) return fallback;
    const hex = match[1].length === 3 ? match[1].split("").map((char) => char + char).join("") : match[1];
    return [0, 2, 4].map((offset) => parseInt(hex.slice(offset, offset + 2), 16));
  };
  const ramp = (position, stops) => {
    const scaled = clamp(Number.isFinite(position) ? position : 0.5, 0, 1) * (stops.length - 1);
    const segment = Math.min(stops.length - 2, Math.floor(scaled));
    const fraction = scaled - segment;
    return stops[segment].map((channel, k) => Math.round(channel + (stops[segment + 1][k] - channel) * fraction));
  };
  const rgb = (colour) => `rgb(${colour.join(", ")})`;

  // --------------------------------------------------------------- toolbar
  let compareOptionsFor = null;
  const renderToolbar = () => {
    if (el.endpoint && !el.endpoint.options.length) {
      el.endpoint.innerHTML = endpoints.map((endpoint, index) => (endpoint.exact
        ? `<option value="${index}">${esc(endpoint.name)} · ${esc(endpoint.unit_label || "no unit")} (${endpoint.exact} exact)</option>`
        : "")).join("");
    }
    if (el.endpoint) el.endpoint.value = String(state.endpoint);
    if (el.compare && compareOptionsFor !== state.endpoint) {
      compareOptionsFor = state.endpoint;
      el.compare.innerHTML = everyIndex.map((index) => {
        const exact = exactOf(index) !== null;
        const tags = [index === referenceIndex ? "reference" : "", exact ? "" : "no exact result"].filter(Boolean);
        return `<option value="${index}"${exact ? "" : " disabled"}>${esc(reg(index))}${tags.length ? ` · ${esc(tags.join(", "))}` : ""}</option>`;
      }).join("");
    }
    const c = compareIndex();
    if (el.compare && c !== null) el.compare.value = String(c);
    el.tabs.forEach((tab) => {
      const active = tab.dataset.patternTab === state.tab;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    el.panels.forEach((panel) => {
      panel.hidden = panel.dataset.patternPanel !== state.tab;
    });
    if (el.group) el.group.value = state.group;
  };

  // ------------------------------------------------------ R-group effects
  const substitutedCount = (label) => everyIndex.filter((index) => onCore(index) && isSubstituted(siteOf(index, label))).length;
  const renderSitePins = () => {
    if (!el.siteMap) return;
    let layer = el.siteMap.querySelector(".pattern-site-pins");
    if (!layer) {
      layer = document.createElement("div");
      layer.className = "pattern-site-pins";
      el.siteMap.append(layer);
    }
    layer.innerHTML = sites.filter((site) => Number.isFinite(site.x) && Number.isFinite(site.y)).map((site) => {
      const active = state.siteMode === "combine" && siteLabels.length > 1
        ? [state.rows, state.cols, state.split].includes(site.label)
        : site.label === state.site;
      return `<button type="button" class="pattern-site-pin${active ? " is-active" : ""}${site.y < 0.24 ? " is-below" : ""}" data-pe-site="${esc(site.label)}" aria-pressed="${active}" aria-label="Show R-group effects at ${esc(site.label)}" style="left:${(site.x * 100).toFixed(2)}%;top:${(site.y * 100).toFixed(2)}%">${esc(site.label)}</button>`;
    }).join("");
  };
  const renderSiteSummary = () => {
    if (!el.siteSummary || !state.site) return;
    const label = state.site;
    const chips = sites.map((site) => `<button type="button" class="pattern-site-chip" data-pe-site="${esc(site.label)}" aria-pressed="${site.label === label}"><b>${esc(site.label)}</b>${esc(plural(substitutedCount(site.label), "compound"))} substituted</button>`).join("");
    const members = everyIndex.filter(onCore);
    const groups = new Set(members.map((index) => siteKey(siteOf(index, label))));
    const substituted = members.filter((index) => isSubstituted(siteOf(index, label)));
    const features = new Map();
    substituted.forEach((index) => {
      const list = siteOf(index, label).features;
      (list.length ? list : ["no classified feature"]).forEach((feature) => features.set(feature, (features.get(feature) || 0) + 1));
    });
    const featureText = [...features.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([feature, count]) => `<span class="pattern-site-facts__item">${esc(feature)} (${count})</span>`)
      .join(" · ") || "none";
    const rings = substituted.filter((index) => siteOf(index, label).rings > 0).length;
    const hint = comparisonHint(label);
    const hintHtml = hint
      ? `<p class="pattern-site-hint">No compound differs from ${esc(reg(compareIndex()))} only at ${esc(label)}. <button type="button" class="pattern-link-button" data-pe-set-compare="${hint.candidate}">Compare to ${esc(reg(hint.candidate))}</button> to see ${esc(joinList(hint.partners.slice(0, 3).map(reg)))} change only here.</p>`
      : "";
    el.siteSummary.innerHTML = `<div class="pattern-site-chips" role="group" aria-label="Choose an R-site">${chips}</div>
      <p class="pattern-site-facts"><strong>${esc(label)}</strong>: ${esc(plural(groups.size, "different group"))} across ${esc(plural(members.length, "compound"))} on the shared core.<br>Features here: ${featureText}.<br>Ring-containing groups: ${rings} of ${substituted.length}.</p>${hintHtml}`;
  };

  const groupOf = (site) => {
    const substituted = isSubstituted(site);
    if (state.group === "feature") {
      const label = !substituted ? "H (no R-group)" : site.features.length ? site.features.join(" + ") : "No classified feature";
      return { key: label, label, frag: null, detail: "" };
    }
    if (state.group === "ring") {
      const label = !substituted ? "H (no R-group)" : site.rings ? plural(site.rings, "ring") : "Acyclic";
      return { key: label, label, frag: null, detail: "" };
    }
    if (state.group === "rb") {
      const label = !substituted ? "H (no R-group)" : plural(site.rb, "rotatable bond");
      return { key: label, label, frag: null, detail: "" };
    }
    return { key: siteKey(site), label: site?.name || "H", frag: substituted ? site.frag : null, detail: compactDetail(site) };
  };

  // One row per group at the chosen site; ranges use exact values of single-site partners only.
  const stripRows = () => {
    const endpoint = endpoints[state.endpoint];
    const c = compareIndex();
    const reference = c === null ? null : exactOf(c);
    const rows = new Map();
    everyIndex.forEach((index) => {
      if (!onCore(index)) return;
      const group = groupOf(siteOf(index, state.site));
      if (!rows.has(group.key)) rows.set(group.key, { ...group, members: [] });
      const value = valueOf(index);
      const exact = value && value.s === "e" ? value.v : null;
      rows.get(group.key).members.push({
        index,
        value,
        matched: c !== null && differsOnlyAt(index, c, state.site),
        delta: exact !== null && reference !== null ? deltaOf(reference, exact, endpoint) : null,
      });
    });
    const sign = betterSign(endpoint) || 1;
    const list = [...rows.values()].map((row) => {
      const matchedDeltas = row.members.filter((member) => member.matched && member.index !== c && member.delta !== null).map((member) => member.delta);
      const exactValues = row.members.filter((member) => member.value && member.value.s === "e").map((member) => member.value.v);
      const hasComparison = row.members.some((member) => member.index === c);
      return {
        ...row,
        matchedDeltas,
        hasComparison,
        confounded: row.members.filter((member) => !member.matched && member.index !== c && member.value && member.value.s === "e").length,
        thresholds: row.members.filter((member) => member.value && member.value.s === "c").length,
        untested: row.members.filter((member) => !member.value).length,
        rank: matchedDeltas.length ? median(matchedDeltas) * sign : hasComparison ? 0 : null,
        typical: exactValues.length ? median(exactValues) * sign : null,
      };
    });
    // Most favourable matched effect first; groups without a single-site partner follow.
    list.sort((a, b) => {
      if ((a.rank === null) !== (b.rank === null)) return a.rank === null ? 1 : -1;
      if (a.rank !== null && a.rank !== b.rank) return b.rank - a.rank;
      if ((a.typical === null) !== (b.typical === null)) return a.typical === null ? 1 : -1;
      if (a.typical !== null && a.typical !== b.typical) return b.typical - a.typical;
      return String(a.label).localeCompare(String(b.label));
    });
    return { rows: list, c, reference };
  };
  const rowSummary = (row, endpoint, c) => {
    const deltas = row.matchedDeltas;
    const meta = [];
    let main = c === null ? "—" : "no single-site pair";
    let summaryTone = "muted";
    if (deltas.length) {
      const low = Math.min(...deltas);
      const high = Math.max(...deltas);
      main = high - low < 1e-9 ? fmtDelta(low, endpoint, true) : `${fmtDelta(low, endpoint, true)} to ${fmtDelta(high, endpoint, true)}`;
      summaryTone = tone(median(deltas), endpoint);
      meta.push(plural(deltas.length, "matched pair"));
    } else if (row.hasComparison) {
      main = "comparison";
      summaryTone = "compare";
    }
    if (row.confounded) meta.push(deltas.length ? `${row.confounded} unmatched` : `${row.confounded} differ${row.confounded === 1 ? "s" : ""} elsewhere`);
    if (row.thresholds) meta.push(plural(row.thresholds, "threshold"));
    if (row.untested) meta.push(`${row.untested} no result`);
    return { main, meta: meta.join(" · "), tone: summaryTone };
  };
  const stripPoint = (member, x, y, row, endpoint, c, labelY = null) => {
    const { index, value } = member;
    const censored = value.s === "c";
    const isComparison = index === c;
    let mark;
    if (censored) {
      // Chevron points toward the true value: right for "> bound", left for "< bound".
      const direction = String(value.q || "").startsWith("<") ? -1 : 1;
      mark = `<path class="pe-dot pe-chevron pe-tone-none" d="M${at(x - 4 * direction)} ${at(y - 6)} L${at(x + 4 * direction)} ${at(y)} L${at(x - 4 * direction)} ${at(y + 6)}"></path>`;
    } else if (isComparison) {
      mark = `<path class="pe-dot pe-tone-compare" d="M${x} ${at(y - 7)} L${at(x + 7)} ${at(y)} L${x} ${at(y + 7)} L${at(x - 7)} ${at(y)} Z"></path>`;
    } else {
      const toneClass = c === null ? "neutral" : tone(member.delta, endpoint);
      mark = `<circle class="pe-dot pe-tone-${toneClass}${member.matched ? "" : " is-hollow"}" cx="${x}" cy="${at(y)}" r="5.5"></circle>`;
    }
    const text = labelY === null ? "" : `<text class="pe-point-label" x="${x}" y="${at(labelY)}" text-anchor="middle">${esc(reg(index))}</text>`;
    const description = [
      reg(index),
      `${row.label} at ${state.site}`,
      `${endpoint.name} ${fmtValue(value, endpoint)}`,
      isComparison ? "comparison compound" : member.delta !== null ? `${fmtDelta(member.delta, endpoint)} versus ${reg(c)}` : "",
      isComparison || c === null ? "" : member.matched ? `differs from ${reg(c)} only at ${state.site}` : "also differs elsewhere",
      censored ? "threshold value, left out of ranges" : "",
    ].filter(Boolean).join(", ");
    return pointGroup(`c:${index}`, description, `<circle class="pe-ring" cx="${x}" cy="${at(y)}" r="10"></circle>${mark}${text}`);
  };

  const renderStrip = () => {
    if (!el.strip || !state.site) return;
    const endpoint = endpoints[state.endpoint];
    const { rows, c, reference } = stripRows();
    if (!rows.length) {
      el.strip.innerHTML = `<p class="pattern-empty">No compound on the shared core has an assignment at ${esc(state.site)}.</p>`;
      return;
    }
    const width = chartWidth(el.strip);
    const labelWidth = clamp(Math.round(width * 0.27), 150, 240);
    const summaryWidth = clamp(Math.round(width * 0.23), 130, 220);
    const plotLeft = labelWidth + 16;
    const plotRight = width - summaryWidth - 18;
    const rowHeight = 50;
    const top = 30;
    const bottom = top + rows.length * rowHeight;
    const height = bottom + 46;
    const values = [];
    rows.forEach((row) => row.members.forEach((member) => { if (member.value) values.push(member.value.v); }));
    if (reference !== null) values.push(reference);
    const axis = makeAxis(values, endpoint.scale === "fold", [plotLeft, plotRight]);
    const bands = [];
    const guides = [];
    const labels = [];
    const points = [];
    axis.ticks.forEach((tick) => {
      const x = axis.scale(tick);
      if (x === null || x < plotLeft - 1 || x > plotRight + 1) return;
      guides.push(`<line class="pe-grid" x1="${at(x)}" x2="${at(x)}" y1="${top - 4}" y2="${bottom}"></line><text class="pe-tick" x="${at(x)}" y="${bottom + 16}" text-anchor="middle">${esc(tickLabel(tick))}</text>`);
    });
    guides.push(`<text class="pe-axis-title" x="${at((plotLeft + plotRight) / 2)}" y="${bottom + 36}" text-anchor="middle">${esc(`${endpointTitle(endpoint)} · ${directionText(endpoint)}`)}</text>`);
    if (reference !== null) {
      const x = axis.scale(reference);
      if (endpoint.scale !== "linear" && betterSign(endpoint)) {
        // Shaded band: within ±0.3 log units (about 2-fold) of the comparison compound.
        const low = endpoint.scale === "fold" ? reference / Math.pow(10, NOISE) : reference - NOISE;
        const high = endpoint.scale === "fold" ? reference * Math.pow(10, NOISE) : reference + NOISE;
        const x1 = clamp(axis.scale(low), plotLeft, plotRight);
        const x2 = clamp(axis.scale(high), plotLeft, plotRight);
        guides.unshift(`<rect class="pe-noise" x="${at(x1)}" y="${top - 4}" width="${at(Math.max(0, x2 - x1))}" height="${bottom - top + 4}"></rect>`);
      }
      guides.push(`<line class="pe-compare-line" x1="${at(x)}" x2="${at(x)}" y1="${top - 8}" y2="${bottom}"></line>`);
      guides.push(`<text class="pe-compare-label" x="${at(clamp(x, plotLeft + 50, plotRight - 50))}" y="${top - 12}" text-anchor="middle">${esc(`${reg(c)} · ${F.number(reference, endpoint.unit)}`)}</text>`);
    }
    rows.forEach((row, rowIndex) => {
      const y0 = top + rowIndex * rowHeight;
      if (rowIndex % 2 === 0) bands.push(`<rect class="pe-band" x="0" y="${y0}" width="${width}" height="${rowHeight}" rx="6"></rect>`);
      let textX = 6;
      if (row.frag) {
        labels.push(`<rect class="pe-frag-bg" x="6" y="${y0 + 6}" width="62" height="38" rx="4"></rect><use href="#${esc(row.frag)}" x="6" y="${y0 + 6}" width="62" height="38"></use>`);
        textX = 76;
      }
      const room = labelWidth - textX;
      labels.push(`<text class="pe-row-name" x="${textX}" y="${y0 + (row.detail ? 22 : 30)}">${esc(truncate(row.label, Math.max(5, Math.floor(room / 7.4))))}<title>${esc(row.label)}</title></text>`);
      if (row.detail) labels.push(`<text class="pe-row-detail" x="${textX}" y="${y0 + 38}">${esc(truncate(row.detail, Math.max(8, Math.floor(room / 5.1))))}<title>${esc(row.detail)}</title></text>`);
      const summary = rowSummary(row, endpoint, c);
      labels.push(`<text class="pe-row-effect pe-fx-${summary.tone}" x="${plotRight + 18}" y="${y0 + 22}">${esc(summary.main)}</text>`);
      if (summary.meta) labels.push(`<text class="pe-row-meta" x="${plotRight + 18}" y="${y0 + 38}">${esc(truncate(summary.meta, Math.floor(summaryWidth / 5.1)))}<title>${esc(summary.meta)}</title></text>`);
      // Dots first (nudged vertically when they would overlap), then compound-ID labels that avoid
      // every dot and earlier label in the row. A label that cannot fit is left to the tooltip.
      const placed = [];
      row.members.filter((member) => member.value).sort((a, b) => a.value.v - b.value.v).forEach((member) => {
        const x = axis.scale(member.value.v);
        if (x === null) return;
        const offset = [0, -10, 10, -19, 19].find((candidate) => placed.every((point) => Math.abs(point.x - x) >= 12 || Math.abs(point.offset - candidate) >= 12)) ?? 0;
        placed.push({ member, x, offset, y: y0 + rowHeight / 2 + offset });
      });
      const boxes = placed.map((point) => ({ left: point.x - 7, right: point.x + 7, top: point.y - 7, bottom: point.y + 7, owner: point }));
      const free = (box, owner) => boxes.every((other) => other.owner === owner || box.right <= other.left || box.left >= other.right || box.bottom <= other.top || box.top >= other.bottom);
      placed.forEach((point) => {
        let labelY = null;
        if (compounds.length <= LABEL_LIMIT) {
          const half = (reg(point.member.index).length * 5.6) / 2 + 2;
          labelY = [point.y + 17, point.y - 10].find((candidate) => free({ left: point.x - half, right: point.x + half, top: candidate - 8, bottom: candidate + 2 }, point)) ?? null;
          if (labelY !== null) boxes.push({ left: point.x - half, right: point.x + half, top: labelY - 8, bottom: labelY + 2, owner: null });
        }
        points.push(stripPoint(point.member, at(point.x), point.y, row, endpoint, c, labelY));
      });
    });
    const band = endpoint.scale === "fold" ? "2-fold" : "0.3 log units";
    const caption = c === null
      ? `No compound has an exact ${endpoint.name} result to compare against.`
      : !betterSign(endpoint)
        ? `No better/worse direction is declared for ${endpoint.name}, so dots are not coloured. Differences are measured from ${reg(c)}.`
        : endpoint.scale === "linear"
          ? `Colour compares with ${reg(c)}: green better, red worse. Ranges on the right use filled dots only.`
          : `Colour compares with ${reg(c)}: green better, red worse, grey within ${band} (shaded band). Ranges on the right use filled dots only.`;
    el.strip.innerHTML = `${svgOpen(width, height, `R-group effects at ${state.site} on ${endpoint.name}: ${rows.length} groups`)}${bands.join("")}${guides.join("")}${labels.join("")}${points.join("")}</svg><p class="pattern-caption">${esc(caption)}</p>`;
  };

  // ------------------------------------------------------ combine sites
  // Rows × columns of groups at two R-sites (optionally one grid per group at a third). Other
  // sites and the core are held at the comparison compound's, so each cell differs from the
  // comparison only at the chosen sites. Additive estimates are presentation-only arithmetic.
  const MAX_PANELS = 8;
  const SPLIT = "\u0001";
  const normalizeCombo = () => {
    if (!siteLabels.includes(state.rows)) state.rows = siteLabels.includes(state.site) ? state.site : siteLabels[0] || null;
    if (!siteLabels.includes(state.cols) || state.cols === state.rows) state.cols = siteLabels.find((label) => label !== state.rows) || null;
    if (state.split && (!siteLabels.includes(state.split) || state.split === state.rows || state.split === state.cols)) state.split = "";
  };
  const comboModel = () => {
    normalizeCombo();
    const endpoint = endpoints[state.endpoint];
    const c = compareIndex();
    const reference = c === null ? null : exactOf(c);
    const chosen = [state.rows, state.cols, state.split].filter(Boolean);
    const others = siteLabels.filter((label) => !chosen.includes(label));
    const anchor = c !== null && onCore(c) ? c : null;
    const inFrame = (index) => anchor === null || (compounds[index].core === compounds[anchor].core
      && others.every((label) => siteKey(siteOf(index, label)) === siteKey(siteOf(anchor, label))));
    const axes = { rows: new Map(), cols: new Map(), splits: new Map() };
    const axisValues = { rows: new Map(), cols: new Map(), splits: new Map() };
    const cells = new Map();
    let excluded = 0;
    const note = (axis, group, value) => {
      axes[axis].set(group.key, group);
      if (!axisValues[axis].has(group.key)) axisValues[axis].set(group.key, []);
      if (value !== null) axisValues[axis].get(group.key).push(value);
    };
    everyIndex.forEach((index) => {
      if (!onCore(index)) return;
      const clean = inFrame(index);
      if (!clean && !state.loose) {
        excluded += 1;
        return;
      }
      const value = exactOf(index);
      const row = groupOf(siteOf(index, state.rows));
      const col = groupOf(siteOf(index, state.cols));
      const split = state.split ? groupOf(siteOf(index, state.split)) : { key: "", label: "", frag: null };
      note("rows", row, value);
      note("cols", col, value);
      note("splits", split, value);
      const id = [split.key, row.key, col.key].join(SPLIT);
      if (!cells.has(id)) cells.set(id, []);
      cells.get(id).push({ index, clean });
    });
    const sign = betterSign(endpoint) || 1;
    const anchorKey = (label) => (anchor === null || !label ? null : groupOf(siteOf(anchor, label)).key);
    // The comparison compound's group first, then the most favourable typical result.
    const ordered = (axis, label) => {
      const first = anchorKey(label);
      return [...axes[axis].values()].sort((a, b) => {
        if (a.key === first || b.key === first) return a.key === first ? -1 : 1;
        const ma = median(axisValues[axis].get(a.key) || []);
        const mb = median(axisValues[axis].get(b.key) || []);
        if ((ma === null) !== (mb === null)) return ma === null ? 1 : -1;
        if (ma !== null && ma !== mb) return (mb - ma) * sign;
        return String(a.label).localeCompare(String(b.label));
      });
    };
    const cellAt = (split, row, col) => cells.get([split, row, col].join(SPLIT)) || null;
    const cellValue = (split, row, col) => median((cellAt(split, row, col) || []).map((member) => exactOf(member.index)).filter((value) => value !== null));
    // Additive estimate for (row, col) = (row, col₀) + (row₀, col) − (row₀, col₀), in log units,
    // where row₀ / col₀ are the comparison compound's groups. Linear results are not combined.
    const row0 = anchorKey(state.rows);
    const col0 = anchorKey(state.cols);
    const toLog = (value) => (endpoint.scale === "fold" ? (value > 0 ? Math.log10(value) : null) : value);
    const additive = (split, row, col) => {
      if (endpoint.scale === "linear" || row0 === null || col0 === null || row === row0 || col === col0) return null;
      const parts = [cellValue(split, row, col0), cellValue(split, row0, col), cellValue(split, row0, col0)].map((value) => (value === null ? null : toLog(value)));
      if (parts.some((value) => value === null)) return null;
      const estimate = parts[0] + parts[1] - parts[2];
      return endpoint.scale === "fold" ? Math.pow(10, estimate) : estimate;
    };
    return {
      endpoint, c, reference, anchor, others, excluded, cellAt, additive,
      rows: ordered("rows", state.rows), cols: ordered("cols", state.cols), splits: ordered("splits", state.split),
    };
  };
  const matrixHeader = (group) => `${group.frag ? `<span class="pattern-matrix__frag"><svg viewBox="0 0 130 80" aria-hidden="true"><use href="#${esc(group.frag)}"></use></svg></span>` : ""}<span class="pattern-matrix__name" title="${esc(group.label)}">${esc(truncate(group.label, 16))}</span>`;
  const matrixCell = (model, split, row, col) => {
    const { endpoint, c, reference } = model;
    const members = model.cellAt(split.key, row.key, col.key);
    const estimate = model.additive(split.key, row.key, col.key);
    const where = [`${state.rows} ${row.label}`, `${state.cols} ${col.label}`, state.split ? `${state.split} ${split.label}` : ""].filter(Boolean).join(", ");
    if (!members) {
      if (estimate === null) return `<td><div class="pattern-matrix__empty"><span class="sr-only">${esc(where)}: </span><small>not made</small></div></td>`;
      const delta = reference !== null ? deltaOf(reference, estimate, endpoint) : null;
      const text = F.number(estimate, endpoint.unit);
      return `<td><div class="pattern-matrix__empty pattern-matrix__estimate pe-fx-${tone(delta, endpoint)}" title="Not made. Additive estimate from the row and column effects: ${esc(text)}"><span class="sr-only">${esc(where)}: not made, additive estimate </span><span class="pattern-matrix__value">≈ ${esc(text)}</span><small>additive estimate · not made</small></div></td>`;
    }
    const exact = members.filter((member) => exactOf(member.index) !== null);
    const value = median(exact.map((member) => exactOf(member.index)));
    const isComparison = members.some((member) => member.index === c);
    const lead = (members.find((member) => member.index === c) || exact.find((member) => member.clean) || exact[0] || members[0]).index;
    const delta = value !== null && reference !== null ? deltaOf(reference, value, endpoint) : null;
    const cellTone = isComparison ? "compare" : value === null ? "none" : c === null ? "neutral" : tone(delta, endpoint);
    const shown = members.find((member) => valueOf(member.index));
    const main = value !== null ? F.number(value, endpoint.unit) : shown ? fmtValue(valueOf(shown.index), endpoint, false) : "no result";
    const facts = [isComparison ? "comparison" : delta !== null ? `${fmtDelta(delta, endpoint, true)} vs ${reg(c)}` : ""];
    let additivity = "";
    if (!isComparison && value !== null && estimate !== null) {
      // Observed minus additive, in log units (log10 fold for fold-scale results).
      const gap = endpoint.scale === "fold" ? Math.log10(value / estimate) : value - estimate;
      additivity = Math.abs(gap) < NOISE ? "≈ additive" : `${fmtDelta(gap, endpoint, true)} vs additive`;
      facts.push(additivity);
    }
    const ids = members.map((member) => reg(member.index));
    const loose = members.some((member) => !member.clean);
    const label = [`${where}: ${joinList(ids)}`, `${endpoint.name} ${main}${exact.length > 1 ? ` (median of ${exact.length})` : ""}`, ...facts.filter(Boolean), loose ? "includes compounds that also differ at other sites" : ""].filter(Boolean).join("; ");
    return `<td><button type="button" class="pe-point pattern-matrix__cell pe-cell-${cellTone}${loose ? " is-loose" : ""}" data-pe-key="c:${lead}" aria-pressed="false" aria-label="${esc(label)}">
      <span class="pattern-matrix__value">${esc(main)}</span>${facts[0] ? `<small>${esc(facts[0])}</small>` : ""}${additivity ? `<small class="pattern-matrix__additivity">${esc(additivity)}</small>` : ""}<small class="pattern-matrix__ids">${esc(truncate(ids.join(", "), 22))}</small></button></td>`;
  };
  const matrixTable = (model, split) => {
    const title = state.split ? `${state.split} = ${split.label}` : `${state.rows} × ${state.cols}`;
    const head = `<tr><th scope="col" class="pattern-matrix__corner">${esc(state.rows)} ↓ · ${esc(state.cols)} →</th>${model.cols.map((col) => `<th scope="col">${matrixHeader(col)}</th>`).join("")}</tr>`;
    const body = model.rows.map((row) => `<tr><th scope="row">${matrixHeader(row)}</th>${model.cols.map((col) => matrixCell(model, split, row, col)).join("")}</tr>`).join("");
    const caption = state.split ? `<figcaption>${matrixHeader(split)}<span class="pattern-matrix__split">at ${esc(state.split)}</span></figcaption>` : "";
    return `<figure class="pattern-matrix__panel">${caption}<div class="pattern-matrix__scroll"><table class="pattern-matrix__table"><caption class="sr-only">${esc(`${endpoints[state.endpoint].name} by ${title}`)}</caption><thead>${head}</thead><tbody>${body}</tbody></table></div></figure>`;
  };
  const renderMatrix = () => {
    if (!el.matrix) return;
    const model = comboModel();
    const { endpoint, c, anchor, others, excluded } = model;
    if (!model.rows.length || !model.cols.length) {
      el.matrix.innerHTML = `<p class="pattern-empty">No compound on the shared core can be placed at ${esc(state.rows)} and ${esc(state.cols)}${excluded ? ` without also differing at ${esc(joinList(others))}` : ""}.</p>`;
      return;
    }
    const panels = model.splits.slice(0, MAX_PANELS);
    const notes = [];
    if (anchor === null) notes.push("Choose a comparison compound on the shared core to hold the other sites fixed and to see additive estimates.");
    else if (others.length) notes.push(`${joinList(others)} and the core are held at ${reg(anchor)}'s groups.`);
    else notes.push(`The core is held at ${reg(anchor)}'s.`);
    if (excluded) notes.push(`${plural(excluded, "compound")} also differ${excluded === 1 ? "s" : ""} ${others.length ? `at ${joinList(others)} or ` : ""}in the core and ${excluded === 1 ? "is" : "are"} left out; tick “Include compounds that also differ at other sites” to show ${excluded === 1 ? "it" : "them"} (dashed cells).`);
    if (c !== null && betterSign(endpoint)) notes.push(`Colour compares each cell with ${reg(c)}: green better, red worse, grey within ${endpoint.scale === "fold" ? "2-fold" : endpoint.scale === "log" ? "0.3 log units" : "no change"}. Cells with several compounds show the median.`);
    if (endpoint.scale === "linear") notes.push("Additive estimates are only shown for log-scale results.");
    else if (anchor !== null) notes.push(`Empty cells show an additive estimate when both single changes from ${reg(anchor)}'s groups (the top-left cell${state.split ? " of each grid" : ""}) were measured: row effect + column effect. It assumes the sites act independently and is not a prediction; measured cells show how far they sit from it.`);
    if (model.splits.length > MAX_PANELS) notes.push(`Showing the first ${MAX_PANELS} of ${model.splits.length} groups at ${state.split}.`);
    el.matrix.innerHTML = `<div class="pattern-matrix__panels">${panels.map((split) => matrixTable(model, split)).join("")}</div><p class="pattern-caption">${esc(notes.join(" "))}</p>`;
  };
  const syncComboControls = () => {
    const possible = siteLabels.length > 1;
    const combine = state.siteMode === "combine" && possible;
    if (el.siteMode) {
      const option = el.siteMode.querySelector('option[value="combine"]');
      if (option) {
        option.disabled = !possible;
        option.textContent = possible ? "Combine sites" : "Combine sites (needs two R-sites)";
      }
      el.siteMode.value = combine ? "combine" : "one";
    }
    [el.comboControls, el.matrix].forEach((node) => { if (node) node.hidden = !combine; });
    [el.oneLegend, el.strip].forEach((node) => { if (node) node.hidden = combine; });
    // Only the visible view keeps its points, so selection and focus restore find the right node.
    const idle = combine ? el.strip : el.matrix;
    if (idle) idle.innerHTML = "";
    if (!combine) return;
    normalizeCombo();
    const options = (list, blank = "") => `${blank ? `<option value="">${esc(blank)}</option>` : ""}${list.map((label) => `<option value="${esc(label)}">${esc(label)}</option>`).join("")}`;
    if (el.comboRows) {
      el.comboRows.innerHTML = options(siteLabels);
      el.comboRows.value = state.rows;
    }
    if (el.comboCols) {
      el.comboCols.innerHTML = options(siteLabels.filter((label) => label !== state.rows));
      el.comboCols.value = state.cols;
    }
    if (el.comboSplit) {
      const third = siteLabels.filter((label) => label !== state.rows && label !== state.cols);
      el.comboSplit.innerHTML = options(third, third.length ? "No third site" : "No third site available");
      el.comboSplit.value = state.split;
      el.comboSplit.disabled = !third.length;
    }
    if (el.comboLoose) el.comboLoose.checked = state.loose;
  };

  // ------------------------------------------------------ activity landscape
  const landscapePoints = () => {
    const endpoint = endpoints[state.endpoint];
    const points = [];
    let thresholds = 0;
    (data.pairs || []).forEach(([i, j, similarity]) => {
      const a = valueOf(i);
      const b = valueOf(j);
      if (!a || !b) return;
      if (a.s !== "e" || b.s !== "e") {
        thresholds += 1;
        return;
      }
      const delta = deltaOf(a.v, b.v, endpoint);
      if (delta === null || !Number.isFinite(delta)) return;
      points.push({ i, j, similarity, size: Math.abs(delta), change: pairChange(i, j) });
    });
    return { points, thresholds };
  };
  // Configure the |Δ| slider for the endpoint's scale; log-unit thresholds carry across log/fold endpoints.
  const syncLandscapeControls = (points) => {
    const endpoint = endpoints[state.endpoint];
    let min = 0.2;
    let max = 3;
    let step = 0.1;
    let fallback = 1;
    if (endpoint.scale === "linear") {
      const largest = points.reduce((best, point) => Math.max(best, point.size), 0) || 1;
      const ticks = niceTicks(0, largest, 4);
      max = Math.max(largest, ticks[ticks.length - 1]);
      step = Number((max / 60).toPrecision(1)) || 0.01;
      min = step;
      fallback = Number((max / 3).toPrecision(2));
    }
    const logLike = (scale) => scale === "log" || scale === "fold";
    const previous = state.deltaFor;
    const compatible = state.delta !== null && previous && (logLike(endpoint.scale) ? logLike(previous.scale) : previous.key === endpoint.key);
    state.delta = clamp(compatible ? state.delta : fallback, min, max);
    state.deltaFor = { key: endpoint.key, scale: endpoint.scale };
    if (el.delta) {
      el.delta.min = String(min);
      el.delta.max = String(max);
      el.delta.step = String(step);
      el.delta.value = String(state.delta);
      state.delta = Number(el.delta.value) || state.delta;
    }
    if (el.deltaOut) el.deltaOut.textContent = thresholdText(state.delta, endpoint);
    if (el.deltaLabel) el.deltaLabel.textContent = endpoint.scale === "fold" ? "Steep at fold change ≥" : "Steep at |Δ| ≥";
    if (el.sim) el.sim.value = String(state.sim);
    if (el.simOut) el.simOut.textContent = state.sim.toFixed(2);
    if (el.single) el.single.checked = state.single;
  };

  const renderLandscape = () => {
    if (!el.landscape) return;
    const endpoint = endpoints[state.endpoint];
    const { points: all } = landscapePoints();
    syncLandscapeControls(all);
    const shown = state.single ? all.filter((point) => point.change.single) : all;
    if (!shown.length) {
      el.landscape.innerHTML = `<p class="pattern-empty">${all.length
        ? "No single-change pair has exact results for both compounds. Clear the filter to see every pair."
        : `Fewer than two compounds have exact ${esc(endpoint.name)} results that can be paired.`}</p>`;
      if (el.cliffs) el.cliffs.innerHTML = "";
      return;
    }
    const inZone = (point) => point.similarity >= state.sim && point.size >= state.delta;
    let points = shown;
    if (points.length > MAX_LANDSCAPE_POINTS) {
      points = [...points].sort((a, b) => Number(inZone(b)) - Number(inZone(a)) || b.similarity - a.similarity).slice(0, MAX_LANDSCAPE_POINTS);
    }
    points = [...points].sort((a, b) => a.similarity - b.similarity || a.size - b.size);
    const width = chartWidth(el.landscape);
    const height = clamp(Math.round(width * 0.7), 260, 380);
    const margin = { left: 56, right: 16, top: 16, bottom: 46 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const [minSimilarity] = extent(points.map((point) => point.similarity));
    const [, maxSize] = extent(points.map((point) => point.size));
    const xLow = Math.min(0.2, Math.floor(minSimilarity * 10) / 10);
    const yHigh = Math.max(state.delta * 1.25, maxSize * 1.08) || 1;
    const sx = (value) => at(margin.left + ((value - xLow) / (1 - xLow)) * plotW);
    const sy = (value) => at(margin.top + plotH - (value / yHigh) * plotH);
    const parts = [];
    const zoneX = sx(state.sim);
    const zoneY = sy(Math.min(state.delta, yHigh));
    parts.push(`<rect class="pe-cliff-zone" x="${zoneX}" y="${margin.top}" width="${at(margin.left + plotW - zoneX)}" height="${at(zoneY - margin.top)}"></rect>`);
    niceTicks(xLow, 1, 5).forEach((tick) => {
      const x = sx(tick);
      parts.push(`<line class="pe-grid" x1="${x}" x2="${x}" y1="${margin.top}" y2="${margin.top + plotH}"></line><text class="pe-tick" x="${x}" y="${margin.top + plotH + 16}" text-anchor="middle">${tick.toFixed(1)}</text>`);
    });
    const yTicks = endpoint.scale === "fold"
      ? logTicks(0, yHigh).map((fold) => ({ position: Math.log10(fold), label: `${tickLabel(fold)}×` }))
      : niceTicks(0, yHigh, 5).map((value) => ({ position: value, label: tickLabel(value) }));
    yTicks.forEach((tick) => {
      if (tick.position < -1e-9 || tick.position > yHigh + 1e-9) return;
      const y = sy(tick.position);
      parts.push(`<line class="pe-grid" x1="${margin.left}" x2="${margin.left + plotW}" y1="${y}" y2="${y}"></line><text class="pe-tick" x="${margin.left - 8}" y="${at(y + 4)}" text-anchor="end">${esc(tick.label)}</text>`);
    });
    const yTitle = endpoint.scale === "fold" ? `${endpoint.name} fold difference` : `|Δ ${endpoint.name}|${endpoint.unit_label ? ` (${endpoint.unit_label})` : ""}`;
    parts.push(`<text class="pe-axis-title" x="${at(margin.left + plotW / 2)}" y="${height - 8}" text-anchor="middle">Structural similarity (Tanimoto, Morgan radius 2) →</text>`);
    parts.push(`<text class="pe-axis-title" transform="translate(14 ${at(margin.top + plotH / 2)}) rotate(-90)" text-anchor="middle">${esc(yTitle)} →</text>`);
    parts.push(`<line class="pe-axis" x1="${margin.left}" x2="${margin.left + plotW}" y1="${margin.top + plotH}" y2="${margin.top + plotH}"></line><line class="pe-axis" x1="${margin.left}" x2="${margin.left}" y1="${margin.top}" y2="${margin.top + plotH}"></line>`);
    parts.push(`<line class="pe-threshold" x1="${zoneX}" x2="${zoneX}" y1="${margin.top}" y2="${margin.top + plotH}"></line><line class="pe-threshold" x1="${margin.left}" x2="${margin.left + plotW}" y1="${zoneY}" y2="${zoneY}"></line>`);
    parts.push(`<text class="pe-zone-label" x="${margin.left + plotW - 6}" y="${margin.top + 14}" text-anchor="end">Activity cliffs</text><text class="pe-zone-label pe-zone-label--smooth" x="${margin.left + plotW - 6}" y="${margin.top + plotH - 8}" text-anchor="end">Smooth SAR</text>`);
    points.forEach((point) => {
      const x = sx(point.similarity);
      const y = sy(point.size);
      const zone = inZone(point);
      const [from, to] = orient(point.i, point.j);
      const delta = deltaOf(exactOf(from), exactOf(to), endpoint);
      const label = `${reg(from)} → ${reg(to)}, ${endpoint.name} ${fmtDelta(delta, endpoint)}, Tanimoto ${point.similarity.toFixed(2)}, ${changeText(from, to)}${zone ? ", in the activity-cliff region" : ""}`;
      const dot = `<circle class="pe-dot pe-tone-${zone ? "cliff" : "pair"}${point.change.single ? "" : " is-hollow"}" cx="${x}" cy="${y}" r="${zone ? 5.5 : 4.5}"></circle>`;
      parts.push(pointGroup(`p:${point.i}-${point.j}`, label, `<circle class="pe-ring" cx="${x}" cy="${y}" r="9"></circle>${dot}`, "pe-pair"));
    });
    const capped = shown.length > points.length ? `, ${points.length} of ${shown.length} shown` : "";
    el.landscape.innerHTML = `${svgOpen(width, height, `Activity landscape for ${endpoint.name}: ${plural(shown.length, "compound pair")}${capped}`)}${parts.join("")}</svg>`;
    renderCliffList(shown.filter(inZone), endpoint);
  };

  const renderCliffList = (cliffs, endpoint) => {
    if (!el.cliffs) return;
    const sorted = [...cliffs].sort((a, b) => b.size - a.size || b.similarity - a.similarity);
    const items = sorted.slice(0, CLIFF_LIST_LIMIT).map((point) => {
      const [from, to] = orient(point.i, point.j);
      const delta = deltaOf(exactOf(from), exactOf(to), endpoint);
      return `<li><button type="button" class="pattern-cliff" data-pe-key="p:${point.i}-${point.j}" aria-pressed="false"><span class="pattern-cliff__top"><span class="pattern-cliff__pair"><b>${esc(reg(from))}</b> → <b>${esc(reg(to))}</b></span><span class="pattern-cliff__delta pe-fx-${tone(delta, endpoint)}">${esc(fmtDelta(delta, endpoint, true))}</span></span><span class="pattern-cliff__meta">Tc ${point.similarity.toFixed(2)} · ${esc(changeText(from, to))}</span></button></li>`;
    }).join("");
    const more = sorted.length > CLIFF_LIST_LIMIT ? `<p class="pattern-empty">${sorted.length - CLIFF_LIST_LIMIT} more in the chart.</p>` : "";
    el.cliffs.innerHTML = `<h4>Steepest similar pairs <span>${sorted.length}</span></h4>${sorted.length
      ? `<ol>${items}</ol>${more}`
      : '<p class="pattern-empty">No pair passes both thresholds. Lower either threshold to widen the search.</p>'}`;
  };

  // --------------------------------------------------------- property space
  const axisGroups = () => {
    const results = [];
    const efficiency = [];
    endpoints.forEach((endpoint, index) => {
      if (!endpoint.exact) return;
      results.push({ value: `e:${index}`, label: `${endpoint.name} (${endpoint.unit_label || "no unit"})` });
      if (endpoint.potency) {
        efficiency.push({ value: `lipe:${index}`, label: `LipE: ${endpoint.name} − cLogP` });
        efficiency.push({ value: `le:${index}`, label: `LE: 1.37 × ${endpoint.name} / heavy atoms` });
      }
    });
    return [
      { label: "Calculated properties", options: PROPERTY_AXES.map((axis) => ({ value: `p:${axis.key}`, label: axis.label })) },
      { label: "Results", options: results },
      { label: "Efficiency", options: efficiency },
    ].filter((group) => group.options.length);
  };
  const colourGroups = () => [
    { label: "Structure", options: [
      ...siteLabels.map((label) => ({ value: `site:${label}`, label: `Group at ${label}` })),
      ...siteLabels.map((label) => ({ value: `feature:${label}`, label: `Features at ${label}` })),
      { value: "core", label: "Core match" },
    ] },
    { label: "Results", options: endpoints.map((endpoint, index) => (endpoint.exact ? { value: `e:${index}`, label: `${endpoint.name} (${endpoint.unit_label || "no unit"})` } : null)).filter(Boolean) },
    { label: "Other", options: [{ value: "none", label: "No colour" }] },
  ].filter((group) => group.options.length);
  const optionHtml = (groups) => groups.map((group) => `<optgroup label="${esc(group.label)}">${group.options.map((option) => `<option value="${esc(option.value)}">${esc(option.label)}</option>`).join("")}</optgroup>`).join("");
  const hasOption = (groups, value) => groups.some((group) => group.options.some((option) => option.value === value));

  const syncPropertyControls = () => {
    const axes = axisGroups();
    const colours = colourGroups();
    if (!hasOption(axes, state.x)) state.x = "p:clogp";
    if (!hasOption(axes, state.y)) state.y = `e:${state.endpoint}`;
    if (!state.colour || !hasOption(colours, state.colour)) state.colour = siteLabels.length && state.site ? `site:${state.site}` : "core";
    [[el.x, axes, state.x], [el.y, axes, state.y], [el.colour, colours, state.colour]].forEach(([select, groups, value]) => {
      if (!select) return;
      if (!select.options.length) select.innerHTML = optionHtml(groups);
      select.value = value;
    });
  };

  const axisMeta = (option) => {
    const [kind, key] = String(option).split(":");
    if (kind === "p") {
      const axis = PROPERTY_AXES.find((item) => item.key === key) || PROPERTY_AXES[0];
      return { kind, label: axis.label, log: false };
    }
    const endpoint = endpoints[Number(key)];
    if (!endpoint) return null;
    if (kind === "e") return { kind, endpoint, label: endpointTitle(endpoint), log: endpoint.scale === "fold" };
    if (kind === "lipe") return { kind, endpoint, label: `LipE (${endpoint.name} − cLogP)`, log: false };
    if (kind === "le") return { kind, endpoint, label: `LE (kcal/mol per heavy atom, ${endpoint.name})`, log: false };
    return null;
  };
  const axisValue = (option, index) => {
    const [kind, key] = String(option).split(":");
    const compound = compounds[index];
    const props = compound.props || {};
    if (kind === "p") return Number.isFinite(props[key]) ? { v: props[key], censored: false } : null;
    const value = valueOf(index, Number(key));
    if (!value) return null;
    if (kind === "e") return { v: value.v, censored: value.s === "c", q: value.q || "" };
    if (value.s !== "e") return null;
    if (kind === "lipe") return Number.isFinite(props.clogp) ? { v: value.v - props.clogp, censored: false } : null;
    if (kind === "le") return props.hac > 0 ? { v: (1.37 * value.v) / props.hac, censored: false } : null;
    return null;
  };

  const categoryOf = (option, index) => {
    const [kind, label] = String(option).split(":");
    const compound = compounds[index];
    if (!onCore(index)) return ["no_run", "not_in_run"].includes(compound.status) ? NOT_DECOMPOSED : OUTSIDE;
    if (kind === "core") {
      const referenceCore = referenceIndex !== null ? compounds[referenceIndex].core : null;
      return compound.core === referenceCore ? "Same core as reference" : compound.core_note || "Core atoms differ";
    }
    const site = siteOf(index, label);
    if (kind === "site") return site?.name || "H";
    if (!isSubstituted(site)) return "H (no R-group)";
    return site.features.length ? site.features.join(" + ") : "No classified feature";
  };

  const colourScheme = (option, members) => {
    if (!option || option === "none") return { kind: "none", legend: "" };
    if (option.startsWith("e:")) {
      const e = Number(option.slice(2));
      const endpoint = endpoints[e];
      const position = (value) => (endpoint.scale === "fold" ? (value > 0 ? Math.log10(value) : null) : value);
      const [low, high] = extent(members.map((index) => exactOf(index, e)).filter((value) => value !== null).map(position));
      const sign = betterSign(endpoint);
      const stops = sign
        ? [cssColour("--heat-bad", [224, 100, 92]), cssColour("--heat-mid", [140, 138, 122]), cssColour("--heat-good", [63, 185, 127])]
        : [cssColour("--blue", [130, 201, 255]), cssColour("--orange", [255, 152, 89])];
      const fillFor = (index) => {
        const value = exactOf(index, e);
        if (value === null || position(value) === null) return null;
        let t = high - low > 1e-12 ? (position(value) - low) / (high - low) : 0.5;
        if (sign < 0) t = 1 - t;
        return rgb(ramp(t, stops));
      };
      const shown = (edge) => F.number(endpoint.scale === "fold" ? Math.pow(10, edge) : edge, endpoint.unit);
      const [left, right] = sign < 0 ? [shown(high), shown(low)] : [shown(low), shown(high)];
      const gradient = `linear-gradient(90deg, ${[0, 0.5, 1].map((t) => rgb(ramp(t, stops))).join(", ")})`;
      const legend = `<span class="pattern-legend__item">${esc(endpoint.name)}: ${esc(left)} <span class="pattern-ramp" style="background:${gradient}"></span> ${esc(right)}${sign ? " (green = better)" : ""}</span><span class="pattern-legend__item"><span class="pattern-swatch pattern-swatch--hollow"></span>threshold or no exact result</span>`;
      return { kind: "sequential", fillFor, legend };
    }
    const counts = new Map();
    members.forEach((index) => {
      const category = categoryOf(option, index);
      counts.set(category, (counts.get(category) || 0) + 1);
    });
    const muted = [OUTSIDE, NOT_DECOMPOSED];
    const ordered = [...counts.entries()].filter(([category]) => !muted.includes(category)).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    const classes = new Map(ordered.map(([category], position) => [category, position < 8 ? `pe-cat-${position}` : "pe-cat-other"]));
    muted.forEach((category) => classes.set(category, "pe-cat-muted"));
    const otherCount = ordered.slice(8).reduce((sum, [, count]) => sum + count, 0);
    const legendItems = [
      ...ordered.slice(0, 8),
      ...(otherCount ? [["Other groups", otherCount, "pe-cat-other"]] : []),
      ...[...counts.entries()].filter(([category]) => muted.includes(category)),
    ];
    const legend = legendItems.map(([category, count, className]) => `<span class="pattern-legend__item"><span class="pattern-swatch ${className || classes.get(category)}"></span>${esc(category)} <small>${count}</small></span>`).join("");
    return { kind: "categorical", classFor: (index) => classes.get(categoryOf(option, index)) || "pe-cat-other", legend };
  };

  // LipE (potency − cLogP) diagonals, or LE (1.37 × potency / heavy atoms) rays, when the axes allow.
  const efficiencyGuides = (xAxis, yAxis, yMeta) => {
    const parts = [];
    if (!yMeta || yMeta.kind !== "e" || !yMeta.endpoint.potency || yMeta.log) return parts;
    const line = (x1, x2, f, text) => {
      if (x2 - x1 <= 1e-9) return;
      const [X1, Y1, X2, Y2] = [xAxis.scale(x1), yAxis.scale(f(x1)), xAxis.scale(x2), yAxis.scale(f(x2))];
      parts.push(`<line class="pe-guide" x1="${at(X1)}" y1="${at(Y1)}" x2="${at(X2)}" y2="${at(Y2)}"></line><text class="pe-guide-label" x="${at(X2 - 4)}" y="${at(Y2 + 12)}" text-anchor="end">${esc(text)}</text>`);
    };
    if (state.x === "p:clogp") {
      const first = Math.ceil(yAxis.low - xAxis.high);
      const last = Math.floor(yAxis.high - xAxis.low);
      const step = last - first > 8 ? 2 : 1;
      for (let k = first; k <= last; k += step) {
        line(Math.max(xAxis.low, yAxis.low - k), Math.min(xAxis.high, yAxis.high - k), (x) => x + k, `LipE ${k}`);
      }
    } else if (state.x === "p:hac") {
      [0.3, 0.4, 0.5].forEach((efficiency) => {
        const slope = efficiency / 1.37;
        line(Math.max(xAxis.low, yAxis.low / slope, 1e-6), Math.min(xAxis.high, yAxis.high / slope), (x) => slope * x, `LE ${efficiency}`);
      });
    }
    return parts;
  };
  const propertyHelp = (yMeta) => {
    const potency = yMeta && yMeta.kind === "e" && yMeta.endpoint.potency;
    if (potency && state.x === "p:clogp") return `Each dot is a compound. Dashed diagonals mark equal lipophilic efficiency (LipE = ${yMeta.endpoint.name} − cLogP): moving up and left gains potency without extra lipophilicity. Hollow dots are threshold values.`;
    if (potency && state.x === "p:hac") return "Each dot is a compound. Dashed lines mark ligand efficiency (LE = 1.37 × potency / heavy atoms) of 0.3, 0.4 and 0.5. Hollow dots are threshold values.";
    return "Each dot is a compound. Plot a potency result against cLogP or heavy atoms to see efficiency guides. Hollow dots are threshold values drawn at their bound.";
  };

  const renderProperties = () => {
    if (!el.properties) return;
    syncPropertyControls();
    const xMeta = axisMeta(state.x);
    const yMeta = axisMeta(state.y);
    if (!xMeta || !yMeta) return;
    const points = [];
    everyIndex.forEach((index) => {
      const x = axisValue(state.x, index);
      const y = axisValue(state.y, index);
      if (!x || !y || (xMeta.log && !(x.v > 0)) || (yMeta.log && !(y.v > 0))) return;
      points.push({ index, x, y });
    });
    if (el.propertyHelp) el.propertyHelp.textContent = propertyHelp(yMeta);
    if (points.length < 2) {
      el.properties.innerHTML = `<p class="pattern-empty">Fewer than two compounds have both ${esc(xMeta.label)} and ${esc(yMeta.label)}.</p>`;
      if (el.legend) el.legend.innerHTML = "";
      return;
    }
    const width = chartWidth(el.properties);
    const height = clamp(Math.round(width * 0.52), 280, 420);
    const margin = { left: 62, right: 20, top: 16, bottom: 48 };
    const xAxis = makeAxis(points.map((point) => point.x.v), xMeta.log, [margin.left, width - margin.right]);
    const yAxis = makeAxis(points.map((point) => point.y.v), yMeta.log, [height - margin.bottom, margin.top]);
    const scheme = colourScheme(state.colour, points.map((point) => point.index));
    const parts = [];
    xAxis.ticks.forEach((tick) => {
      const x = xAxis.scale(tick);
      if (x === null || x < margin.left - 1 || x > width - margin.right + 1) return;
      parts.push(`<line class="pe-grid" x1="${at(x)}" x2="${at(x)}" y1="${margin.top}" y2="${height - margin.bottom}"></line><text class="pe-tick" x="${at(x)}" y="${height - margin.bottom + 16}" text-anchor="middle">${esc(tickLabel(tick))}</text>`);
    });
    yAxis.ticks.forEach((tick) => {
      const y = yAxis.scale(tick);
      if (y === null || y < margin.top - 1 || y > height - margin.bottom + 1) return;
      parts.push(`<line class="pe-grid" x1="${margin.left}" x2="${width - margin.right}" y1="${at(y)}" y2="${at(y)}"></line><text class="pe-tick" x="${margin.left - 8}" y="${at(y + 4)}" text-anchor="end">${esc(tickLabel(tick))}</text>`);
    });
    parts.push(...efficiencyGuides(xAxis, yAxis, yMeta));
    parts.push(`<line class="pe-axis" x1="${margin.left}" x2="${width - margin.right}" y1="${height - margin.bottom}" y2="${height - margin.bottom}"></line><line class="pe-axis" x1="${margin.left}" x2="${margin.left}" y1="${margin.top}" y2="${height - margin.bottom}"></line>`);
    parts.push(`<text class="pe-axis-title" x="${at((margin.left + width - margin.right) / 2)}" y="${height - 8}" text-anchor="middle">${esc(xMeta.label)}</text>`);
    parts.push(`<text class="pe-axis-title" transform="translate(14 ${at((margin.top + height - margin.bottom) / 2)}) rotate(-90)" text-anchor="middle">${esc(yMeta.label)}</text>`);
    points.sort((a, b) => a.x.v - b.x.v || a.y.v - b.y.v).forEach((point) => {
      const x = at(xAxis.scale(point.x.v));
      const y = at(yAxis.scale(point.y.v));
      const fill = scheme.kind === "sequential" ? scheme.fillFor(point.index) : null;
      const colourClass = scheme.kind === "categorical" ? scheme.classFor(point.index) : scheme.kind === "sequential" ? (fill ? "pe-tone-custom" : "pe-cat-muted") : "pe-tone-neutral";
      const hollow = point.x.censored || point.y.censored || (scheme.kind === "sequential" && !fill);
      const paint = fill && !hollow ? ` style="fill:${fill};stroke:${fill}"` : "";
      const arrows = [];
      // Threshold values: an arrow shows which way the true value lies.
      if (point.y.censored) {
        const up = !String(point.y.q).startsWith("<");
        const d = up ? -1 : 1;
        arrows.push(`<path class="pe-censor-arrow" d="M${x} ${at(y + d * 8)} L${x} ${at(y + d * 16)} M${at(x - 3)} ${at(y + d * 13)} L${x} ${at(y + d * 16)} L${at(x + 3)} ${at(y + d * 13)}"></path>`);
      }
      if (point.x.censored) {
        const d = String(point.x.q).startsWith("<") ? -1 : 1;
        arrows.push(`<path class="pe-censor-arrow" d="M${at(x + d * 8)} ${y} L${at(x + d * 16)} ${y} M${at(x + d * 13)} ${at(y - 3)} L${at(x + d * 16)} ${y} L${at(x + d * 13)} ${at(y + 3)}"></path>`);
      }
      const nearRight = x > width - margin.right - 60;
      const text = compounds.length <= LABEL_LIMIT ? `<text class="pe-point-label" x="${at(nearRight ? x - 9 : x + 9)}" y="${at(y - 7)}" text-anchor="${nearRight ? "end" : "start"}">${esc(reg(point.index))}</text>` : "";
      const xText = xMeta.kind === "p" ? F.number(point.x.v, "", PROPERTY_AXES.find((axis) => `p:${axis.key}` === state.x)?.digits ?? 2) : `${point.x.censored ? `${point.x.q} ` : ""}${F.number(point.x.v, xMeta.endpoint?.unit || "", xMeta.kind === "e" ? null : 2)}`;
      const yText = yMeta.kind === "p" ? F.number(point.y.v, "", PROPERTY_AXES.find((axis) => `p:${axis.key}` === state.y)?.digits ?? 2) : `${point.y.censored ? `${point.y.q} ` : ""}${F.number(point.y.v, yMeta.endpoint?.unit || "", yMeta.kind === "e" ? null : 2)}`;
      const label = `${reg(point.index)}, ${xMeta.label} ${xText}, ${yMeta.label} ${yText}${scheme.kind === "categorical" ? `, ${categoryOf(state.colour, point.index)}` : ""}`;
      const dot = `<circle class="pe-dot ${colourClass}${hollow ? " is-hollow" : ""}" cx="${x}" cy="${y}" r="6"${paint}></circle>`;
      parts.push(pointGroup(`c:${point.index}`, label, `<circle class="pe-ring" cx="${x}" cy="${y}" r="10"></circle>${arrows.join("")}${dot}${text}`));
    });
    el.properties.innerHTML = `${svgOpen(width, height, `Property space: ${yMeta.label} against ${xMeta.label}, ${plural(points.length, "compound")}`)}${parts.join("")}</svg>`;
    const hidden = compounds.length - points.length;
    if (el.legend) el.legend.innerHTML = `${scheme.legend}${hidden ? `<span class="pattern-legend__note">${esc(plural(hidden, "compound"))} without both values ${hidden === 1 ? "is" : "are"} not shown</span>` : ""}`;
  };

  // -------------------------------------------------------------- inspector
  const valueLink = (value, endpoint) => {
    if (!value) return "—";
    const text = esc(fmtValue(value, endpoint, false));
    if (!value.sid || !links.summaries) return text;
    return `<a href="${esc(`${links.summaries}#ev-${value.sid}`)}" title="Open the saved summary${value.n ? ` (n = ${esc(value.n)})` : ""}">${text}</a>`;
  };
  const button = (attribute, value, text) => `<button type="button" class="button button--secondary button--small" ${attribute}="${esc(value)}">${esc(text)}</button>`;

  const compoundCard = (index, asComparison) => {
    const compound = compounds[index];
    const c = compareIndex();
    const showDelta = c !== null && c !== index;
    const badges = [
      index === c ? '<span class="status-badge status-badge--neutral">comparison</span>' : "",
      index === referenceIndex ? '<span class="status-badge status-badge--neutral">pharmacophore reference</span>' : "",
    ].join("");
    const note = onCore(index)
      ? (compound.core_note ? `<p class="pattern-card__warn">${esc(compound.core_note)} (versus the reference).</p>` : "")
      : `<p class="pattern-card__warn">${esc(statusText(compound.status))}</p>`;
    const siteItems = onCore(index)
      ? siteLabels.map((label) => {
        const site = siteOf(index, label);
        return `<li class="pattern-card__site"><b>${esc(label)}</b>${fragmentSvg(site)}<span>${esc(site?.name || "H")}<small>${esc(siteDetail(site))}</small></span></li>`;
      }).join("")
      : "";
    const rows = endpoints.map((endpoint, e) => {
      const value = valueOf(index, e);
      if (!value) return "";
      const other = showDelta ? exactOf(c, e) : null;
      const delta = value.s === "e" && other !== null ? deltaOf(other, value.v, endpoint) : null;
      const deltaCell = showDelta ? `<td class="numeric pe-fx-${tone(delta, endpoint)}">${esc(fmtDelta(delta, endpoint, true))}</td>` : "";
      return `<tr${e === state.endpoint ? ' class="is-current"' : ""}><th scope="row">${esc(endpoint.name)} <small>${esc(endpoint.unit_label)}</small></th><td class="numeric">${valueLink(value, endpoint)}</td>${deltaCell}</tr>`;
    }).join("");
    const table = rows
      ? `<table class="pattern-values"><caption class="visually-hidden">Saved results for ${esc(compound.reg)}</caption><thead><tr><th scope="col">Result</th><th scope="col" class="numeric">Value</th>${showDelta ? `<th scope="col" class="numeric">vs ${esc(reg(c))}</th>` : ""}</tr></thead><tbody>${rows}</tbody></table>`
      : '<p class="pattern-empty">No saved results for this compound.</p>';
    const props = compound.props || {};
    const propItems = PROPERTY_AXES.filter((axis) => Number.isFinite(props[axis.key])).map((axis) => `<li>${esc(axis.short)} <b>${esc(F.number(props[axis.key], "", axis.digits))}</b></li>`);
    const potency = endpoints[state.endpoint];
    const pValue = exactOf(index);
    if (potency.potency && pValue !== null) {
      if (Number.isFinite(props.clogp)) propItems.push(`<li title="LipE = ${esc(potency.name)} − cLogP">LipE <b>${(pValue - props.clogp).toFixed(2)}</b></li>`);
      if (props.hac > 0) propItems.push(`<li title="LE = 1.37 × ${esc(potency.name)} / heavy atoms">LE <b>${((1.37 * pValue) / props.hac).toFixed(2)}</b></li>`);
    }
    const actions = [
      index !== c && exactOf(index) !== null ? button("data-pe-set-compare", index, "Use as comparison") : "",
      links.sar ? `<a class="button button--secondary button--small" href="${esc(links.sar)}">Open SAR series</a>` : "",
      asComparison ? "" : button("data-pe-clear", "1", "Clear selection"),
    ].join("");
    return `<article class="pattern-card">${asComparison ? '<p class="pattern-card__kicker">Comparison compound</p>' : ""}${structureSvg(index)}
      <div class="pattern-card__title"><strong>${esc(compound.reg)}</strong>${compound.name ? `<span>${esc(compound.name)}</span>` : ""}${badges}</div>
      ${note}${siteItems ? `<ul class="pattern-card__sites" aria-label="R-groups">${siteItems}</ul>` : ""}${table}
      ${propItems.length ? `<ul class="pattern-props" aria-label="Calculated properties">${propItems.join("")}</ul>` : ""}
      <div class="pattern-card__actions">${actions}</div></article>`;
  };

  const pairCard = (i, j) => {
    const [from, to] = orient(i, j);
    const key = pairKey(i, j);
    const similarity = tanimoto.get(key);
    const overlap = mcsOverlap.get(key);
    const change = pairChange(from, to);
    const changeItems = change.known
      ? change.sites.map((label) => `<li><b>${esc(label)}</b>${fragmentSvg(siteOf(from, label))}<span>${esc(siteOf(from, label)?.name || "H")}</span><span aria-hidden="true">→</span>${fragmentSvg(siteOf(to, label))}<span>${esc(siteOf(to, label)?.name || "H")}</span></li>`)
      : [`<li><span>${esc(changeText(from, to))}</span></li>`];
    if (change.core) changeItems.push(`<li><b>Core</b><span>${esc(coreText(from, to))}</span></li>`);
    if (change.known && !changeItems.length) changeItems.push("<li><span>Same R-groups and core, for example stereoisomers or repeat registrations.</span></li>");
    const rows = endpoints.map((endpoint, e) => {
      const a = valueOf(from, e);
      const b = valueOf(to, e);
      if (!a && !b) return "";
      const delta = a && b && a.s === "e" && b.s === "e" ? deltaOf(a.v, b.v, endpoint) : null;
      return `<tr${e === state.endpoint ? ' class="is-current"' : ""}><th scope="row">${esc(endpoint.name)} <small>${esc(endpoint.unit_label)}</small></th><td class="numeric">${valueLink(a, endpoint)}</td><td class="numeric">${valueLink(b, endpoint)}</td><td class="numeric pe-fx-${tone(delta, endpoint)}">${esc(fmtDelta(delta, endpoint, true))}</td></tr>`;
    }).join("");
    // Links jump to the row in the Activity cliffs table below; rows beyond the first 12 per result are export-only.
    const records = [...(savedCliffs.get(key) || [])].sort((a, b) => a.e - b.e).map((record) => {
      const name = esc(endpoints[record.e]?.name || "Result");
      return record.shown
        ? `<a href="#ev-${esc(record.id)}" title="Open this pair in the Activity cliffs table">${name}</a>`
        : `<span title="Not among the rows shown in the table; listed in the JSON export">${name}</span>`;
    });
    const similarityText = [
      similarity !== undefined ? `Tanimoto ${similarity.toFixed(2)}` : "Tanimoto not calculated for this pair",
      overlap !== undefined ? `MCS overlap ${overlap.toFixed(2)} (saved pair record)` : "",
    ].filter(Boolean).join(" · ");
    return `<article class="pattern-card"><div class="pattern-pair"><figure>${structureSvg(from)}<figcaption>${esc(reg(from))}</figcaption></figure><span class="pattern-pair__arrow" aria-hidden="true">→</span><figure>${structureSvg(to)}<figcaption>${esc(reg(to))}</figcaption></figure></div>
      <p class="pattern-card__meta">${esc(similarityText)}</p>
      <h4 class="pattern-card__subhead">What changed</h4><ul class="pattern-change">${changeItems.join("")}</ul>
      <table class="pattern-values"><caption class="visually-hidden">Results for ${esc(reg(from))} and ${esc(reg(to))}</caption><thead><tr><th scope="col">Result</th><th scope="col" class="numeric">${esc(reg(from))}</th><th scope="col" class="numeric">${esc(reg(to))}</th><th scope="col" class="numeric">Change</th></tr></thead><tbody>${rows}</tbody></table>
      ${records.length ? `<p class="pattern-records">Saved activity-cliff records: ${records.join(", ")}</p>` : ""}
      <div class="pattern-card__actions">${button("data-pe-inspect", from, `Inspect ${reg(from)}`)}${button("data-pe-inspect", to, `Inspect ${reg(to)}`)}${button("data-pe-clear", "1", "Clear")}</div></article>`;
  };

  const renderInspector = () => {
    if (!el.inspector) return;
    const selection = state.selection;
    if (!selection) {
      const c = compareIndex();
      el.inspector.innerHTML = `<p class="pattern-empty">Select a dot or a pair in the list to see structures, saved results and what changed.</p>${c !== null ? compoundCard(c, true) : ""}`;
      return;
    }
    el.inspector.innerHTML = selection.type === "compound" ? compoundCard(selection.i, false) : pairCard(selection.i, selection.j);
  };

  const renderNote = () => {
    if (!el.note) return;
    const endpoint = endpoints[state.endpoint];
    const c = compareIndex();
    const notes = [];
    if (state.compare !== null && c !== null && c !== state.compare) notes.push(`${reg(state.compare)} has no exact ${endpoint.name} result, so differences are measured from ${reg(c)}.`);
    const outside = everyIndex.filter((index) => ["unmatched", "invalid"].includes(compounds[index].status)).map(reg);
    if (outside.length && siteLabels.length) {
      const names = `${joinList(outside.slice(0, 6))}${outside.length > 6 ? ` and ${outside.length - 6} more` : ""}`;
      notes.push(`${names} ${outside.length === 1 ? "does" : "do"} not contain the shared core, so ${outside.length === 1 ? "it is" : "they are"} left out of R-group effects but kept in the other views.`);
    }
    const pending = compounds.filter((compound) => compound.status === "not_in_run").length;
    if (pending) notes.push(`${plural(pending, "compound")} ${pending === 1 ? "was" : "were"} added after the last pattern check; refresh pattern checks to place ${pending === 1 ? "it" : "them"} on R-sites.`);
    const thresholds = everyIndex.filter((index) => valueOf(index)?.s === "c").length;
    if (thresholds) notes.push(`${plural(thresholds, `${endpoint.name} result`)} ${thresholds === 1 ? "is a threshold value" : "are threshold values"}: drawn at the bound and left out of ranges and pairs.`);
    const untested = everyIndex.filter((index) => !valueOf(index)).length;
    if (untested) notes.push(`${plural(untested, "compound")} without a ${endpoint.name} result.`);
    el.note.textContent = notes.join(" ");
  };

  // --------------------------------------------------- substructure highlight
  const renderHighlight = () => {
    if (!el.highlight) return;
    const highlight = state.highlight;
    el.highlight.hidden = !highlight;
    el.highlight.innerHTML = highlight
      ? `<span>Substructure: <strong>${highlight.ids.size} of ${compounds.length}</strong> match</span>${highlight.smarts ? `<code title="${esc(highlight.smarts)}">${esc(highlight.smarts)}</code>` : ""}<button type="button" class="pattern-link-button" data-pattern-highlight-clear>Clear</button>`
      : "";
  };
  const setHighlight = (highlight) => {
    state.highlight = highlight;
    try {
      if (highlight) window.sessionStorage.setItem(highlightKey, JSON.stringify({ ids: [...highlight.ids].map((index) => compounds[index].id), smarts: highlight.smarts, label: highlight.label }));
      else window.sessionStorage.removeItem(highlightKey);
    } catch (_error) {
      // Storage is optional; the highlight still applies on this page.
    }
    renderAll();
    if (el.live) el.live.textContent = highlight ? `${highlight.ids.size} compounds containing the substructure are highlighted in every view.` : "Substructure highlight cleared.";
  };

  // ---------------------------------------------------------------- tooltip
  // Visual only (aria-hidden): every point already carries the same facts in its aria-label.
  const tooltip = document.createElement("div");
  tooltip.className = "pattern-tooltip";
  tooltip.setAttribute("aria-hidden", "true");
  tooltip.hidden = true;
  document.body.append(tooltip);
  let tipTarget = null;
  const tipContent = (target) => {
    const key = target.dataset.peKey || "";
    const endpoint = endpoints[state.endpoint];
    if (key.startsWith("c:")) {
      const index = Number(key.slice(2));
      const detail = onCore(index) ? siteLabels.map((label) => `${label} ${siteOf(index, label)?.name || "H"}`).join(" · ") : statusText(compounds[index].status);
      return `<span class="pattern-tooltip__structures">${structureSvg(index, "pattern-tooltip__structure")}</span><strong>${esc(reg(index))}</strong><span>${esc(endpoint.name)} ${esc(fmtValue(valueOf(index), endpoint))}</span><span>${esc(detail)}</span>`;
    }
    if (!key.startsWith("p:")) return "";
    const [i, j] = key.slice(2).split("-").map(Number);
    const [from, to] = orient(i, j);
    const similarity = tanimoto.get(pairKey(i, j));
    return `<span class="pattern-tooltip__structures pattern-tooltip__structures--pair">${structureSvg(from, "pattern-tooltip__structure")}${structureSvg(to, "pattern-tooltip__structure")}</span><strong>${esc(reg(from))} → ${esc(reg(to))}</strong><span>${esc(endpoint.name)} ${esc(fmtDelta(deltaOf(exactOf(from), exactOf(to), endpoint), endpoint))}${similarity !== undefined ? ` · Tc ${similarity.toFixed(2)}` : ""}</span><span>${esc(changeText(from, to))}</span>`;
  };
  const placeTip = (clientX, clientY) => {
    const box = tooltip.getBoundingClientRect();
    let left = clientX + 16;
    let top = clientY + 16;
    if (left + box.width > window.innerWidth - 8) left = clientX - box.width - 16;
    if (top + box.height > window.innerHeight - 8) top = clientY - box.height - 16;
    tooltip.style.left = `${Math.max(8, left)}px`;
    tooltip.style.top = `${Math.max(8, top)}px`;
  };
  const showTip = (target, clientX, clientY) => {
    const html = tipContent(target);
    if (!html) return;
    tipTarget = target;
    tooltip.innerHTML = html;
    tooltip.hidden = false;
    placeTip(clientX, clientY);
  };
  const hideTip = () => {
    tipTarget = null;
    tooltip.hidden = true;
  };

  // -------------------------------------------------------------- selection
  const setRovingStop = (svg, preferred = null) => {
    const points = [...svg.querySelectorAll(".pe-point")];
    if (!points.length) return;
    const focused = points.find((point) => point === document.activeElement);
    const target = preferred || focused || points.find((point) => point.classList.contains("is-selected")) || points.find((point) => point.getAttribute("tabindex") === "0") || points[0];
    points.forEach((point) => point.setAttribute("tabindex", point === target ? "0" : "-1"));
  };
  const applySelection = () => {
    const selection = state.selection;
    const chosen = new Set(selection ? (selection.type === "compound" ? [selection.i] : [selection.i, selection.j]) : []);
    const matches = state.highlight ? state.highlight.ids : null;
    root.dataset.selection = selection ? selection.type : "";
    root.dataset.highlight = matches ? "on" : "";
    root.querySelectorAll("[data-pe-key]").forEach((node) => {
      const key = node.dataset.peKey;
      let selected = false;
      let linked = false;
      let match = false;
      if (key.startsWith("c:")) {
        const index = Number(key.slice(2));
        selected = chosen.has(index);
        match = Boolean(matches && matches.has(index));
      } else if (key.startsWith("p:")) {
        const [i, j] = key.slice(2).split("-").map(Number);
        selected = Boolean(selection && selection.type === "pair" && selection.i === i && selection.j === j);
        linked = Boolean(selection && selection.type === "compound" && (selection.i === i || selection.i === j));
        // A pair counts as matching only when both compounds contain the substructure.
        match = Boolean(matches && matches.has(i) && matches.has(j));
      }
      node.classList.toggle("is-selected", selected);
      node.classList.toggle("is-linked", linked);
      node.classList.toggle("is-match", match);
      node.setAttribute("aria-pressed", String(selected));
    });
    root.querySelectorAll("svg.pattern-svg").forEach((svg) => setRovingStop(svg));
  };
  const selectionText = () => {
    const selection = state.selection;
    const endpoint = endpoints[state.endpoint];
    if (!selection) return "Selection cleared.";
    if (selection.type === "compound") return `Selected ${reg(selection.i)}: ${endpoint.name} ${fmtValue(valueOf(selection.i), endpoint)}.`;
    const [from, to] = orient(selection.i, selection.j);
    return `Selected ${reg(from)} → ${reg(to)}: ${endpoint.name} ${fmtDelta(deltaOf(exactOf(from), exactOf(to), endpoint), endpoint)}. ${changeText(from, to)}.`;
  };
  const select = (selection, { toggle = true } = {}) => {
    const current = state.selection;
    const same = Boolean(selection && current && selection.type === current.type && selection.i === current.i && (selection.type === "compound" || selection.j === current.j));
    state.selection = toggle && same ? null : selection;
    applySelection();
    renderInspector();
    if (el.live) el.live.textContent = selectionText();
    save();
  };
  const activate = (node) => {
    const key = node.dataset.peKey || "";
    if (key.startsWith("c:")) select({ type: "compound", i: Number(key.slice(2)) });
    if (key.startsWith("p:")) {
      const [i, j] = key.slice(2).split("-").map(Number);
      select({ type: "pair", i: Math.min(i, j), j: Math.max(i, j) });
    }
  };

  // ----------------------------------------------------------------- render
  const renderActive = () => {
    if (state.tab === "sites") {
      renderSitePins();
      renderSiteSummary();
      syncComboControls();
      if (state.siteMode === "combine" && siteLabels.length > 1) renderMatrix();
      else renderStrip();
    } else if (state.tab === "landscape") {
      renderLandscape();
    } else {
      renderProperties();
    }
  };
  // Re-rendering replaces the pins, chips and dots, so remember what had focus.
  const focusSelector = () => {
    const active = document.activeElement;
    if (!(active instanceof Element) || !root.contains(active)) return null;
    if (active.dataset.peSite) return `.${active.classList[0]}[data-pe-site="${cssEscape(active.dataset.peSite)}"]`;
    if (active.dataset.peKey) return `.${active.classList[0]}[data-pe-key="${cssEscape(active.dataset.peKey)}"]`;
    return null;
  };
  let frame = 0;
  const renderAll = () => {
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
    const restore = focusSelector();
    renderToolbar();
    renderHighlight();
    renderActive();
    applySelection();
    renderInspector();
    renderNote();
    save();
    if (restore) {
      const target = root.querySelector(restore);
      if (target) {
        const chart = target.closest("svg.pattern-svg");
        if (chart && target.classList.contains("pe-point")) setRovingStop(chart, target);
        target.focus();
      }
    }
  };
  const schedule = () => {
    if (!frame) frame = window.requestAnimationFrame(renderAll);
  };
  const setTab = (tab, focusTab = false) => {
    if (!TABS.includes(tab)) return;
    state.tab = tab;
    hideTip();
    renderAll();
    if (focusTab) el.tabs.find((item) => item.dataset.patternTab === tab)?.focus();
  };
  const setSite = (label) => {
    if (!siteLabels.includes(label)) return;
    // Combining sites: a chosen site becomes the rows; the previous rows move to the columns.
    if (state.siteMode === "combine" && label !== state.rows) {
      const previous = state.rows;
      state.rows = label;
      if (state.cols === label) state.cols = previous;
      if (state.split === label) state.split = "";
    }
    state.site = label;
    // Property-space colouring follows the chosen site when it is colouring by site.
    if (/^(site|feature):/.test(state.colour || "")) state.colour = `${state.colour.split(":")[0]}:${label}`;
    renderAll();
  };

  // ----------------------------------------------------------------- events
  el.endpoint?.addEventListener("change", () => {
    const previous = state.endpoint;
    state.endpoint = Number(el.endpoint.value);
    // A Y axis that followed the previous result follows the new one.
    const [kind, key] = String(state.y || "").split(":");
    if (["e", "lipe", "le"].includes(kind) && Number(key) === previous) {
      state.y = kind !== "e" && !endpoints[state.endpoint].potency ? `e:${state.endpoint}` : `${kind}:${state.endpoint}`;
    }
    renderAll();
  });
  el.compare?.addEventListener("change", () => {
    state.compare = Number(el.compare.value);
    renderAll();
  });
  el.group?.addEventListener("change", () => {
    state.group = GROUPS.includes(el.group.value) ? el.group.value : "substituent";
    renderAll();
  });
  el.siteMode?.addEventListener("change", () => {
    state.siteMode = el.siteMode.value === "combine" && siteLabels.length > 1 ? "combine" : "one";
    if (state.siteMode === "combine") state.rows = state.site;
    hideTip();
    renderAll();
  });
  el.comboRows?.addEventListener("change", () => {
    const previous = state.rows;
    state.rows = el.comboRows.value;
    if (state.cols === state.rows) state.cols = previous;
    state.site = state.rows;
    renderAll();
  });
  el.comboCols?.addEventListener("change", () => {
    state.cols = el.comboCols.value;
    renderAll();
  });
  el.comboSplit?.addEventListener("change", () => {
    state.split = el.comboSplit.value;
    renderAll();
  });
  el.comboLoose?.addEventListener("change", () => {
    state.loose = el.comboLoose.checked;
    renderAll();
  });
  el.sim?.addEventListener("input", () => {
    state.sim = Number(el.sim.value);
    if (el.simOut) el.simOut.textContent = state.sim.toFixed(2);
    schedule();
  });
  el.delta?.addEventListener("input", () => {
    state.delta = Number(el.delta.value);
    if (el.deltaOut) el.deltaOut.textContent = thresholdText(state.delta, endpoints[state.endpoint]);
    schedule();
  });
  el.single?.addEventListener("change", () => {
    state.single = el.single.checked;
    renderAll();
  });
  [["x", el.x], ["y", el.y], ["colour", el.colour]].forEach(([name, select]) => select?.addEventListener("change", () => {
    state[name] = select.value;
    renderAll();
  }));
  el.tabs.forEach((tab) => tab.addEventListener("click", () => setTab(tab.dataset.patternTab)));
  el.tablist?.addEventListener("keydown", (event) => {
    const index = el.tabs.indexOf(event.target);
    if (index < 0) return;
    const moves = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: el.tabs.length - 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = el.tabs[(moves[event.key] + el.tabs.length) % el.tabs.length];
    setTab(next.dataset.patternTab, true);
  });

  root.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const site = target.closest("[data-pe-site]");
    if (site) return setSite(site.dataset.peSite);
    const compare = target.closest("[data-pe-set-compare]");
    if (compare) {
      state.compare = Number(compare.dataset.peSetCompare);
      renderAll();
      el.inspector?.focus();
      return undefined;
    }
    const inspect = target.closest("[data-pe-inspect]");
    if (inspect) {
      select({ type: "compound", i: Number(inspect.dataset.peInspect) }, { toggle: false });
      el.inspector?.focus();
      return undefined;
    }
    if (target.closest("[data-pe-clear]")) return select(null);
    if (target.closest("[data-pattern-highlight-clear]")) return setHighlight(null);
    const point = target.closest("[data-pe-key]");
    if (point) activate(point);
    return undefined;
  });
  // Charts are one tab stop each; arrow keys move between points (roving tabindex).
  root.addEventListener("keydown", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const point = target?.closest(".pe-point");
    if (event.key === "Escape") {
      if (!tooltip.hidden) hideTip();
      else if (point && state.selection) select(null);
      return;
    }
    if (!point) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(point);
      return;
    }
    // Matrix cells are ordinary buttons in the tab order; only SVG charts use arrow-key roving.
    const chart = point.closest("svg.pattern-svg");
    if (!chart) return;
    const points = [...chart.querySelectorAll(".pe-point")];
    const position = points.indexOf(point);
    const moves = { ArrowRight: position + 1, ArrowDown: position + 1, ArrowLeft: position - 1, ArrowUp: position - 1, Home: 0, End: points.length - 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = points[clamp(moves[event.key], 0, points.length - 1)];
    setRovingStop(point.closest("svg"), next);
    next.focus();
  });

  const tipSource = (event) => (event.target instanceof Element ? event.target.closest(".pe-point, .pattern-cliff") : null);
  root.addEventListener("pointerover", (event) => {
    const target = tipSource(event);
    if (target && target !== tipTarget) showTip(target, event.clientX, event.clientY);
  });
  root.addEventListener("pointermove", (event) => {
    if (tipTarget) placeTip(event.clientX, event.clientY);
  });
  root.addEventListener("pointerout", (event) => {
    const target = tipSource(event);
    if (target && !(event.relatedTarget instanceof Node && target.contains(event.relatedTarget))) hideTip();
  });
  root.addEventListener("focusin", (event) => {
    const target = event.target instanceof Element ? event.target.closest(".pe-point") : null;
    if (!target) return;
    const box = target.getBoundingClientRect();
    showTip(target, box.right, box.bottom);
  });
  root.addEventListener("focusout", (event) => {
    if (event.target instanceof Element && event.target.closest(".pe-point")) hideTip();
  });
  window.addEventListener("scroll", hideTip, { passive: true });

  // Redraw on width changes (charts are drawn at their real pixel width) and theme switches.
  if ("ResizeObserver" in window && el.main) {
    let lastWidth = 0;
    new ResizeObserver((entries) => {
      const width = Math.round(entries[0]?.contentRect.width || 0);
      if (!width || Math.abs(width - lastWidth) < 8) return;
      lastWidth = width;
      schedule();
    }).observe(el.main);
  }
  new MutationObserver(schedule).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  // Find by structure (static/structure_search.js): use a hit as the comparison, or highlight all hits.
  (window.SARStructureActions = window.SARStructureActions || []).push(
    {
      id: "explorer-compare",
      scope: "compound",
      label: "Use as explorer comparison",
      run: ({ compoundId }) => {
        const index = compoundById.get(compoundId);
        if (index === undefined) return false;
        state.compare = index;
        renderAll();
        root.scrollIntoView({ behavior: "smooth", block: "start" });
        return true;
      },
    },
    {
      id: "explorer-highlight",
      scope: "set",
      label: "Highlight matches in the explorer",
      run: ({ ids, smarts, label }) => {
        const indices = (ids || []).map((id) => compoundById.get(id)).filter((index) => index !== undefined);
        setHighlight({ ids: new Set(indices), smarts: smarts || "", label: label || "substructure" });
        root.scrollIntoView({ behavior: "smooth", block: "start" });
        return true;
      },
    },
  );

  syncPropertyControls();
  renderAll();
})();
