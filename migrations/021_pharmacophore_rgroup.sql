CREATE TABLE IF NOT EXISTS pharmacophore_rgroup_assignments (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    reference_compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    scaffold_smarts TEXT NOT NULL,
    match_atoms_json TEXT NOT NULL,
    sites_json TEXT NOT NULL,
    features_json TEXT NOT NULL,
    core_features_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('assigned', 'unmatched', 'ambiguous', 'invalid')),
    failure_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pharmacophore_rgroup_run
    ON pharmacophore_rgroup_assignments(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_pharmacophore_rgroup_compound
    ON pharmacophore_rgroup_assignments(compound_id);
CREATE INDEX IF NOT EXISTS idx_pharmacophore_rgroup_reference
    ON pharmacophore_rgroup_assignments(reference_compound_id);
