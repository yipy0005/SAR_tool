# SAR Workbench web-app guide

This guide is for chemists and biologists using the production-shaped SAR Workbench through its web interface. It covers the normal evidence path:

**Start → Check results → Build summaries → Compare → Find patterns → Choose next**

The workbench keeps uploaded observations, calculated summaries, review warnings, recommendations, and proposed designs separate. A calculated pattern is an observed association to review; it is not proof that a structural change caused an effect.

## 1. Start the local web app

All Python commands use Pixi. From the repository directory:

```bash
cd /Users/yipy/Documents/GitHub/SAR_tool
pixi install
```

### Synthetic walkthrough

For a safe, self-contained walkthrough with fictional data:

```bash
pixi run local
```

The setup asks for a local login email and password, lets you choose a dataset, creates an owner project, and can import the selected synthetic fixture. The recommended `showcase` dataset starts the local Gunicorn server on `http://127.0.0.1:5001`.

The data in the example files is synthetic and must not be treated as a validated scientific conclusion.

### Blank workspace for your own results

The `--no-seed-example` safety check refuses to reuse a database that already contains scientific records. This commonly happens when `pixi run local` was run first, because that command seeds the default `instance/local/` workspace.

Create the blank workspace in separate ignored paths:

```bash
pixi run setup-local --no-seed-example \
  --database /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/sar.db \
  --upload-dir /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/uploads \
  --env-file /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/local.env

pixi run python scripts/setup_local.py --start-existing \
  --env-file /Users/yipy/Documents/GitHub/SAR_tool/instance/blank/local.env
```

The setup still asks for a local login email and password. Open the printed loopback address and sign in with those credentials. Generated credentials, the SQLite database, and uploaded files remain under the ignored `instance/` directory; do not share the generated environment file.

If you already created the seeded synthetic workspace and only want to use it, do not run `--no-seed-example`; start that existing workspace instead:

```bash
pixi run start-local
```

If port `5001` is already occupied, add `--port 5010` to the setup command above or use `SAR_GUNICORN_PORT=5010 pixi run start-local` for an existing default workspace.

Do not use the illustrative demo mode for project decisions. Use the production-shaped workspace with a project-scoped database and authenticated local login.

## 2. Create or select a project

A project is the boundary for compounds, source files, assay contexts, summaries, analyses, review items, and exports.

- To use the project created during setup, open **Start**.
- To create another blank project, open **More tools → New project**, enter a project name and optional description, and choose **Create blank project**.
- Keep unrelated chemical series or programs in separate projects.

A new blank project does not change existing projects or delete existing results.

## 3. Upload a laboratory results file

On **Start**, choose **Add your lab results** and select **Choose a lab export**. The web app accepts:

- CSV files (`.csv`)
- Tab-separated files (`.tsv`)
- Excel workbooks (`.xlsx`)

Choose **Check this file** first. The file is quarantined for review; it is not authoritative project data until you choose **Save accepted results**.

### Recommended long-format CSV/TSV

Use one measurement per row. The most reliable columns are:

| Column | Meaning | Guidance |
| --- | --- | --- |
| `compound_id` | Registered compound identifier | Keep it stable across files. |
| `smiles` | Structure associated with the row | Use a valid, explicit SMILES string. |
| `assay` | Test or endpoint name | Use consistent names such as `IC50`, `Cellular`, `CLint`, or `Solubility`. |
| `result` | Original reported result | Keep the laboratory value and recognized missing state. |
| `unit` | Unit for the result | Always provide the unit; do not mix nM, µM, and pIC50 in one context. |
| `qualifier` | Exactness or censoring | Use `=` for exact observations and `>`, `<`, `>=`, or `<=` for thresholds. |
| `replicate` | Replicate number | Recommended when repeated measurements exist. |
| `date` | Measurement or run date | Recommended for provenance and later review. |

Minimal example:

```csv
compound_id,smiles,assay,result,unit,qualifier,replicate,date
CMP-001,CCO,IC50,100,nM,=,1,2026-09-01
CMP-001,CCO,IC50,120,nM,=,2,2026-09-02
CMP-002,CCN,IC50,30,nM,=,1,2026-09-01
CMP-002,CCN,IC50,35,nM,=,2,2026-09-02
```

The importer preserves raw values, units, qualifiers, source rows, missingness, and structure identity. Values such as `not reported`, `not run`, and `not detected` remain states rather than becoming zeroes.

### Wide laboratory exports

The preview can recognize common wide headers such as `IC50`, `IC50_nM`, `Cellular_pIC50`, `LogP`, `TPSA`, `CLint`, `Papp`, `Solubility`, and `CYP3A4`. Treat inferred units as a warning to verify. Unknown endpoint columns remain visible for explicit review instead of being guessed.

### Excel workbooks

When an `.xlsx` file is selected, the file check displays workbook details. Before checking the sheet:

1. Select the worksheet.
2. Set the row containing column names.
3. Set the row where results begin.
4. Acknowledge that the workbench does not calculate formula values.
5. Choose **Check this sheet**.

Correct the workbook or the column choices if the preview attaches a value, unit, assay, or structure to the wrong source column.

## 4. Check the import before saving

The file check report shows:

- accepted and rejected rows;
- inferred column mappings;
- structure and identity warnings;
- invalid units, qualifiers, or result values;
- the source row and cell location for each issue.

Review the report before saving. If there are rejected rows, either correct the source file and check it again or intentionally proceed only with the accepted rows. Do not treat a file with unresolved identity or unit problems as comparison-ready evidence.

Choose **Save accepted results** only after the mapping and row-level report are understood. The original uploaded file remains linked to the saved records.

After the first file is saved, **Start** shows two explicit paths:

- **Add another file** opens the same project’s upload form so you can extend the evidence set.
- **Start over with a new project** creates a blank project while leaving the current project and its source records unchanged.

Use the second path when you want to test a different file from a clean starting point. The workbench does not delete or replace the existing project’s uploaded evidence.

## 5. Build comparison-ready summaries

After saving measurements:

1. Open **Check results** and inspect the uploaded source values.
2. Open **Build summaries**.
3. Choose **Build result summaries**.
4. Wait for the status message confirming that summaries are saved.
5. Choose **Open Compare**.

Summaries group compatible repeated results while retaining the original measurements and source IDs. Censored, missing, non-exact, and incompatible results remain visible and are not silently turned into exact numerical comparisons.

Run **Refresh summaries** after importing a later batch of measurements. This creates the current derived read model without overwriting the uploaded results.

## 6. Compare endpoints

**Compare** is useful when the question involves two compatible tests, such as:

- biochemical potency versus cellular potency;
- a primary assay versus an explicit comparator;
- a potency result versus another assay context.

Select an endpoint and inspect the result states. A missing or censored value is a reason to review the source, not a negative potency result.

## 7. Run SAR analysis and find patterns

Open **Run SAR analysis** from **More tools** or the project overview. Select one calculated endpoint, then choose a mode:

- **Discover all series** groups observed compounds by shared scaffold.
- **Start from a selected compound** shows its observed scaffold or similarity neighbourhood.
- **Compare prodrug–active pairs** compares a saved list of linked relationships together.

The standard pattern workflow is available from **Find patterns → Run pattern checks**. Review the calculated outputs alongside the source evidence. MMP, graph R-group, pharmacophore R-group, activity-cliff, selectivity, cellular-translation, ADME, property, Pareto, contradiction, information-gap, and prediction views have separate evidence and review boundaries.

### Pharmacophore-level R-group decomposition

