# SAR Workbench MCP Coverage Audit

## Summary answer

**Overall completeness: PARTIAL, approximately 6/10 for operating the complete web app from Kiro.** The rating covers end-to-end user journeys rather than the number of registered tools. Confidence is **high** for the MCP-to-route inventory, permission behavior, and automated-test evidence; it is **medium** for browser-level usability because this repository has no browser end-to-end qualification and this audit did not drive a real Kiro UI session.

The MCP is strong for authenticated, project-scoped lookup and persisted derived analysis. Its current catalog is live and internally consistent: 16 tools in `read-only`, 19 in `analyze`, and 22 in `full`. It can list/select projects, inspect raw measurements and summaries, standardize/search structures, run most persisted analyses, train/predict with the qualification gate, create projects, preview/commit CSV-like imports, export data, and return links into the web app. The HTTP client signs in like the browser, sends CSRF headers, re-authenticates an expired session, restricts redirects to the configured origin, and applies a project allowlist.

It is **not complete for the web app's common workflows**. The most important gap for the stated ester/prodrug-versus-acid use case is that the web app has an explicit mapping workflow—preview mappings, save project-scoped `prodrug_of` relationships, then compare all pairs—but MCP exposes only read access to saved relationships through `sar_list_records(kind="relationships")`. There is no MCP tool to preview mappings, save them, or run the prodrug-active comparison. The user must switch to the browser after importing data. Other significant gaps are XLSX/profile/sheet-selection import, full staged-import review, a structured endpoint-comparison result, interactive pattern-explorer state, and all human review writes for recommendations, hypotheses, curated designs, and series.

The smallest high-value completion is therefore not a broad API mirror. It is: (1) add explicit prodrug mapping preview/save/compare tools with separate permission and confirmation boundaries; (2) add import profile/full-preview support, including a safe binary/multipart path for XLSX; and (3) add a read-only endpoint comparison tool that preserves units, qualifiers, missingness, and source IDs. Human approval/review writes should remain browser-only unless the product deliberately changes that safety policy.

## Scope and method

This read-only audit compared the production web workflow, MCP implementation, documentation, permissions, links, and tests. Files inspected include:

- Web app and routes: `app.py`, `prodrug_engine.py`, `export_engine.py`, `auth.py`, `authorization.py`, `substructure_search.py`.
- MCP implementation: `sar_mcp_server.py`, `sar_mcp/cli.py`, `config.py`, `client.py`, `server.py`, `toolkit.py`, `tools.py`, `tools_lookup.py`, `tools_chem.py`, `tools_open.py`, `tools_run.py`, `links.py`, `shaping.py`, and `schema.py`.
- Documentation/configuration: `README.md`, `docs/WEB_APP_GUIDE.md`, `docs/MCP_GUIDE.md`, `docs/CLIENT_DEMO_RUNBOOK.md`, `docs/kiro-mcp.example.json`, `.kiro/settings/mcp.json`, `pixi.toml`.
- Tests: `tests/test_mcp_unit.py`, `tests/test_mcp_integration.py`, `tests/test_prodrug_relationships.py`, `tests/test_production_analysis_api.py`, `tests/test_production_dashboard.py`, `tests/test_deep_links.py`, `tests/test_export_api.py`, `tests/test_production_foundation.py`, `tests/test_release_gates.py`, and related engine tests.

No application code, configuration, tests, or documentation were edited. No existing web-app server was restarted. The live `pixi run mcp-check` queried the already-running loopback app; MCP integration tests create their own temporary test app/server as part of the test fixture.

## Coverage matrix

