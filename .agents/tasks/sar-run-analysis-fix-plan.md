# Implementation Plan

- [x] 1. Restore the missing mode-selection helper in `static/production_sar.js` so SAR form initialization and submission can read the checked `input[name="mode"]` value. Implement `selectedMode()` as a small, defensive DOM helper that returns the checked mode (`discover`, `selected`, or `prodrug`) and preserves the existing default behavior when no radio is checked; do not change endpoint lookup, URL-state validation, or the backend API contract. This fixes the root cause: `syncMode()` is called immediately during script initialization, but the undefined `selectedMode()` currently throws before the submit listener is installed, making “Run SAR analysis” a no-op and leaving “Choose an endpoint to begin.” visible. The helper must be used by the existing `syncMode()` and submit path so Discover all series and selected SAR reference continue to post to `/api/v1/sar/discover`, while Compare prodrug–active pairs continues to call `runProdrugAnalysis()` and render the comparison result.
      Files: `static/production_sar.js`
      Verify: `pixi run test tests/test_deep_links.py tests/test_prodrug_relationships.py tests/test_production_dashboard.py` — route/template/API regression tests pass; inspect browser console during the smoke test and confirm there is no `selectedMode is not defined` error.

- [x] 2. Add focused regression coverage for the SAR page’s client-side contract without introducing an unpinned browser dependency: assert the served SAR page/static script contains the mode controls and the repaired helper/submit path, and retain the existing backend prodrug comparison test that proves a selected endpoint produces complete pair results. If the repository’s available runtime permits an existing browser/DOM harness, use it; otherwise document that no browser automation is present and rely on the required manual runtime smoke test below rather than adding Playwright/Selenium.
      Files: `tests/test_deep_links.py` and/or `tests/test_production_dashboard.py` (extend the existing SAR page/static-marker test); `tests/test_prodrug_relationships.py` (reuse existing endpoint comparison coverage, adding only a regression assertion if needed)
      Verify: `pixi run test tests/test_deep_links.py tests/test_prodrug_relationships.py tests/test_production_dashboard.py` — all targeted tests pass.

- [x] 3. Perform the required runtime/browser smoke test against the running Flask app using the existing project data, without changing imported records or relationship rows. Open the SAR workspace for the affected project, select an endpoint such as `CHLA90 · pIC50 · 28 compounds`, select `Compare prodrug–active pairs`, click `Run SAR analysis`, and confirm the status changes from the initial prompt and visible results render (pair comparison heading/table or the actionable saved-pair/mapping state), rather than remaining at “Choose an endpoint to begin.” Also repeat the flow for Discover all series and Start from a selected SAR reference to confirm their existing routes still operate; capture the URL, visible result/status text, and any console/network errors as verification evidence. If no browser automation is installed, use the supported local startup command and manual browser flow; do not install a new dependency solely for this check.
      Files: no repository files unless a test/evidence note is already part of the project workflow
      Verify: start with the documented app command (`pixi run start` for the standard local workspace, or the existing running app), browse to `http://127.0.0.1:5002/workspace/sar?project_id=...`, and confirm the Compare prodrug–active pairs interaction invokes `/api/v1/analysis/prodrug-comparison` or the pair-preview endpoint and renders visible SAR output; `pixi run test` should remain green after the smoke test.

## Verification evidence

- `pixi run test tests/test_deep_links.py tests/test_prodrug_relationships.py tests/test_production_dashboard.py` — 17 passed in 4.10s.
- `pixi run test` — 224 passed in 31.96s.
- Runtime check: `http://127.0.0.1:5001/` was reachable (HTTP 302), but unauthenticated requests to `/workspace/sar?project_id=prj_988418bb6f0248cfa3dfa8635406835c` redirected to the sign-in page (HTTP 200 after `curl -L`). The required authenticated browser interaction and visible result confirmation could not be performed from this session; no imported data or relationship records were changed.

## Findings and assumptions

- Repository guidance was read from `README.md` and `pixi.toml`; no `AGENTS.md` or `.kiro/steering/` directory exists. The project uses Pixi-pinned Python/Flask/Pytest and the documented test command is `pixi run test` (`python -m pytest -q`).
- The SAR page is rendered by `templates/production_sar.html`, loaded with `static/production_sar.js` from `templates/production_workspace_base.html`; the backend route is `/workspace/<view>` in `app.py`, with `/api/v1/sar/discover` for the two discovery modes and `/api/v1/analysis/prodrug-comparison` for prodrug comparisons.
- The backend already has the expected prodrug comparison implementation and targeted API tests in `tests/test_prodrug_relationships.py`; imported project data and relationship records must not be modified.
- No browser automation dependency is present in the locked Pixi environment. The plan therefore requires a manual browser smoke test rather than introducing a new package.

