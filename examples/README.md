# Synthetic SAR example data

This directory contains self-contained synthetic fixtures for exercising the persisted SAR Workbench workflow. The structures, measurements, dates, and identifiers are fictional. No proprietary or personal data is included.

For the screen-by-screen browser workflow, see [`/Users/yipy/Documents/GitHub/SAR_tool/docs/WEB_APP_GUIDE.md`](../docs/WEB_APP_GUIDE.md). It explains how to check and save uploads, build summaries, run SAR analysis, export project evidence, and compare bulk prodrug–active mappings.

## Fast local start

From `/Users/yipy/Documents/GitHub/SAR_tool`, run:

```bash
pixi install
pixi run local
```

The interactive setup creates the local production-shaped SQLite database, local login, owner project, and optionally imports the selected primary fixture and contradiction follow-up. It starts Gunicorn on `http://127.0.0.1:5001` when setup completes. The default dataset is now `showcase`; choose `druglike` for the broader original series or `compact` when you only need a small importer smoke test. For setup without starting the server, use `pixi run setup-local`, then `pixi run start-local`. If port 5001 is occupied, use `SAR_GUNICORN_PORT=5010 pixi run start-local` and open `http://127.0.0.1:5010`. See the main repository README for environment-file and reset-path details.

To select the showcase profile explicitly:

```bash
pixi run setup-local --dataset showcase
```

Files:

- `example_showcase_measurements.csv` — high-contrast ten-compound series with a clear IC50 gradient, correlated cellular response, selectivity separation, ADME trade-offs, one censored value, and one missing state.
- `example_showcase_followup.csv` — follow-up import containing a material IC50 contradiction, a cellular divergence, an OffTarget unit conflict, and an exact-versus-censored solubility review case.
- `example_druglike_measurements.csv` — broader ten-compound aryl–methylene–amide–pyridyl series with H, methyl, methoxy, fluoro, chloro, CF3, heteroaryl, linker, N-methyl-amide, and morpholine variations.
- `example_druglike_contradiction_followup.csv` — follow-up import for the drug-like series with divergent IC50/cellular values and a mixed-unit OffTarget conflict.
- `example_measurements.csv` — compact six-compound smoke import with potency, cellular, selectivity, ADME, property, replicate, censored, and missingness examples.
- `example_contradiction_followup.csv` — follow-up for the compact smoke fixture.
- `example_invalid_rows.csv` — intentionally rejected rows for testing quarantine validation: invalid SMILES, non-finite result, unknown unit, and invalid qualifier.
- `example_api_requests.json` — request-body templates with project/run/evidence placeholders.
- `example_prodrug_active_measurements.csv` — compact synthetic IC50 measurements for four explicit prodrug and active-form pairs.
- `example_prodrug_pairs.csv` — the matching `prodrug_id,active_id,notes` mapping for the bulk SAR comparison mode.

## Data coverage

`example_measurements.csv` uses the importer’s primary fields:

```text
compound_id,smiles,assay,result,unit,qualifier,replicate,date
```

The imported compatibility keys are:

```text
IC50:import-v1
Cellular:import-v1
OffTarget:import-v1
CLint:import-v1
Papp:import-v1
Solubility:import-v1
CYP3A4:import-v1
```

For chemist-facing wide files, the preview also recognizes common headers such as `IC50`, `IC50_nM`, `Cellular_pIC50`, `LogP`, `TPSA`, `CLint`, `Papp`, `Solubility`, and `CYP3A4`. It expands those columns into source-linked endpoint rows and displays inferred units as a warning for verification before commit. Unknown endpoint headers remain visible for explicit review rather than being guessed.

`example_showcase_measurements.csv` contains ten RDKit-valid analogs built around the same shared motif as the drug-like fixture, but with intentionally high-contrast synthetic values for demonstrations. The primary IC50 series moves from roughly 1,050 nM for POS-001 to 5–6 nM for POS-006, making the H → methyl → methoxy → fluoro → chloro → CF3 direction easy to see. POS-007 creates a visible activity cliff relative to the chloro analogue, while POS-009 shows a linker penalty and POS-010 shows a strong morpholine result. Cellular pIC50 follows the biochemical trend, OffTarget pIC50 stays comparatively flat, and CLint/Papp/Solubility show a readable exposure trade-off. POS-009 has a not-reported cellular result, POS-008 has a not-run CYP3A4 result, and POS-010 has a censored solubility value so the showcase still demonstrates evidence boundaries.