| Web-app capability | MCP support | Exact implementation, documentation, and tests | User friction or blocker |
| --- | --- | --- | --- |
| **Start, readiness, sign-in, and project selection** | **Complete for list/read; complete for blank-project creation in `full`** | Browser routes `/`, `/workspace/new`, `/login`; API `GET/POST /api/v1/projects`, `GET /api/v1/readyz`. MCP tools `sar_status`, `sar_list_projects`, `sar_create_project`; `sar_create_project` is `full`/write tier. `sar_mcp/tools_lookup.py:status/list_projects`; `sar_mcp/tools_run.py:create_project`; `app.py:create_project_api/list_projects_api`. `docs/MCP_GUIDE.md` setup and permission sections. `tests/test_mcp_integration.py:test_signs_in_with_csrf_and_runs_the_whole_workflow`, `test_an_expired_session_signs_in_again_without_losing_the_call`, `test_project_allowlist_blocks_other_projects`. | Every later call needs a `project_id`; there is no durable server-side selected-project context. `pixi run mcp-check` itself defaults to `analyze`, while the checked-in Kiro config is `full`, so CLI output can differ from the active Kiro server until the user reconnects. An allowlist filters existing projects but `sar_create_project` has no project ID and is not blocked when `SAR_MCP_PROJECT_IDS` is set, creating a possible orphan/confusing project. |
| **Evidence upload, profiling, quarantine, row review, and commit** | **Partial** | Browser/API supports CSV, TSV, and XLSX through `/api/v1/imports/profile`, `/api/v1/imports/preview`, `GET /api/v1/imports/<import_id>`, and `/api/v1/imports/<import_id>/commit`; `static/production.js` implements workbook sheet/header/data-row selection, formula acknowledgement, mapping overrides, row-by-row review, and session restoration. MCP has only `sar_import_preview` and `sar_import_commit` in `sar_mcp/tools_run.py`. The MCP preview sends JSON `csv_text` and returns mapping/counts plus `rows_sample[:25]`; it has no `sar_import_profile`, no `sar_get_import`, no binary/multipart XLSX path, and no sheet/mapping/formula arguments. `docs/WEB_APP_GUIDE.md` sections 3–4 describe the wider browser workflow; `docs/MCP_GUIDE.md` explicitly describes the MCP tool as CSV text. Integration coverage is `tests/test_mcp_integration.py:test_signs_in_with_csrf_and_runs_the_whole_workflow` and `test_commit_requires_explicit_confirmation`. | A Kiro user cannot complete the documented XLSX path or inspect all quarantined rows/source-cell locations before committing. The `user_confirmed=true` guard is good, but the model may have seen only 25 sampled rows. There is also no MCP link to the staged import review page, so the user must manually return to Start. CSV is usable; the complete web import surface is not. |
| **Build/refresh comparison-ready summaries** | **Complete for project-level summaries** | Web `POST /api/v1/measurement-summaries/project`; MCP `sar_run_analysis` with `analysis="measurement_summaries"` and optional `aggregation_method`/`analysis_version`; read tools `sar_list_endpoints` and `sar_list_result_summaries`. `sar_mcp/tools_run.py:ANALYSES/run_analysis`; `app.py:create_project_measurement_summaries_api`; `docs/WEB_APP_GUIDE.md` section 5. The integration test runs summaries and verifies endpoint keys. | The user must know to run summaries after every import; the MCP endpoint-list response gives an actionable note when none exist. There is no dedicated `sar_build_summaries` name, but the existing tool is functional. |
| **Compare two endpoints in the web Compare workspace** | **Partial; browser-link fallback only** | Browser `/workspace/explore` plus `static/production_explorer.js` provides endpoint A/B selection, coverage filters, search, result-state rendering, unit mismatch handling, source links, browser-local sessions, and client-side CSV export. MCP has `sar_list_endpoints`, `sar_list_result_summaries`, and `sar_open_in_workbench(page="explore", endpoint_a=..., endpoint_b=...)`; `_assay_id` in `sar_mcp/tools_open.py` builds `summary|<key>|<unit>` URL state. `docs/WEB_APP_GUIDE.md` section 6 and link table; `tests/test_mcp_unit.py:test_open_in_workbench_builds_only_valid_combinations`, `tests/test_mcp_integration.py:test_every_link_the_tools_return_opens_the_right_page_in_the_app`. | There is no structured MCP comparison result that enforces the browser's unit/qualifier/missingness rules. The model must fetch two summary sets and reason over them or send the user to the browser. Browser-local saved sessions and explorer CSV export are not project records and are unavailable from MCP. |
| **Discover SAR series** | **Partial** | Web `POST /api/v1/sar/discover` supports the ordinary `discover` and `selected` series modes. The browser's separate `prodrug` radio mode is implemented in `static/production_sar.js` through the compound-relationship preview/save/compare routes, not through `/api/v1/sar/discover`; MCP `sar_discover_series` calls `/api/v1/sar/discover` and its schema allows `discover` and `selected` only. `sar_mcp/tools_chem.py:discover_series`; `app.py:discover_sar_series_api`; `docs/WEB_APP_GUIDE.md` section 7; `tests/test_mcp_integration.py` verifies discover and the web link. | Ordinary scaffold discovery works from Kiro. The prodrug mode is not available through this tool (and is separately absent as a relationship tool). MCP cannot save a reviewed series: browser uses `POST /api/v1/series` and `/api/v1/series/<id>/versions`, while MCP intentionally exposes only `sar_list_records(kind="series")`. |
| **Persisted pattern analyses** | **Mostly complete for calculation; partial for later readback** | `sar_run_analysis` maps to measurement summaries, properties, MMP, graph R-group, pharmacophore R-group, activity cliffs, selectivity, cellular translation, ADME, Pareto, contradictions, information gap, and recommendation generation in `sar_mcp/tools_run.py:ANALYSES`. `sar_train_prediction_model` and `sar_predict_compounds` cover the prediction paths. `app.py` has the corresponding POST routes. The tool returns a stored result and a run/page link. | `sar_get_analysis_run` in `sar_mcp/tools_chem.py:ANALYSIS_PATHS` supports MMP, pharmacophore R-group, selectivity, properties, cellular translation, ADME, Pareto, contradictions, and information gap, but not graph `rgroup` or `activity_cliffs`. The app itself has no corresponding GET route for those two result types; the immediate MCP response is the durable readback available. The MCP integration test exercises summaries, R-group, pharmacophore R-group, activity cliffs, information-gap linking, and prediction-related catalog behavior, but not every analysis type end to end. |
| **Interactive Find patterns explorer** | **Partial; link to browser only** | `app.py:_production_pattern_explorer`, `static/production_patterns.js`, and `templates/production_analysis.html` provide one-site/combine-site R-group views, activity landscape, property space, selections, local sessions, and presentation-only exploration over persisted results. `sar_open_in_workbench(page="analysis")` can pin a compound/run and open the pattern explorer; tests cover deep links and rendered markers. | No MCP result exposes the explorer's derived matrix/landscape/session state. The assistant can run persisted analyses and tell the user to open the link, but cannot reproduce the complete interactive workflow in Kiro. |
| **Explicit prodrug/active-form relationship mapping and bulk comparison** | **Partial only for reading saved mappings; functionally missing for the common path** | Web/API has `GET/POST /api/v1/compound-relationships`, `POST /api/v1/compound-relationships/preview`, and `POST /api/v1/analysis/prodrug-comparison`; `prodrug_engine.py:preview_relationships/save_relationships/list_relationships/compare_relationships` resolves project IDs, flags ambiguity/duplicates/self-pairs, stores `needs_review`, preserves source summary IDs, and refuses deltas for missing/censored/unit-mismatch/non-exact inputs. Browser UI is in `templates/production_sar.html` and `static/production_sar.js`. MCP only reaches the GET route through `sar_list_records(kind="relationships")`; there are no preview/save/compare tools. `tests/test_prodrug_relationships.py` verifies the complete browser/API path, but no MCP test can exercise it. | This is the principal blocker for the requested ester-versus-acid workflow. After importing measurements and building summaries through Kiro, the user must switch to the browser to paste/upload the pair list, review it, save it, and compare all pairs. The MCP has no direct prodrug comparison result and `sar_open_in_workbench` has no URL state to open the pair mode with an endpoint. |
| **Recommendations / evidence-gap generation** | **Partial, intentionally review-gated** | MCP `sar_run_analysis(analysis="information_gap")` and `sar_run_analysis(analysis="recommendations")` call the analysis and generation routes; `sar_list_records(kind="recommendations")` reads generated items and links to Choose next. `docs/MCP_GUIDE.md` explicitly says recommendations require human review. Browser `/api/v1/recommendations/<id>/review` and `static/production.js` provide approve/reject. | Kiro can generate and inspect recommendations, but cannot approve/reject them. This prevents an entirely Kiro-driven review workflow, but is an intentional safety boundary, not an accidental missing route. The handoff link is generic `/workspace/designs?project_id=...`; cited evidence links are not returned as direct MCP browser links. |
| **Hypotheses and curated design candidates** | **None for writes; read/list and stateless structure proposal available** | Browser writes `POST /api/v1/hypotheses` and `POST /api/v1/designs`; `static/production_designs.js` validates a proposed structure, selects evidence, and saves an untested candidate. MCP exposes `sar_propose_substituent` (stateless), `sar_list_records(kind="claims"|"designs")`, `sar_open_in_workbench`, and readback through exports, but no hypothesis/design/claim creation. `tests/test_mcp_unit.py:test_review_gates_are_never_exposed` explicitly rejects tool names containing hypothesis/design/claim/review tokens. | A user can ask Kiro for a proposed structure and evidence-based wording, but must use the web app to save a hypothesis/design and human-review it. This should remain a deliberate boundary unless the product changes its governance policy. |
| **Prediction train/apply/read** | **Complete for the exposed, qualification-gated path** | MCP `sar_train_prediction_model`, `sar_predict_compounds`, and `sar_list_records(kind="prediction_models")`; app routes `/api/v1/predictions`, `/train`, and `/<model_id>/predict`. Tool schema deliberately does not expose threshold overrides; response interpretation requires a qualified model and applicability domain. `tests/test_mcp_unit.py:test_review_gates_are_never_exposed` checks fixed thresholds; `tests/test_mcp_integration.py` covers catalog/read-only boundaries, while prediction engine tests cover the app. | The model must call list/read tools to inspect qualification before using a result. That is a sound safety sequence, but there is no dedicated MCP `get_prediction_model` tool; list-record output is the readback path. |
| **Project JSON/CSV exports** | **Partial** | Web `GET /api/v1/exports/project/<project_id>?format=json|csv`; MCP `sar_export_overview` fetches JSON, returns section counts or a bounded section sample, and returns a browser `download_link` for CSV. `export_engine.py:build_project_export/export_project_csv`; `sar_mcp/tools_lookup.py:export_overview`; docs and `tests/test_export_api.py`, `tests/test_mcp_unit.py:test_oversized_results_are_cut_and_announced`. | MCP does not return a complete JSON bundle as a model result and has no JSON download link; it relies on bounded sections or browser download. The tool is tiered as `read` even though every export inserts an audit event; the Kiro config manually excludes it from `autoApprove`, but its MCP descriptor still advertises `readOnlyHint: true`. More seriously, JSON export includes `compound_relationships`, while `export_project_csv()` never iterates that section, so the web CSV and MCP CSV link omit saved prodrug mappings despite the web guide claiming relationships are included in export. |
| **Authentication, CSRF, project roles, allowlist, and reconnect** | **Mostly complete and well tested** | `sar_mcp/client.py:SarClient` probes `/login`, submits the form CSRF token, stores the session cookie, sends `X-CSRF-Token`, retries once after auth/CSRF expiry, limits wrong-password attempts to three, and follows only same-origin redirects. `sar_mcp/config.py:Settings` enforces owner-only credential files, origin-only URLs, off-host HTTP refusal, modes, bounds, and `SAR_MCP_PROJECT_IDS`. App auth and roles are in `auth.py`, `authorization.py`, and `app.py:enforce_production_access`. `tests/test_mcp_unit.py` covers URL/credential/redirect/allowlist/config behavior; integration covers sign-in, wrong password, expired session, no-login app, and real stdio. | The credential/setup workflow requires a manual Kiro MCP reconnect after saving credentials or changing the mode; the docs state this but do not provide a Kiro-specific verification command beyond `sar_status`. External SSO is not implemented by the app (`README.md` release boundary), so MCP inherits the local-login boundary. The allowlist's create-project behavior is not tested and should be made explicit. |
| **Links back to the browser** | **Good for existing pages; incomplete for staged imports and prodrug mode** | `sar_mcp/links.py:Links`, `sar_mcp/tools_open.py:open_in_workbench`, and `SAR_MCP_PUBLIC_URL` support browser/proxy address separation. Integration test `test_every_link_the_tools_return_opens_the_right_page_in_the_app` opens returned links against a real app and checks project isolation. | Links generally open a page, not an exact evidence row. `sar_get_compound`, `sar_list_measurements`, and `sar_list_result_summaries` link to generic evidence/summaries pages without compound/measurement/summary query state. There is no staged-import deep link or prodrug mapping/endpoint deep link. |

