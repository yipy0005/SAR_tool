# SAR Workbench MCP coverage roadmap — technical design

**Repository:** `/Users/yipy/Documents/GitHub/SAR_tool`  
**Status:** Revised after design review; design only, with no production implementation.  
**Locked stack:** Pixi Python 3.12.14, Flask 3.1.1, RDKit 2026.03.3, SQLite/PostgreSQL application backends, pytest 8.4.1, and the repository’s stdlib-only MCP stdio/HTTP client facilities.  
**Baseline:** application schema 22; modes `read-only`, `analyze`, `full`; catalog 16/19/22 tools; baseline test counts 200 total, 33 MCP-focused, and 20 specialized prodrug/API/dashboard/deep-link tests.

## Overview

This roadmap closes the highest-value gaps in the Kiro workflow: project selection, real CSV/TSV/XLSX import review, summaries, explicit ester/prodrug-to-acid mapping, compatible endpoint comparison, provenance-preserving export, and precise browser handoff. It extends the existing MCP boundary rather than exposing arbitrary Flask routes. `app.py` remains the authentication, CSRF, project membership/role, request-size, transaction, and browser-state boundary. `SarClient` remains the only MCP HTTP client. `sar_mcp/tools*.py` and `toolkit.py` remain registration and mode boundaries; `schema.py`, `shaping.py`, and `links.py` remain model-facing validation, output, and URL boundaries. `import_pipeline.py`/`import_formats.py`, `prodrug_engine.py`, the existing analysis engines, `rgroup_engine.py`, and `export_engine.py` remain domain/provenance sources of truth.

The three-mode model is retained exactly. The catalog target after implementation is 17 read-tier tools, 22 tools visible in `analyze`, and 28 visible in `full`; a catalog fingerprint and deterministic test, rather than prose alone, define the expected result. `analyze` is a capability/mode boundary, not a guarantee that every operation persists: it may expose an editor-gated, non-mutating preview or a durable derived operation. `side_effect` and application role checks define mutation. Human governance writes remain browser-only. No relationship is inferred from SMILES, names, similarity, endpoint behavior, or a model, and no comparison delta is activation or causal evidence.

## Shared contracts

### Tool registration, modes, descriptors, and approval

Extend the frozen `Tool` record in `sar_mcp/toolkit.py` with `side_effect`, an enum of `none`, `quarantine`, `audit`, `derived`, or `project_data`. Preserve `TIERS = {"read": ("read-only", "analyze", "full"), "analyze": ("analyze", "full"), "write": ("full",)}`. Every descriptor contains the standard annotations and this SAR extension:

```json
{
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "_meta": {"sar.workbench": {"side_effect": "none"}}
}
```

`readOnlyHint` is true only for `tier=read` and `side_effect=none`; `idempotentHint` is true only when the existing `idempotent` flag is true and that same predicate holds. `sar_export_overview` stays read-tier for mode compatibility but is `side_effect=audit`, so it is not described or configured as pure read/idempotent. `destructiveHint` remains false because this scope adds no deletion or destructive overwrite. The Kiro configuration derives `autoApprove` from exactly `tier=read and side_effect=none`; there are no name-based exceptions.

Tool placement is as follows: prodrug preview is `analyze`, non-mutating but editor-gated; prodrug save is `write`, project data; durable prodrug compare is `analyze`, derived; import profile is `write`, no persistent side effect; import preview/get-preview are `write`, quarantine; import commit is `write`, project data; endpoint comparison is `read`, none; analysis readback is `read`, none. An analyze-mode caller may therefore see a preview that does not store anything or a durable derived operation; this is intentional and is tested as capability visibility plus side-effect behavior.

### Input validation, errors, and invariants

The MCP schema validator must traverse every item in every bounded array, not only the first 200. Maximum pair input is 5,000 rows; display pages are at most 500 relationship/import rows and 200 endpoint-comparison rows. Project IDs use the existing `ID_PATTERN`; endpoint keys use `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$`; notes are at most 1,000 characters; search text is at most 200 characters; filenames are basenames of at most 255 characters. Unknown fields are rejected. One-of and cross-field checks happen before any `SarClient` call.

The MCP allowlist is checked before HTTP when `project_id` is present. `sar_create_project` is a special fail-closed case: it is refused before HTTP whenever `SAR_MCP_PROJECT_IDS` is non-empty because a newly created ID cannot be authorized in advance. Flask remains authoritative for membership, role, ownership, CSRF, and import ownership even when MCP input is manipulated.

Errors have concrete boundaries. Schema/one-of/range/confirmation errors are caller-correctable MCP tool errors with no HTTP call. HTTP 400/422 is a caller-correctable input or prerequisite error; 403 is an authentication, role, membership, or allowlist error; 404 is a safe missing/inaccessible ID; 409 is a duplicate, stale batch, or transaction conflict; 413 is a wire or decoded-upload size error; and 5xx/database failures are fatal for that operation, rolled back where transactional, logged server-side without payload data, and returned as a generic safe error. Connection/session errors are recoverable tool errors; `SarClient` retains its one-time re-authentication/CSRF retry. Passwords, CSRF/session values, workbook/base64 bytes, raw pair text, SQL, and untrusted notes never enter errors, URLs, or logs.

