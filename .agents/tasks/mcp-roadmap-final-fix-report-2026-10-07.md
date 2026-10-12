# MCP Roadmap Final Fix Report

Date: 2026-10-07

## Changed files

- `import_pipeline.py`: staged import batch and accepted-row reads now occur inside the same repeatable-read transaction that commits the batch. Review counts are checked against that snapshot before any compound, measurement, row-status, batch-status, or audit mutation.
- `sar_mcp/links.py`: added a shared browser Compare endpoint-id builder and result-link builder. Canonical `summary|<endpoint>|<unit>` state is emitted only when endpoint metadata has one safe, unique unit; otherwise the link is a safe unselected Compare link.
- `sar_mcp/tools_lookup.py` and `sar_mcp/tools_open.py`: endpoint comparison and workbench Compare links use the shared builder. Both sectioned and unsectioned exports retain the safe download link, export identifiers, provenance policy, explicit audit-side-effect handoff, and bounded pagination metadata (`offset`, `returned`, `has_more`, `next_offset`). Sectioned responses also include bounded rows and continuation state.
- `tests/test_security_adversarial.py`: added changed-staged-row coverage proving a commit cannot use stale accepted rows or mark the batch committed after the snapshot has changed.
- `tests/test_mcp_unit.py`: added endpoint deep-link regression coverage for canonical state and safe ambiguous-unit fallback, plus sectioned export handoff/pagination coverage.
- `tests/test_mcp_integration.py`: added real MCP `sar_get_analysis_run` readback calls for graph R-group and activity-cliff runs, and sectioned export metadata assertions.
- `tests/test_mcp_catalog.py`, `README.md`, and `docs/MCP_GUIDE.md`: added a checked current test-count marker and drift test. Both documents report the current 224-test collection consistently.

## Validation

- Focused affected suites: **74 passed** covering import snapshot safety, endpoint comparison/deep links, exports, MCP unit/integration/readback, import cleaning, adversarial safety, and catalog/documentation drift.
- Full suite: `pixi run test` — **224 passed**.
- Catalog/schema derivation: schema **22**; **17 read-only / 22 analyze / 28 full**; fingerprint `109a86ce9b7ab797d24c45f0d6c7b5b2f37f91e5ca016f588ea82e94a614174b`.
- Existing loopback validation: `pixi run mcp-check` passed against the already-running `http://127.0.0.1:5001` app without restart or overwrite; it reported analyze mode, schema 22, sign-in required, and 3 visible projects.
- `git diff --check` passed.
- `test/CellLinePanel_pIC50s.xlsx` remained SHA-256 `21e55dc33b2f625d68b6fc62285bd8263a560e831aacd177f8fd9dc094ff8b8d`. The pre-existing generated CSVs `test/prodrug_long_measurements.csv` and `test/prodrug_pairs.csv` remain unmodified/untracked.
- No commit, push, restart, worktree, or server overwrite was performed.

## Limitations

- Browser/Kiro UI end-to-end interactions were not executed; native Flask/static and MCP integration coverage remains the verification boundary.
- No live PostgreSQL server or live concurrent PostgreSQL transaction test was available; transaction behavior is covered by deterministic SQLite changed-snapshot/concurrent-commit tests and transaction-mode checks.
