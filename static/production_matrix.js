// SAR table interactions: column sorting, compound search, structure size, and
// display-only shading. Nothing here changes stored values.
(() => {
  "use strict";

  const table = document.querySelector("[data-sar-matrix]");
  if (!table) return;
  const panel = table.closest(".sar-matrix-panel");
  const wrap = panel?.querySelector(".sar-matrix-wrap");
  const tbody = table.tBodies[0];
  const rows = () => [...tbody.rows];
  const projectId = document.querySelector("[data-production-project]")?.dataset.productionProject || "";
  const storageKey = `sar-workbench-matrix-directions:${projectId}`;
  const shadeToggle = panel?.querySelector("[data-matrix-shade]");

  const loadDirections = () => {
    try { return JSON.parse(window.localStorage.getItem(storageKey) || "{}"); } catch (_error) { return {}; }
  };
  const saveDirections = (directions) => {
    try { window.localStorage.setItem(storageKey, JSON.stringify(directions)); } catch (_error) { /* optional */ }
  };

  const cellFor = (row, index) => row.cells[index + 2];

  // Shade each column from worse (0) to better (1) among exact values only.
  const shadeColumn = (index, direction) => {
    const cells = rows().map((row) => cellFor(row, index)).filter(Boolean);
    const values = cells.map((cell) => Number(cell.dataset.value)).filter(Number.isFinite);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const enabled = shadeToggle ? shadeToggle.checked : true;
    cells.forEach((cell) => {
      const value = Number(cell.dataset.value);
      cell.classList.remove("is-shaded", "is-shaded-low", "is-shaded-high");
      cell.style.removeProperty("--u");
      if (!enabled || direction === "none" || !Number.isFinite(value) || values.length < 2 || max === min) return;
      let t = (value - min) / (max - min);
      if (direction === "lower") t = 1 - t;
      // Three-stop scale (worse → neutral → better) avoids muddy red/green blends mid-range.
      const high = t >= 0.5;
      cell.style.setProperty("--u", (high ? (t - 0.5) * 2 : t * 2).toFixed(3));
      cell.classList.add("is-shaded", high ? "is-shaded-high" : "is-shaded-low");
    });
  };

  const directions = loadDirections();
  const directionSelects = [...table.querySelectorAll("[data-matrix-direction]")];
  const applyShading = () => {
    directionSelects.forEach((select) => shadeColumn(Number(select.dataset.matrixDirection), select.value));
  };
  directionSelects.forEach((select) => {
    const index = select.dataset.matrixDirection;
    const heading = select.closest("th");
    if (directions[heading?.title] && [...select.options].some((option) => option.value === directions[heading.title])) {
      select.value = directions[heading.title];
    }
    select.addEventListener("change", () => {
      if (heading?.title) {
        directions[heading.title] = select.value;
        saveDirections(directions);
      }
      shadeColumn(Number(index), select.value);
    });
  });
  shadeToggle?.addEventListener("change", applyShading);
  applyShading();

  // Sorting: exact values first (by direction), then thresholds by bound, then untested/missing.
  const stateRank = { exact: 0, censored: 1, missing: 2, not_tested: 3 };
  let sortState = { key: "id", ascending: true };
  const sortBy = (key, ascending) => {
    sortState = { key, ascending };
    const factor = ascending ? 1 : -1;
    const sorted = rows().sort((left, right) => {
      if (key === "id") {
        return factor * left.dataset.registrationId.localeCompare(right.dataset.registrationId, undefined, { numeric: true });
      }
      const index = Number(key);
      const a = cellFor(left, index);
      const b = cellFor(right, index);
      const rankA = stateRank[a?.dataset.state] ?? 3;
      const rankB = stateRank[b?.dataset.state] ?? 3;
      if (rankA !== rankB) return rankA - rankB;
      const valueA = Number(a?.dataset.sort);
      const valueB = Number(b?.dataset.sort);
      if (!Number.isFinite(valueA) || !Number.isFinite(valueB)) return 0;
      return factor * (valueA - valueB);
    });
    sorted.forEach((row) => tbody.appendChild(row));
    table.querySelectorAll("thead th").forEach((th) => th.removeAttribute("aria-sort"));
    const button = table.querySelector(`[data-matrix-sort="${key}"]`);
    button?.closest("th")?.setAttribute("aria-sort", ascending ? "ascending" : "descending");
  };
  table.querySelectorAll("[data-matrix-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.matrixSort;
      if (sortState.key === key) {
        sortBy(key, !sortState.ascending);
      } else if (key === "id") {
        sortBy(key, true);
      } else {
        // First click on an endpoint puts the best compounds on top.
        const direction = table.querySelector(`[data-matrix-direction="${key}"]`)?.value;
        sortBy(key, direction === "lower");
      }
    });
  });

  panel?.querySelector("[data-matrix-search]")?.addEventListener("input", (event) => {
    const query = String(event.target.value || "").trim().toLowerCase();
    rows().forEach((row) => {
      row.hidden = Boolean(query) && !row.dataset.registrationId.toLowerCase().includes(query);
    });
  });

  panel?.querySelector("[data-matrix-size]")?.addEventListener("change", (event) => {
    if (wrap) wrap.dataset.size = event.target.value;
  });
})();
