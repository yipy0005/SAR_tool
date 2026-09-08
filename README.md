# SAR Workbench

Decision-centric Flask system for medicinal chemists and biologists. The product model is:

**raw experimental data → validated identities and measurements → evidence → hypotheses → designs → new learning**

The repository is being migrated from a UI prototype into a production scientific SAR system. The current implementation persists source documents and raw measurements, validates structures with RDKit, preserves qualifiers and censoring, supports quarantine-first CSV/TSV/XLSX imports, and protects production mode with a local session/CSRF boundary. Schema migrations currently apply through version 16, including measurement summaries, R-group/activity-cliff evidence, curated design candidates, project-scoped authorization, selectivity observations, persisted RDKit property profiles, cellular-translation observations, ADME evidence panels, Pareto observations, contradiction warnings, heuristic information-gap observations, generated recommendations requiring review, and quarantine-first CSV/TSV/XLSX profiling with source-cell provenance and formula policy.

## Current release boundary

The default `SAR_ENV=demo` mode renders the illustrative dashboard from `/Users/yipyewmun/GitHub/SAR_tool/sar_data.py`. Demo mode is intentionally labelled and must not be used for scientific decisions.

Production mode never falls back to that data. It reads only persisted records from the configured SQLite or PostgreSQL backend. Raw observations, derived analyses, curated designs, and generated recommendations remain separate; the UI and APIs expose derived SAR outputs only after a versioned run and never present a candidate as experimentally confirmed.

Implemented production foundation:

- Pixi-pinned Python, Flask, Pytest, and RDKit runtime.
- SQLite schema with forward-only migrations in `/Users/yipyewmun/GitHub/SAR_tool/migrations/`; current schema version is 16.
- Backend-aware database layer with an exact Pixi-pinned psycopg 3.3.4 adapter for PostgreSQL URLs, while preserving SQLite as the default local backend.
- Compound, structure record, source document, import batch, assay definition, assay run, raw measurement, measurement summary, hypothesis, analysis, claim, activity-cliff, R-group, design-candidate, and audit entities.
- RDKit sanitization, canonical/isomeric SMILES, InChIKey, stereochemistry status, disconnected-component warnings, and RDKit-rendered SVG structures.
- Explicit measurement units, qualifiers, missingness, lower/upper bounds, and concentration-to-pIC50 transformation provenance.
- Two-phase CSV/TSV/XLSX workflow: bounded profiling, quarantine preview, row-level validation, explicit mapping and formula policy review, explicit commit, source hashing, idempotence checks, and rollback on conflict.
- Production local login mode with secure session settings, CSRF protection, security headers, and fail-closed configuration validation.
- Project-scoped authorization for local identities: owner/editor/viewer roles, membership-filtered project reads, CSRF-protected member management, and ownership-scoped quarantined imports.
- Structured JSON request logs, request/status/endpoint counters, an authenticated operational metrics endpoint, and configurable stdout/file sink retention.
- Pixi-managed database initialization, readiness, SQLite backup/restore verification, and PostgreSQL custom-format backup/archive verification/restore-rehearsal commands.
- A separate WSGI entry point plus conservative single-worker Gunicorn, systemd, and HTTPS reverse-proxy examples in `/Users/yipyewmun/GitHub/SAR_tool/deploy/`.
- Deterministic RDKit MMP analysis with versioned analysis runs, assay compatibility grouping, MCS context, effect sizes, censored-value exclusion, and immutable observation claims linked to pair evidence.
- Deterministic replicate-aware measurement summaries with technical/biological/unspecified counts, assay compatibility checks, censoring/missingness preservation, dispersion, and source IDs.
- Explicit-scaffold R-group decomposition and activity-cliff analysis over observed compatible summaries only, with persisted versioned evidence.
- Explicit primary/comparator selectivity analysis over compatible summary units, with selective, non-selective, and incomplete states plus persisted source summary IDs and thresholds.
- Deterministic RDKit property profiles for persisted structures, with versioned analysis runs, explicit descriptor units, missing-structure states, and structure-record provenance.
- Cellular-translation analysis comparing explicit biochemical and cellular summary contexts, with unit/direction compatibility checks, loss thresholds, source summary IDs, and incomplete states.
- ADME/developability evidence panels over explicit assay contexts, retaining complete, censored, missing, and mixed evidence states per compound.
- Explicit-objective Pareto analysis over observed summaries and deterministic derived properties, with objective provenance, incomplete-input exclusion, and domination counts.
- Contradiction-reconciliation analysis that persists unit conflicts, divergent summary values, and exact-versus-censored conflicts as unreconciled review warnings.
- Curated design-candidate validation, RDKit structure checking, feasibility/novelty/information-gain metadata, transparent ranking components, and persistence that cannot mark a candidate experimentally confirmed.
- Heuristic information-gap analysis with explicit target contexts, replicate/censoring/missingness/contradiction components, persisted assumptions and limitations, and an explicit non-predictive uncertainty label.
- Generated evidence-gap recommendations that cite persisted observations, remain separate from curated design candidates, require human review, and cannot be marked experimentally confirmed.
- Project-scoped JSON/CSV exports with bounded row counts, raw/derived/curated/generated origin labels, provenance fields, and successful-export audit events.
- Production dashboard read models for raw measurements, derived summaries/analyses, curated design candidates, and generated recommendations with visible origin labels.

