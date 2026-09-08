(() => {
  "use strict";

  const data = window.SAR_DATA || { matrix: {}, compounds: [] };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const toast = $("#toast");
  let toastTimer;
  const compareSelection = new Set();

  const showToast = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("is-visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 3400);
  };

  const scrollToId = (id) => {
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    target.setAttribute("tabindex", "-1");
    window.setTimeout(() => target.focus({ preventScroll: true }), 450);
  };

  $$('[data-scroll-to]').forEach((button) => {
    button.addEventListener("click", () => scrollToId(button.dataset.scrollTo));
  });

  $$('[data-nav-target]').forEach((button) => {
    button.addEventListener("click", () => scrollToId(button.dataset.navTarget));
  });

  $$('[data-nav]').forEach((link) => {
    link.addEventListener("click", () => {
      $$('[data-nav]').forEach((item) => item.classList.toggle("is-active", item === link));
    });
  });

  const renderMatrix = (endpoint = "biochemical") => {
    const matrix = data.matrix[endpoint];
    const tableBody = $("#sarMatrix tbody");
    if (!matrix || !tableBody) return;
    $("#matrixLabel").textContent = `${matrix.label} · ${matrix.direction}`;
    $("#matrixNote").textContent = matrix.note;
    tableBody.innerHTML = matrix.rows.map((row) => {
      const cells = row.cells.map((cell, index) => {
        const value = cell.value;
        if (!value) {
          return `<td class="matrix-gap"><button type="button" data-gap="${row.r1}/${matrix.columns[index]}" aria-label="Design gap R1 ${row.r1}, R2 ${matrix.columns[index]}">+ explore</button></td>`;
        }
        const numeric = Number(value);
        const isClearance = endpoint === "adme";
        const tier = isClearance ? (numeric <= 18 ? "matrix-high" : numeric <= 28 ? "matrix-mid" : "") : (numeric >= 7 ? "matrix-high" : numeric >= 6.4 ? "matrix-mid" : "");
        return `<td class="${tier}"><div class="matrix-cell"><strong>${value}</strong><small>n=${cell.n}</small></div></td>`;
      }).join("");
      return `<tr><td class="matrix-row-label" scope="row">${row.r1}</td>${cells}</tr>`;
    }).join("");

    $$('[data-gap]').forEach((button) => button.addEventListener("click", () => {
      showToast(`Design gap selected: ${button.dataset.gap}. Added to the Fill queue.`);
      scrollToId("designs");
    }));
  };

  renderMatrix();
  $$('[data-endpoint]').forEach((button) => {
    button.addEventListener("click", () => {
      $$('[data-endpoint]').forEach((item) => item.classList.toggle("is-active", item === button));
      renderMatrix(button.dataset.endpoint);
    });
  });

  const compoundCards = $$('[data-compound-card]');
  $$('[data-compound-filter]').forEach((button) => {
    button.addEventListener("click", () => {
      $$('[data-compound-filter]').forEach((item) => item.classList.toggle("is-active", item === button));
      const filter = button.dataset.compoundFilter;
      let shown = 0;
      compoundCards.forEach((card) => {
        const visible = filter === "all" || (filter === "cellular" ? card.dataset.hasCellular === "true" : card.dataset.status === filter);
        card.hidden = !visible;
        if (visible) shown += 1;
      });
      $("#compoundEmpty").hidden = shown !== 0;
    });
  });

  const renderCompareTray = () => {
    const tray = $("#compareTray");
    const items = $("#compareItems");
    if (!tray || !items) return;
    tray.hidden = compareSelection.size === 0;
    $("#compareCount").textContent = `${compareSelection.size} ${compareSelection.size === 1 ? "compound" : "compounds"} selected`;
    items.innerHTML = [...compareSelection].map((id) => `<span class="compare-pill">${id}<button type="button" data-remove-compare="${id}" aria-label="Remove ${id} from comparison"><svg class="icon icon--tiny" aria-hidden="true"><use href="#i-close"></use></svg></button></span>`).join("");
    $$('[data-remove-compare]').forEach((button) => button.addEventListener("click", () => toggleCompare(button.dataset.removeCompare, false)));
  };

  const toggleCompare = (id, force) => {
    const shouldAdd = typeof force === "boolean" ? force : !compareSelection.has(id);
    if (shouldAdd && compareSelection.size >= 4) {
      showToast("Compare up to four compounds at a time.");
      return;
    }
    if (shouldAdd) compareSelection.add(id);
    else compareSelection.delete(id);
    $$(`[data-compare-id="${CSS.escape(id)}"]`).forEach((button) => {
      button.setAttribute("aria-pressed", String(shouldAdd));
      button.textContent = shouldAdd ? "Selected" : (id.startsWith("DES-") ? "Add to compare" : "Compare");
    });
    renderCompareTray();
  };

  $$('[data-compare-id]').forEach((button) => button.addEventListener("click", () => toggleCompare(button.dataset.compareId)));
  $("#clearCompareButton")?.addEventListener("click", () => {
    [...compareSelection].forEach((id) => toggleCompare(id, false));
  });
  $("#openCompareButton")?.addEventListener("click", () => {
    showToast(compareSelection.size > 1 ? `Comparison ready for ${[...compareSelection].join(" and ")}.` : "Select at least two compounds to compare.");
    if (compareSelection.size > 1) scrollToId("best-compounds");
  });

  const openDialog = (id) => {
    const dialog = document.getElementById(id);
    if (!dialog) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    const firstField = $("input, textarea, button", dialog);
    firstField?.focus();
  };
  const closeDialog = (id) => {
    const dialog = document.getElementById(id);
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  };

  $("#importButton")?.addEventListener("click", () => openDialog("importDialog"));
  $("#designButton")?.addEventListener("click", () => scrollToId("designs"));
  $("#saveHypothesisButton")?.addEventListener("click", () => openDialog("hypothesisDialog"));
  $("#newHypothesisButton")?.addEventListener("click", () => openDialog("hypothesisDialog"));
  $("#commandTrigger")?.addEventListener("click", () => openDialog("commandDialog"));
  $$('[data-close-dialog]').forEach((button) => button.addEventListener("click", () => closeDialog(button.dataset.closeDialog)));
  $$('dialog').forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog(dialog.id);
  }));

  const fileInput = $("#fileInput");
  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    $("#fileName").textContent = file.name;
    $("#importPreview").hidden = false;
    $("#previewImportButton").disabled = false;
    showToast(`Preview ready for ${file.name}.`);
  });
  $("#previewImportButton")?.addEventListener("click", () => {
    closeDialog("importDialog");
    showToast("Mapping preview saved. Confirm the inferred fields in the next step.");
  });

  const queryResponses = [
    { test: /substitutions improve potency/i, response: "R2 electron-withdrawing groups are the strongest supported potency gain: Cl and F improve biochemical potency by a median 0.72 pIC50 across four independent pairs. The effect is strongest with aromatic R1; broader context remains untested." },
    { test: /186.*142|142.*186/i, response: "SAR-186 is 1.4 pIC50 units more potent biochemically than SAR-142. The matched change is consistent with replacing R2-H by R2-Cl; SAR-186 also has lower logD and lower clearance. This is an observation, not a mechanistic claim." },
    { test: /not tried|R2/i, response: "The largest R2 gaps are polar and ionizable groups in the R1 = Me context. The R1 = Me / R2 = F and Cl cells are empty, making them useful designs to test context dependence." },
    { test: /changed|last week/i, response: "Two new matched pairs strengthened the R2-Cl hypothesis, three Series B cellular measurements narrowed the low-logD translation gap, and SAR-288 was flagged for a 70-fold replicate disagreement." },
  ];
  const runQuery = (query) => {
    const response = queryResponses.find((item) => item.test.test(query))?.response || "I can only answer from the evidence currently loaded. Try asking about potency substitutions, SAR-186 versus SAR-142, unexplored R2 space, or what changed.";
    $("#queryResponseText").textContent = response;
    $("#queryResponse").hidden = false;
    $("#suggestionList").hidden = true;
  };
  $$('[data-query]').forEach((button) => button.addEventListener("click", () => {
    $("#commandInput").value = button.dataset.query;
    runQuery(button.dataset.query);
  }));
  $("#commandInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runQuery(event.currentTarget.value);
    }
  });

  $("#hypothesisForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const statement = String(form.get("statement") || "").trim();
    const rationale = String(form.get("rationale") || "").trim();
    if (statement.length < 8) {
      showToast("Add a hypothesis with at least eight characters.");
      $("#hypothesisStatement")?.focus();
      return;
    }
    try {
      const response = await fetch("/api/hypotheses", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ statement, rationale }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not save hypothesis.");
      closeDialog("hypothesisDialog");
      event.currentTarget.reset();
      showToast(`${result.hypothesis.id} saved to the hypothesis tracker.`);
    } catch (error) {
      showToast(error.message || "Could not save hypothesis.");
    }
  });

  $("#chartDataToggle")?.addEventListener("click", () => {
    const table = $("#chartDataTable");
    table.hidden = !table.hidden;
    $("#chartDataToggle").setAttribute("aria-label", table.hidden ? "Show chart data" : "Hide chart data");
  });

  const noveltyRange = $("#noveltyRange");
  const noveltyValue = $("#noveltyValue");
  const recommendationCards = $$('[data-novelty]');
  const updateNovelty = () => {
    const value = Number(noveltyRange?.value || 48);
    if (noveltyValue) noveltyValue.textContent = value < 35 ? "Confidence" : value > 68 ? "Exploratory" : "Balanced";
    recommendationCards.forEach((card) => {
      const novelty = Number(card.dataset.novelty || 0);
      card.style.order = String(Math.abs(novelty - value));
    });
  };
  noveltyRange?.addEventListener("input", updateNovelty);
  updateNovelty();

  $$('[data-quality-action]').forEach((button) => button.addEventListener("click", () => showToast(`${button.dataset.qualityAction} workflow opened.`)));
  $$('[data-toast]').forEach((button) => button.addEventListener("click", () => showToast(button.dataset.toast)));
  $$('[data-design-id]').forEach((button) => button.addEventListener("click", () => showToast(`Evidence panel opened for ${button.dataset.designId}.`)));
  $$('[data-compound]').forEach((button) => button.addEventListener("click", () => showToast(`${button.dataset.compound}: evidence-linked compound detail opened.`)));

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openDialog("commandDialog");
    }
    if (event.key === "Escape") {
      $$('dialog[open]').forEach((dialog) => closeDialog(dialog.id));
    }
  });
})();