Every result carries a data origin. Uncommitted profile/staged rows are `quarantined_import`; committed measurements and explicit relationship mappings are `imported`; summaries, comparisons, and analyses are `derived`; recommendations are `generated`; curated records remain `curated`; and bundles are `production_export`. The Flask layer derives staged origin from current batch status, never from an MCP relabel. Qualifiers, censored/missing/review states, units, source measurement IDs, summary IDs, review states, algorithm versions, and evidence boundaries are retained. Exact subtraction requires non-null numeric values, `summary_state` beginning with `observed`, an explicitly present qualifier in `{"=", "~"}` on both sides, and equal non-empty canonical units. Missing/null qualifiers are non-exact; they are never treated as `=`. Threshold/censored, missing, non-exact, review, or unit-mismatched rows remain visible with `delta: null`.

### Bounded output and continuation

Paged domain responses use their own continuation contract and are not dependent on generic fitting. They contain `total`, `offset`, `returned`, `has_more`, `next_offset` (null at the end), and a stable-order description/key. Prodrug preview pages source rows by original input index; import pages use the canonical import-row key below; endpoint and analysis pages use their specified stable keys. Counts are calculated over the complete bounded input/result before page slicing.

`shaping.py` continues to remove drawings, SVGs, and molblocks recursively. Paged handlers reserve output space for their counts, stable-order metadata, and continuation object, then choose the largest row prefix that fits the configured MCP budget. Any final `_truncated` marker retains valid JSON and contains the continuation tool and arguments. `McpServer._success()` must never cut serialized JSON by character slicing. If a scalar-heavy result still exceeds the budget after fitting, it returns a structured tool error, not malformed output:

```json
{
  "error": "response_too_large",
  "message": "Reduce the requested fields or page size; the response cannot fit the MCP output budget.",
  "continuation": {"tool": "sar_<tool>", "arguments": {"limit": 1, "offset": 0}}
}
```

For a tool with no meaningful continuation, the error gives the tool name and actionable smaller request guidance. Tests cover scalar-heavy overflow and verify `isError=true` with parseable JSON, and page tests verify that counts/continuation survive fitting.

### XLSX/base64 transport

The selected transport is strict standard RFC 4648 base64 in the existing JSON client path, not a new multipart client implementation. The exact upload body is:

```json
{
  "project_id": "prj_...",
  "filename": "CellLinePanel_pIC50s.xlsx",
  "content_base64": "<base64 without data: prefix>",
  "sheet_name": "Sheet1",
  "header_row": 1,
  "data_start_row": 2,
  "mapping_overrides": {},
  "formula_acknowledged": false,
  "offset": 0,
  "limit": 100
}
```

