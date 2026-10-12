# SAR Workbench MCP Coverage Roadmap Requirements

**Repository:** `/Users/yipy/Documents/GitHub/SAR_tool`  
**Baseline:** schema 22; MCP catalog currently 16 `read-only`, 19 `analyze`, and 22 `full` tools; baseline tests report 200 passing tests, 33 MCP unit/integration tests, and 20 specialized prodrug/API/dashboard/deep-link tests.  
**Status:** Requirements only; no implementation is specified or performed by this artifact.

## Summary

The SAR Workbench MCP must make common, project-scoped SAR Workbench workflows usable from Kiro as far as safely possible. The highest-priority user journey is importing real measurements, building summaries, explicitly mapping ester prodrugs to their active acids, comparing compatible endpoint observations, and returning evidence and browser links without inferring chemical relationships or overstating causality.

Requirements fit the existing repository boundaries: Flask application routes and `app.py` remain the application authorization boundary; `authorization.py` continues to enforce membership and roles; the MCP stdio server, `SarClient`, `toolkit.py` tiers, schemas, links, shaping, and tool modules remain the MCP boundary; `import_pipeline.py`, `prodrug_engine.py`, analysis engines, and `export_engine.py` remain the domain/provenance sources of truth. The locked runtime is the existing Pixi environment (Python 3.12.14, Flask 3.1.1, RDKit 2026.03.3, pytest 8.4.1, stdlib-only MCP transport/client facilities, SQLite/PostgreSQL application backends). No new dependency is required by these requirements; if browser automation is unavailable, the repository-native fallback is deterministic route/UI smoke coverage.

The roadmap is a complete scope, not a documentation-only exercise. Items that call for implementation must be implemented. Items that depend on a missing browser/runtime capability must still receive the strongest repository-native coverage possible, with the limitation and unverified portion recorded in documentation and a deterministic test or smoke check.

### Assumptions and resolved ambiguities

- A prodrug mapping preview input is mutually exclusive: exactly one of a structured `pairs` array or CSV/TSV-like `pair_text` is accepted. Supplying neither or both is invalid before any application call.
- `relationship_type` is currently restricted to `prodrug_of`; no SMILES-based inference, name-based inference beyond explicit token resolution, or causal interpretation is allowed.
- Saved prodrug comparisons should use the safer durable path aligned with existing versioned analysis/audit conventions. Therefore the persisted comparison is an `analyze`-tier operation; if an implementation must retain an ephemeral read-only path for a specific mode, it must label it ephemeral and preserve all returned provenance, but it must not silently replace the durable default.
- “All rows” means all staged import rows/source locations available through bounded pages or continuation tokens, not one unbounded model response. Every truncated response must announce what remains.
- The repository's existing source-cell and formula policy are authoritative: formulas are never calculated by the Workbench; scientific-field formulas remain blocked, and metadata-only formulas require explicit acknowledgement.
- Browser-only human governance remains intentional. Kiro may generate/read recommendations and read curated records, but may not approve recommendations, approve hypotheses/designs, create curated designs/claims/series memberships, or alter project membership unless policy and tests are explicitly changed in a future scope.
- “Current test count” in documentation must be generated or checked against the repository's actual test collection where practical; otherwise it must be updated to the observed count and accompanied by a drift detector.

## Functional Requirements

### P0 — Unblock complete Kiro workflows

#### FR-P0-1 — Explicit MCP prodrug relationship workflow

Add MCP coverage for the existing `/api/v1/compound-relationships/preview`, `/api/v1/compound-relationships`, and `/api/v1/analysis/prodrug-comparison` workflow.

**Preview requirements.** The preview tool must be available only at a mode/tier that does not save authoritative project data, accept `project_id`, `relationship_type` (only `prodrug_of`), and exactly one of:

- a bounded structured pair array whose pair objects contain explicit prodrug and active-form references plus optional notes; or
- bounded CSV/TSV-like pair text with optional header and notes column.

