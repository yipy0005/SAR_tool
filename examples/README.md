# Synthetic SAR example data

This directory contains self-contained synthetic fixtures for exercising the persisted SAR Workbench workflow. The structures, measurements, dates, and identifiers are fictional. No proprietary or personal data is included.

## Fast local start

From `/Users/yipyewmun/GitHub/SAR_tool`, run:

```bash
pixi install
pixi run local
```

The interactive setup creates the local production-shaped SQLite database, local login, owner project, and optionally imports the selected primary fixture and contradiction follow-up. It starts Gunicorn on `http://127.0.0.1:5001` when setup completes. The default dataset is `druglike`; choose `compact` when you only need a small importer smoke test. For setup without starting the server, use `pixi run setup-local`, then `pixi run start-local`. If port 5001 is occupied, use `SAR_GUNICORN_PORT=5010 pixi run start-local` and open `http://127.0.0.1:5010`. See the main repository README for environment-file and reset-path details.

Files:

- `example_druglike_measurements.csv` — recommended ten-compound aryl–methylene–amide–pyridyl series with H, methyl, methoxy, fluoro, chloro, CF3, heteroaryl, linker, N-methyl-amide, and morpholine variations.
- `example_druglike_contradiction_followup.csv` — follow-up import for the drug-like series with divergent IC50/cellular values and a mixed-unit OffTarget conflict.
- `example_measurements.csv` — compact six-compound smoke import with potency, cellular, selectivity, ADME, property, replicate, censored, and missingness examples.
- `example_contradiction_followup.csv` — follow-up for the compact smoke fixture.
- `example_invalid_rows.csv` — intentionally rejected rows for testing quarantine validation: invalid SMILES, non-finite result, unknown unit, and invalid qualifier.
- `example_api_requests.json` — request-body templates with project/run/evidence placeholders.

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

`example_druglike_measurements.csv` contains ten RDKit-valid, drug-like analogs built around a shared aryl–methylene–amide–pyridyl motif. DLG-001 through DLG-006 reproduce the supplied diagram’s H, CH3, OCH3, F, Cl, and CF3 R¹ series with IC50 values of 120, 250, 480, 45, 18, and 6 nM. DLG-007 changes the heteroaryl nitrogen position, DLG-008 tests an N-methyl amide, DLG-009 extends the linker, and DLG-010 adds a morpholine substituent. The series spans approximately 212–297 Da and contains exact replicates, a censored value, missing cellular/ADME states, cellular pIC50, OffTarget pIC50, CLint, Papp, Solubility, and CYP3A4 contexts. These are synthetic illustrative measurements, not validated scientific conclusions. The compact fixture remains available in `example_measurements.csv` for small importer tests.

## Recommended workflow

Run the workflow in production-shaped mode, not the illustrative demo mode. Use the dashboard or an authenticated API client; production mutations require the configured local identity and CSRF boundary.

1. Create a new project and retain its `project_id`.
2. Preview and commit `example_druglike_measurements.csv` as one import. Use `example_measurements.csv` instead when running the compact smoke fixture. Do not commit the invalid fixture as part of the valid project.
3. Verify that the import reports ten drug-like compounds for the recommended fixture and that invalid structures are absent. The primary structures should be accepted by RDKit.
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
/Users/yipyewmun/GitHub/SAR_tool/examples/example_druglike_measurements.csv
```

For an API client, the sequence is:

```text
POST /api/v1/imports/preview       multipart upload of example_druglike_measurements.csv
GET  /api/v1/imports/<import_id>   inspect inferred mapping and row errors
POST /api/v1/imports/<import_id>/commit
```

Use `/Users/yipyewmun/GitHub/SAR_tool/examples/example_measurements.csv` for the compact smoke fixture. Repeat the same sequence for `example_druglike_contradiction_followup.csv` only after the first summary snapshot exists. Upload `example_invalid_rows.csv` separately to confirm that preview/quarantine exposes row-level errors and that commit does not create authoritative measurements from rejected rows.

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
cd /Users/yipyewmun/GitHub/SAR_tool
pixi run python -m pytest tests/test_release_gates.py
pixi run test
```

The example fixtures are synthetic qualification data. They do not replace scientific review, assay compatibility review, deployment qualification, or the remaining external identity/operations/browser gates documented in the repository README.
