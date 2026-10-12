# MCP roadmap technical design review

**Design reviewed:** `.agents/tasks/mcp-roadmap-design.md`  
**Requirements:** `.agents/tasks/mcp-roadmap-requirements.md`  
**Baseline audit:** `.agents/tasks/mcp-coverage-audit-2026-10-07.md`  
**Repository source checked:** SAR Workbench application, import/prodrug/export engines, MCP modules, database migrations, and browser JavaScript.

## Findings

1. **HIGH — The proposed base64 request limit is incompatible with the existing Flask limit unless a second limit is introduced.**
   **Location:** Design `Shared contracts → XLSX/base64 transport`, especially the `6,732,204`-byte JSON-body limit and “Flask enforces the serialized request-size limit.”

   The repository’s `config.py` has one `SAR_MAX_UPLOAD_BYTES` setting, defaulting to 5,000,000. `app.py` installs that value as Flask `MAX_CONTENT_LENGTH` and also passes it to `profile_upload()` and `create_preview()` as the decoded upload limit. A valid 5,000,000-byte XLSX encoded in JSON is approximately 6,732,204 bytes, so the current global Flask limit rejects the design’s largest valid MCP upload before JSON decoding. Raising the one setting to 6,732,204 instead permits multipart/browser bodies above the intended decoded 5,000,000-byte limit unless every multipart path has a separate post-read check.

   **Concrete fix:** Define separate settings, for example `SAR_MAX_UPLOAD_BYTES` (decoded file bytes) and `SAR_MAX_JSON_REQUEST_BYTES = BASE64_BYTES(SAR_MAX_UPLOAD_BYTES) + JSON_ENVELOPE_BYTES`. Set Flask’s global request limit to the larger wire limit, enforce the decoded limit on multipart content and after base64 decoding, and enforce the exact serialized JSON limit in `SarClient` before sending. Specify which 413 error code each boundary returns. Add tests that send exactly 5,000,000 decoded bytes through JSON and multipart, reject 5,000,001 decoded bytes, and reject a body one byte above the wire limit without creating a batch.

2. **HIGH — Durable comparison provenance is not transactionally anchored to the source snapshot.**
   **Location:** Design `P0-1 → sar_compare_prodrug_pairs`, which requires an immutable snapshot and says the analysis/audit IDs are generated in one transaction, but does not require the relationship, summary, and endpoint reads to occur in that same transaction.

   The current `prodrug_engine.compare_relationships()` reads relationships and latest summaries through separate read connections and returns an ephemeral result. The design can insert an `analysis_runs` row and audit event atomically while still having read the source rows before that transaction. A concurrent summary refresh or relationship update can therefore make the stored snapshot’s claimed “latest” source state and its persisted project state inconsistent. This is a scientific-provenance gap, not merely an implementation detail.

   **Concrete fix:** Define a single transaction/snapshot boundary: validate project and selected relationships, read the relationship rows and endpoint summaries, construct the complete snapshot, insert `analysis_runs`, and insert its audit event before commit. State the SQLite/PostgreSQL isolation/locking behavior and what happens on a serialization conflict (return 409, with no run). Add an integration test that attempts a source update during comparison or otherwise verifies that the stored snapshot contains one consistent source version and never changes on later refreshes.

3. **HIGH — The design promises audit IDs in export without defining where the export obtains or emits the comparison audit ID.**
   **Location:** Design `P0-1` readback/export paragraphs and `P0-3 — Export and comparison provenance integrity`; also `File-level implementation map` claim that no migration is required.

   The existing schema has `analysis_runs.input_selection_json` and a separate `audit_events` table, but no `analysis_runs.audit_event_id` column. The current export implementation selects `analysis_runs` with `SELECT *`; JSON export includes the raw analysis row, while CSV only serializes fields through generic `metadata_json`. The design says the durable comparison’s “audit IDs appear in the existing `analysis_runs` export section” but does not define a field, join, or JSON/CSV representation. An implementer could satisfy the POST/GET tests while silently losing the audit linkage in exports, violating AC-014.

   **Concrete fix:** Choose and specify one contract. For example, store `audit_event_id` in the versioned comparison snapshot envelope and expose it as a top-level `audit_event_id` in the comparison analysis export record; alternatively define an export-time project-scoped join on `(resource_type='analysis_run', resource_id=run_id, operation='prodrug_comparison')` and emit the resulting field. Define the CSV metadata key and JSON key explicitly, require JSON/CSV parity, and test that a foreign project cannot obtain the linked audit row.

