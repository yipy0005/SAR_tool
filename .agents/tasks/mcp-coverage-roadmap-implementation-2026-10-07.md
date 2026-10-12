# SAR Workbench MCP Coverage Roadmap Implementation Report

## Summary of what was implemented across P0/P1/P2

The roadmap implementation completed batches 1–8 in the working tree. The P0 workflow now covers bounded, project-scoped import profiling and staged review for CSV/TSV/XLSX, explicit prodrug/active mapping, durable prodrug comparison with audit and provenance linkage, safe pagination/truncation, numeric and qualifier handling, and JSON/CSV provenance parity. Upload limits, strict base64 and credential serialization, project/role/CSRF enforcement, allowlist behavior, response shaping, and catalog drift checks were hardened.

P1 coverage adds structured endpoint comparison with browser-equivalent coverage and missingness rules, project-safe deep links and SAR/Compare state compatibility, bounded R-group and activity-cliff analysis readback, export side-effect metadata, fail-closed allowlisted project creation, and broader analysis/import/prodrug/prediction/role/governance integration coverage. Browser-only human-governance actions remain unavailable through MCP by design.

P2 work updates the README and MCP guide, pins catalog/schema drift expectations, adds the native route/static fallback for the browser workflow, hardens credential-file round trips and permissions, and adds local Kiro/MCP configuration, mode/reconnect, proxy-link, and safety checks. The recorded implementation status says all roadmap batches are implemented; browser/Kiro UI E2E remains partial/unverified, and live PostgreSQL concurrency remains unexecuted.

## Changed files

The current working tree contains the following implementation and test changes, grouped by area. This inventory is from the read-only `git status --short` and `git diff --stat` review.

### Application, domain, persistence, and import/export
- `app.py`
- `config.py`
- `database.py`
- `export_engine.py`
- `import_formats.py`
- `import_pipeline.py`
- `prodrug_engine.py`
- `prodrug_compare_persistence.py` (new)
- `endpoint_comparison_engine.py` (new)
- `rgroup_engine.py`
- `scientific.py`

### MCP transport, configuration, catalog, tools, and links
- `sar_mcp/cli.py`
- `sar_mcp/client.py`
- `sar_mcp/config.py`
- `sar_mcp/links.py`
- `sar_mcp/schema.py`
- `sar_mcp/server.py`
- `sar_mcp/toolkit.py`
- `sar_mcp/catalog.py` (new)
- `sar_mcp/tools_chem.py`
- `sar_mcp/tools_lookup.py`
- `sar_mcp/tools_run.py`

### Browser/static/template compatibility
- `static/production_explorer.js`
- `static/production_sar.js`
- `templates/production_evidence.html`
- `templates/production_sar.html`

### Documentation
- `README.md`
- `docs/MCP_GUIDE.md`

### Tests and native smoke coverage
- `tests/test_database_backend.py`
- `tests/test_deep_links.py`
- `tests/test_endpoint_comparison.py` (new)
- `tests/test_export_api.py`
- `tests/test_import_cleaning.py`
- `tests/test_mcp_catalog.py` (new)
- `tests/test_mcp_integration.py`
- `tests/test_mcp_native_smoke.py` (new)
- `tests/test_mcp_unit.py`
- `tests/test_production_analysis_api.py`
- `tests/test_production_foundation.py`
- `tests/test_prodrug_relationships.py`

The task and review records under `.agents/tasks/` are also present in the untracked `.agents/` tree shown by git status.

## Final MCP catalog counts and live app schema

The plan’s final Verification log records the derived catalog as **17 read-only tools, 22 analyze tools, and 28 full tools**, with live/application **schema version 22**. The final recorded catalog fingerprint is `109a86ce9b7ab797d24c45f0d6c7b5b2f37f91e5ca016f588ea82e94a614174b`.

The same log records that the existing loopback app was already ready and was checked without restart or overwrite: `pixi run mcp-check` passed in analyze mode, required sign-in, reported schema 22, and reported 3 visible projects. These counts and schema are taken from the plan’s Verification log, including its final catalog checks; `mcp-check` was not rerun for this report.

## Tests and results

Recorded focused validation included the following final results:

- `tests/test_mcp_unit.py tests/test_prodrug_relationships.py`: **38 passed**.
- `tests/test_mcp_unit.py tests/test_prodrug_relationships.py tests/test_import_cleaning.py tests/test_mcp_integration.py tests/test_mcp_native_smoke.py`: **58 passed** after two initially failing assertions/tests were corrected.
- `tests/test_endpoint_comparison.py tests/test_prodrug_relationships.py tests/test_export_api.py`: **12 passed**.
- `tests/test_endpoint_comparison.py`: **3 passed** after one initial regression expectation was aligned with the browser-equivalent missing predicate.
- `tests/test_mcp_catalog.py`: **3 passed**; schema/count derivation matched the expected catalog values.
- The latest recorded import/deep-link/unit/integration focused run, covering `tests/test_import_cleaning.py`, `tests/test_mcp_unit.py`, `tests/test_mcp_integration.py`, and `tests/test_deep_links.py`, finished with **55 passed** after correcting one test’s MCP envelope parsing assertion.
- Other recorded batch/review-focused selectors passed, including **28 passed**, **62 passed**, **64 passed**, **73 passed**, and **47 passed** runs across the affected roadmap suites.