## Functional coverage gaps

### F1 — Prodrug mapping and comparison are not covered by MCP (P0)

The browser workflow is explicit by design: it does not infer an ester/acid relationship from structures. `prodrug_engine.py:preview_relationships` validates registration IDs/compound IDs/names, detects ambiguity, duplicate pairs, self-pairs, and missing compounds; `save_relationships` writes project-scoped relationships with `review_status='needs_review'` and an audit event; `compare_relationships` joins the latest summaries and returns exact deltas only when both observations are compatible exact values with matching units. This is exactly the scientific boundary needed for the user's use case.

MCP's `RECORDS` map includes `"relationships": ("/api/v1/compound-relationships", "sar")`, so saved relationships can be read. Nothing in `sar_mcp/tools_run.py`, `tools_lookup.py`, or `tools_chem.py` invokes the preview, save, or comparison routes. The 22-tool catalog confirms the omission at runtime. The result is not merely a missing convenience: the common Kiro journey stops before the relationship is created, so no prodrug/active comparison can be returned.

### F2 — MCP import is narrower and less reviewable than the web import

The web supports profiling an XLSX before sheet selection, selecting a sheet/header/data-start row, formula acknowledgement, mapping overrides, complete row-level validation, source-cell provenance, and restoring a staged preview. MCP `sar_import_preview` accepts only a string and filename, and intentionally returns the first 25 rows. `sar_import_commit` has a confirmation flag but no way for the model to request the remaining review rows. For a CSV with a clean long-format schema this is workable; for the named `.xlsx` source it is not an end-to-end replacement for the web app.

