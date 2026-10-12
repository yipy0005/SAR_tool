# Project-scoped MCP SAR workflow and provenance hardening

The roadmap implementation expands the MCP boundary across the real import → summary → explicit prodrug mapping → compatible comparison → provenance/export workflow. It adds strict XLSX/base64 transport, staged import pagination, durable comparison snapshots and audit linkage, endpoint comparison, project-safe deep links, analysis readback, side-effect descriptors, allowlist protection, credential serialization, catalog drift checks, and deterministic coverage for `test/CellLinePanel_pIC50s.xlsx`. The recorded verification reports the focused suites, 222 passing full-suite tests, schema 22 with catalog counts 17/22/28, the existing loopback `mcp-check`, and the native browser fallback; it does not claim browser/Kiro UI or live PostgreSQL concurrency execution. **Watch for:** **confirmed, HIGH** stale staged rows can be omitted after the MCP commit review count is checked; **confirmed, MEDIUM** the structured endpoint-comparison tool returns a browser link that cannot restore the requested endpoint state; **confirmed, MEDIUM** section-mode export responses omit the required download/provenance contract; **confirmed, MEDIUM** AC-021 and AC-027 lack the required evidence in code/tests/docs.

**Verdict**: NEEDS_CHANGES

## High-level view

The import and explicit relationship path is project-scoped and bounded: one-of upload and mapping inputs are rejected before HTTP, staged rows retain source locations and pagination, and the real workbook journey uses `Sample_ID` rather than `WuXi_ID_core`. Prodrug mappings resolve only explicit project-local references, preserve `needs_review`, use the quarantined/imported origin split, and do not infer ester/acid relationships from structures or endpoint behavior. The prior review’s false-to-true import attestation defect is fixed.

Durable prodrug comparison is the strongest part of the implementation: relationship, endpoint, and summary reads occur inside the persistence transaction, the versioned snapshot is immutable on readback, and the run/audit linkage is exposed in JSON and CSV. Exact deltas remain limited to compatible exact observations, while qualifiers, missingness, invalid numeric values, units, source measurements, summaries, review state, and evidence boundaries remain visible.

The remaining integration-boundary gaps are **confirmed, HIGH** for commit snapshot safety and **confirmed, MEDIUM** for endpoint-link state, sectioned export metadata, and acceptance evidence. The endpoint-comparison MCP handler uses raw compatibility keys where the browser consumes `summary|<endpoint>|<unit>` IDs; sectioned export responses omit the documented download/provenance fields; commit uses rows read before its transaction; and the roadmap evidence does not show MCP readback coverage for R-group/activity-cliff runs or a checked current total test count in the documentation.

<details>
<summary>Issues (4)</summary>

1. **Staged commit is not source-snapshot safe** — **confirmed, HIGH:** `import_pipeline.py:commit_import()` reads the batch and accepted rows before opening its transaction, then only rechecks status and row count. A concurrent staged-row change can leave newly accepted rows uncommitted while the batch is marked committed; reload and validate the batch and rows inside the same transaction, or reject a changed snapshot with 409 before marking it committed.
2. **Endpoint comparison link loses endpoint state** — **confirmed, MEDIUM:** `sar_mcp/tools_lookup.py:compare_endpoints()` puts compatibility keys such as `IC50:import-v1` into `endpoint_a`/`endpoint_b`, but `static/production_explorer.js` selects only `summary|<compatibility_key>|<unit>` IDs. Convert keys through one shared link builder using returned unit metadata, or return a safe unselected link when a single unit cannot be determined.
3. **Sectioned export omits the documented handoff** — **confirmed, MEDIUM:** `sar_mcp/tools_lookup.py:export_overview()` returns only `section`, `total`, `returned`, and `rows` when `section` is supplied, omitting the safe download link, export/provenance policy, and explicit audit-side-effect handoff required by FR-P0-3. Include those fields in both branches and announce whether more rows remain.
4. **Required acceptance evidence is incomplete** — **confirmed, MEDIUM:** the changed tests exercise direct R-group/activity-cliff API readback but do not exercise `sar_get_analysis_run` for those two new MCP readback routes, and README/MCP guide do not state a checked current total test count. Add MCP readback integration coverage and a generated/checkable test-count marker to satisfy AC-021, AC-024, and AC-027.

