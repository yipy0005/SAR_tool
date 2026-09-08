CREATE TABLE IF NOT EXISTS property_profiles (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    structure_record_id TEXT REFERENCES structure_records(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('computed', 'missing_structure', 'invalid_structure')),
    reason TEXT,
    descriptors_json TEXT NOT NULL DEFAULT '{}',
    uncertainty_json TEXT NOT NULL DEFAULT '{}',
    algorithm_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_property_profiles_run
    ON property_profiles(analysis_run_id, status);
CREATE INDEX IF NOT EXISTS idx_property_profiles_project_compound
    ON property_profiles(project_id, compound_id, created_at);