### F3 — Endpoint comparison is not a first-class MCP result

The Compare workspace is a substantial client-side workflow, not a single route: it chooses two summary/raw contexts, filters by coverage, handles units and result states, and carries source links. MCP exposes enough data to let a capable model calculate a comparison, and `sar_open_in_workbench` can set `endpoint_a`, `endpoint_b`, and `stage=compare`, but there is no tool contract that makes the comparison rules and output explicit. This leaves more room for a model to mishandle qualifiers or compare incompatible units.

### F4 — Persisted analysis write coverage exceeds analysis readback coverage

The MCP can run all entries in `ANALYSES`, but later readback is incomplete for graph R-group and activity-cliff results because `sar_get_analysis_run` has no corresponding analysis enum/path. The app has no GET route for those two result payloads either, so this is an app/API design gap exposed by the MCP. Immediate tool responses are useful, but multi-turn Kiro work should not depend on the model retaining a large prior result.

### F5 — Human-governed writes are intentionally browser-only

The browser can save hypotheses, review generated recommendations, save curated series, create series versions, and save design candidates. MCP deliberately omits these writes. `tests/test_mcp_unit.py:test_review_gates_are_never_exposed` verifies the policy. This means the web app cannot be operated entirely from Kiro for governance actions, but adding these writes would be a policy change rather than a simple coverage fix. The current design is defensible: generated recommendations and designs are not experimental confirmation, and review status belongs to a human.

### F6 — Export provenance is inconsistent for prodrug relationships

`export_engine.py:build_project_export` includes `compound_relationships` in JSON and row counts. `export_project_csv` writes compounds, structures, measurements, summaries, analyses, pharmacophore assignments, claims, designs, series, memberships, and recommendations, but no `compound_relationships` loop. The web guide (`docs/WEB_APP_GUIDE.md`, section 9) says relationship mappings are included in the export. The JSON path is therefore stronger than the CSV path, and the MCP's `download_link` points to the incomplete CSV path.

The prodrug comparison response itself contains algorithm version, relationship IDs, summary IDs, units, qualifiers/states, and an evidence boundary, but `compare_relationships` does not persist an analysis run or comparison audit event. The mapping save is audited; the derived comparison can disappear unless the user captures or exports the response. This is a provenance gap for any future MCP compare tool.