`example_druglike_measurements.csv` contains ten RDKit-valid, drug-like analogs built around a shared aryl–methylene–amide–pyridyl motif. DLG-001 through DLG-006 reproduce the supplied diagram’s H, CH3, OCH3, F, Cl, and CF3 R¹ series with IC50 values of 120, 250, 480, 45, 18, and 6 nM. DLG-007 changes the heteroaryl nitrogen position, DLG-008 tests an N-methyl amide, DLG-009 extends the linker, and DLG-010 adds a morpholine substituent. The series spans approximately 212–297 Da and contains exact replicates, a censored value, missing cellular/ADME states, cellular pIC50, OffTarget pIC50, CLint, Papp, Solubility, and CYP3A4 contexts. These are synthetic illustrative measurements, not validated scientific conclusions. The compact fixture remains available in `example_measurements.csv` for small importer tests.

## Showcase profile

For a short positive demo, import `example_showcase_measurements.csv`, generate project summaries, and then use the Compare, Find patterns, and Choose next steps. The most visible observations are:

- **Potency gradient:** POS-001 → POS-006 improves by roughly two orders of magnitude across the R¹ series.
- **Activity cliff:** POS-005 and POS-007 are close in scaffold context but deliberately separated in potency.
- **Cellular translation:** cellular pIC50 broadly tracks the biochemical ranking while retaining one incomplete input.
- **Selectivity:** primary potency improves while OffTarget pIC50 remains in a narrow band.
- **Developability trade-off:** potency and permeability improve across the strongest compounds while solubility and CYP3A4 behavior remain visibly non-uniform.
- **Human review boundary:** `example_showcase_followup.csv` should produce contradiction and unit-conflict warnings rather than silently reconcile the records.

These are synthetic illustrative patterns for demonstrating the product workflow, not validated scientific conclusions.

## Recommended workflow

Run the workflow in production-shaped mode, not the illustrative demo mode. Use the dashboard or an authenticated API client; production mutations require the configured local identity and CSRF boundary.

1. Create a new project and retain its `project_id`.
2. Preview and commit the primary fixture for the selected profile. Use `example_showcase_measurements.csv` for the default positive demo, `example_druglike_measurements.csv` for the broader original series, or `example_measurements.csv` for the compact smoke fixture. Do not commit the invalid fixture as part of the valid project.
3. Verify that the import reports ten validated compounds for the selected fixture and that invalid structures are absent. The primary structures should be accepted by RDKit.
4. Generate project measurement summaries:

   ```text
   POST /api/v1/measurement-summaries/project
   {"project_id": "<project_id>"}
   ```

   Persist this first summary run before importing the contradiction follow-up.

5. Run the derived analyses in this order. Payload templates are in `example_api_requests.json`; add `project_id` to each request as required by the API:

   - `POST /api/v1/analysis/properties`
   - `POST /api/v1/analysis/mmp`
   - `POST /api/v1/analysis/rgroup` with scaffold `c1ccccc1`
   - `POST /api/v1/analysis/pharmacophore-rgroup` with a reference compound ID; leave `scaffold_smarts` blank to derive a ring-complete shared series core, or provide a reviewed core explicitly
   - `POST /api/v1/analysis/activity-cliffs` with `effect_threshold: 0.5`
   - `POST /api/v1/analysis/selectivity` using `IC50:import-v1` as primary and `OffTarget:import-v1` as comparator
   - `POST /api/v1/analysis/cellular-translation` using `IC50:import-v1` and `Cellular:import-v1`
   - `POST /api/v1/analysis/adme` for `CLint:import-v1`, `Papp:import-v1`, `Solubility:import-v1`, and `CYP3A4:import-v1`
   - `POST /api/v1/analysis/pareto` with observed potency and derived `logp` objectives
   - `POST /api/v1/analysis/information-gain` with the cellular and CYP3A4 contexts in the template

6. Generate recommendations from the persisted information-gap run:

   ```text
   POST /api/v1/recommendations/generate
   {
     "project_id": "<project_id>",
     "information_gain_run_id": "<run_id>",
     "limit": 10
   }
   ```

   Review each generated recommendation through `POST /api/v1/recommendations/<recommendation_id>/review`. Review is mandatory; generated recommendations remain separate from experimentally confirmed evidence and from curated design candidates.

7. Optionally create the curated design candidate from the template after replacing its project, evidence, and parent-compound placeholders. The candidate must cite an evidence ID from the same project, pass RDKit validation, and remain unconfirmed.