4. **MEDIUM — The data-origin label for an unsaved prodrug preview contradicts the shared provenance contract.**
   **Location:** Design `Shared contracts → Input validation, errors, and invariants` versus `P0-1 → sar_preview_prodrug_relationships`.

   The shared contract says uncommitted/staged material is `quarantined_import`, while committed explicit mappings are `imported`. The preview response is explicitly non-persisting (`saved=false`, `nothing_saved=true`) but is assigned `data_origin="imported"`. That makes a user-supplied candidate mapping look like a committed project record and conflicts with the requirements’ distinction between raw/quarantined, imported, derived, and curated data.

   **Concrete fix:** Use `data_origin="quarantined_import"` for preview-only pair rows, or define a separate `explicit_mapping_preview` origin and document it across all tools. Reserve `imported` for saved relationships, list results, comparison snapshots’ relationship inputs, and exports. Add an assertion covering preview, save, compare, readback, and export origins.

5. **MEDIUM — Prodrug comparison pagination and selection semantics are incomplete.**
   **Location:** Design `sar_compare_prodrug_pairs` schema and response description.

   `relationship_ids` is optional and may be an empty array, but the design does not say whether omitted means “all relationships,” an empty array means “none,” or both mean “all.” It also does not reject duplicate IDs, define whether output preserves request order, or define a stable order for the all-relationships case. The shared pagination section names stable-order rules for prodrug preview, imports, endpoint comparison, and analysis pages, but not comparison pairs. Consequently POST page 1, POST page 2, and GET readback can disagree or duplicate/omit pairs.

   **Concrete fix:** Specify: omitted `relationship_ids` means all eligible non-rejected relationships; a supplied empty array is rejected with 422; IDs must be unique and project-scoped; and all results are ordered by `(prodrug_registration_id, active_registration_id, relationship_id)` (or another named key). Return `stable_order` and apply that order before snapshotting and before slicing. Add tests for omitted, empty, duplicate, foreign, page-boundary, and later-readback cases.

6. **MEDIUM — Endpoint-comparison cross-field validation and browser parity are underspecified.**
   **Location:** Design `P1-4 → sar_compare_endpoints` schema and engine rules.

   `endpoint_b` is optional, while `coverage` includes `both`; the design does not define the result or error when `coverage="both"` has no B endpoint, whether the same endpoint may be selected for A and B, or how an explicit “no B” value is represented. The existing `production_explorer.js` uses the sentinel `"none"`, filters `q` case-insensitively against `registration_id` only, and treats `endpoint-a`, `both`, and `review` with specific missing/review predicates. “Match `production_explorer.js`” is not a sufficient API contract for these cross-field cases.

   **Concrete fix:** Define the schema and rules explicitly: either make `endpoint_b` nullable/allow the documented `"none"`, require B for `both`, define `all`/`endpoint-a`/`review` when B is absent, and reject identical A/B keys (or document why they are allowed). Specify exact `q` normalization and matching fields. Return 422 for invalid combinations and add fixture tests comparing every coverage/search combination with the browser’s selected rows.

7. **MEDIUM — The proposed deep-link query names and consumers do not match the current browser state contract.**
   **Location:** Design `P1-5 → Deep links and browser state`, especially the table’s `selected_compound_id` key and the `sar_mode`/`relationship_id`/`comparison_run_id` state.

   The checked-in `static/production_explorer.js` reads and writes `selected`, not `selected_compound_id`. The checked-in `static/production_sar.js` does not initialize its radio mode or endpoint from URL query parameters at all. Therefore a link generated exactly from the design can omit the selected compound or open SAR in its default mode, and the stated precedence for relationship/run IDs has no defined browser implementation. This also risks breaking existing links, which the requirements require preserving.

   **Concrete fix:** Either retain the existing keys (`selected`, etc.) or define a compatibility migration: the JavaScript must read both old and new names, serialize one canonical name, and validate every referenced ID against the requested project before selecting it. Specify the SAR initialization order for `sar_mode`, endpoint, relationship, and comparison run, plus the safe empty/not-found marker. Add tests for old links, new links, foreign IDs, and the exact rendered mode/selection.