## UX and discoverability gaps

1. **Mode confusion between CLI and Kiro configuration.** `.kiro/settings/mcp.json` is configured for `full`, but `pixi run mcp-check` without `SAR_MCP_MODE` reports `analyze`. The CLI does not load the Kiro JSON, and the docs say to reconnect after changing settings. A user can reasonably conclude that the mode change failed or that the new tools are missing. `sar_status` is the right runtime check, but the setup guide should explicitly show `SAR_MCP_MODE=full pixi run mcp-check` for a full-mode check and state that Kiro must be reconnected.
2. **The import preview has no model-facing full-review path.** Returning a sample plus a commit instruction is not enough for a large or ambiguous upload. The browser has a page and session storage for this; MCP has neither a preview readback tool nor a link that opens the staged batch.
3. **Endpoint keys are technical and must be discovered.** The user must call `sar_list_endpoints` and pass values such as `IC50:import-v1`; the browser labels them more naturally. This is acceptable for an expert tool but a dedicated compare tool should accept endpoint keys and return human labels/unit metadata.
4. **No prodrug deep link.** `sar_open_in_workbench` can open the SAR page, but it cannot set the `prodrug` radio mode, endpoint, or mapping text. The user must click through the page and paste/upload the mapping again.
5. **Evidence links are page-level rather than record-level.** MCP results contain IDs/source rows, but returned links from `sar_get_compound`, `sar_list_measurements`, and `sar_list_result_summaries` do not identify the selected compound or row in the URL. A human can still inspect the raw data in the tool response, but browser verification takes manual searching.
6. **Documentation does not describe the boundary where it matters most.** `docs/MCP_GUIDE.md` lists 22 tools and says relationship records can be read, but it does not explicitly say that creating/mapping/comparing prodrug relationships is browser-only. The web guide documents the prodrug workflow as if the MCP link path were generally available. This is likely to produce the exact expectation mismatch seen in the prior session.
7. **Repository documentation has drift independent of MCP.** `README.md` says migrations apply through schema 20 and reports `pixi run test` as 121 tests. The audited runtime reports schema 22 and 200 passing tests. The MCP live check also reported schema 22. This weakens setup and release-gate trust even though it does not change MCP execution.

## Safety and permission assessment

### Strengths verified in code/tests

- **Mode gating is explicit.** `sar_mcp/toolkit.py:TIERS` maps read/analyze/write tools to `read-only`, `analyze`, and `full`; `McpServer` hides unavailable tools and rejects direct calls to hidden names. Runtime catalog counts are 16/19/22.
- **Commit confirmation is explicit.** `sar_import_commit` refuses to call the API unless `user_confirmed` is exactly true. Kiro's generated `autoApprove` list contains only read tools and excludes exports, analyses, and writes.
- **Review writes are not exposed.** No tool name contains review/approve/reject/claim/design/member/hypothesis tokens; this is tested. The app still enforces editor/owner roles if a user uses the browser/API.
- **Authentication is shared with the web app.** `SarClient` uses form login and the app's CSRF token/session cookie, and `app.py` enforces membership and role on production routes. Wrong passwords stop after three attempts; expired sessions are re-established and the rejected request is retried once.
- **Transport boundaries are conservative.** Credentials/data are refused over off-host plain HTTP by default; redirects are same-origin only; credentials files must be owner-only; URLs are origin-only; tool IDs and path segments are schema-validated.
- **Model-output safety is deliberate.** `shaping.py` strips SVG/molblocks, limits response size, and announces list truncation. Server instructions require treating imported names/notes as data, preserving qualifiers/missingness, and avoiding causal language.
- **Project scope is applied at both MCP and app layers.** `SAR_MCP_PROJECT_IDS` filters project listing and blocks top-level project calls; app membership remains the real boundary. `get_analysis_run` additionally checks the run owner against the allowlist.

### Safety/permission gaps or ambiguities

1. **Export metadata is semantically misleading.** `sar_export_overview` is a read-tier tool and therefore has `readOnlyHint: true`/`idempotentHint: true`, but the export route writes an audit record. The generated Kiro config compensates by excluding it from `autoApprove`, but another MCP client could trust the descriptor and auto-approve it. Either model the audit side effect explicitly or make the permission annotation and tier policy consistent.
2. **Allowlist behavior for project creation is undefined.** The top-level allowlist check only runs when `arguments.project_id` is present. `sar_create_project` has no project ID, so a server restricted to specific IDs can still create a new project that is not in the allowlist, then cannot use it for subsequent project-scoped tools. This is primarily a scope/UX defect, but it should be fail-closed and tested.
3. **Data-sharing warning is present but not enforced.** Both the README and MCP guide correctly state that tool results are sent to the model provider. No code can determine whether a project is permitted to leave the machine; the user must configure project IDs and make that decision. This is an operational boundary, not a missing API.
4. **Credential parser edge cases are not verified.** Tests cover permissions, ownership, quotes around ordinary values, and environment precedence, but not passwords containing embedded quotes/backslashes. `cli.save_credentials` writes a quoted line without escaping embedded quotes. This was not exercised in the audit and should be treated as a P2 hardening/test item rather than a confirmed production failure.