`csv_text` and `content_base64` are mutually exclusive. The strict shared decoder rejects a data-URI prefix, whitespace, non-RFC-4648 characters, bad padding, and decoded bytes above `MAX_UPLOAD_BYTES`; it accepts exactly `MAX_UPLOAD_BYTES`. The suffix selects the parser/content type: `.xlsx` uses `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `.csv` uses `text/csv`, and `.tsv` uses `text/tab-separated-values`. A suffix/payload mismatch is rejected: XLSX must be a valid supported ZIP workbook and CSV/TSV must be valid UTF-8 text for the selected parser. Macro-enabled/unsafe/unsupported workbooks are rejected by the existing import pipeline.

Let `BASE64_BYTES(n) = 4 * ceil(n/3)` and `JSON_ENVELOPE_BYTES = 65,536`. The base64 JSON body limit is `BASE64_BYTES(MAX_UPLOAD_BYTES) + JSON_ENVELOPE_BYTES`; at the default 5,000,000 decoded-byte limit this is 6,732,204 bytes. Flask enforces the serialized request-size limit before JSON parsing using the configured request limit; decoded-size validation occurs again after decoding. `SarClient` computes the UTF-8 serialized body before sending and rejects bodies above the same limit. Multipart browser routes retain their existing path and receive the decoded-size check. Boundary tests cover exact decoded size, decoded over-limit, body over-limit, invalid padding/whitespace, malformed XLSX, macro-enabled files, suffix mismatch, and no batch creation on rejection.

## P0 — unblock the complete Kiro workflow

### P0-1 — Explicit prodrug relationship workflow

Add these tools in `sar_mcp/tools_run.py` and add the corresponding precise application behavior in `app.py`/`prodrug_engine.py`.

#### `sar_preview_prodrug_relationships`

Tier `analyze`, `side_effect=none`, `idempotent=false`, editor-gated, not auto-approved. Schema:

```json
{
  "type":"object", "additionalProperties":false,
  "required":["project_id","relationship_type"],
  "properties":{
    "project_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
    "relationship_type":{"type":"string","enum":["prodrug_of"]},
    "pairs":{"type":"array","minItems":1,"maxItems":5000,"items":{
      "type":"object","additionalProperties":false,
      "required":["prodrug_id","active_id"],
      "properties":{
        "prodrug_id":{"type":"string","minLength":1,"maxLength":100},
        "active_id":{"type":"string","minLength":1,"maxLength":100},
        "notes":{"type":"string","maxLength":1000}
      }}},
    "pair_text":{"type":"string","minLength":1,"maxLength":1000000},
    "offset":{"type":"integer","minimum":0,"maximum":1000000},
    "limit":{"type":"integer","minimum":1,"maximum":500}
  }
}
```

Exactly one of `pairs` or `pair_text` is required. Structured pairs use explicit compound/registration/name tokens only; text uses the existing CSV/TSV parser with optional header and notes. The handler validates all rows before calling `POST /api/v1/compound-relationships/preview`, which requires `project_id`, `relationship_type=prodrug_of`, and editor access. `prodrug_engine.preview_relationships()` resolves only within that project and reports unknown, ambiguous, duplicate, self, blank, and malformed rows. It does not inspect structures to infer a relationship.

The application computes all classifications first, then the MCP page is a deterministic slice of the source-row sequence. The response includes `total_count`, `valid_count`, `invalid_count`, `offset`, `returned`, `has_more`, `next_offset`, `stable_order="input_source_line_then_input_index"`, `valid` and `invalid` page subsets, row line/index, resolved internal and registration IDs, notes, relationship type, `saved=false`, `nothing_saved=true`, `data_origin="imported"`, and a safe prodrug browser link. The continuation request repeats the same bounded input with the returned offset/limit; no raw input is stored in a URL or continuation token. Counts include rows not present in the current page. The response never claims that a page is a complete review.

A 403 is a recoverable role/scope error; a 422 is a caller-correctable mapping error; no relationship or audit row is created. Analyze mode explicitly permits this editor-gated, non-persisting preview despite the mode’s ability to expose durable analyses. Tests verify mode visibility, no mutation/audit, complete validation of an invalid row at index 200+, continuation past the first page, source order, and link safety.

#### `sar_save_prodrug_relationships`

Tier `write`, `side_effect=project_data`, full mode only, never auto-approved. It uses the preview schema plus required boolean `user_confirmed`; exact JSON `true` is required. The handler performs one-of, full-array, and confirmation validation before `POST /api/v1/compound-relationships`. The application recomputes the preview inside the transaction, checks the project and editor role, and rejects the whole request on any invalid, foreign, duplicate, or self-pair; no partial relationship is returned.

Duplicate semantics are explicit: if any valid pair already exists in the project, the operation returns 409 `duplicate_relationship` with the existing relationship ID(s) and performs no mutation. It is therefore not idempotent; a retry must be reviewed and omitted or handled by a future explicit update workflow. Duplicate-in-batch is a 422 validation error. This chosen 409 policy avoids silently replacing prior source context and makes retries auditable.

For new rows, `activation_context_json` stores bounded structured context, not raw pair text:

```json
{
  "mapping_source":"mcp_explicit_mapping",
  "source_format":"pair_text",
  "source_line":12,
  "source_row":12,
  "source_columns":["prodrug","active","notes"]
}
```

Structured pairs use `source_format=structured_pairs` and a 1-based pair index. Saved rows are `relationship_type=prodrug_of`, `review_status=needs_review`, and `relationship_source=bulk_mapping`. The same context survives list, comparison, JSON export, CSV metadata, and audit metadata. The audit event includes `audit_event_id`, actor/request/project context, relationship type, saved IDs/count, source formats/context, and review status, but no credentials, raw file, or raw pair text. The result includes those IDs, context, review state, `data_origin=imported`, and a prodrug link. Transaction/database failures roll back and are fatal for this call; role/scope/mapping/duplicate errors are recoverable caller errors. Tests cover structured/text context, retry/duplicate behavior, all-or-nothing save, project isolation, export/list preservation, and audit fields.

#### `sar_compare_prodrug_pairs`

Tier `analyze`, `side_effect=derived`, `idempotent=false`, editor-gated, not auto-approved. Schema:

```json
{
  "type":"object", "additionalProperties":false,
  "required":["project_id","endpoint_key"],
  "properties":{
    "project_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
    "endpoint_key":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$"},
    "relationship_ids":{"type":"array","maxItems":5000,"items":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}},
    "limit":{"type":"integer","minimum":1,"maximum":500},
    "offset":{"type":"integer","minimum":0,"maximum":1000000}
  }
}
```

The MCP handler always sends `persist=true` to `POST /api/v1/analysis/prodrug-comparison`. The app validates that every selected relationship belongs to the project and creates one `analysis_runs` row plus one `analysis_run` audit event in the same transaction. `analysis_runs.input_selection_json` contains an immutable snapshot envelope with snapshot version, project, endpoint metadata, relationship IDs, pair relationship/source context/review state, summary IDs, source measurement IDs, values, units, qualifiers, states, statuses, deltas where allowed, algorithm version `prodrug-active-pair-compare-v1`, and evidence boundary. It contains no drawings, molblocks, raw pair text, or unbounded notes.

The same transaction generates `analysis_run_id` and `audit_event_id`. The audit row has `resource_type="analysis_run"`, `resource_id=analysis_run_id`, `operation="prodrug_comparison"`, the exact project, actor user ID, request ID, algorithm/version, endpoint, relationship IDs, summary/source IDs, status, and evidence-boundary metadata. Safe opaque actor/request identifiers are returned; passwords, CSRF/session values, and raw inputs are not. The POST response returns `analysis_run_id`, `audit_event_id`, `project_id`, `persistence="durable"`, `data_origin="derived"`, page/count fields, algorithm/version, endpoint metadata, pairs, and the browser link.

Add `GET /api/v1/analysis/prodrug-comparison/<run_id>` with required query `project_id`, `limit`, and `offset`. It checks `WHERE id=? AND project_id=?` before reading the snapshot, then viewer access and allowlist scope. It reads the stored snapshot only; it never recalculates against newer summaries. It returns the same `analysis_run_id`, `audit_event_id` (resolved by `resource_id=analysis_run_id` and operation), project, actor/request metadata, endpoint, algorithm, page/count fields, pairs, `persistence="durable"`, `data_origin="derived"`, and evidence boundary. Missing or foreign runs are a safe 404 before snapshot disclosure. Export includes the same analysis-run snapshot and audit linkage. Tests follow both returned IDs and assert exact project, relationship IDs, endpoint, summaries, source measurements, algorithm, actor/request context, and evidence boundary; they also mutate later summaries and prove the stored readback does not change.

`prodrug_engine._exact_summary()` must require `summary.get("summary_qualifier") in {"=", "~"}` with no fallback. `_summary_view()` preserves null/missing qualifiers. A missing qualifier produces `prodrug_non_exact` or `active_non_exact`, with `delta=null`, in comparison, snapshot, readback, and export tests. Threshold/censored, missing, non-exact, and unit-mismatch pairs remain visible and are never subtracted.

### P0-2 — Staged import review and XLSX through MCP

#### `sar_import_profile`

Tier `write`, `side_effect=none`, full-only, not auto-approved because it accepts user file content. Schema:

```json
{"type":"object","additionalProperties":false,"required":["project_id","filename"],"properties":{
  "project_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
  "filename":{"type":"string","minLength":1,"maxLength":255},
  "csv_text":{"type":"string","minLength":1,"maxLength":1000000},
  "content_base64":{"type":"string","minLength":1,"maxLength":6666668}
}}
```

Exactly one content field is required. The handler checks the MCP allowlist; `POST /api/v1/imports/profile` is extended to require a valid project and editor access for the MCP JSON contract before parsing. Profile creates no source document, batch, measurement, or audit row. It returns bounded format/size/hash/workbook metadata, sheets/dimensions, visible-sheet ambiguity, formula policy, and warnings with `data_origin=quarantined_import` (uncommitted file content), never bytes.

#### Enhanced `sar_import_preview`

Retain the tool name, tier `write`, `side_effect=quarantine`, full-only. Its schema adds the same `content_base64` alternative and:

```json
{
  "sheet_name":{"type":"string","minLength":1,"maxLength":255},
  "header_row":{"type":"integer","minimum":1,"maximum":10000},
  "data_start_row":{"type":"integer","minimum":1,"maximum":1000000},
  "mapping_overrides":{"type":"object","maxProperties":50,"additionalProperties":{"type":"string","minLength":1,"maxLength":200}},
  "formula_acknowledged":{"type":"boolean"},
  "offset":{"type":"integer","minimum":0,"maximum":1000000},
  "limit":{"type":"integer","minimum":1,"maximum":500}
}
```

Required fields are `project_id` and `filename`; exactly one of `csv_text`/`content_base64` is required. The app requires editor access, selects a visible sheet explicitly when multiple sheets exist, rejects unknown mapping overrides, and delegates formula/source-cell policy to the existing import pipeline. Formulas are never calculated; scientific-field formulas are blocked and metadata formulas require acknowledgement.

Preview creates the quarantine batch once, computes all rows, and returns the requested page. `rows_total`/`total`, `offset`, `returned`, `has_more`, `next_offset`, stable order key, status, import/project IDs, source metadata, mapping/confidence/issues, accepted/rejected/warning counts, source sheet/cell locations, formula policy, `data_origin=quarantined_import`, and a staged-review link are included. A response page is never called a complete review. Storage/transaction failure is fatal and cannot return a successful batch.

#### `sar_get_import_preview`

Add a full-only, `write`, `side_effect=quarantine`, `idempotent=true` tool:

```json
{"type":"object","additionalProperties":false,"required":["project_id","import_id"],"properties":{
  "project_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
  "import_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
  "offset":{"type":"integer","minimum":0,"maximum":1000000},
  "limit":{"type":"integer","minimum":1,"maximum":500}
}}
```

It calls `GET /api/v1/imports/<import_id>?project_id=<project_id>&offset=<offset>&limit=<limit>`. This project binding is mandatory and is enforced before reading rows: the SQL batch lookup is `WHERE ib.id = ? AND ib.project_id = ?`; a missing, mismatched, or foreign project is a safe 404 (the route must not fall back to the batch’s actual project). `_import_access(import_id, project_id=project_id)` remains a second ownership/access check. Only after both checks may the route read rows. This closes the cross-project allowlist risk identified in review.

Use one canonical order everywhere in `get_import_api` and `commit_import()`: numeric prefix before the first `:`, then the complete `source_row_id`, then the persisted `import_rows.id`, ascending. For example, `2:EC50` and `2:IC50` have the same numeric prefix but are ordered by full source ID and then row ID, never by `CAST(source_row_id AS INTEGER)` alone. Each returned row includes its stable order key. Offset/limit applies to this order; repeated page requests cannot duplicate or omit tied wide-row rows. Tests use multiple `2:*` rows and compare retrieval and commit order.

Before commit, statuses `preview`/`rejected` report `data_origin=quarantined_import`; after commit, `imported`. The result includes mapping/options/issues, formula policy, source locations, page metadata, and a safe staged link. Invalid page parameters are 422; missing/mismatch/foreign IDs are safe 404; access failures are 403 where the application convention requires it; GET has no mutation.

#### Commit and tests

`sar_import_commit` remains full-only, `write`, `side_effect=project_data`, non-idempotent. Its schema requires `project_id`, `import_id`, exact boolean `user_confirmed=true`, `reviewed_all_rows=true`, and `reviewed_row_count` equal to the current staged total. The MCP fields are client attestations, not proof that a human/model inspected every page; response says `review_attestation_unverified=true`. Flask repeats project binding, ownership, status, and total checks before the existing transactional `commit_import()`. Stale totals are 409 and no commit. The response contains bounded counts/IDs/origin/link, never all rows.

Tests are split between `tests/test_mcp_unit.py` (schemas, bounds, mode/descriptor, strict transport, no-call validation, shaping) and integration/domain suites: `tests/test_mcp_integration.py`, `tests/test_import_cleaning.py`, `tests/test_production_dashboard.py`, and `tests/test_deep_links.py` cover CSV/TSV/XLSX/profile, sheet/header/data-row selection, formulas, overrides, size/unsafe errors, project roles/allowlist, missing/mismatched/foreign imports, tied-row pagination, status-origin transition, commit attestation, transaction failure, and staged links.

### P0-3 — Export and comparison provenance integrity

`export_engine.export_project_csv()` must emit one `compound_relationship` record for every JSON `compound_relationships` row, with relationship ID, project ID, prodrug/active compound and registration IDs, relationship type/source, review status, notes, structured activation/source context, evidence IDs, and `data_origin=imported` in CSV metadata. The durable `prodrug_comparison` analysis snapshot and audit IDs appear in the existing `analysis_runs` export section. JSON and CSV are project-filtered and semantically equivalent for relationship/provenance fields; the export route continues to write an audit event.

`sar_export_overview` accepts `project_id`, optional bounded `section`, and bounded `limit`; its result reports section totals/returned rows, safe export/download links, and provenance policy. It has `side_effect=audit`, is not auto-approved, and says explicitly that fetching the export writes an audit record. 403/404 scope errors, 422 row ceilings, formula-injection escaping, relationship parity, comparison provenance, audit linkage, and project isolation are tested in `tests/test_export_api.py` plus MCP unit/integration tests.

## P1 — trust and usability

### P1-4 — Structured endpoint comparison: `sar_compare_endpoints`

Register in `sar_mcp/tools_lookup.py` as `read`, `side_effect=none`, `idempotent=true`, the only new data-analysis tool eligible for auto-approval. Schema:

```json
{"type":"object","additionalProperties":false,"required":["project_id","endpoint_a"],"properties":{
  "project_id":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
  "endpoint_a":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$"},
  "endpoint_b":{"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$"},
  "coverage":{"type":"string","enum":["all","endpoint-a","both","review"]},
  "q":{"type":"string","maxLength":200},
  "limit":{"type":"integer","minimum":1,"maximum":200},
  "offset":{"type":"integer","minimum":0,"maximum":1000000}
}}
```

Add `endpoint_comparison_engine.py` and `GET /api/v1/endpoint-comparison`. The route requires viewer access and known project summaries, creates no session or project record, and orders by registration ID, compound ID. Units use NFKC normalization and trim only; there is no case folding, internal whitespace collapse, or conversion table. Equal non-empty canonical units are comparable; missing-unit and mismatch are explicit. State precedence is missing, then non-exact/review, then missing unit, then mismatch, then comparable exact. Results preserve values, original/canonical units, qualifiers, states, missingness, summary IDs, counts, continuation, and a Compare link; no delta is silently calculated. Coverage and selection match `static/production_explorer.js`. Engine unit tests cover exact/censored/missing/review/null-qualifier/Unicode/mismatch cases; route/MCP tests cover pagination, project isolation, shaping and browser parity.

### P1-5 — Deep links and browser state

Replace arbitrary query construction for new states with named builders in `sar_mcp/links.py`: `staged_import_review(project_id, import_id)`, `prodrug(project_id, endpoint_key=None, relationship_id=None, comparison_run_id=None)`, `evidence(project_id, compound_id=None, measurement_id=None, summary_id=None)`, and `explore_compare(project_id, endpoint_a, endpoint_b=None, stage="compare", coverage="all", q="", selected_compound_id=None)`. All validate IDs/keys/enum values/lengths and reject control characters, `%`, `/`, `\\`, `?`, `#`, path traversal, credentials, CSRF/session strings, raw pair text, and workbook bytes. `SAR_MCP_PUBLIC_URL` supplies only an origin; API calls stay on `SAR_MCP_BASE_URL`.

