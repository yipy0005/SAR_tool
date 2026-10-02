# SAR Workbench Client Demo Runbook

A live-demo checklist for the synthetic showcase project. Keep this file open while presenting.

> **Important:** All example data is fictional and synthetic. Present displayed relationships as observed associations, not validated scientific conclusions or causal claims.

## Demo objective

Show the complete evidence workflow:

```text
Import → Validate → Save → Summarize → Compare → Find patterns → Propose → Review contradictions → Export
```

Recommended duration: **15–20 minutes**.

Recommended dataset:

```text
examples/example_showcase_measurements.csv
examples/example_showcase_followup.csv
```

---

## 1. Prepare and launch the demo

From Terminal:

```bash
cd /Users/yipy/Documents/GitHub/SAR_tool
pixi install
pixi run local
```

When prompted:

- Choose the `showcase` dataset.
- Import the primary example: **Yes**.
- Import the contradiction follow-up: **No** for the initial walkthrough.
- Create a local demo email and password.

Open the printed address, normally:

```text
http://127.0.0.1:5001
```

Sign in with the credentials created during setup.

### Opening statement

> “This is a synthetic SAR project. We will start with a laboratory-style export, check it before saving, build comparison-ready summaries, identify structural and assay relationships, and review where the evidence is incomplete or contradictory.”

### Keep these points clear

- The records are synthetic.
- Raw measurements remain separate from calculated summaries.
- Missing, censored and incompatible values are not silently converted into exact values.
- Recommendations and proposed compounds remain untested.

---

## 2. Start: introduce the project and matrix

Open **Start**.

If the dataset was seeded during setup, the compound-by-endpoint matrix is already available. If not, use the import steps in Section 3 first.

Show:

- The project name and production-data boundary.
- The workflow navigation:
  - Start
  - Check results
  - Build summaries
  - Compare
  - Find patterns
  - Choose next
- The **Structure** column.
- Endpoint columns.
- Exact values, threshold values, missing values and not-tested cells.
- Cell shading and sorting.
- The `n=` replicate count.
- The hover/focus detail containing measurement count and spread.

### Showcase chemical story

The main series is:

```text
POS-001 → POS-002 → POS-003 → POS-004 → POS-005 → POS-006
H       → Me      → OMe     → F       → Cl      → CF₃
```

The primary IC50 trend is approximately:

```text
1,000 nM → 5–6 nM
```

Say:

> “The table shows an observed association between these structural differences and measured potency. It does not, by itself, prove that a substitution caused the effect.”

---

## 3. Demonstrate import validation

Use this section if the file was not seeded or if you want to show the import boundary explicitly.

1. On **Start**, open **Add your lab results**.
2. Select:

   ```text
   /Users/yipy/Documents/GitHub/SAR_tool/examples/example_showcase_measurements.csv
   ```

3. Choose **Check this file**.
4. Review the quarantine/validation report.
5. Choose **Save accepted results** only after reviewing it.

Show:

- Accepted and rejected rows.
- Column mapping.
- Compound identifiers and structures.
- Assay names and units.
- Qualifiers such as `=` and `>`.
- Replicate numbers and dates.
- Source-row locations.

Say:

> “Checking the file does not save scientific data. It creates a quarantined preview. Only explicit acceptance makes rows authoritative project records.”

Expected primary dataset:

- 10 valid compounds.
- RDKit-valid structures.
- Multiple assay endpoints.
- Replicate measurements.
- Missing and thresholded states.

---

## 4. Check results: show raw evidence

Open **Check results**.

Show the raw measurements table and point out:

- Original reported values.
- Canonical values.
- Units.
- Qualifiers.
- Exact versus threshold states.
- QC state.
- Source-row details.
- Assay-run and protocol information.

Open a measurement’s **Trail** panel.

Say:

> “The display may round values for readability, but the original value, source row, assay run and protocol remain traceable.”

Point out examples from the showcase file:

- IC50 in nM.
- Cellular results in pIC50.
- CLint in `µL/min/mg`.
- Papp in `10⁻⁶ cm/s`.
- A censored solubility result.
- A missing cellular result.
- A not-run CYP3A4 result.

Say:

> “Not reported, not run and thresholded are evidence states. They are not silently converted into zero or an exact value.”

---

## 5. Build summaries: preserve raw measurements

Open **Build summaries**.

Choose:

```text
Build result summaries
```

Wait for the success message, then show:

- Summary value.
- Replicate count.
- Exact count.
- Threshold count.
- Missing count.
- Source measurement links.
- Dispersion where available.

Say:

> “This creates a replicate-aware calculated summary. It does not overwrite or replace the original measurements.”

Choose **Open Compare**.

---

## 6. Compare: connect assay contexts

Open **Compare** and show the available endpoints.

Use these comparison topics:

1. Biochemical potency versus cellular potency.
2. Primary potency versus OffTarget pIC50.
3. Potency versus ADME contexts.

Expected observations:

