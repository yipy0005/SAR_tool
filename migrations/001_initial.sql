CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    data_origin TEXT NOT NULL DEFAULT 'imported',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'scientist',
    password_hash TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    byte_size INTEGER NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    storage_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    mapping_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('preview', 'committed', 'rejected')),
    accepted_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS import_rows (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    source_row_id TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    normalized_json TEXT,
    validation_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'committed')),
    UNIQUE (import_batch_id, source_row_id)
);

CREATE TABLE IF NOT EXISTS compounds (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    registration_id TEXT NOT NULL,
    preferred_name TEXT NOT NULL DEFAULT '',
    lifecycle_status TEXT NOT NULL DEFAULT 'registered',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, registration_id)
);

CREATE TABLE IF NOT EXISTS structure_records (
    id TEXT PRIMARY KEY,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    raw_input TEXT NOT NULL,
    input_format TEXT NOT NULL,
    original_molblock TEXT NOT NULL,
    standardized_molblock TEXT NOT NULL,
    canonical_smiles TEXT NOT NULL,
    isomeric_smiles TEXT NOT NULL,
    inchikey TEXT,
    parent_canonical_smiles TEXT NOT NULL,
    parent_inchikey TEXT,
    component_count INTEGER NOT NULL DEFAULT 1,
    stereochemistry_status TEXT NOT NULL,
    standardization_profile TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    rendered_svg TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS structure_components (
    id TEXT PRIMARY KEY,
    structure_record_id TEXT NOT NULL REFERENCES structure_records(id) ON DELETE CASCADE,
    component_index INTEGER NOT NULL,
    canonical_smiles TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'component',
    UNIQUE (structure_record_id, component_index)
);

CREATE TABLE IF NOT EXISTS assay_definitions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    endpoint_code TEXT NOT NULL,
    modality TEXT NOT NULL DEFAULT 'unspecified',
    canonical_unit TEXT,
    direction TEXT NOT NULL DEFAULT 'higher_is_better',
    compatibility_key TEXT NOT NULL,
    protocol_version TEXT NOT NULL DEFAULT 'unversioned',
    created_at TEXT NOT NULL,
    UNIQUE (project_id, name, endpoint_code, protocol_version)
);

CREATE TABLE IF NOT EXISTS assay_runs (
    id TEXT PRIMARY KEY,
    assay_definition_id TEXT NOT NULL REFERENCES assay_definitions(id) ON DELETE RESTRICT,
    source_document_id TEXT REFERENCES source_documents(id) ON DELETE RESTRICT,
    run_date TEXT,
    biological_context_json TEXT NOT NULL DEFAULT '{}',
    qc_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measurements (
    id TEXT PRIMARY KEY,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    assay_run_id TEXT NOT NULL REFERENCES assay_runs(id) ON DELETE RESTRICT,
    replicate_group_id TEXT,
    replicate_type TEXT NOT NULL DEFAULT 'unspecified',
    replicate_index INTEGER,
    raw_value_text TEXT NOT NULL,
    value_numeric REAL,
    unit_ucum TEXT,
    qualifier TEXT CHECK (qualifier IN ('=', '<', '<=', '>', '>=', '~') OR qualifier IS NULL),
    lower_bound REAL,
    upper_bound REAL,
    canonical_value REAL,
    canonical_unit TEXT,
    transform_id TEXT,
    missing_reason TEXT,
    source_row_id TEXT NOT NULL,
    well_id TEXT,
    qc_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TEXT NOT NULL,
    UNIQUE (assay_run_id, source_row_id)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    analysis_type TEXT NOT NULL,
    input_selection_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sar_claims (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    claim_type TEXT NOT NULL CHECK (claim_type IN ('observation', 'interpretation', 'prediction')),
    statement TEXT NOT NULL,
    scope_definition_json TEXT NOT NULL,
    effect_json TEXT NOT NULL DEFAULT '{}',
    uncertainty_json TEXT NOT NULL DEFAULT '{}',
    evidence_strength TEXT NOT NULL DEFAULT 'preliminary',
    status TEXT NOT NULL DEFAULT 'active',
    analysis_run_id TEXT REFERENCES analysis_runs(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL DEFAULT 1,
    supersedes_claim_id TEXT REFERENCES sar_claims(id) ON DELETE RESTRICT,
    created_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES sar_claims(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    measurement_id TEXT REFERENCES measurements(id) ON DELETE RESTRICT,
    compound_id TEXT REFERENCES compounds(id) ON DELETE RESTRICT,
    relationship TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    statement TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'untested',
    created_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT,
    project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    request_id TEXT,
    outcome TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_compounds_project ON compounds(project_id);
CREATE INDEX IF NOT EXISTS idx_structures_compound ON structure_records(compound_id);
CREATE INDEX IF NOT EXISTS idx_assays_project ON assay_definitions(project_id);
CREATE INDEX IF NOT EXISTS idx_measurements_compound ON measurements(compound_id);
CREATE INDEX IF NOT EXISTS idx_measurements_assay ON measurements(assay_run_id);
CREATE INDEX IF NOT EXISTS idx_import_rows_batch ON import_rows(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_claims_project ON sar_claims(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_project_time ON audit_events(project_id, created_at);