Before proposing an R-group change, choose a reference compound and declared core in the controls above **Run pattern checks**. To draw the core instead of typing SMARTS, choose **Draw core**, draw it (or load a compound's ring scaffold and trim it), then choose **Use drawing as core**; the drawing's SMARTS goes straight into the field. **Find compounds** is optional: it shows which compounds contain the core, and its results also offer **Use as declared core**. A terminal `*` or R# marks an attachment point and is dropped from the core, because substituents are found wherever a compound extends the core. Atom lists such as `[N,C]` stay in the core. Both R-group analyses use the same core. When no core is supplied, the showcase uses a ring-complete shared core derived from the selected reference series. A reviewed explicit core can still be entered when the project has a declared scaffold.

The **PHARMACOPHORE / R-GROUP** card uses RDKit’s versioned `BaseFeatures.fdef` profile plus a medicinal-chemistry normalization layer to project 2-D feature families onto the same R-sites as the graph decomposition. When no core is supplied, the workbench derives a ring-complete shared core from the selected reference series instead of defaulting to benzene. It reports:

- H-bond donor;
- H-bond acceptor;
- aromatic;
- hydrophobe;
- halogen substituent (halogens are not presented as H-bond acceptors);
- positive or negative ionizable;
- zinc binder where applicable;
- internal rotatable-bond count;
- ring count and aromatic-ring count;
- acyclic/ring-containing status; and
- non-ring heavy-atom count.

Feature gains/losses and compatible exact endpoint ranges are shown relative to the selected reference at each site. Chemically valid fragment drawings are shown alongside the feature and structural summaries. Features that cross the scaffold boundary are marked as **Core / R-group interface**; features touching multiple sites are marked **Ambiguous across sites** instead of being silently assigned. Atom IDs, feature types, structural descriptors, the selected reference, the scaffold SMARTS and the feature-definition hash are persisted in the derived run and JSON export.

If an analogue changes a heteroatom position inside the shared core rather than adding an R-group, the **Core feature changes** column reports that separately. For example, the showcase `POS-007` row reports **H-bond acceptor relocated within core**; it is not incorrectly presented as an ordinary substituent change. Outliers without the shared core remain unmatched and are not assigned core deltas.

Do not write “the substituent caused the effect” solely from a displayed pattern. Use language such as “this structural difference is associated with the observed response difference,” then inspect the source measurements, assay context, censoring, missingness and contradictory evidence.

### Combine R-sites in the pattern explorer

In **Find patterns → Navigate the series visually → R-group effects**, set **View** to **Combine sites** to see two or three R-sites together. Choose the **Rows** and **Columns** sites; **One grid per** adds a third site as one grid per group there. Each cell is a combination of groups, showing its result (the median when several compounds share it) and the difference from the comparison compound. Other R-sites and the core are held at the comparison compound's groups, so a cell differs from it only at the chosen sites; tick **Include compounds that also differ at other sites** to add the rest as dashed cells.

For log-scale results, a combination that has not been made shows an additive estimate (row effect + column effect, measured from the top-left cell of its grid), and a made combination shows how far it sits from that estimate. A large gap suggests the sites interact. The estimate assumes independent sites and is not a prediction; nothing is saved.

### Find compounds by drawing a substructure

Every workspace page has **Find by structure**. Draw a fragment, or load a project compound as a starting point, then search: matching compounds are listed with the match highlighted, and each hit can be used as the SAR reference, a pattern-explorer comparison, or a pharmacophore reference.

When Ketcher is installed (see the README), it is the default editor and supports atom lists such as `[F,Cl]`, R-group labels (matched as any substituent) and query bonds. The **Simple sketcher** tab is always available for plain drawings. Switching editors keeps the drawing only when the simple sketcher can show it; query features stay in Ketcher. Searches are read-only and are not saved to the project.

## 8. Compare prodrug–active pairs in bulk

A prodrug-to-active-form link is a distinct relationship, not an ordinary scaffold transformation. The workbench does not infer this relationship from SMILES; provide the mapping explicitly.

### Prepare the measurement file

First upload a normal measurement file containing both the prodrug and active-form compounds. The example file is:

```text
/Users/yipy/Documents/GitHub/SAR_tool/examples/example_prodrug_active_measurements.csv
```

Check and save it, then build result summaries.

### Prepare the pair-list file

Use the project’s registration IDs in a two-column mapping file:

```csv
prodrug_id,active_id,notes
PRO-001,ACT-001,Ethyl ester and corresponding acid
PRO-002,ACT-002,Para-methyl ester and acid pair
PRO-003,ACT-003,Para-chloro ester and acid pair
PRO-004,ACT-004,Para-methoxy ester and acid pair
```

A ready-to-upload example is:

```text
/Users/yipy/Documents/GitHub/SAR_tool/examples/example_prodrug_pairs.csv
```

Then:

1. Open **Run SAR analysis**.
2. Select the endpoint, for example `IC50`.
3. Choose **Compare prodrug–active pairs**.
4. Upload the pair-list CSV/TSV or paste its two columns.
5. Review all valid and invalid mappings in the preview.
6. Choose **Run SAR analysis**.
7. Choose **Save mapping and compare all pairs**.

The resulting table displays both structures, endpoint values, signed deltas, comparison status, relationship review status, and evidence/source counts. Exact deltas are calculated only when both summaries are compatible exact observations. Missing, censored, non-exact, and unit-mismatch pairs remain visible but non-comparable.

## 9. Review calculated outputs and exports

The **Choose next** page contains generated evidence-gap questions and curated design candidates when their prerequisites exist. Review their cited evidence before deciding whether to act. Generated recommendations and proposed designs are not experimental results and cannot be treated as confirmed findings.

Use **More tools → Download JSON** or **Download CSV** to export a project-scoped bundle. Exports retain provenance and distinguish uploaded, calculated, curated, and generated records. Relationship mappings are included in the export.

## Example upload files

All synthetic examples are under `/Users/yipy/Documents/GitHub/SAR_tool/examples/`:

| File | Use it for |
| --- | --- |
| [`example_measurements.csv`](../examples/example_measurements.csv) | Compact end-to-end importer smoke test with potency, cellular, selectivity, ADME, missing, and censored states. |
| [`example_showcase_measurements.csv`](../examples/example_showcase_measurements.csv) | Recommended visual walkthrough with a high-contrast synthetic SAR series. |
| [`example_druglike_measurements.csv`](../examples/example_druglike_measurements.csv) | Broader synthetic aryl–methylene–amide–pyridyl series with multiple R-group changes. |
| [`example_prodrug_active_measurements.csv`](../examples/example_prodrug_active_measurements.csv) | Synthetic prodrug and active-form compounds for the bulk relationship workflow. |
| [`example_prodrug_pairs.csv`](../examples/example_prodrug_pairs.csv) | Pair-list mapping to upload or paste in **Compare prodrug–active pairs** mode. |
| [`example_showcase_followup.csv`](../examples/example_showcase_followup.csv) | Follow-up observations that demonstrate contradiction and unit-conflict review. |
| [`example_druglike_contradiction_followup.csv`](../examples/example_druglike_contradiction_followup.csv) | Follow-up contradiction fixture for the broader drug-like series. |
| [`example_invalid_rows.csv`](../examples/example_invalid_rows.csv) | Intentional invalid structures, non-finite values, unknown units, and invalid qualifiers for testing the quarantine report. |

For a detailed synthetic analysis sequence and expected observations, see [`/Users/yipy/Documents/GitHub/SAR_tool/examples/README.md`](../examples/README.md).

## Troubleshooting

- **No endpoint is available in SAR analysis:** save measurements and run **Build result summaries** first.
- **A row is rejected:** inspect the source row and correction message in **Check results**; do not bypass an invalid structure or unit warning.
- **The browser cannot connect:** confirm that the loopback server is running and use the port printed by `pixi run start-local`.
- **A new blank setup refuses to reuse a database:** choose new `--database`, `--upload-dir`, and `--env-file` paths; the setup deliberately refuses to treat a database with existing scientific records as blank.
- **A prodrug pair is unknown or ambiguous:** use the exact registration ID from the current project and make sure both compounds were imported before previewing the pair list.

The application organizes and traces scientific evidence; it does not replace qualified medicinal-chemistry, biology, statistics, or regulatory review.