Not yet released for real project data:

- External SSO/identity provider integration beyond the local identity boundary. `/Users/yipyewmun/GitHub/SAR_tool/identity_provider.py` now validates an HTTPS OIDC/SAML configuration contract without implementing provider-specific token exchange or session mapping.
- Centralized observability sink, alerting, retention policy, and automated restore rehearsal in the target hosting environment.
- Target-host PostgreSQL failure recovery, physical-backup verification, and sustained deployment-level load qualification. The isolated synthetic PostgreSQL 16.4 run has passed schema 14, 13 scientific tables, 73 indexes, 62 foreign keys, advisory-locked concurrent migrations (4/4), transaction rollback, concurrent project/analysis writes, information-gap and generated-recommendation persistence/review, 8/8 two-worker readiness requests, and `pg_dump`/`pg_restore` into an isolated database.
- Browser end-to-end verification in this environment and qualified scientific review using synthetic fixtures.

## Run the demo locally

All Python dependencies and Python commands are managed through Pixi:

```bash
cd /Users/yipyewmun/GitHub/SAR_tool
pixi install
pixi run start
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001). Port `5001` avoids the macOS AirPlay Receiver conflict on port `5000`.

Run tests:

```bash
pixi run test
```

## Set up the local app with synthetic example data

For a Mac-only production-shaped smoke test, use the interactive Pixi command instead of the illustrative demo task:

```bash
cd /Users/yipyewmun/GitHub/SAR_tool
pixi install
pixi run local
```

The setup prompts for a local login email and password, lets you select the recommended drug-like SAR series or the compact smoke fixture, creates an isolated SQLite database under `/Users/yipyewmun/GitHub/SAR_tool/instance/local/`, writes the ignored `instance/local.env` file with mode `0600`, creates the owner project, and asks whether to import the selected measurements and contradiction follow-up. It can then start the pinned Gunicorn WSGI server at `http://127.0.0.1:5001`.

The recommended drug-like profile is a ten-compound aryl–methylene–amide–pyridyl series with H, methyl, methoxy, fluoro, chloro, CF3, heteroaryl, linker, N-methyl-amide, and morpholine variations. The first six compounds mirror the supplied SAR-diagram pattern: H, CH3, OCH3, F, Cl, and CF3 R¹ variants with IC50 values of 120, 250, 480, 45, 18, and 6 nM. The compact profile remains available for small importer smoke tests.

To select a profile non-interactively, pass `--dataset druglike` or `--dataset compact` to `/Users/yipyewmun/GitHub/SAR_tool/scripts/setup_local.py`.

For setup without starting the server, use:

```bash
pixi run setup-local
pixi run start-local
```

If loopback port `5001` is already occupied, reuse the existing setup on another port without rerunning setup:

```bash
SAR_GUNICORN_PORT=5010 pixi run start-local
open http://127.0.0.1:5010
```

The drug-like primary fixture is `/Users/yipyewmun/GitHub/SAR_tool/examples/example_druglike_measurements.csv` and its optional follow-up is `/Users/yipyewmun/GitHub/SAR_tool/examples/example_druglike_contradiction_followup.csv`. The compact smoke fixtures remain `/Users/yipyewmun/GitHub/SAR_tool/examples/example_measurements.csv` and `/Users/yipyewmun/GitHub/SAR_tool/examples/example_contradiction_followup.csv`. The complete import and analysis sequence is documented in `/Users/yipyewmun/GitHub/SAR_tool/examples/README.md`.

The local setup defaults `SAR_SECURE_COOKIES=false` because this smoke test uses plain loopback HTTP. Use `SAR_SECURE_COOKIES=true` only when the app is behind an HTTPS reverse proxy. Generated credentials, uploads, and the local database remain under the ignored `instance/` directory. To start over without deleting the existing setup, provide a different `--database`, `--upload-dir`, and `--env-file` path to `scripts/setup_local.py`.

## Run the persisted data foundation

Create or upgrade the local SQLite schema:

```bash
pixi run db-init
pixi run db-check
```

Create a consistent backup through SQLite’s backup API:

```bash
SAR_DATABASE_PATH=/secure/path/sar.db \
SAR_UPLOAD_DIR=/secure/path/uploads \
pixi run python manage.py backup-db --output /secure/path/backups/sar-$(date +%Y%m%d).db

pixi run python manage.py verify-backup \
  --input /secure/path/backups/sar-$(date +%Y%m%d).db

pixi run python manage.py restore-check \
  --input /secure/path/backups/sar-$(date +%Y%m%d).db
```

The SQLite boundary is deliberate: one controlled application instance or a small deployment with one writer at a time, reliable local storage, and tested backups. The database layer also accepts PostgreSQL URLs through the exact Pixi-pinned `psycopg==3.3.4` adapter:

```bash
export SAR_DATABASE_PATH='postgresql://<user>:<password>@<host>:5432/<database>'
pixi run db-init
pixi run db-check
```

The isolated synthetic PostgreSQL qualification has passed schema 14, constraint review, load/concurrency testing, information-gap and recommendation workflows, two-worker readiness, and restore rehearsal. Use `postgres-backup` for atomic custom-format archives, `postgres-verify-backup` for archive or physical-backup verification, and `postgres-restore-check` with `SAR_RESTORE_DATABASE_PATH` for an isolated target. Target hosts must provide approved `pg_dump`, `pg_restore`, and `pg_verifybackup` binaries or set the corresponding `SAR_PG_*_BIN` overrides; do not place database credentials in repository files or command history. Target-host failure recovery, physical-backup verification, centralized operations, deployment-level load, and restore rehearsal remain external release gates.

Move to PostgreSQL before sustained multi-worker writes or multiple application replicas.

## Local production-mode smoke configuration

Production mode refuses demo fallback, missing database configuration, weak secrets, and missing local credentials. Generate a password hash through Pixi, not a system Python:

```bash
SAR_AUTH_PASSWORD_HASH="$(pixi run python -c 'from werkzeug.security import generate_password_hash; print(generate_password_hash("change-this-password"))')"
export SAR_ENV=production
export SAR_DEMO_MODE=false
export SAR_DATABASE_PATH=/secure/path/sar.db
export SAR_UPLOAD_DIR=/secure/path/uploads
export SAR_SECRET_KEY='replace-with-a-long-random-secret'
export SAR_AUTH_MODE=local
export SAR_AUTH_EMAIL=scientist@example.org
export SAR_AUTH_PASSWORD_HASH
export SAR_SECURE_COOKIES=true
pixi run db-init
pixi run start
```

This local auth mode is a first deployment boundary, not an enterprise SSO implementation. Migration 006 does not guess ownership for pre-existing projects; assign an owner membership explicitly before those projects are exposed to a local identity. Do not expose the development Flask server directly to the public internet; use the pinned WSGI entry point, HTTPS-terminating reverse proxy, restricted host configuration, non-privileged service account, and secret manager. Deployment examples and the SQLite one-writer contract are documented in `/Users/yipyewmun/GitHub/SAR_tool/deploy/README.md`.

## API foundation

Demo endpoints remain available only in demo mode:

- `GET /api/overview`
- `GET /api/compounds`
- `GET /api/matrix/<endpoint>`
- `POST /api/hypotheses`

Production-shaped endpoints:

- `GET /api/v1/readyz`
- `GET, POST /api/v1/projects`
- `GET /api/v1/projects/<project_id>/members`
- `POST /api/v1/projects/<project_id>/members`
- `GET /api/v1/metrics`
- `POST /api/v1/structure/standardize`
- `POST /api/v1/imports/profile`
- `POST /api/v1/imports/preview`
- `GET /api/v1/imports/<import_id>`
- `POST /api/v1/imports/<import_id>/commit`
- `GET /api/v1/compounds?project_id=...`
- `GET /api/v1/measurements?project_id=...`
- `GET, POST /api/v1/hypotheses`
- `POST /api/v1/measurement-summaries`
- `GET /api/v1/measurement-summaries/<summary_id>`
- `GET /api/v1/measurement-summaries?project_id=...`
- `POST /api/v1/measurement-summaries/project`
- `POST /api/v1/analysis/mmp`
- `GET /api/v1/analysis/mmp/<run_id>`
- `POST /api/v1/analysis/mmp/<run_id>/claims`
- `POST /api/v1/analysis/rgroup`
- `POST /api/v1/analysis/activity-cliffs`
- `POST /api/v1/analysis/selectivity`
- `GET /api/v1/analysis/selectivity/<run_id>`
- `POST /api/v1/analysis/properties`
- `GET /api/v1/analysis/properties/<run_id>`
- `POST /api/v1/analysis/cellular-translation`
- `GET /api/v1/analysis/cellular-translation/<run_id>`
- `POST /api/v1/analysis/adme`
- `GET /api/v1/analysis/adme/<run_id>`
- `POST /api/v1/analysis/pareto`
- `GET /api/v1/analysis/pareto/<run_id>`
- `POST /api/v1/analysis/contradictions`
- `GET /api/v1/analysis/contradictions/<run_id>`
- `GET /api/v1/sar/claims?project_id=...`
- `POST /api/v1/analysis/information-gain`
- `GET /api/v1/analysis/information-gain/<run_id>`
- `POST /api/v1/recommendations/generate`
- `GET /api/v1/recommendations?project_id=...`
- `POST /api/v1/recommendations/<recommendation_id>/review`
- `GET /api/v1/exports/project/<project_id>?format=json|csv`
- `POST /api/v1/designs`
- `GET /api/v1/designs?project_id=...`

