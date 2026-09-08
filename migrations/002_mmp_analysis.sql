CREATE TABLE IF NOT EXISTS mmp_pairs (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_a_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    compound_b_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    measurement_a_ids_json TEXT NOT NULL,
    measurement_b_ids_json TEXT NOT NULL,
    mcs_smarts TEXT NOT NULL,
    similarity REAL NOT NULL,
    transformation_json TEXT NOT NULL,
    effect_value REAL NOT NULL,
    effect_unit TEXT NOT NULL,
    applicability_status TEXT NOT NULL DEFAULT 'eligible',
    created_at TEXT NOT NULL
);

ALTER TABLE claim_evidence ADD COLUMN mmp_pair_id TEXT REFERENCES mmp_pairs(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_mmp_pairs_run ON mmp_pairs(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_mmp_pairs_compounds ON mmp_pairs(compound_a_id, compound_b_id);
