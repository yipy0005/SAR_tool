# Restore SAR analysis submission across all modes

The SAR page now installs its form submit handler before mode-dependent initialization, closing the failure window that left “Run SAR analysis” inert when initialization threw. The change keeps the existing mode dispatch: prodrug comparisons use the comparison flow, while Discover and Selected continue using the SAR discovery endpoint. The recorded authenticated Chrome smoke test exercised the real page and all three modes without changing imported data or relationship rows. **Watch for:** **confirmed** — no blocking concerns remain; the evidence covers the reported click-to-render path and the two preserved modes.

**Verdict**: APPROVED

## High-level view

The client registers submission before calling `syncMode()` or restoring saved state, so initialization cannot prevent the form from having a submit path. Endpoint and selected-reference validation remain in the submit path, with failure handling and button re-enablement unchanged.

Mode behavior remains separated at the same boundary: Compare prodrug–active pairs calls the existing comparison/preview flow, while Discover all series and Selected SAR reference post to `/api/v1/sar/discover`. Authenticated Chrome DOM evidence confirmed visible output for all three paths: 11 linked prodrug pairs, 8 discovered series, and 1 selected-reference series.

Comparison requests use `persist: false`, and the recorded verification reports that imported project data and relationship records were unchanged.

<details>
<summary>Issues (0)</summary>

No actionable issues.

</details>

<details>
<summary>Details</summary>

### Submit registration closes the no-op window

**Confirmed:** the previous ordering called mode-dependent initialization before registering the form submit handler. The updated script registers `form.addEventListener("submit", ...)` first, then attaches mode-change listeners, runs `syncMode()`, and restores requested state. This directly addresses the reported failure mode without changing the endpoint or selected-compound guards.

The focused regression test checks that submit registration precedes mode initialization and that the submit region still contains both the prodrug branch and the discovery fetch. The authenticated Chrome smoke test selected each mode, clicked the rendered Run SAR analysis control, and observed the resulting DOM.

### Prodrug comparison and discovery modes remain compatible

**Confirmed:** prodrug submission still enters `runProdrugAnalysis(endpointKey)`, including saved-pair comparison and pair-text preview. With a selected `CHLA90` endpoint, the browser result changed to `Bulk prodrug–active comparison complete.`, displayed `CHLA90 across 11 linked pairs`, and removed the initial empty result state.

**Confirmed:** Discover all series and Selected SAR reference still post through the existing `/api/v1/sar/discover` path. The same browser flow produced `SAR analysis complete · 8 series found.` with 8 visible cards and `SAR analysis complete · 1 series found.` with 1 visible card, respectively.

### Data-integrity and verification boundary

**Confirmed:** the run-time comparison requests set `persist: false`; persistence remains behind explicit mapping or reviewed-series save actions. The verification evidence reports no imported project data or relationship records changed.

**Confirmed:** the recorded checks include 17 targeted tests, 224 tests in the full suite, JavaScript syntax validation, whitespace validation, and an authenticated Chrome DOM smoke against the running app. The browser evidence includes the served URL, initial status, final result text, and all three mode outcomes, which is sufficient for this client-side no-op.

</details>

<details>
<summary>Files changed</summary>

- `static/production_sar.js` — registers SAR submission before initialization and preserves the mode-specific request flows.
- `tests/test_deep_links.py` — checks the client-side ordering and retained mode dispatch markers.
- `.agents/tasks/sar-run-analysis-fix-plan.md` — records implementation, test, browser smoke, and data-integrity evidence.

Full diff: `git diff origin/main..HEAD`.

</details>