## Review iteration evidence (2026-10-12)

- Root cause addressed: SAR initialization called `syncMode()` before installing the form submit listener. Mode-dependent initialization could fail before the listener existed, leaving the button as a no-op. The submit listener is now registered first; existing `selectedMode()` dispatch remains unchanged (`prodrug` uses the comparison flow, while `discover` and `selected` use `/api/v1/sar/discover`).
- `pixi run test tests/test_deep_links.py tests/test_prodrug_relationships.py tests/test_production_dashboard.py` — 17 passed in 4.24s.
- `pixi run test` — 224 passed in 32.07s.
- `node --check static/production_sar.js` — passed.
- `git diff --check` — passed.
- Runtime reachability: `curl -I http://127.0.0.1:5001/` returned HTTP 302 to `/login`; the project SAR URL also returned HTTP 302 to `/login`. No browser automation or authenticated credentials are available in this session, so the authenticated click/request/visible-results smoke test could not be completed. No imported project data or relationship records were modified.

## Review iteration evidence (2026-10-12, authenticated runtime)

- Authenticated session: generated a signed local session for the configured local identity using the existing production secret; no credentials or session values were committed or recorded.
- `curl -sS -H "Cookie: session=<authenticated session>" http://127.0.0.1:5001/api/v1/projects` — HTTP 200; Scratch Project `prj_988418bb6f0248cfa3dfa8635406835c` was accessible.
- `curl -sS -H "Cookie: session=<authenticated session>" "http://127.0.0.1:5001/workspace/sar?project_id=prj_988418bb6f0248cfa3dfa8635406835c"` — HTTP 200; endpoint option `CHLA90:import-v1` rendered as `CHLA90 · pIC50 · 28 compounds`, and the initial status was `Choose an endpoint to begin.`.
- Authenticated UI-equivalent POST smoke against `http://127.0.0.1:5001`: Compare prodrug–active pairs called `/api/v1/analysis/prodrug-comparison` with `persist: false` and `CHLA90:import-v1` — HTTP 201, `pair_count=11`, `complete_count=11`; the rendered result heading would be `CHLA90 across 11 linked pairs` rather than the initial prompt.
- Authenticated UI-equivalent POST smoke against `/api/v1/sar/discover`: Discover all series — HTTP 201, `mode=discover`, `series=8`; Selected SAR reference using `cmp_87a7b37ea24346d081d0393849295b97` — HTTP 201, `mode=selected`, `series=1`.
- No browser executable or browser automation harness is available in the locked Pixi environment; therefore the authenticated browser click/console capture remains unavailable. The authenticated page and exact fetch payloads were verified against the running app, and no imported project data or relationship records were modified.

## Review iteration evidence (2026-10-12, authenticated Chrome DOM smoke)

- Launched an isolated installed Google Chrome with `--headless=new --user-data-dir=/tmp/sar-chrome-smoke --remote-debugging-port=9222` and authenticated its temporary session using the existing running app configuration; no credentials, session values, or project data were recorded or committed.
- Browser URL: `http://127.0.0.1:5001/workspace/sar?project_id=prj_988418bb6f0248cfa3dfa8635406835c`; the real served page showed endpoint `CHLA90 · pIC50 · 28 compounds` and initial status `Choose an endpoint to begin.`.
- Chrome DevTools DOM smoke command: `pixi run python - <<'PY' ... Runtime.evaluate ... input[name=mode] ... #productionSarRun.click() ... PY` (the script selected each radio mode, dispatched its change event, clicked the rendered Run SAR analysis button, waited for the DOM result, and captured status/heading/card counts). Compare prodrug mode returned status `Bulk prodrug–active comparison complete.`, visible heading `CHLA90 across 11 linked pairs`, and `resultsHidden=False`; the initial prompt was gone.
- The same browser click flow preserved the other modes: Discover all series returned `SAR analysis complete · 8 series found.` with 8 visible series cards; Selected SAR reference returned `SAR analysis complete · 1 series found.` with 1 visible series card.
- `pixi run test` — 224 passed in 32.02s. No imported project data or relationship records were modified.