8. **MEDIUM — “Existing CSV/TSV parser” does not define the prodrug text mapping contract needed for reproducible review.**
   **Location:** Design `sar_preview_prodrug_relationships` input description.

   The actual `prodrug_engine._pair_rows_from_text()` chooses tab only when the first line contains a tab and no comma; otherwise it chooses comma. It detects a header only from the first two normalized fields, accepts arbitrary extra fields but keeps only the third as notes, skips blank lines, and uses case-sensitive token resolution after trimming. The design calls this an existing CSV/TSV parser but does not state delimiter selection, accepted header aliases, extra-column behavior, malformed-row behavior, source-line numbering, or token case/Unicode normalization. Different implementations can therefore produce different mappings and provenance while passing the high-level tests.

   **Concrete fix:** Specify a versioned parser contract: accepted delimiters, quoting/UTF-8 rules, exact header aliases, whether a notes column is required/optional, whether extra columns are rejected or joined, blank-line behavior, 1-based source line behavior, and exact/case-sensitive token normalization. Keep the same parser for preview and save and add tests for quoted commas/tabs, headers, blank/malformed rows, extra columns, duplicate tokens, case variants, and line-number preservation.

9. **MEDIUM — Exact-delta handling does not define invalid or non-finite numeric values.**
   **Location:** Design `Shared contracts → Exact subtraction` and `P0-1 → _exact_summary()`; also the endpoint-comparison rules.

   The design requires a non-null numeric value but does not define conversion and finiteness. The current `_exact_summary()` only checks non-null/state/qualifier; current comparison then calls `float(...)` directly. A malformed database value can raise `ValueError`/`OverflowError` or permit `NaN`/infinite output instead of a safe non-comparable state. Endpoint comparison has the same unspecified boundary.

   **Concrete fix:** Define numeric validity as successful conversion to a finite real number (`math.isfinite`), with no implicit string/unit conversion. Invalid values must remain visible with `status="invalid_numeric"` and `delta=null` (or produce a documented 422 if found during request validation), never become a 500 or a complete delta. Add tests for nonnumeric strings, `NaN`, positive/negative infinity, zero, and numeric strings in both comparison engines and exports.

10. **MEDIUM — The durable `persist=true` comparison behavior is ambiguous for the existing browser route.**
    **Location:** Design `sar_compare_prodrug_pairs`, which says the MCP handler always sends `persist=true`, while the existing browser route is currently a non-persisting POST used by `production_sar.js`.

    The design does not state the default when the browser omits `persist`, nor whether the browser JavaScript must be changed. If the app changes the route default to durable, every existing browser comparison can create an analysis run and audit event; if it leaves the route ephemeral without defining the MCP-only branch, the MCP acceptance test cannot prove durability. This is both a compatibility and scope decision.

    **Concrete fix:** Make the API contract explicit: `persist` is a boolean defaulting to `false` for the legacy browser call, and the MCP handler sends `persist=true`; or update the browser to send `persist=false` and reject omitted values. Define role requirements and response fields for both paths, label the non-persisting path `persistence="ephemeral"`, and test that browser comparison creates no run while MCP comparison creates exactly one run/audit pair.

11. **MEDIUM — The roadmap does not require the named real XLSX workflow as a verification fixture.**
    **Location:** Design P0-2 tests and P2-12 native smoke, which refer only to “the existing XLSX fixture” and generic CSV/TSV/XLSX coverage.

    The user’s authoritative workflow depends on `test/CellLinePanel_pIC50s.xlsx`, but the design never names it or requires its actual sheet/header/data-row/mapping behavior to be exercised. A synthetic workbook could satisfy AC-007/AC-008 while the real workbook is rejected, selects the wrong sheet, loses source-cell locations, or cannot support the explicit ester/acid mappings. That would leave the primary workflow unverified.

    **Concrete fix:** Add a deterministic fixture-specific acceptance test using `test/CellLinePanel_pIC50s.xlsx`: profile it, assert the available sheet metadata, select its actual sheet/header/data start rows and mapping overrides, preview every staged page with source locations/formula policy, commit in a test project, build summaries, and run the explicit prodrug preview/save/compare path using explicit IDs from that fixture. Keep the test data classified as measurements and assert no structure-inferred relationship is created.