Canonical browser-state table:

| Builder argument | Query key | Page/state consumer |
|---|---|---|
| `project_id` | `project_id` | all workspace pages; selected project |
| `import_id` | `import_id` | `/workspace/evidence`, `stage=import_review`; staged batch review |
| prodrug mode | `sar_mode=prodrug` | `/workspace/sar`, prodrug radio/mode marker |
| prodrug endpoint | `endpoint_key` | SAR prodrug comparison endpoint |
| `relationship_id` | `relationship_id` | selected explicit mapping, if project-scoped |
| `comparison_run_id` | `comparison_run_id` | durable comparison readback; takes precedence over relationship selection |
| compound | `selected_compound_id` | evidence/Compare selected compound |
| measurement/summary | `measurement_id`, `summary_id` | exact evidence marker and source row |
| endpoint pair | `endpoint_a`, `endpoint_b` | Compare selectors |
| stage/filter | `stage`, `coverage`, `q` | Compare stage, coverage, search |

State precedence is deterministic: valid `comparison_run_id` in the requested project selects the durable comparison; otherwise a valid `relationship_id` selects the mapping; otherwise endpoint/mode defaults render. Missing, foreign, malformed, or disallowed IDs produce a safe not-found/empty-state marker for the requested project and never fall back to another project’s record. Rendered-marker tests assert import-review, prodrug mode/endpoint, exact evidence, comparison endpoint A/B/stage/coverage/search/selection, and existing project/run/search link compatibility. Tests assert secrets, raw pair text, workbook content, CSRF, session identifiers, and credentials are absent from URLs and captured logs.

