# MCP Coverage Roadmap Baseline

Date: 2026-10-07
Repository: `/Users/yipy/Documents/GitHub/SAR_tool`

## Test baseline

- `pixi run test`: **200 passed** in 21.71s.
- MCP-focused tests, `pixi run python -m pytest -q tests/test_mcp_unit.py tests/test_mcp_integration.py`: **33 passed** in 8.86s.
- Specialized prodrug/API/dashboard/deep-link tests, `pixi run python -m pytest -q tests/test_prodrug_relationships.py tests/test_production_analysis_api.py tests/test_production_dashboard.py tests/test_deep_links.py`: **20 passed** in 3.82s.

## MCP catalog and live check

`pixi run mcp-check` was run against the existing loopback app at `http://127.0.0.1:5001`; no server was started, restarted, or overwritten. It reported:

- MCP server: 0.1.0
- App schema: **22**
- Runtime mode: `analyze`
- Sign-in: successful; 3 projects visible
- Catalog counts (also verified by `tests/test_mcp_unit.py`): **16 read-only**, **19 analyze**, **22 full** tools.

## Existing audit

The audit exists at `/Users/yipy/Documents/GitHub/SAR_tool/.agents/tasks/mcp-coverage-audit-2026-10-07.md`. Key findings: overall MCP coverage is partial (approximately 6/10); authenticated project-scoped lookup and persisted analyses are strong, but the main stated ester/prodrug-versus-acid workflow is not complete from MCP because mapping preview/save and prodrug comparison tools are absent. Other major gaps are XLSX/profile/full import review, structured endpoint comparison, and interactive pattern-explorer state. Human-governed review/design writes remain intentionally browser-only.

## Git status baseline

Before writing this note, `git status --short` showed these already-untracked paths:

```text
?? .DS_Store
?? .agents/
?? test/
```

The `test/` path includes user files such as `test/CellLinePanel_pIC50s.xlsx` and generated test data; these were not modified, deleted, staged, or committed. The `.agents/` path already contained the audit; this baseline note was added as requested. No other project files were changed.