## Provenance and scientific-integrity assessment

The core scientific boundaries are strong and consistently communicated. Raw measurements remain separate from summaries; qualifiers and missingness survive tool shaping; analysis results carry `data_origin`, algorithm/input metadata, and source IDs; prodrug comparisons refuse to turn censored or unit-mismatched values into exact deltas. The server's `INSTRUCTIONS` and the web guide explicitly prohibit causal claims from calculated associations.

The weak points are workflow provenance rather than calculation logic:

- The MCP import result is sampled, so a model may not see every source-cell warning before a user confirms a commit.
- A prodrug mapping is audited when saved, but the comparison result is not a stored run/audit event and has no MCP tool today.
- JSON export carries relationships; CSV export does not, despite the documentation statement.
- Generic browser links do not carry enough state to take a reviewer directly to the exact measurement or pair row.
- `sar_export_overview` writes an audit event but presents itself as a pure read operation in MCP annotations.

## Testing assessment

### Evidence that passed

- `pixi run python -m pytest -q tests/test_mcp_unit.py tests/test_mcp_integration.py` → **33 passed in 9.15s**.
  - Covers JSON-RPC/stdio behavior, 16/19/22 mode visibility, schemas, output truncation/drawing removal, project allowlist, credentials/transport/redirects, import confirmation, generated Kiro config, API errors, sign-in/CSRF, wrong-password lockout, session expiry, read-only blocking, real analyses/searches, and browser-link opening.
- `pixi run python -m pytest -q tests/test_prodrug_relationships.py tests/test_production_analysis_api.py tests/test_production_dashboard.py tests/test_deep_links.py` → **20 passed in 3.73s**.
  - Verifies the web/API prodrug preview/save/compare journey, invalid/duplicate mappings, project scoping, browser SAR UI markers, production pages, analysis routes, and deep links.
- `pixi run test` → **200 passed in 21.75s**.
  - This is the repository's real Pixi test command from `pixi.toml`; it passed without source changes.
- `pixi run mcp-check` → **OK** against the configured loopback app: app ready at schema 22, sign-in required, credentials accepted, three projects visible. It ran with the CLI's default `analyze` mode, not the checked-in Kiro setting's `full` mode.
- A live catalog check using the Pixi interpreter reported the exact 22 names and mode counts described above.

### What the tests do not verify

- No MCP test covers prodrug relationship preview, save, or comparison because no such tools exist.
- No MCP test covers XLSX bytes, workbook profiling, sheet selection, mapping overrides, formula acknowledgement, or full staged-preview retrieval.
- No MCP test covers a structured endpoint comparison or interactive Compare session.
- No MCP test covers `SAR_MCP_PROJECT_IDS` combined with `sar_create_project`.
- No test asserts that the MCP descriptor's export side effect is represented accurately.
- No test asserts that CSV export contains `compound_relationships`; current source inspection shows it does not.
- The MCP integration "whole workflow" is broad but not exhaustive: it does not execute all selectivity, cellular-translation, ADME, Pareto, contradiction, recommendation-review, hypothesis, design, series-save, membership, or prodrug journeys through MCP.
- The repository README says browser end-to-end verification and qualified scientific review remain outstanding. This audit did not run a browser automation suite or a real Kiro approval/reconnect UI session.
- External SSO/identity-provider integration and remote HTTPS/proxy behavior were not verified here; the repository documents external identity as a release boundary.

## Smallest high-value additions

These additions should be implemented as narrow workflow tools rather than exposing every web route.

### A. Prodrug mapping and comparison tools (P0)

1. **`sar_preview_prodrug_relationships`**
   - **Permission:** `analyze` tier (or a new non-mutating editor-review tier). The app requires editor access to preview mappings even though the preview itself does not save authoritative records; keep that role check.
   - **Input:**
     ```json
     {
       "project_id": "prj_...",
       "pair_text": "prodrug_id,active_id,notes\nPRO-001,ACT-001,ester/acid\n",
       "pairs": [{"prodrug_id": "PRO-001", "active_id": "ACT-001", "notes": "ester/acid"}],
       "relationship_type": "prodrug_of"
     }
     ```
     Require exactly one of `pair_text` or `pairs`; cap rows at the existing 5,000 limit; reject unknown relationship types.
   - **Output:** `total_count`, `valid_count`, `invalid_count`, fully resolved valid rows with internal/registration IDs and notes, invalid rows with line/tokens/errors, `data_origin="imported"`, and a `web_link` to the current SAR page. Do not return SVGs to the model. Preserve ambiguity/duplicate/self-pair errors exactly.
   - **Confirmation:** no save and no confirmation prompt beyond normal tool approval; the response must say that nothing was saved and must be the required precursor to save.
   - **Link fallback:** current `/workspace/sar?project_id=...` plus an explicit browser instruction to select **Compare prodrug–active pairs**. Prefer adding URL state such as `sar_mode=prodrug` and `endpoint_key` later.