It must validate the project reference and MCP allowlist before calling the application, preserve notes and source line/row context, resolve references only within the project, and report unknown, ambiguous, duplicate, and self-pairs. The response must include complete bounded validation results (counts, each accepted/rejected row or a deterministic page/continuation mechanism, resolved compound IDs and registration IDs, notes, relationship type, review state), `data_origin`, and a browser link. It must clearly state that nothing was saved. It must not infer a relationship from structures.

**Save requirements.** The save tool must be `full`/write-only, require `user_confirmed` to be exactly `true`, recompute validation server-side from the submitted mapping, refuse the entire request when any row is invalid, and save only explicit project-scoped relationships with `review_status=needs_review`. It must preserve notes, source context, relationship type, actor/request audit metadata, and return saved relationship IDs, source context, review status, data origin, and a browser link. It must clearly state that the mapping is explicit user-provided mapping, not an inferred relationship and not causal or activation evidence.

**Compare requirements.** The compare tool must accept `project_id`, `endpoint_key`, and bounded reasonable filters/pagination where the result can exceed the response budget. It must return endpoint metadata, algorithm/version, pair counts, relationship IDs, prodrug and active values, units, qualifiers, states, source measurement IDs, summary IDs, review state, explicit comparison status, evidence boundary, and a browser link. It may not subtract threshold/censored, missing, non-exact, or unit-incompatible values. Exact delta is allowed only for compatible exact observed summaries. The durable default must persist or audit the comparison consistently with existing analysis-run conventions and expose the resulting run/audit identifiers; a read-only ephemeral result, if retained, must be labelled ephemeral and never be presented as a stored analysis.

MCP schemas, tier registration, output shaping, truncation notices, documentation, and unit/integration tests must cover this workflow against the production-mode app and existing prodrug fixtures.

**Acceptance check:** AC-001 through AC-006.

#### FR-P0-2 — Staged import review and XLSX support through MCP

Expose import profiling and staged-preview retrieval through MCP. Both operations must enforce project access and import ownership/scope through the application, and all staged rows and source locations must be retrievable through bounded `limit`/`offset` or continuation-token pages. A page must report total/returned/has-more or its equivalent and must never silently imply that a sample is the complete review.

Enhance MCP import preview to pass through the web application's supported semantics where applicable: `sheet_name`, `header_row`, `data_start_row`, `mapping_overrides`, and `formula_acknowledged`. Retain CSV and TSV text input. Add a bounded XLSX binary path using the existing client/server conventions (multipart or a bounded base64 representation); reject oversized/invalid/unsafe uploads and macro-enabled/unsupported formats according to the import pipeline. The result must expose workbook metadata, available sheets, selected sheet, dimensions/profile, mapping options/confidence/issues, source-cell locations, and formula policy without calculating formulas.

Make commit confirmation meaningful: MCP commit must continue to require explicit `user_confirmed=true`, must identify the staged import and project, and must require or strongly recommend that the user reviewed a complete paginated preview before committing. The commit path must preserve the application's transactional validation and ownership checks. Model-facing output must remain bounded and announce truncation.

**Acceptance check:** AC-007 through AC-012.

#### FR-P0-3 — Export and comparison provenance integrity

Ensure CSV exports include `compound_relationships` when the documented export contract says relationships are included; prefer fixing the implementation so JSON and CSV contain equivalent relationship/source provenance rather than silently weakening the contract. Add tests for JSON/CSV parity, project isolation, relationship IDs, relationship review/source fields, and data-origin labels.

Make the prodrug comparison provenance durable or explicitly model it as ephemeral. The selected safer default is durable persistence/audit consistent with existing analysis-run conventions, including algorithm version, relationship IDs, summary IDs, source measurement IDs, endpoint metadata, and evidence boundary. A comparison must not be durable in a way that loses its input relationship or summary provenance.

**Acceptance check:** AC-013 and AC-014.

### P1 — Trust and usability

#### FR-P1-1 — Structured read-only endpoint comparison