## Verified Assumptions

- `app.py` currently exposes multipart/text import profile and preview routes, explicit relationship preview/save/compare routes, paged-import-unaware `GET /api/v1/imports/<import_id>`, and JSON/CSV project export. The current import GET queries only by `import_id` before applying `_import_access`, so the design’s project-bound query is a real required change rather than an existing helper behavior.
- `import_formats.py` is a dependency-free XLSX reader that rejects `.xls`, `.xlsm`, macros, external workbook links, unsafe ZIP paths/ratios/sizes, malformed XML, and invalid UTF-8 CSV/TSV. It does not evaluate formulas. The design’s safety boundary is feasible, but its MCP transport still needs the separate wire/decoded limits in Finding 1.
- `prodrug_engine.preview_relationships()` resolves only project-local compound ID, registration ID, or preferred-name tokens and does not inspect SMILES or infer ester/acid relationships. It reports unknown, ambiguous, duplicate, and self-pairs. The existing save path sets `needs_review` and audits the bulk save, but currently updates an existing duplicate rather than returning the design’s proposed 409.
- The current `_exact_summary()` has a fallback that treats a missing qualifier as `"="`; `_summary_view()` preserves a missing qualifier as null. The design’s explicit-qualifier rule is not already implemented and must be tested through compare, snapshot, readback, and export.
- The existing `analysis_runs` table has `input_selection_json`, `algorithm_version`, status, and project scope; `audit_events` separately has an ID, project/resource/request fields, and metadata. `compound_relationships.activation_context_json` already exists, so the no-migration claim is plausible for those payloads but does not itself provide an analysis-to-audit foreign key.
- `export_engine.build_project_export()` already includes project-filtered `compound_relationships` in JSON and marks them `imported`; `export_project_csv()` currently has no relationship loop, so JSON/CSV relationship parity is not present in the baseline.
- The current MCP server hard-cuts serialized success text with `text[:budget]` after shaping, and the current `Tool` descriptor has no `side_effect` or `_meta` field. The design’s catalog/side-effect and structured-overflow work is therefore new implementation, not an existing guarantee.
- The current browser Compare implementation reads `endpoint_a`, `endpoint_b`, `stage`, `coverage`, `q`, and `selected` from the URL. The current SAR script uses form state and does not read the proposed prodrug query state. This source fact underlies Finding 7.
- The baseline catalog and mode counts are 16 read-only, 19 analyze, and 22 full tools. Adding one read-tier tool and two analyze-tier tools plus three full-only tools gives the proposed 17/22/28 visibility counts, assuming no existing tool changes tier.

## Unverified/Wrong Assumptions

- The design’s “Responses to the design review findings” are assertions about future implementation, not evidence in the current repository. In particular, staged-import project binding, comparison audit identity, strict missing-qualifier exactness, continuation, tied-row ordering, strict upload transport, deep-link state, analysis enum mapping, and structured overflow are not verified by the baseline source or baseline tests.
- The assumption that a comparison audit ID will naturally appear in the existing export is wrong as written: the current export has no defined analysis-to-audit field or join in its contract.
- The assumption that the existing one-value Flask request limit can enforce both a 5,000,000-byte decoded upload and a 6,732,204-byte base64 JSON body is wrong without separate wire and decoded enforcement.
- The assumption that the existing browser state will consume `selected_compound_id`, `sar_mode`, `relationship_id`, or `comparison_run_id` is unverified and currently false for the checked-in Compare/SAR scripts.
- The assumption that current prodrug comparison already has durable analysis-run semantics is wrong: `compare_relationships()` currently returns a derived response and the route returns it with status 201, but no `analysis_runs` or comparison audit row is created.
- The assumption that current export CSV satisfies the documented relationship contract is wrong: source inspection shows it omits `compound_relationships` even though JSON includes them.
- The credential-hardening requirement is still necessary: current `sar_mcp/cli.py` writes quoted email/password values without escaping embedded quotes or backslashes. The design identifies the required fix, but no implementation or round-trip evidence exists yet.
- No browser E2E or real Kiro reconnect/session was verified by this review. The native fallback must remain explicit about layout, browser-local Compare sessions, and Kiro UI behavior it cannot prove.