A production import is intentionally two-phase. Preview stores source content outside the static web root, reports inferred mappings and row-level errors, and creates no authoritative measurements. Commit requires a project, validates the quarantined rows again, writes all scientific records transactionally, and records an audit event.

## Scientific integrity rules

- Registered compound IDs and chemical identity are separate concepts.
- Original structure input is retained alongside standardized identity.
- Stereochemistry is preserved; undefined stereochemistry is flagged.
- Disconnected components are preserved and parent identity is reported separately.
- Censored values are not converted into exact observations. For example, `IC50 > 10 nM` becomes `pIC50 < 8`.
- Missing, invalid, not-run, not-reported, and not-detected states remain distinguishable.
- Raw measurements are not overwritten by aggregation.
- Selectivity comparisons require explicit primary/comparator assay contexts and matching canonical units; censored, missing, or non-exact summaries remain incomplete rather than becoming margins.
- RDKit property profiles are deterministic derived descriptors linked to the exact structure record and algorithm version; they do not replace experimental measurements or uncertainty review.
- Cellular translation requires explicit biochemical and cellular assay contexts, matching canonical units and directions, exact observed summaries, and a declared loss threshold; censored or missing inputs remain incomplete.
- ADME panels retain each requested assay context separately and never convert censored, missing, or mixed evidence into complete developability claims.
- Pareto objectives must identify their source and direction; observed summaries and derived properties retain separate evidence classes, while incomplete objectives are excluded from the front.
- Contradiction analysis preserves conflicting summary IDs and marks warnings unreconciled until qualified review resolves them.
- Information-gap scores are assumption-bound heuristics, not predictive or model-based expected information gain; raw and weighted components, context priorities, replicate thresholds, and limitations remain persisted.
- Generated recommendations cite persisted evidence-gap observations, remain separate from curated design candidates, require explicit human review, and are never experimentally confirmed by the workflow.
- Exports preserve project scope, provenance, data-origin labels, and audit events; bounded export safety limits do not silently truncate scientific records.
- Imported data, derived values, hypotheses, and predictions must remain distinct.
- No production SAR statement is valid until it points to source measurements, assay context, transformation policy, analysis version, and contradictory evidence handling.

## Test and release gates

The current production slice is validated with `pixi run test` (87 tests), including structure identity, invalid-structure rejection, censored pIC50 conversion, quarantine/commit behavior, transaction rollback, duplicate and concurrent import protection, upload confinement, high-volume upload/export limits, malformed-body and safe-error handling, project-scoped evidence validation, authorization revocation races, provenance, schema-14 migration idempotence, strict current-schema/scientific-table readiness, backup integrity verification, isolated restore rehearsal, deterministic RDKit property profiles, local login, CSRF rejection, authenticated mutation, project membership and role enforcement, security headers, structured metrics and log retention, deterministic MMP analysis, replicate-aware summaries, R-group/activity-cliff analysis, primary/comparator selectivity analysis, transparent design ranking, information-gap heuristics, generated recommendation review boundaries, project-scoped JSON/CSV exports, PostgreSQL backend behavior, PostgreSQL advisory migration locking, PostgreSQL backup/archive operations, SQLite multi-worker refusal, production API integration, deployment read-model rendering, external identity boundary validation, and immutable claim evidence links.

Before accepting real proprietary data, complete the remaining gates:

1. Add external SSO/identity-provider integration beyond the local identity boundary.
2. Extend adversarial testing to higher-volume uploads, exports, authorization races, and deployment-level concurrency.
3. Add a centralized observability sink, alerting, retention policy, and automated restore rehearsal in the target hosting environment.
4. Complete target-host PostgreSQL operational qualification: failure recovery, physical backup verification, deployment-level load, and restore rehearsal before sustained multi-worker or multi-replica writes. The isolated synthetic PostgreSQL 16.4 migration, constraint, concurrency, readiness, information-gap, generated-recommendation, export, and logical-backup gates have passed.
5. Add browser end-to-end verification and a qualified scientific review using synthetic fixtures.

The application organizes and traces scientific evidence; it does not replace qualified medicinal-chemistry, biology, statistics, or regulatory review.
