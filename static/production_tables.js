// Client-side filtering for long evidence tables (compound ID text + assay select).
(() => {
  "use strict";
  document.querySelectorAll("[data-table-filter]").forEach((filter) => {
    const table = document.getElementById(filter.dataset.tableFilter);
    if (!table) return;
    const text = filter.querySelector("[data-filter-text]");
    const assay = filter.querySelector("[data-filter-assay]");
    const count = filter.querySelector("[data-filter-count]");
    const rows = [...table.tBodies[0].rows].filter((row) => row.dataset.registrationId !== undefined);
    const apply = () => {
      const query = String(text?.value || "").trim().toLowerCase();
      const assayValue = assay?.value || "";
      let shown = 0;
      rows.forEach((row) => {
        const matches = (!query || row.dataset.registrationId.toLowerCase().includes(query))
          && (!assayValue || row.dataset.assay === assayValue);
        row.hidden = !matches;
        if (matches) shown += 1;
      });
      if (count) count.textContent = query || assayValue ? `${shown} of ${rows.length} rows` : `${rows.length} rows`;
    };
    text?.addEventListener("input", apply);
    assay?.addEventListener("change", apply);
    apply();
  });
})();