8. Test contradiction handling with the required snapshot sequence:

   1. Import and commit `example_druglike_contradiction_followup.csv` into the same project. Use `example_contradiction_followup.csv` for the compact fixture.
   2. Run `POST /api/v1/measurement-summaries/project` again.
   3. Run `POST /api/v1/analysis/contradictions` with `value_tolerance: 0.5`.

   The first and second summary snapshots are intentionally separate. In the drug-like follow-up, `DLG-001` has a materially divergent IC50 summary, `DLG-006` has a divergent cellular result, and `DLG-002` has both an existing pIC50 OffTarget summary and a follow-up nanomolar OffTarget observation, which should remain a unit conflict rather than being silently merged.

9. Export only after the records and analyses are present:

   ```text
   GET /api/v1/exports/project/<project_id>?format=json
   GET /api/v1/exports/project/<project_id>?format=csv
   ```

   Exports are project-scoped, bounded, provenance-bearing, and label records as raw, derived, curated, or generated. Do not treat derived values or recommendations as raw experimental confirmation.

## Preview and commit examples

The API is authenticated and CSRF-protected, so the exact cookie and CSRF header depend on the configured local login session. In the dashboard, use the import preview action, select the absolute file path, inspect the row-level validation report, then explicitly commit:

```text
/Users/yipy/Documents/GitHub/SAR_tool/examples/example_druglike_measurements.csv
```

For an API client, the sequence is:

```text
POST /api/v1/imports/preview       multipart upload of example_druglike_measurements.csv
GET  /api/v1/imports/<import_id>   inspect inferred mapping and row errors
POST /api/v1/imports/<import_id>/commit
```

Use `/Users/yipy/Documents/GitHub/SAR_tool/examples/example_measurements.csv` for the compact smoke fixture. Repeat the same sequence for `example_druglike_contradiction_followup.csv` only after the first summary snapshot exists. Upload `example_invalid_rows.csv` separately to confirm that preview/quarantine exposes row-level errors and that commit does not create authoritative measurements from rejected rows.

## Browser walkthrough: bulk prodrug–active comparison

For a focused web-app example, use the two files below in the same project:

1. Start the authenticated local app with `pixi run local`, or create a blank workspace in separate ignored paths:

   ```bash
   pixi run setup-local --no-seed-example \
     --database /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/sar.db \
     --upload-dir /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/uploads \
     --env-file /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/local.env
   pixi run python scripts/setup_local.py --start-existing \
     --env-file /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/local.env
   ```
2. In **Start**, upload `example_prodrug_active_measurements.csv`, choose **Check this file**, review the rows, and choose **Save accepted results**.
3. Open **Build summaries** and choose **Build result summaries**.
4. Open **Run SAR analysis**, select the `IC50` endpoint, and choose **Compare prodrug–active pairs**.
5. Upload or paste `example_prodrug_pairs.csv`, review the four mappings, and choose **Save mapping and compare all pairs**.
6. Inspect the structures, endpoint values, signed deltas, review status, and evidence links in the all-pairs table.

The first three pairs have exact compatible IC50 summaries. `ACT-004` is intentionally `not reported`, so the fourth pair remains visible but incomplete rather than receiving a fabricated delta.

## Expected observations

- Ten compounds and ten validated drug-like structure identities are created by the recommended import.
- `DLG-001` through `DLG-006` form the primary R¹ series shown in the supplied SAR diagram: H, CH₃, OCH₃, F, Cl, and CF₃ with IC50 values of 120, 250, 480, 45, 18, and 6 nM.
- `DLG-007` changes the heteroaryl nitrogen position, `DLG-008` tests an N-methyl amide, `DLG-009` extends the linker, and `DLG-010` adds a morpholine substituent.
- Cellular translation is incomplete for `DLG-009` because its cellular result is `not reported`.
- CLint and CYP3A4 include missing/not-run states; Solubility includes a censored state.
- Selectivity and cellular translation operate only on compatible exact summary inputs. Censored or missing inputs remain incomplete rather than becoming fabricated margins.
- ADME retains each requested assay context separately.
- Pareto analysis can combine observed potency with deterministic RDKit properties while preserving objective provenance.
- Contradiction analysis persists review warnings and source summary IDs; it does not reconcile them automatically.
- Information-gap scores are bounded heuristics, not predictive model output or guaranteed expected information gain.
- Generated recommendations cite persisted evidence-gap observations, require review, and can never be marked experimentally confirmed.

## Validation

Use Pixi for all Python commands:

```bash
cd /Users/yipy/Documents/GitHub/SAR_tool
pixi run python -m pytest tests/test_release_gates.py
pixi run test
```

The example fixtures are synthetic qualification data. They do not replace scientific review, assay compatibility review, deployment qualification, or the remaining external identity/operations/browser gates documented in the repository README.