### P1-6 — Graph R-group and activity-cliff readback

Extend `sar_get_analysis_run` rather than adding another read tool. Schema requires `project_id`, `analysis`, and `run_id`; analysis enum is `mmp`, `rgroup`, `pharmacophore_rgroup`, `activity_cliffs`, `selectivity`, `properties`, `cellular_translation`, `adme`, `pareto`, `contradictions`, or `information_gain`, with bounded `limit`/`offset`. Add stored read routes for graph R-group and activity cliffs: `GET /api/v1/analysis/rgroup/<run_id>` and `GET /api/v1/analysis/activity-cliffs/<run_id>`, using `_analysis_project_id()` and viewer access, safe 404 for missing/foreign runs, and stable paged rows.

The canonical mapping is explicit: `rgroup -> /api/v1/analysis/rgroup/<id> -> analysis_runs.analysis_type=rgroup`; `activity_cliffs -> /api/v1/analysis/activity-cliffs/<id> -> analysis_runs.analysis_type=activity_cliff`. The MCP response echoes `analysis="activity_cliffs"` and additionally returns `analysis_type="activity_cliff"`; it echoes `rgroup`/`rgroup` for R-group. Existing route/storage names remain singular where already established. Every response carries project/run, algorithm/version, input selection, status, evidence/source IDs, page metadata, and shaped rows without drawings/molblocks. Tests cover both new routes, all existing enums, project/allowlist mismatch, pagination, safe foreign 404, and no structure renderings.