Add a structured read-only MCP endpoint comparison tool accepting endpoint keys plus bounded coverage filters, search, `limit`, and `offset`. It must return endpoint metadata and canonical units, per-compound values, qualifiers, missingness/state, source summary IDs, explicit comparability and unit-mismatch states, total/returned counts, continuation information, and a browser Compare link.

The rules must match `static/production_explorer.js`: exact observed values may be plotted/compared; threshold/censored, missing, and review states remain visible but are not treated as exact; incompatible units must never be silently compared or normalized without an explicit supported conversion. The tool is read-only and must not create browser-local sessions or project records.

**Acceptance check:** AC-015 through AC-017.

#### FR-P1-2 — Deep links and browser state

Extend MCP/browser deep links for staged import review, prodrug comparison mode and endpoint, exact compound/evidence context, and endpoint comparison state. Links must preserve project scope and the relevant safe identifiers/state while excluding credentials, CSRF tokens, session identifiers, and secrets from URLs and logs. Existing links for project, run, search, compound, and endpoint state must remain valid or receive compatible migration handling.

Staged-import links must open the correct project/import review context when the browser session has access. Prodrug links must open the SAR prodrug mode with the selected endpoint and, where policy permits, the saved relationship/comparison context without putting raw pair text or secrets in the URL. Exact evidence links must identify the compound/measurement/summary context sufficiently for browser review. Endpoint comparison links must preserve endpoint A/B, stage, coverage, search, and selected compound state using the established `summary|<endpoint>|<unit>` convention or its compatible successor.

**Acceptance check:** AC-018 through AC-020.

#### FR-P1-3 — MCP readback for graph R-group and activity-cliff results

Add MCP readback for graph R-group and activity-cliff results, using a general stored-analysis result route/tool where appropriate. Readback must preserve project/run scope checks, algorithm/version, endpoint/input selection, result states, evidence/source IDs, and bounded model-facing shaping. It must not expose drawings or molblocks under existing shaping rules and must not allow a run from another project or disallowed allowlist project to be read.

**Acceptance check:** AC-021.

#### FR-P1-4 — Correct export MCP side-effect metadata

Correct tool tier and MCP annotations for exports so an operation that writes an export audit event is not falsely described as pure read-only/idempotent. The generated Kiro configuration must not auto-approve audit-writing exports. Any updated annotation must accurately distinguish data retrieval from audit side effects without weakening the existing confirmation behavior for stored analyses and writes.

**Acceptance check:** AC-022.

#### FR-P1-5 — Fail-closed project allowlist and project creation

Make `sar_create_project` behavior explicit and safe when `SAR_MCP_PROJECT_IDS` is non-empty. The selected behavior is fail-closed: refuse the create operation before the application call when an allowlist is configured because the requested new ID is not known and cannot be authorized in advance. Return an actionable tool error without creating an orphan project. With no allowlist, project creation must continue to use the application's ownership and role rules; the returned project must then be usable only if it satisfies the configured scope.

Add tests for allowlisted project listing, blocked creation, unallowlisted creation, subsequent project-scoped access, and application-level membership/role enforcement.

**Acceptance check:** AC-023.

#### FR-P1-6 — Broad MCP analysis and role-failure integration coverage

Add integration coverage for remaining important MCP analysis and workflow paths where practical: selectivity, cellular translation, ADME, Pareto, contradictions, information-gap, recommendation generation/readback, prediction training/apply/readback, CSV/XLSX/staged import paths, explicit prodrug chain, endpoint comparison, and project scoping. Tests must include representative role failures (viewer versus editor/owner), authentication/CSRF/session-expiry behavior where relevant, invalid prerequisites, and allowlist failures.

Do not expose recommendation approval, hypothesis/design approval, curated design writes, series membership writes, project membership/role writes, or other browser-only human governance actions unless existing policy/tests clearly require a deliberate policy change. Retain and strengthen review-gate tests that prove these actions remain unavailable through MCP.

**Acceptance check:** AC-024 through AC-026.

### P2 — Polish and hardening

#### FR-P2-1 — Documentation reflects current MCP boundary and operation