</details>

<details>
<summary>Details</summary>

### Staged import commit and transaction boundary

`commit_import()` in `import_pipeline.py` opens a read connection to load the batch and all `import_rows`, sorts them, and constructs `accepted_rows`. Only afterward does it enter `transaction(database_path)`. The transaction rechecks that the batch still exists, is not committed, and—when MCP attestation is present—that the current row count equals `reviewed_row_count`, but it does not reload the accepted rows or compare their IDs/status/normalized payloads with the pre-transaction snapshot.

**Confirmed, HIGH:** a concurrent staged-row insertion or update can make the reviewed count and the preloaded `accepted_rows` diverge. If the caller attests to the new count, the transaction can mark the batch committed while skipping a newly accepted row; if a row’s status or normalized data changes, the commit can write stale content. AC-010 and the design’s transactional validation require the commit decision and source rows to share one validated boundary. Load the project-bound batch and rows inside the write transaction, compare a stable batch/row snapshot, and return 409 with no status change on conflict.

### Endpoint comparison browser handoff

The endpoint-comparison API returns compatibility keys, while the browser’s `production_explorer.js` builds endpoint IDs from the kind, compatibility key, and unit and checks URL state against those IDs. The MCP `compare_endpoints()` handler passes the raw `endpoint_a` and `endpoint_b` values directly to `Links.page()` rather than translating them to the browser’s canonical IDs. A link returned by the structured comparison tool can therefore open Compare with the project page but silently fall back to the saved/default endpoint instead of the endpoint just compared.

**Confirmed, MEDIUM:** this violates the browser-link portion of AC-015 and the endpoint-state portion of AC-018. The existing `sar_open_in_workbench` path already performs a summary-key/unit lookup; the structured comparison tool should reuse that contract and preserve both requested endpoints, or explicitly omit endpoint state when mixed units make a unique browser selection impossible.

### Export overview section contract

The no-section branch of `export_overview()` returns `download_link`, `export_id`, `provenance_policy`, section counts, and a note that fetching the export writes an audit record. The section branch returns only a bounded slice and counts. It has no download link or provenance policy, no explicit export/audit handoff, and no `has_more`/continuation field even when `returned < total`.

**Confirmed, MEDIUM:** callers asking for a specific section cannot receive the complete documented export handoff from that response, and a bounded section result does not state how to retrieve later rows. AC-013/AC-014 require provenance-preserving export behavior and FR-P0-3 requires safe links and provenance policy on this MCP surface. Return the same safe export metadata in both branches and add explicit continuation or a clear bounded-section contract.

### Coverage and documentation evidence

The implementation includes project-bound API handlers for R-group and activity-cliff readback, and `sar_get_analysis_run()` maps those analysis names to the new routes. The changed production API tests validate the routes directly, but the recorded MCP integration tests read back a pharmacophore run and information-gap run, not the new R-group and activity-cliff MCP paths. Separately, the catalog drift test pins schema, tool counts, and descriptor fingerprint, but the README and MCP guide do not contain a current total test count or a checked count marker.

**Confirmed, MEDIUM:** the implementation evidence does not prove AC-021’s MCP readback contract for both newly exposed analysis types, and the documentation does not meet AC-027’s current-test-count requirement. Add the two MCP readback calls with foreign/disallowed scope assertions, then derive or check the documented total from collection so it cannot drift silently.

The verification log records that browser/Kiro UI interactions and live PostgreSQL concurrency were not executed, while the native Flask/static fallback and deterministic transaction-mode checks were run. That limitation is documented rather than presented as browser verification; it does not replace the four fixes above.

</details>

<details>
<summary>Files changed</summary>

The working tree updates the Flask application, import/prodrug/comparison/export/analysis engines, MCP client/configuration/schema/server/tool/link modules, production SAR/Compare/evidence UI, documentation, and roadmap tests. New implementation and coverage files include `endpoint_comparison_engine.py`, `prodrug_compare_persistence.py`, `sar_mcp/catalog.py`, endpoint/catalog/native-smoke tests, and the real-workbook fixture path. See the complete working-tree diff from `git diff` together with the untracked files listed by `git status --short`.

</details>