### P1-7 — Correct export side-effect metadata

Implement the shared `Tool.side_effect` descriptor contract for existing and new tools. `sar_export_overview` is `read` plus `audit`, so its read-only/idempotent hints are false and it is absent from `autoApprove`. A matrix test covers ordinary lookup (`none`), export (`audit`), quarantine preview, durable comparison, and project/write operations. Integration compares audit-event counts before and after export.

### P1-8 — Fail-closed allowlist and project creation

Keep `sar_create_project` schema as name 2–160 characters plus optional description up to 2,000. `McpServer._call` refuses it before a client request whenever `SAR_MCP_PROJECT_IDS` is non-empty, with a secret-free actionable message. Without an allowlist, `POST /api/v1/projects` retains application ownership rules; the returned project is usable only through normal app membership and configured scope. Tests cover no-call refusal, unallowlisted owner creation/list/use, another-project denial, and role enforcement.

### P1-9 — Broad integration coverage and governance boundary

Extend `tests/test_mcp_integration.py` against the authenticated local production-mode app for selectivity, cellular translation, ADME, Pareto, contradictions, information-gap/recommendation generation/readback, prediction training/apply/readback, CSV/TSV/XLSX/profile/staging, the complete explicit prodrug chain, endpoint comparison, export, roles, session expiry/CSRF renewal, and project isolation. Invalid prerequisites are 422; viewer/editor/owner outcomes follow Flask authorization; allowlist failures happen before HTTP.