Update `README.md`, `docs/MCP_GUIDE.md`, and any affected MCP documentation to reflect schema 22, the current test count or a checked count, all current tool modes/tier counts, and the exact MCP-versus-browser coverage boundary. Documentation must explicitly cover CSV versus XLSX support, profile/staged review, prodrug tools, human review-only writes, the mode/reconnect sequence, full-mode verification, allowlist behavior, credential handling, public URL/proxy behavior, truncation, provenance, and scientific-integrity limits.

Documentation must not claim browser/Kiro-level verification unless the corresponding test or smoke check was actually executed. It must identify remaining browser/runtime limitations and the repository-native fallback.

**Acceptance check:** AC-027.

#### FR-P2-2 — Reduce schema/test count drift

Make tool/schema/mode/test counts less drift-prone using repository-native mechanisms. Prefer deriving catalog counts and documentation-facing values from the registered tools or a deterministic check. If fully dynamic documentation is not appropriate, update counts accurately and add a test/check that fails when the documented or expected counts drift. Preserve the 16/19/22 mode model unless a new tier is clearly necessary and documented.

**Acceptance check:** AC-028.

#### FR-P2-3 — Browser E2E or strongest native fallback

Add automated browser E2E coverage for the feasible production workflow: start/project selection → import review → summaries → Compare → SAR → explicit prodrug mapping/comparison → export. Reuse any existing test stack/dependencies and do not introduce an unpinned dependency.

If a supported browser/runtime dependency is absent, do not block all roadmap work or add an unpinned dependency solely to satisfy E2E. Add the strongest deterministic repository-native route/UI smoke coverage possible, including Flask test-client route behavior, rendered deep-link markers, JavaScript/static contract markers, import/prodrug/export API integration, and MCP integration. Document exactly which browser interactions remain unverified and make the smoke check deterministic.

**Acceptance check:** AC-029.

#### FR-P2-4 — Harden credential-file serialization

Make credential-file serialization round-trip passwords containing embedded double quotes and backslashes, while retaining owner-only permissions and rejecting line breaks. Credentials must not be exposed in tool results, URLs, stdout, stderr, logs, generated `mcp.json`, or error messages. Add deterministic tests for quote/backslash passwords, permissions, environment-over-file precedence, malformed values, and sign-in without logging the secret.

**Acceptance check:** AC-030.

#### FR-P2-5 — Kiro/MCP smoke check and proxy behavior

Add a Kiro-client/MCP smoke check for generated `mcp.json`, auto-approval safety, mode changes/reconnect expectations, and `SAR_MCP_PUBLIC_URL` when the browser address differs from the MCP base address behind a proxy where the existing architecture permits. The check must not require a live external service. It must verify that generated configuration contains no secrets, auto-approves only safe read tools, hides/refuses tools outside the selected mode, and uses the public URL only for browser links while API calls continue to use the configured base origin.

**Acceptance check:** AC-031 and AC-032.

## Non-Functional Requirements

### Security and access control

1. The MCP layer must continue to enforce `SAR_MCP_PROJECT_IDS` and the application must continue to enforce authentication, project membership, and role requirements. MCP allowlist checks must fail closed when project scope cannot be established.
2. Credentials and project data must not travel over disallowed off-host plain HTTP. Same-origin redirect restrictions, owner-only credential-file permissions, CSRF behavior, and bounded request/response sizes must remain intact.
3. External input—including imported file names, compound names, notes, pair notes, mapping text, and workbook content—must be treated as untrusted data. It must never alter MCP instructions or cause secrets to be logged.
4. URLs must contain only safe, bounded, encoded state. They must not contain passwords, tokens, CSRF values, session cookies, raw credentials, or unbounded file/pair content.

### Scientific integrity and provenance