- Primary potency improves through the main POS series.
- Cellular pIC50 broadly follows the biochemical trend.
- OffTarget pIC50 stays comparatively flatter.
- Potency and developability do not move identically.

Say:

> “Compare puts compatible evidence side by side. Missing or censored results remain incomplete instead of becoming fabricated comparisons.”

Use cautious language:

> “The data suggests improved primary potency with a comparatively limited change in the off-target context. This is a review signal, not a confirmed selectivity claim.”

---

## 7. Find patterns: run calculated analyses

Open **Find patterns**.

Choose:

```text
Run pattern checks
```

Review the result cards in this order.

### 7.1 R-group analysis

Show the shared scaffold and R-group table.

Point out:

- R1/R2 position headers.
- The same position label across compounds.
- Named groups such as Me, OMe, F, Cl, CF₃ and morpholino.
- Drawn substituents where no common name is available.
- The collapsed **Method details** panel.

Say:

> “R labels are position-consistent across the series. R1 means the same core site in every compound rather than being renumbered independently for each molecule.”

### 7.2 Pharmacophore-level R-group analysis

Before suggesting a substitution, show the reference-anchored feature view:

1. In the analysis controls, choose a reference compound, for example `POS-001`.
2. Leave the declared core SMARTS at the reference-derived default, or enter a reviewed core explicitly.
3. Choose **Run pattern checks**.
4. Open **PHARMACOPHORE / R-GROUP**.

Point out:

- RDKit feature families such as H-bond donor, H-bond acceptor, aromatic, hydrophobe and halogen substituent.
- The same R-site labels used by the graph decomposition.
- Chemically valid fragment drawings rather than raw atom lists.
- Rotatable-bond count, ring count, aromatic-ring count, acyclic/ring-containing status and non-ring heavy atoms for each fragment.
- Feature gains and losses relative to the selected reference at each site.
- Core feature relocation changes, including the POS-007 pyridyl H-bond acceptor position change.
- Compatible exact endpoint ranges in the expandable effect summary.
- `Core / R-group interface` and `Ambiguous across sites` warnings.

Say:

> “This is a graph-level 2-D pharmacophore projection combined with fragment-level structural descriptors. It helps us ask whether an R-group adds, removes or preserves a donor, acceptor, hydrophobic or halogen feature, and whether it changes flexibility or ring topology. It is not a 3-D binding-mode assignment and does not prove that a feature drives the endpoint.”

The analysis stores atom-level feature provenance and includes it in the JSON project export.

### 7.3 Matched molecular pairs

Show the MMP table.

Point out changes such as:

```text
R2: H → CF₃
R2: Me → CF₃
R2: Me → OMe
```

Say:

> “The platform identifies the structural site and displays the medicinal-chemistry change rather than showing only an atom-level formula.”

Open a source disclosure and say:

> “Every calculated comparison can be traced back to the underlying measurement records.”

### 7.4 Activity cliffs

Open **Activity cliffs**.

Discuss the intentionally separated POS-005/POS-007 example.

Say:

> “These are structurally similar compounds with materially different measured results. They are useful review targets, but the application does not assume the structural difference alone explains the result.”

### 7.5 Cellular translation

Show the biochemical-versus-cellular panel.

Point out:

- Broad trend translation.
- The incomplete cellular observation for POS-009.
- The difference between an observed translation and an unavailable comparison.

### 7.6 ADME and properties

Show:

- CLint.
- Papp.
- Solubility.
- CYP3A4.
- MW.
- cLogP.
- TPSA.

Say:

> “Potency is only one dimension of a development decision. This view makes trade-offs visible without collapsing them into one unsupported score.”

---

## 8. Choose next: turn evidence into a reviewable question

Open **Choose next**.

If suggested questions are present, show:

- The generated question.
- Its cited evidence.
- Its review status.
- The distinction between generated question, human hypothesis and proposed experiment.

Say:

> “The system separates a generated question from a human hypothesis and from a proposed compound. None of these are experimental results.”

If the page is empty, return to **Find patterns** and ensure the information-gap analysis has been run.

---

## 9. Demonstrate the proposed-structure editor

In the proposed-experiment form:

1. Select parent compound `POS-004`.
2. Confirm the parent structure is displayed.
3. Under **Swap a substituent**, select:
   - Site: `R2`
   - Replacement: `CF₃`
4. Choose **Apply**.

The proposed structure should update to the CF₃ analogue.

Say:

> “This is a proposal editor, not a prediction engine. The structure is validated and redrawn, but no activity is claimed and no measurement is saved.”

Then:

1. Select at least one supporting evidence record.
2. Complete the title.
3. Complete **Why this compound?**
4. Complete **Question it tests**.
5. Complete **Expected outcome**.
6. Complete **Uncertainty**.
7. Choose **Save proposed candidate**.

Show the evidence links on the saved candidate.

Say:

> “A reviewer can move directly from the proposal back to the observation that motivated it.”

Do not call the candidate validated, active or predicted to work.

---

## 10. Demonstrate contradiction handling

Import the follow-up file:

```text
/Users/yipy/Documents/GitHub/SAR_tool/examples/example_showcase_followup.csv
```

Steps:

1. Return to **Start**.
2. Choose **Add another file**.
3. Select the follow-up file.
4. Choose **Check this file**.
5. Review the preview.
6. Choose **Save accepted results**.
7. Open **Build summaries**.
8. Choose **Refresh summaries**.
9. Return to **Find patterns**.
10. Refresh or run contradiction analysis.

Expected review topics:

- Materially divergent IC50 observation.
- Cellular divergence.
- OffTarget unit conflict.
- Exact-versus-censored solubility case.

Say:

> “The platform retains both observations and raises a review item. It does not silently average incompatible results or erase the contradiction.”

This is the key data-governance moment of the demo.

---

## 11. Demonstrate clickable evidence

From a proposed candidate or suggested question:

1. Choose a cited evidence link.
2. Confirm that the relevant page opens.
3. Confirm that a collapsed details section opens automatically.
4. Confirm that the cited row is highlighted.

Say:

> “A reviewer does not need to manually search through the project to verify the supporting observation.”

---

## 12. Export the project

Open **More tools**.

Choose:

- **Download CSV export**, or
- the JSON project export if available.

Say:

> “The export is project-scoped and provenance-bearing. Raw measurements, calculated summaries, generated recommendations and curated designs remain distinguishable.”

Point out:

- Compound IDs.
- Source measurements.
- Summary records.
- Analysis outputs.
- Evidence IDs.
- Provenance categories.

---

## 13. Closing statement

> “The workbench provides a traceable path from laboratory export to reviewable SAR hypothesis. It preserves raw evidence, keeps missing and contradictory data visible, and ties proposed experiments back to the observations that motivated them.”

Close with three points:

1. **Traceability:** conclusions link back to source evidence.
2. **Uncertainty:** missing, censored and conflicting data remain visible.
3. **Actionability:** the platform helps formulate the next question without pretending that a proposed experiment is already validated.

---

## Reset for the next client

Stop the server with:

```text
Ctrl-C
```

Create a fresh isolated demo workspace:

```bash
cd /Users/yipy/Documents/GitHub/SAR_tool

pixi run setup-local \
  --database /Users/yipy/Documents/GitHub/SAR_tool/instance/client_demo_02/sar.db \
  --upload-dir /Users/yipy/Documents/GitHub/SAR_tool/instance/client_demo_02/uploads \
  --env-file /Users/yipy/Documents/GitHub/SAR_tool/instance/client_demo_02/local.env \
  --dataset showcase \
  --seed-example \
  --no-seed-contradiction
```

Start the new workspace:

```bash
pixi run python scripts/setup_local.py \
  --start-existing \
  --env-file /Users/yipy/Documents/GitHub/SAR_tool/instance/client_demo_02/local.env
```

Use separate directories for separate demos:

```text
instance/client_demo_01
instance/client_demo_02
instance/client_demo_03
```

Do not overwrite a database containing prior scientific records.

---

## Optional focused demos

### Chemistry-focused demo

Use:

```text
examples/example_druglike_measurements.csv
examples/example_druglike_contradiction_followup.csv
```

Emphasize the shared aryl–methylene–amide–pyridyl scaffold, R-group changes, linker changes, N-methyl amide and morpholine analogue.

### Data-quality demo

Use:

```text
examples/example_invalid_rows.csv
```

Show invalid SMILES, non-finite result, unknown unit and invalid qualifier in the quarantine report.

### Prodrug demo

Use:

```text
examples/example_prodrug_active_measurements.csv
examples/example_prodrug_pairs.csv
```

Workflow:

1. Upload and save the measurement file.
2. Build summaries.
3. Open **Run SAR analysis**.
4. Select `IC50`.
5. Choose **Compare prodrug–active pairs**.
6. Upload or paste the pair file.
7. Review all four mappings.
8. Run the comparison.

The incomplete fourth pair remains visible but receives no fabricated delta.

---

## Troubleshooting

### Browser cannot connect

```bash
lsof -nP -iTCP:5001 -sTCP:LISTEN
```

If port 5001 is occupied:

```bash
SAR_GUNICORN_PORT=5010 pixi run start-local
```

Open:

```text
http://127.0.0.1:5010
```

### No endpoints appear

Run this sequence:

1. Save accepted measurements.
2. Open **Build summaries**.
3. Choose **Build result summaries**.
4. Return to **Compare** or **Find patterns**.

### Follow-up contradiction does not appear

Confirm that you:

1. Imported the primary file first.
2. Built the first summary snapshot.
3. Imported the follow-up file second.
4. Refreshed summaries.
5. Ran contradiction analysis.

### Need a smaller demo

Use:

```text
examples/example_measurements.csv
```

### Need to show validation failures

Use:

```text
examples/example_invalid_rows.csv
```

---

## Synthetic data disclaimer

The example fixtures are synthetic qualification and demonstration data. They do not replace qualified medicinal-chemistry review, assay compatibility review, statistical analysis, deployment qualification or regulatory review.