2. **`sar_save_prodrug_relationships`**
   - **Permission:** `full`/write tier only; never auto-approve.
   - **Input:** the same mapping plus `user_confirmed: boolean` and optionally a `preview_fingerprint` if the app begins persisting preview identity. Refuse unless `user_confirmed=true`; recompute validation server-side and refuse any invalid row, matching the browser's `canSave` rule.
   - **Output:** `saved_count`, relationship IDs, resolved pair summaries, `review_status="needs_review"`, audit event ID or audit metadata, `data_origin="imported"`, and a SAR-page link. The response must state that the mapping is explicit/project-scoped and still requires human review; it must never imply the ester/acid relationship was inferred or causally validated.
   - **Confirmation boundary:** the MCP server should require the user to review the preview in the conversation before calling this tool. Keep out of `autoApprove`.

3. **`sar_compare_prodrug_pairs`**
   - **Permission:** `read-only` tier if it remains a non-persisting calculation; viewer access is enough. If comparison is changed to persist a versioned run/audit event, move it to `analyze` and say so in the tool description.
   - **Input:** `{ "project_id": "...", "endpoint_key": "IC50:import-v1" }`, optionally relationship IDs or a limit for very large projects.
   - **Output:** `algorithm_version`, endpoint name/key, pair/complete/incomplete counts, each prodrug and active result with value/unit/state/qualifier/source measurement IDs, comparison status (`complete`, `missing_prodrug`, `missing_active`, `prodrug_non_exact`, `active_non_exact`, `unit_mismatch`), delta only for exact compatible results, direction/effect, `evidence_boundary`, and a `web_link` to SAR. Never average or subtract threshold values.
   - **Link fallback:** add a browser deep-link state so the returned link can open the prodrug mode and endpoint; until then, return `/workspace/sar?project_id=...` with the exact click instruction.

4. **Persist or audit the comparison.** The existing `compare_relationships` result is richly annotated but not stored as an analysis run. Either create a `prodrug_comparison` run with relationship IDs, endpoint key, summary IDs, and algorithm version, or explicitly document that the result is an ephemeral derived response and add a JSON export section for comparison outputs.

### B. Complete import review tools (P0/P1)

1. **`sar_import_profile`**
   - **Permission:** `full` because it accepts laboratory file content in the import workflow, even though profiling alone creates no authoritative measurements.
   - **Input/output:** `filename` plus a bounded file payload. For CSV/TSV, retain `csv_text`; for XLSX, add a bounded `content_base64` or a multipart request implementation in `SarClient`. Return `file_format`, byte size, sheet names/dimensions, warnings, and formula policy. Never calculate formulas.
2. **Enhance `sar_import_preview`**
   - Accept `sheet_name`, `header_row`, `data_start_row`, `mapping_overrides`, and `formula_acknowledged`; return the full mapping issues and bounded row pages with source locations, errors, warnings, and a continuation token/offset rather than only `rows_sample[:25]`.
3. **`sar_get_import_preview`**
   - **Permission:** available in `full` for the staged-import workflow, with the same app membership/import-access checks.
   - **Input:** `project_id`, `import_id`, `offset`, `limit`.
   - **Output:** staged status, source document metadata, mapping, counts, row page, and source-cell locations; include a Start/evidence link if the UI can accept an import ID. This makes `sar_import_commit(user_confirmed=true)` meaningful for large/ambiguous files.

### C. Structured endpoint comparison (P1)

Add **`sar_compare_endpoints`** as a `read` tool:

```json
{
  "project_id": "prj_...",
  "endpoint_a": "IC50:import-v1",
  "endpoint_b": "Cellular:import-v1",
  "coverage": "all|endpoint-a|both|review",
  "q": "POS-",
  "limit": 200,
  "offset": 0
}
```

Return endpoint metadata, canonical units, explicit `comparable`/`unit_mismatch` state, per-compound A/B values, qualifiers, missingness, source summary IDs, and counts. Use the same exact/censored/missing policy as `production_explorer.js`; do not silently compare incompatible units. Include a `web_link` generated through `sar_open_in_workbench`/`Links.page(..., endpoint_a, endpoint_b, stage="compare")`.

### D. Readback and deep-link improvements (P1)

- Add a general stored-analysis read path for graph R-group and activity-cliff results, or add app GET routes and an MCP `sar_get_analysis_result` with `analysis`, `run_id`, and project-scope validation.
- Extend `sar_open_in_workbench`/browser URL state for `sar_mode=prodrug`, import review, exact evidence compound/measurement/summary, and endpoint comparison. Keep tokens/passwords/CSRF values out of URLs.
- Add record-level evidence links or stable page query/fragment handling so `sar_get_compound` and summary tools open the referenced compound/row rather than a generic page.

## Prioritized recommendations

### P0 — unblock the stated Kiro workflow

1. **Add the three prodrug tools** (preview, explicit-confirm save, read-only compare) and test them against the real production-mode app with the same fixture used by `tests/test_prodrug_relationships.py`. This is the highest-value gap because the web workflow already exists and the user's question depends on it.
2. **Make the save/compare provenance boundary explicit.** Persist or audit comparison results, preserve relationship and source-summary IDs, and add the missing relationship section to CSV export or change the documentation to say relationships are JSON-only.
3. **Add complete staged-import review before any new commit tool.** At minimum, expose `GET /api/v1/imports/<id>` through MCP with paging; for the named XLSX workflow, add profile and bounded binary/multipart support rather than claiming full web coverage.