5. Raw measurements, derived summaries/analyses, generated recommendations, curated designs, and explicit imported relationship mappings must remain distinct data origins. Returned records must retain qualifiers, censored/missing states, units, source IDs, algorithm versions, review states, and evidence boundaries.
6. Prodrug relationships must be explicit and reviewed. No feature may infer ester/acid relationships from SMILES, structures, similarity, names, or endpoint behavior. A saved relationship is not biological activation proof, and a delta is not causal evidence.
7. Threshold/censored values, missing values, incompatible units, and non-exact states must never be silently converted to exact observations, ranked as exact values, or subtracted for exact deltas.
8. Bounded model-facing shaping must strip drawings/molblocks according to existing rules, announce truncation, and provide pagination/continuation for complete review. Scientific records must not be silently truncated at a commit or export boundary.

### Compatibility and operations

9. Existing mode semantics (`read-only`, `analyze`, `full`) and the existing catalog behavior must be preserved unless a new tier is clearly justified, documented, and covered by tests. MCP tools must use the repository's existing schemas, client/session behavior, links, and tier annotations rather than bypassing them.
10. No dependency may be added unless truly required; any addition must be exact/pinned and compatible with the Pixi runtime. No commits or pushes are part of this scope.
11. Tests must distinguish unit checks (schema validation, shaping, link construction, serialization, tier/annotation behavior) from integration checks (authenticated production-mode app, CSRF/session renewal, project roles, real import/prodrug/analysis/export routes). Runtime-dependent checks must record whether they executed or used the documented fallback.

## Acceptance Criteria

The following numbered tests/smoke checks prove the requirements. Each functional requirement above points to the applicable criteria.