Keep the governance registry test proving no MCP tool approves/rejects recommendations, approves hypotheses/designs, creates claims/curated designs/series memberships, or changes project membership/roles. Generation and readback remain available with explicit human-review wording. These browser-only writes are not hidden implementation requirements; they are out of scope.

## P2 — polish and hardening

### P2-10 — Documentation accuracy

Update `README.md`, `docs/MCP_GUIDE.md`, and affected MCP docs with schema 22, generated 17/22/28 counts/fingerprint, exact tool modes, CSV/TSV/XLSX/profile/staged pagination, explicit prodrug/no-inference/needs-review rules, durable comparison/audit IDs, export audit side effect, allowlist creation behavior, credentials, reconnect/mode sequence, public proxy URL, truncation/continuation, scientific boundaries, browser-only governance, and native-fallback limitations. A documentation contract test rejects stale counts and unsupported claims of browser/Kiro verification.

### P2-11 — Drift detection

Add `sar_mcp/catalog.py` with `catalog_summary()` derived from `all_tools()`: ordered names, per-mode counts, per-tier and side-effect counts, schema version, and stable SHA-256 fingerprint over name/tier/side-effect/schema JSON. `tests/test_mcp_catalog.py` fails on duplicate names, tier/mode mismatch, descriptor metadata mismatch, governance-name exposure, schema version not 22, or documentation marker/fingerprint/count drift. It preserves the three mode names and expected 16/19/22 baseline semantics; the post-roadmap expectation is 17/22/28 because six tools are added while existing mode rules stay fixed.

### P2-12 — Browser E2E decision and deterministic fallback

Do not add Playwright/Selenium or an unpinned browser dependency: none is present in the locked Pixi environment and the baseline audit did not execute browser automation. Implement `tests/test_mcp_native_smoke.py` using Flask’s test client, production fixtures, the MCP fake/client fixtures, rendered template/static markers, API integration, and route/deep-link contracts. It covers Start/project selection, profile/preview/paged review/commit, summaries, Compare/API/state, explicit prodrug preview/save/durable compare, JSON/CSV export, static JavaScript markers, roles, and project failures. It does not claim to verify real browser clicks/layout, session-storage Compare sessions, Kiro reconnect UI, or model-provider rendering; documentation names each unverified interaction. If a supported browser runtime is later supplied, an optional E2E may exercise Start → import → summaries → Compare → SAR → prodrug mapping/comparison → export.

### P2-13 — Credential-file serialization hardening

In `sar_mcp/cli.py`, write email/password values as JSON string literals in the existing `KEY=VALUE` credential file, rejecting CR/LF before write. Use a same-directory temporary file, mode 0600, flush/close, atomic replace, and final chmod 0600; retain 0700 directory policy. In `config.py:read_credentials_file`, parse JSON-quoted values with `json.loads`, reject malformed quoted values/line breaks with secret-free `ConfigError`, preserve compatible simple quoted/unquoted values, and retain environment-over-file precedence. No password appears in stdout/stderr/logs/errors/URLs/generated `mcp.json`; sign-in retains the three-failure limit. Tests cover exact quote/backslash round trip, newline rejection without overwrite, ownership/mode, malformed values, precedence, secret absence, and local-fixture sign-in.

### P2-14 — Kiro/MCP mode/reconnect and proxy smoke

Extend the native smoke around `kiro_config()`, `Settings`, `Links`, and a local fake Flask server. Generated `mcp.json` uses absolute executable/script paths, selected mode, only base URL/mode environment values, no credentials, and auto-approves only `side_effect=none` read tools. It excludes exports, analyses, imports, profile/preview/commit, project creation, relationship save, and other writes. Instantiating `McpServer` under each mode proves the tool list changes only after a new server/reconnect boundary; `sar_status` reports active mode. With base API origin and a different public browser origin, all HTTP calls use base while links use public; invalid public URL path/query/fragment/userinfo forms are rejected. No live external service is required.

## Out of scope