### P1 — make Kiro results trustworthy and easy to verify

1. Add `sar_compare_endpoints` with strict units/qualifiers/missingness and source IDs.
2. Add exact evidence/prodrug/import deep links and a browser URL state for prodrug mode.
3. Add R-group/activity-cliff result readback or general analysis result API.
4. Correct the export descriptor side-effect metadata and fail closed when a configured project allowlist is combined with `sar_create_project`; add tests for both.
5. Add MCP integration tests for the prodrug chain, staged import review, all remaining analysis types, role failures, project allowlists, and the CSV/JSON export relationship discrepancy.

### P2 — documentation and hardening

1. Update `README.md` schema/test counts to match schema 22 and 200 passing tests, and make the release/test count generated or otherwise less likely to drift.
2. Add an explicit MCP coverage table to `docs/MCP_GUIDE.md`: CSV-only current import, no XLSX/profile, no prodrug mapping/compare, browser-only human review writes, and the exact Kiro reconnect/mode verification sequence.
3. Add an automated browser E2E gate for Start → import review → summaries → Compare → SAR → prodrug mapping → export. The repository currently acknowledges this gate as outstanding.
4. Test credential-file serialization with embedded quote/backslash characters and harden escaping if needed.
5. Add a Kiro-client smoke check for generated `mcp.json`, actual `autoApprove` behavior, reconnect after mode changes, and `SAR_MCP_PUBLIC_URL` behind a proxy.

## Evidence appendix

### Tool catalog and permissions

- `sar_mcp/tools.py:all_tools` registers four tool groups.
- `sar_mcp/toolkit.py:TIERS` defines `read`, `analyze`, and `write` availability; `Tool.descriptor` derives MCP annotations from the tier.
- `sar_mcp/tools_lookup.py:TOOLS` contains 8 lookup/export tools.
- `sar_mcp/tools_chem.py:TOOLS` contains 7 chemistry/series/records/readback tools.
- `sar_mcp/tools_open.py:TOOLS` contains `sar_open_in_workbench`.
- `sar_mcp/tools_run.py:ANALYSES` and `TOOLS` contain 3 analyze tools and 3 full/write tools.
- Live catalog command:
  ```bash
  pixi run python -c 'from sar_mcp.tools import all_tools; from sar_mcp.config import MODES; print({mode: sum(t.available_in(mode) for t in all_tools()) for mode in MODES}); print([t.name for t in all_tools()])'
  ```
  Result: `{'read-only': 16, 'analyze': 19, 'full': 22}`; names are the 22 tools listed in the MCP guide plus the analysis/import tools described above.

### Web route coverage inspected

- Project/auth/readiness: `app.py:create_project_api`, `list_projects_api`, `readyz`, `enforce_production_access`.
- Import: `profile_import_api`, `preview_import_api`, `get_import_api`, `commit_import_api`.
- Relationship workflow: `list_compound_relationships_api`, `preview_compound_relationships_api`, `save_compound_relationships_api`, `compare_prodrug_relationships_api`.
- Summaries and analyses: `create_project_measurement_summaries_api`, `create_*_analysis_api` handlers from MMP through contradictions/information gain, plus prediction routes.
- Human review writes: `review_generated_recommendation_api`, `create_design_candidates_api`, `_save_production_hypothesis`, `create_series_api`, `create_series_version_api`, and member management.
- Export: `export_project_api`; implementation discrepancy is in `export_engine.py:export_project_csv`.
- Browser state/workflows: `static/production.js`, `static/production_sar.js`, `static/production_explorer.js`, `static/production_patterns.js`, `static/production_designs.js`, and templates under `templates/production_*.html`.

### Documentation inspected

- `README.md`: production boundary, MCP modes, API inventory, scientific integrity rules, release gates.
- `docs/WEB_APP_GUIDE.md`: Start → evidence → summaries → Compare → patterns → Choose next; explicit prodrug mapping workflow and link parameters.
- `docs/MCP_GUIDE.md`: setup, 16/19/22 modes, current 22 tools, link behavior, safety, settings, troubleshooting, tests.
- `docs/CLIENT_DEMO_RUNBOOK.md`: complete browser demo including a prodrug demo and human review boundaries.
- `docs/kiro-mcp.example.json` and `.kiro/settings/mcp.json`: absolute interpreter/script paths, base URL, mode, read-only auto-approval list.
- `pixi.toml`: authoritative commands are `pixi run test`, `pixi run mcp-check`, `pixi run mcp-credentials`, and related Pixi tasks.

### Explicitly not verified

- No external web sources were used.
- No browser automation or manual Kiro MCP Server UI session was run.
- No new app server was started or existing server restarted by this audit.
- No real `.xlsx` file was sent through MCP; source inspection and web tests establish that the current MCP schema only accepts text.
- No external SSO, remote HTTPS proxy, model-provider data-sharing policy, or proprietary data qualification was tested.
- The live check verified the configured local credentials and loopback app, but its `analyze` mode was the CLI default; it did not prove the currently connected Kiro process had reloaded the repository's `full` mode configuration.
