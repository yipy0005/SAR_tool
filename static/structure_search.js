// Find by structure: draw (or type) a substructure, search the project, and use a hit.
//
// The dialog is on every workspace page. Pages add their own result actions by
// pushing { id, scope: "compound" | "set", label, run(detail) } onto
// window.SARStructureActions (for example "Set as SAR reference" on the SAR
// page). Elsewhere, links carry the chosen compound to those pages. Read-only:
// the server matches saved structures and nothing is written.
(() => {
  "use strict";

  const dialog = document.querySelector("[data-structure-search]");
  if (!dialog) return;
  const projectId = dialog.dataset.projectId || "";
  let links = {};
  try {
    links = JSON.parse(dialog.dataset.links || "{}");
  } catch (_error) {
    links = {};
  }
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const find = (selector) => dialog.querySelector(selector);
  const el = {
    sketcher: find("[data-structure-sketcher]"),
    text: find("[data-structure-text]"),
    format: find("[data-structure-format]"),
    drawText: find("[data-structure-draw-text]"),
    compound: find("[data-structure-compound]"),
    run: find("[data-structure-run]"),
    use: find("[data-structure-use-drawing]"),
    status: find("[data-structure-status]"),
    results: find("[data-structure-results]"),
    close: find("[data-structure-close]"),
  };
  const storageKey = `sar-structure-search:${projectId}`;
  const highlightKey = `sar-structure-highlight:${projectId}`;
  const DRAWN_LIMIT = 60;
  const KETCHER_LOAD_MS = 60000;
  const KETCHER_CALL_MS = 20000;
  // Two editors behind one interface, both exchanging MDL molfiles with the server: Ketcher
  // (EPAM Systems, Apache-2.0) in an isolated same-origin frame when it is installed, and the
  // built-in sketcher otherwise or when Ketcher cannot start.
  const ketcher = {
    url: dialog.dataset.ketcherUrl || "",
    frame: find("[data-ketcher-frame]"),
    box: find("[data-structure-ketcher]"),
    status: find("[data-ketcher-status]"),
    tabs: [...dialog.querySelectorAll("[data-structure-editor]")],
    api: null,
    loading: null,
  };
  let editor = ketcher.url ? "ketcher" : "simple";
  let sketcher = null;
  let lastEdited = "sketch";
  let last = null;
  let rememberedMolblock = "";
  let rememberTimer = 0;

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const setStatus = (message, kind = "") => {
    if (!el.status) return;
    el.status.textContent = message;
    el.status.dataset.status = kind;
  };
  const post = async (url, body) => {
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch(url, { method: "POST", headers, credentials: "same-origin", body: JSON.stringify(body) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || `request failed (${response.status})`);
    return payload;
  };
  const remember = () => {
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify({ molblock: rememberedMolblock, text: el.text?.value || "", format: el.format?.value || "auto", lastEdited, editor }));
    } catch (_error) {
      // Session storage is optional; the dialog still works without it.
    }
  };
  const restore = () => {
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(storageKey) || "{}") || {};
      rememberedMolblock = typeof saved.molblock === "string" ? saved.molblock : "";
      if (saved.text && el.text) el.text.value = saved.text;
      if (saved.format && el.format) el.format.value = saved.format;
      if (saved.lastEdited === "text" || saved.lastEdited === "sketch") lastEdited = saved.lastEdited;
      if (saved.editor === "simple" || (saved.editor === "ketcher" && ketcher.url)) editor = saved.editor;
    } catch (_error) {
      // Ignore unreadable saved state.
    }
  };
  const withTimeout = (promise, ms, what) => Promise.race([
    Promise.resolve(promise),
    new Promise((_resolve, reject) => { window.setTimeout(() => reject(new Error(`${what} did not respond`)), ms); }),
  ]);
  const ketcherCall = (promise) => withTimeout(promise, KETCHER_CALL_MS, "Ketcher");
  const atomCount = (molfile) => {
    const lines = String(molfile || "").split(/\r?\n/);
    if (/V3000/.test(lines[3] || "")) return parseInt((lines.find((line) => line.startsWith("M  V30 COUNTS")) || "").split(/\s+/)[3], 10) || 0;
    return parseInt((lines[3] || "").slice(0, 3), 10) || 0;
  };
  // The built-in sketcher reads plain V2000 molfiles; Ketcher's query blocks (atom lists, R-groups,
  // query properties) or V3000 would be lost, so such drawings stay in Ketcher.
  const simpleCanShow = (molfile) => {
    const lines = String(molfile || "").split(/\r?\n/);
    if (/V3000/.test(lines[3] || "")) return false;
    const atoms = parseInt((lines[3] || "").slice(0, 3), 10) || 0;
    const bonds = parseInt((lines[3] || "").slice(3, 6), 10) || 0;
    // Query bond types 5-7 (single/double, single/aromatic, double/aromatic) would widen to "any".
    const bondTypes = lines.slice(4 + atoms, 4 + atoms + bonds).map((line) => parseInt(line.slice(6, 9), 10));
    return bondTypes.every((type) => [1, 2, 3, 4, 8].includes(type)) && lines.every((line) => !line.startsWith("M  ") || /^M {2}(CHG|END)/.test(line));
  };
  const ensureSketcher = () => {
    if (sketcher || !el.sketcher || !window.SARSketcher) return sketcher;
    sketcher = window.SARSketcher.create(el.sketcher, {
      label: "Substructure drawing canvas",
      onChange: () => {
        lastEdited = "sketch";
        rememberedMolblock = sketcher.getMolblock();
        remember();
      },
    });
    if (rememberedMolblock && simpleCanShow(rememberedMolblock)) sketcher.setMolblock(rememberedMolblock);
    return sketcher;
  };
  const setKetcherStatus = (message, kind = "") => {
    if (!ketcher.status) return;
    ketcher.status.textContent = message;
    ketcher.status.dataset.status = kind;
    ketcher.status.hidden = !message;
  };
  const onKetcherChange = () => {
    lastEdited = "sketch";
    window.clearTimeout(rememberTimer);
    rememberTimer = window.setTimeout(async () => {
      try {
        rememberedMolblock = await ketcherCall(ketcher.api.getMolfile());
        remember();
      } catch (_error) {
        // Keep the previous copy; the live drawing is still read when searching.
      }
    }, 600);
  };
  // Loads the editor frame on first use and waits for its window.ketcher API and chemistry engine.
  const loadKetcher = () => {
    if (!ketcher.url || !ketcher.frame) return Promise.reject(new Error("Ketcher is not installed"));
    if (ketcher.loading) return ketcher.loading;
    setKetcherStatus("Loading Ketcher…");
    ketcher.loading = new Promise((resolve, reject) => {
      const started = Date.now();
      const poll = () => {
        let api = null;
        try {
          api = ketcher.frame.contentWindow ? ketcher.frame.contentWindow.ketcher : null;
        } catch (_error) {
          api = null;
        }
        if (api && typeof api.getMolfile === "function" && typeof api.setMolecule === "function") resolve(api);
        else if (Date.now() - started > KETCHER_LOAD_MS) reject(new Error("the editor did not start"));
        else window.setTimeout(poll, 200);
      };
      ketcher.frame.addEventListener("load", poll, { once: true });
      ketcher.frame.src = ketcher.url;
    }).then(async (api) => {
      // The UI can appear even when the engine is blocked, so prove the engine answers before using it.
      await withTimeout(api.getSmiles(), KETCHER_CALL_MS, "Ketcher's chemistry engine");
      ketcher.api = api;
      setKetcherStatus("");
      try {
        api.editor?.subscribe?.("change", onKetcherChange);
      } catch (_error) {
        // Change tracking only keeps the drawing across pages; searching still reads the live drawing.
      }
      if (rememberedMolblock) {
        try {
          await ketcherCall(api.setMolecule(rememberedMolblock));
        } catch (_error) {
          // Start with an empty canvas if the saved drawing cannot be shown.
        }
      }
      return api;
    }).catch((error) => {
      setKetcherStatus(`Ketcher could not start (${error.message}). The simple sketcher is used instead.`, "error");
      setEditor("simple", { transfer: false });
      throw error;
    });
    return ketcher.loading;
  };
  // The drawing in the active editor as a molfile ("" when empty).
  const currentDrawing = async () => {
    if (editor === "ketcher" && ketcher.url) {
      const api = await loadKetcher();
      if (typeof api.containsReaction === "function" && api.containsReaction()) throw new Error("reactions cannot be searched; draw a single structure");
      let molfile;
      try {
        molfile = await ketcherCall(api.getMolfile("v2000"));
      } catch (_error) {
        molfile = await ketcherCall(api.getMolfile("v3000"));
      }
      return atomCount(molfile) ? molfile : "";
    }
    ensureSketcher();
    return sketcher && !sketcher.isEmpty() ? sketcher.getMolblock() : "";
  };
  const showDrawing = async (molblock) => {
    if (editor === "ketcher" && ketcher.url) {
      const api = await loadKetcher();
      await ketcherCall(api.setMolecule(molblock));
    } else {
      ensureSketcher();
      if (!sketcher || !sketcher.setMolblock(molblock)) throw new Error("the structure could not be drawn");
    }
    rememberedMolblock = molblock;
  };
  // Switch editors, carrying the current drawing across when the target editor can show it.
  async function setEditor(next, { transfer = true } = {}) {
    const target = next === "ketcher" && ketcher.url ? "ketcher" : "simple";
    let carried = "";
    if (transfer && target !== editor) {
      try {
        carried = await currentDrawing();
      } catch (_error) {
        carried = "";
      }
    }
    editor = target;
    ketcher.tabs.forEach((tab) => tab.setAttribute("aria-pressed", String(tab.dataset.structureEditor === target)));
    if (ketcher.box) ketcher.box.hidden = target !== "ketcher";
    if (el.sketcher) el.sketcher.hidden = target !== "simple";
    remember();
    if (target === "simple") {
      ensureSketcher();
      if (carried && sketcher) {
        if (simpleCanShow(carried)) sketcher.setMolblock(carried);
        else setStatus("That Ketcher drawing uses query features (atom lists, R-groups or query properties) the simple sketcher cannot show. Switch back to Ketcher to keep them.", "error");
      }
      return;
    }
    try {
      const api = await loadKetcher();
      if (carried) await ketcherCall(api.setMolecule(carried));
    } catch (_error) {
      // loadKetcher has already reported the problem and switched to the simple sketcher.
    }
  }
  let restored = false;
  // hint: optional guidance from the opener (for example "draw the core"), shown instead of the default prompt.
  // useId: optional "drawing" action offered as the main button (for example "Use drawing as core").
  const open = (hint = "", useId = "") => {
    setUseAction(useId);
    if (!restored) {
      restore();
      restored = true;
    }
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    setEditor(editor, { transfer: false });
    if (hint) setStatus(hint);
    else if (!last) setStatus(rememberedMolblock ? "Your last drawing is restored. Find compounds, or change it first." : "Draw a substructure, type SMILES / SMARTS, or start from a compound.");
  };
  const close = () => {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  };

  const withParams = (url, params, hash = "") => {
    const target = new URL(url, window.location.origin);
    Object.entries(params).forEach(([key, value]) => target.searchParams.set(key, value));
    return `${target.pathname}${target.search}${hash ? `#${hash}` : ""}`;
  };
  const pageActions = () => (Array.isArray(window.SARStructureActions) ? window.SARStructureActions.filter((action) => action && action.id && typeof action.run === "function") : []);

  // Per-compound actions: this page's own actions first, then links to the pages that use a reference.
  const hitActions = (item, actions) => {
    const own = actions.filter((action) => action.scope !== "set" && action.scope !== "drawing").map((action) => `<button type="button" class="button button--secondary button--small" data-structure-action="${esc(action.id)}" data-compound-id="${esc(item.compound_id)}">${esc(action.label)}</button>`);
    const ownIds = new Set(actions.map((action) => action.id));
    if (!ownIds.has("sar-reference") && links.sar) own.push(`<a class="button button--secondary button--small" href="${esc(withParams(links.sar, { reference: item.compound_id }))}">Set as SAR reference →</a>`);
    if (!ownIds.has("explorer-compare") && links.analysis) own.push(`<a class="button button--secondary button--small" href="${esc(withParams(links.analysis, { compare: item.compound_id }, "pattern-explorer"))}">Compare in Find patterns →</a>`);
    return own.join("");
  };
  const setActions = (actions) => {
    const own = actions.filter((action) => action.scope === "set").map((action) => `<button type="button" class="button button--secondary button--small" data-structure-action="${esc(action.id)}">${esc(action.label)}</button>`);
    if (!actions.some((action) => action.id === "explorer-highlight") && links.analysis) own.push('<button type="button" class="button button--secondary button--small" data-structure-highlight-nav>Show matches in Find patterns →</button>');
    return own.length ? `<div class="structure-search__bulk" role="group" aria-label="Use all matches">${own.join("")}</div>` : "";
  };
  const renderResults = (payload, typed) => {
    const { counts, results, query } = payload;
    const actions = pageActions();
    const target = typed ? `<code>${esc(typed)}</code>` : "the drawn substructure";
    const caveats = [
      payload.complete ? "" : ` The search stopped at the time limit after ${counts.scanned} of ${counts.total} compounds.`,
      counts.unreadable ? ` ${counts.unreadable} saved structure${counts.unreadable === 1 ? "" : "s"} could not be read.` : "",
    ].join("");
    const drawn = results.filter((item) => item.svg);
    const listed = results.filter((item) => !item.svg);
    const cards = drawn.map((item) => `<li class="structure-hit"><div class="structure-hit__svg" role="img" aria-label="${esc(item.registration_id)} with the match highlighted">${item.svg}</div>
      <div class="structure-hit__meta"><strong>${esc(item.registration_id)}</strong>${item.preferred_name ? `<span>${esc(item.preferred_name)}</span>` : ""}<small>${item.match_count > 1 ? `${item.match_count} matches` : "1 match"}</small></div>
      <div class="structure-hit__actions">${hitActions(item, actions)}</div></li>`).join("");
    el.results.innerHTML = `<p class="structure-search__summary"><strong>${counts.matched} of ${counts.total} compounds</strong> contain ${target}.${esc(caveats)}</p>
      <p class="structure-search__smarts">Searched as SMARTS: <code>${esc(query.smarts)}</code></p>
      ${results.length ? setActions(actions) : ""}
      ${cards ? `<ul class="structure-search__grid">${cards}</ul>` : '<p class="structure-search__empty">No compound in this project contains this substructure. Remove atoms, or use A, Q, * or the any bond to make it more general.</p>'}
      ${listed.length ? `<p class="structure-search__more">${listed.length} more match${listed.length === 1 ? "" : "es"} without drawings: ${listed.map((item) => esc(item.registration_id)).join(", ")}</p>` : ""}`;
    el.results.hidden = false;
  };
  const storeHighlight = () => {
    if (!last) return;
    try {
      window.sessionStorage.setItem(highlightKey, JSON.stringify({ ids: last.matched_ids, smarts: last.query.smarts, label: last.label, total: last.counts.total }));
    } catch (_error) {
      // Without storage the explorer simply shows no highlight.
    }
  };

  // The most recently edited input: the drawing, or the typed SMILES / SMARTS. Null when both are empty.
  const readQuery = async () => {
    const typed = (el.text?.value || "").trim();
    let drawing = "";
    if (!(typed && lastEdited === "text")) {
      try {
        drawing = await currentDrawing();
      } catch (error) {
        if (!typed) throw error;
      }
    }
    if (!typed && !drawing) return null;
    const useText = Boolean(typed) && (lastEdited === "text" || !drawing);
    const body = useText ? { project_id: projectId, query: typed, query_format: el.format?.value || "auto" } : { project_id: projectId, molblock: drawing };
    return { body, useText, typed };
  };

  // A page action with scope "drawing" uses the drawing itself (for example as a declared core).
  let useAction = null;
  const setUseAction = (actionId) => {
    useAction = actionId ? pageActions().find((action) => action.id === actionId && action.scope === "drawing") || null : null;
    if (el.use) {
      el.use.hidden = !useAction;
      el.use.textContent = useAction ? useAction.label : "";
    }
    // With a drawing action the search becomes the secondary, optional check.
    el.run?.classList.toggle("button--primary", !useAction);
    el.run?.classList.toggle("button--secondary", Boolean(useAction));
  };
  const useDrawing = async () => {
    if (!useAction) return;
    if (el.use) el.use.disabled = true;
    try {
      const read = await readQuery();
      if (!read) {
        setStatus("Draw a structure or type SMILES / SMARTS first.", "error");
        return;
      }
      setStatus("Converting the structure to SMARTS…");
      const payload = await post("/api/v1/structure/core", read.body);
      const outcome = useAction.run({ result: payload.result, label: read.useText ? read.typed : "drawing" });
      if (typeof outcome === "string") setStatus(outcome, "error");
      else if (outcome !== false) close();
    } catch (error) {
      setStatus(`The structure could not be used: ${error.message}`, "error");
    } finally {
      if (el.use) el.use.disabled = false;
    }
  };

  const run = async () => {
    if (el.run) el.run.disabled = true;
    try {
      const read = await readQuery();
      if (!read) {
        setStatus("Draw a substructure or type SMILES / SMARTS first.", "error");
        return;
      }
      const { body, useText, typed } = read;
      setStatus(useText ? "Searching the typed query…" : editor === "ketcher" ? "Searching the Ketcher drawing…" : "Searching the drawing…");
      const payload = await post("/api/v1/search/substructure", { ...body, limit: DRAWN_LIMIT });
      last = { ...payload, label: useText ? typed : "drawn substructure" };
      renderResults(payload, useText ? typed : "");
      setStatus(`${useText ? "Searched the typed text" : "Searched the drawing"} · ${payload.counts.matched} of ${payload.counts.total} compounds match · ${payload.elapsed_ms} ms.`, "success");
    } catch (error) {
      setStatus(`The search could not run: ${error.message}`, "error");
    } finally {
      if (el.run) el.run.disabled = false;
    }
  };
  const loadSketch = async (body, description) => {
    setStatus(`Loading ${description}…`);
    try {
      const payload = await post("/api/v1/structure/sketch", body);
      await showDrawing(payload.result.molblock);
      lastEdited = "sketch";
      remember();
      setStatus(`Loaded ${description}. Erase or change atoms to make the query more general, then find compounds.`, "success");
      if (editor === "simple") sketcher?.focus();
    } catch (error) {
      setStatus(`Could not load ${description}: ${error.message}`, "error");
    }
  };

  document.addEventListener("click", (event) => {
    const opener = event.target instanceof Element ? event.target.closest("[data-structure-search-open]") : null;
    if (!opener) return;
    event.preventDefault();
    open(opener.dataset.structureHint || "", opener.dataset.structureUse || "");
  });
  el.use?.addEventListener("click", useDrawing);
  el.close?.addEventListener("click", close);
  ketcher.tabs.forEach((tab) => tab.addEventListener("click", () => setEditor(tab.dataset.structureEditor)));
  // A click on the dimmed backdrop (the dialog element itself) closes it.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  el.run?.addEventListener("click", run);
  el.text?.addEventListener("input", () => {
    lastEdited = "text";
    remember();
  });
  el.text?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    lastEdited = "text";
    run();
  });
  el.format?.addEventListener("change", remember);
  el.drawText?.addEventListener("click", () => {
    const typed = (el.text?.value || "").trim();
    if (!typed) {
      setStatus("Type a SMILES string first.", "error");
      return;
    }
    loadSketch({ smiles: typed }, "the typed SMILES");
  });
  dialog.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-structure-load]") : null;
    if (!button) return;
    const compoundId = el.compound?.value || "";
    if (!compoundId) {
      setStatus("Choose a compound to start from.", "error");
      el.compound?.focus();
      return;
    }
    const name = el.compound.selectedOptions[0]?.textContent || "the compound";
    const scaffold = button.dataset.structureLoad === "scaffold";
    loadSketch({ project_id: projectId, compound_id: compoundId, part: scaffold ? "scaffold" : "molecule" }, scaffold ? `the ring scaffold of ${name}` : name);
  });
  el.results?.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const actionButton = target?.closest("[data-structure-action]");
    if (actionButton && last) {
      const action = pageActions().find((item) => item.id === actionButton.dataset.structureAction);
      if (!action) return;
      const detail = { compoundId: actionButton.dataset.compoundId || null, ids: last.matched_ids, smarts: last.query.smarts, query: last.query, label: last.label, counts: last.counts };
      // An action returns false to keep the dialog open, or a message to show in the dialog instead of closing.
      const outcome = action.run(detail);
      if (typeof outcome === "string") setStatus(outcome, "error");
      else if (outcome !== false) close();
      return;
    }
    if (target?.closest("[data-structure-highlight-nav]") && last && links.analysis) {
      storeHighlight();
      window.location.href = withParams(links.analysis, {}, "pattern-explorer");
    }
  });

  // ?find=<SMILES or SMARTS> (with optional ?find_format=auto|smiles|smarts) opens this dialog and runs that
  // search, so a shared link shows exactly what the search returned. Read-only, like the dialog itself.
  const linked = new URLSearchParams(window.location.search);
  const linkedText = (linked.get("find") || "").trim().slice(0, 500);
  const runLinkedSearch = () => {
    const format = linked.get("find_format") || "auto";
    open();
    if (el.text) el.text.value = linkedText;
    if (el.format) el.format.value = ["auto", "smiles", "smarts"].includes(format) ? format : "auto";
    lastEdited = "text";
    remember();
    run();
  };
  if (linkedText) {
    // Deferred page scripts register their result actions before DOMContentLoaded fires.
    if (document.readyState === "complete") runLinkedSearch();
    else document.addEventListener("DOMContentLoaded", runLinkedSearch, { once: true });
  }

  window.SARStructureSearch = Object.freeze({ open, highlightKey, storeHighlight });
})();
