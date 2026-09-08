CREATE TABLE IF NOT EXISTS rgroup_assignments (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    scaffold_smarts TEXT NOT NULL,
    match_atoms_json TEXT NOT NULL,
    assignments_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('assigned', 'unmatched', 'ambiguous', 'invalid')),
    failure_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_cliffs (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_a_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    compound_b_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    summary_a_id TEXT REFERENCES measurement_summaries(id) ON DELETE RESTRICT,
    summary_b_id TEXT REFERENCES measurement_summaries(id) ON DELETE RESTRICT,
    mcs_smarts TEXT NOT NULL,
    similarity REAL NOT NULL,
    transformation_json TEXT NOT NULL,
    effect_value REAL NOT NULL,
    effect_unit TEXT,
    assay_compatibility_key TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rgroup_assignments_run ON rgroup_assignments(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_activity_cliffs_run ON activity_cliffs(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_activity_cliffs_compounds ON activity_cliffs(compound_a_id, compound_b_id);