The authoritative full-suite result in the final Verification log is **`pixi run test` — 222 passed**. Earlier iterations had transient failures while assertions and implementation details were being corrected; the recorded final reruns passed. The log also records `git diff --check` passing throughout the final validation.

## Roadmap items

### COMPLETED
- **Batches 1–8 / AC-001–AC-032:** The plan’s final implementation status records all roadmap batches implemented, with the relevant acceptance coverage represented by unit, integration, catalog, native-smoke, import, prodrug, endpoint, export, dashboard, deep-link, and backend tests.
- **P0 explicit prodrug workflow / AC-001–AC-006:** Structured and text mappings, validation, confirmation, review state, no-inference behavior, durable comparison, numeric safety, pagination, and audit/provenance linkage were implemented and tested.
- **P0 staged import/XLSX workflow / AC-007–AC-012:** Profile, preview, bounded staged retrieval, source-cell/formula policy, mapping overrides, project binding, exact confirmation and complete-review attestation, safe limits, and transactional commit validation were implemented and tested, including the real workbook handler journey.
- **P0 export/comparison provenance / AC-013–AC-014:** Relationship records and durable comparison provenance are represented in JSON and CSV with project isolation and audit linkage.
- **P1 endpoint comparison / AC-015–AC-017:** Browser-equivalent endpoint selection, canonical-unit/missingness handling, finite numeric classification, stable pagination, and bounded read-only output were implemented.
- **P1 deep links / AC-018–AC-020:** Safe staged-import, SAR, Compare, evidence, project, and proxy-aware links with project validation and legacy/current selected-key compatibility were implemented.
- **P1 analysis readback, export metadata, allowlist, and integration/governance coverage / AC-021–AC-026:** R-group/activity-cliff readback, side-effect-aware export descriptors, fail-closed project creation, broad MCP integration paths, role failures, and browser-only governance boundaries were covered.
- **P2 documentation, drift, credentials, configuration, and mode/proxy checks / AC-027–AC-028 and AC-030–AC-032:** Documentation and deterministic checks were updated for schema/counts, catalog drift, credentials, generated configuration, auto-approval safety, mode/reconnect behavior, and public proxy links.

### PARTIAL
- **Browser E2E/fallback / AC-029:** The repository-native Flask/static/native smoke fallback is implemented and recorded as passing, but actual browser/Kiro UI interactions were not executed. Therefore the browser portion is partial/unverified rather than complete.
- **PostgreSQL concurrency validation:** Deterministic PostgreSQL transaction-mode coverage is present, but no live PostgreSQL server was available, so live concurrent-database behavior was not executed. This is a validation limitation, not an unimplemented roadmap batch.

### UNIMPLEMENTED
- **No roadmap implementation batch is recorded as unimplemented.** The plan’s final status states batches 1–8 are implemented. Browser-only approval/rejection, curated design/claim/series-membership writes, and project membership/role changes remain intentionally unavailable through MCP under the requirements, rather than being missing roadmap work.

## Remaining limitations and explicitly unverified areas

- No browser or Kiro UI workflow was executed. In particular, no human UI verification is claimed for project selection, import review, summaries, Compare, SAR, explicit mapping/comparison, or export. Native route/static smoke coverage is the documented fallback.
- No live PostgreSQL concurrency test was executed; the recorded evidence is deterministic transaction-mode testing.
- The existing loopback server was only readied/checked by the recorded validation. This report does not restart, overwrite, or re-run it.
- Browser-only human-governance actions remain outside MCP: recommendation approval/rejection, hypothesis/design approval, curated design/claim/series-membership writes, and project membership/role changes.
- The implementation preserves scientific-integrity boundaries: explicit prodrug mappings are not inferred from structures or names, comparison deltas are not causal evidence, and missing/censored/non-exact/unit-incompatible/invalid-numeric observations are not treated as exact measurements.

## User files, git, and delivery state

The unrelated user fixture `test/CellLinePanel_pIC50s.xlsx` was preserved; its recorded SHA-256 remained `21e55dc33b2f625d68b6fc62285bd8263a560e831aacd177f8fd9dc094ff8b8d`. The pre-existing generated CSVs `test/prodrug_long_measurements.csv` and `test/prodrug_pairs.csv` were also preserved and remained untouched. The recorded verification states that no commit or push was made. This report likewise makes no code changes, staging, commit, push, server restart, or server overwrite.