No external SSO/provider identity exchange; no browser-only recommendation approval/rejection, hypothesis/design approval, claims, curated designs, series memberships, or project membership/role writes; no chemical/structure/name/endpoint inference; no causal or experimental interpretation of deltas, predictions, recommendations, or analyses; no silent unit conversion or exact treatment of thresholds/missing values; no unbounded uploads/model output; no drawings/molblocks or secrets in model output/URLs/logs; no bypass of Flask authorization/CSRF; no unpinned browser dependency or live-service-only smoke check; no unrelated UI redesign; and no commits, pushes, force operations, or deployment.

## File-level implementation map and testability

Modify the focused existing areas only: `sar_mcp/toolkit.py`, `schema.py`, `tools.py`, `tools_lookup.py`, `tools_chem.py`, `tools_open.py`, `tools_run.py`, `server.py`, `shaping.py`, `links.py`, `client.py`, `config.py`, `cli.py`, and new `sar_mcp/catalog.py`; `app.py`; `prodrug_engine.py`; `import_pipeline.py`/`import_formats.py` for reusable profile/order helpers; `export_engine.py`; `rgroup_engine.py`; new `endpoint_comparison_engine.py`; affected templates/static files; README/docs; and the named test files. No migration is required: source context uses `activation_context_json`, and comparison snapshots use `analysis_runs.input_selection_json` under a versioned `snapshot` member.

Unit tests cover schemas, full-array validation, tiers/descriptors/catalog, links, strict base64/size arithmetic, credential serialization, shaping/overflow, exact scientific rules, and endpoint status logic. Integration tests cover authenticated Flask routes, CSRF/session retry, roles, project isolation, import staging/commit, durable snapshot/audit readback, export parity, and browser markers. The native smoke is deterministic and offline. Validation order is focused MCP/unit tests, import/prodrug/export/deep-link/API suites, MCP integration, full `pixi run test`, then an explicitly-modeled `pixi run mcp-check` when local app credentials exist. Unavailable browser/runtime checks remain explicitly unverified.

## Responses to the design review findings

1. **HIGH — staged import project binding: Resolved.** `sar_get_import_preview` requires `project_id`; `get_import_api` queries `ib.id` and `ib.project_id` together, returns safe 404 before row reads on mismatch/missing scope, and still calls `_import_access(import_id, project_id=project_id)`. Tests cover allowed-project-to-other-accessible-project, missing, foreign, and mismatch cases.
2. **HIGH — durable comparison audit identity: Resolved.** The POST transaction returns `analysis_run_id` and `audit_event_id`; audit `resource_id` is exactly the run ID. POST, GET, and export expose the linkage and safe audit metadata. Tests follow both IDs and verify project, relationship/summary/source IDs, endpoint, algorithm, actor/request context, and evidence boundary.
3. **HIGH — missing qualifier exactness: Resolved.** Exactness requires an explicit qualifier in `{"=", "~"}`; null/missing qualifiers are preserved and produce a non-exact status with `delta=null` through compare, snapshot, readback, and export.
4. **HIGH — prodrug preview continuation: Resolved.** Preview accepts bounded `offset`/`limit`, computes all validation counts, slices a stable source-row page, returns `total/returned/has_more/next_offset`, and tells the caller to repeat the same bounded input for continuation. Tests retrieve beyond the first page and validate an invalid row beyond index 200.
5. **MEDIUM — analyze tier semantics: Resolved.** Analyze means capability availability, not mandatory persistence. It may expose editor-gated non-mutating previews or durable derived operations; `side_effect` and role checks state the mutation boundary. Visibility/no-mutation tests cover the preview.
6. **MEDIUM — duplicate save/source context: Resolved.** Existing pairs return 409 with no mutation; retries are intentionally non-idempotent. New rows store structured source context in `activation_context_json`, and tests verify retry, list, comparison, export, and audit behavior.
7. **MEDIUM — tied import ordering: Resolved.** Retrieval and commit use numeric prefix, full `source_row_id`, and row ID, and return the order key. Wide IDs such as `2:IC50`/`2:EC50` are tested across page boundaries.
8. **MEDIUM — exact upload boundary: Resolved.** The design specifies the exact JSON body, strict decoder, content-type/suffix parser mapping, mismatch behavior, serialized/body and decoded limits, pre-JSON Flask enforcement, client pre-send enforcement, and profile/preview boundary tests.
9. **MEDIUM — deep-link state: Resolved.** A builder/query/state table defines every key, precedence, invalid/foreign behavior, and rendered marker for import, prodrug, evidence, and Compare state; URL/log secret exclusion is tested.
10. **MEDIUM — analysis enum mismatch: Resolved.** `activity_cliffs` maps to the singular stored `activity_cliff`; the response exposes both canonical MCP `analysis` and stored `analysis_type`, with regression coverage for all existing enums.
11. **MEDIUM — structured overflow: Resolved.** Paged handlers reserve counts/continuation and fit rows deliberately; `_success` no longer hard-cuts. Residual scalar overflow becomes a parseable `response_too_large` structured tool error with paging/reduction guidance, covered by unit tests.