1. **AC-001 — Preview input exclusivity and schema bounds.** MCP schema validation accepts one bounded structured `pairs` array or one bounded `pair_text`, rejects neither/both, rejects unknown fields, enforces `relationship_type=prodrug_of`, bounds notes/text/row counts, and performs no HTTP write for invalid arguments.
2. **AC-002 — Prodrug preview validation completeness.** Against the production-mode prodrug fixture, preview returns valid and invalid counts, per-row line/source context and notes, resolved project compound IDs/registration IDs, and explicit errors for unknown, ambiguous, duplicate, and self-pairs; no relationship or audit save is created.
3. **AC-003 — Prodrug preview shaping/link safety.** A large pair input returns bounded output with an explicit `_truncated`/continuation notice, strips drawings/molblocks, preserves required validation counts, and includes a project-scoped browser SAR/prodrug link without credentials or CSRF values.
4. **AC-004 — Save confirmation and server recomputation.** In `read-only` and `analyze`, the save tool is hidden/refused. In `full`, missing or non-boolean-true `user_confirmed` fails before the API call; a tampered or newly invalid mapping is rejected by server-side recomputation and creates no partial relationships.
5. **AC-005 — Prodrug save audit and review state.** A valid confirmed mapping creates project-scoped relationships with `prodrug_of`, `needs_review`, preserved notes/source context, saved IDs, and an audit event containing actor/request/project and batch metadata. A different project cannot read them.
6. **AC-006 — Prodrug comparison scientific boundary.** After summaries and saved relationships exist, comparison returns algorithm/version, endpoint metadata, pair/complete/incomplete counts, relationship IDs, both values/units/qualifiers/states, source measurement IDs, summary IDs, review state, status, evidence boundary, and link. Exact delta is present only for exact compatible observations; censored, missing, non-exact, and unit-mismatch pairs have `delta=null` and are never subtracted. The durable default is retrievable with its persisted/audit provenance; any ephemeral result is explicitly labelled ephemeral.
7. **AC-007 — Import profile/readback coverage.** MCP can profile CSV, TSV, and the existing XLSX fixture, returns bounded file/workbook metadata, sheets, dimensions, headers, and formula policy, and refuses unsupported, unsafe, oversized, or malformed uploads with actionable errors.
8. **AC-008 — XLSX preview semantics.** MCP preview can select `sheet_name`, `header_row`, and `data_start_row`; multiple visible sheets require an explicit selection; workbook formulas are not calculated; scientific-field formulas remain blocked; metadata formulas require `formula_acknowledged=true`; source cell/sheet locations and policy version survive the response and staged record.
9. **AC-009 — Mapping overrides and staged retrieval.** MCP preview accepts valid `mapping_overrides`, reports explicit/inferred/ambiguous confidence and mapping issues, and rejects unknown or invalid overrides. A staged import retrieval tool returns every row/source location over bounded pages or continuation tokens with stable ordering, total/returned/has-more metadata, and project/import access enforcement.
10. **AC-010 — Commit review confirmation.** Commit refuses without exact `user_confirmed=true`, recommends or requires a reviewed complete preview, does not commit a staged import belonging to another project/user, preserves transactional validation, and reports accepted/rejected/inserted counts and origin without unbounded output.
11. **AC-011 — Import error/truncation integration.** Production-mode MCP integration tests cover CSV, TSV, XLSX/profile, sheet selection, mapping overrides, formula acknowledgement, invalid/oversized/unsafe files, missing import IDs, access failures, pagination, response truncation notices, and commit confirmation.
12. **AC-012 — Import browser link.** A staged import result contains a safe link that opens the correct project/import review state when supported; if the app cannot open exact staged state, the response and docs identify the limitation and provide the repository-native project/import fallback.
13. **AC-013 — Export relationship parity.** A project containing relationships produces JSON and CSV exports that both include relationship records and equivalent IDs, project scope, prodrug/active references, review/source fields, notes, and data-origin provenance; exports never include another project's records.
14. **AC-014 — Durable comparison provenance.** A persisted comparison or analysis audit can be read back and links the algorithm/version to the exact relationship IDs, endpoint key/metadata, summary IDs, source measurement IDs, project, actor/request context where applicable, and evidence boundary. Export or readback tests detect missing provenance.
15. **AC-015 — Endpoint comparison contract.** The read-only comparison tool accepts endpoint keys, search, coverage (`all`, endpoint-A, both, review or compatible documented values), limit, and offset/continuation; returns endpoint metadata, canonical units, values, qualifiers, states, missingness, source summary IDs, counts, and browser link.
16. **AC-016 — Endpoint comparison rule parity.** Unit mismatch is an explicit state and prevents numeric comparison/plotting; exact observed values can be compared; censored, missing, and review rows remain visible but are not treated as exact. Results match `production_explorer.js` selection and coverage behavior for the same fixture.
17. **AC-017 — Endpoint comparison boundedness.** Large projects return deterministic pages and explicit continuation/truncation information; output strips drawings/molblocks and does not create local browser sessions or stored records.
18. **AC-018 — Deep-link state coverage.** Tests verify safe links for staged import review, prodrug mode/endpoint, endpoint A/B/stage/coverage/search/selected state, and exact compound/measurement/summary evidence context. Existing project/run/search links remain valid.
19. **AC-019 — Link scope and encoding.** Link tests reject passwords, CSRF/session tokens, raw credentials, unbounded pair/file content, path traversal, and cross-project leakage; encoded identifiers cannot add path/query components. A signed-in browser request to a link returns the intended page or a safe access/not-found result.
20. **AC-020 — Proxy public URL.** With `SAR_MCP_BASE_URL` on the API origin and `SAR_MCP_PUBLIC_URL` on the browser/proxy origin, API calls use only the base origin and returned browser links use only the validated public origin. Invalid public URLs are rejected.
21. **AC-021 — Analysis readback.** Graph R-group and activity-cliff runs can be read back by run ID with project/allowlist scope checks, version/input/evidence metadata and bounded results. A foreign or disallowed run is refused; drawings/molblocks are absent; existing analysis types remain covered.
22. **AC-022 — Export annotation safety.** Export descriptors do not claim pure read-only/idempotent behavior when an audit event is written, the tool is not in the auto-approve set, and Kiro configuration/tests still auto-approve only genuinely safe read operations.
23. **AC-023 — Allowlist/project creation.** With a non-empty allowlist, `sar_create_project` fails before an API call and does not create a project. With no allowlist, creation succeeds under app ownership rules; the new project is listed and usable by the owner, while another project/user remains inaccessible. Tests cover allowlist and subsequent scope.
24. **AC-024 — Analysis-path integration.** Production-mode integration tests cover selectivity, cellular translation, ADME, Pareto, contradictions, information-gap, recommendation generation/readback, prediction train/apply/readback, import paths, prodrug chain, endpoint comparison, and project scoping using real app routes and representative fixtures.
25. **AC-025 — Role and authorization failures.** Viewer/editor/owner cases receive the expected safe errors for analyses, imports, relationship preview/save, comparisons, exports, and project creation; app authorization remains authoritative even if MCP arguments are manipulated.
26. **AC-026 — Governance review gates.** Tool registry/schema tests prove no MCP tool can approve/reject recommendations, approve hypotheses/designs, create claims/curated designs/series memberships, or modify project membership/roles. Read/list/generate behavior remains available where already permitted, with explicit review wording.
27. **AC-027 — Documentation accuracy.** A documentation review or automated check confirms README/MCP docs state schema 22, current tool/mode counts or generated values, CSV/XLSX/profile/staged-review behavior, prodrug tools and boundaries, browser-only governance actions, mode/reconnect/full verification, allowlist, proxy, truncation, provenance, and unverified browser/runtime limitations without unsupported claims.
28. **AC-028 — Drift detection.** A deterministic test/check fails when registered tool counts, mode availability, schema version, or documented/expected counts drift unexpectedly; the 16/19/22 model is preserved unless a documented new tier changes it.
29. **AC-029 — Browser E2E/fallback.** If an existing supported browser runtime is available, an automated production workflow exercises Start/project selection, import review, summaries, Compare, SAR, prodrug mapping/comparison, and export. If it is unavailable, a deterministic repository-native smoke suite covers the corresponding routes, rendered state/deep-link markers, static UI contracts, and API/MCP integration, and documentation names every browser interaction not verified.
30. **AC-030 — Credential round trip.** `save_credentials` writes and `read_credentials_file` recovers passwords containing quotes and backslashes exactly, rejects line breaks, preserves mode 0600/owner checks, and never emits the password in stdout/stderr/logs/errors/URLs/config. Sign-in using the round-tripped value succeeds in a local fixture.
31. **AC-031 — Kiro configuration safety.** Generated `mcp.json` uses absolute executable/script paths, contains no credentials, has the selected mode, includes only safe read tools in `autoApprove`, and excludes export audit writes, analyses, imports, project creation, relationship saves, and other writes.
32. **AC-032 — Mode/reconnect smoke.** A local smoke check demonstrates that changing `SAR_MCP_MODE` changes the advertised/available tool set after MCP reconnect/restart, `sar_status` reports the active mode, and the documented sequence works without a live external service. It also verifies public proxy links as in AC-020.

## Out of Scope

- Implementing external SSO, provider-specific identity exchange, or enterprise identity mapping beyond the existing local session/auth boundary.
- Exposing browser-only human governance writes through MCP: recommendation approval/rejection, hypothesis/design approval, curated design writes, SAR claim writes, series membership writes, project membership/role changes, or any equivalent review action, unless a separately approved policy change supersedes this requirement set.
- Inferring prodrug/active relationships from SMILES, structure similarity, ester/acid transformations, names, endpoint values, or any other heuristic.
- Treating a prodrug comparison delta, generated recommendation, prediction, pattern, or derived analysis as causal proof or experimental confirmation.
- Silent unit conversion, subtraction of censored/threshold values, conversion of missing values to zero, or loss of qualifiers/source IDs/review states.
- Removing project membership/role enforcement from the Flask application or replacing the MCP allowlist with application-only filtering.
- Unbounded model output, unbounded uploads, bypassing audit events, returning drawings/molblocks to the model, or putting secrets/CSRF/session data in URLs or logs.
- Adding an unpinned browser or transport dependency, requiring a live external service for smoke checks, or claiming browser/Kiro verification that was not executed.
- Git commits, pushes, force operations, or unrelated product/UI redesign beyond the state needed to support the specified deep links and review workflow.
