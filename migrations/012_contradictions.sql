CREATE TABLE IF NOT EXISTS contradiction_observations (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    compatibility_key TEXT NOT NULL,
    summary_ids_json TEXT NOT NULL,
    values_json TEXT NOT NULL DEFAULT '{}',
    canonical_unit TEXT,
    status TEXT NOT NULL CHECK (status IN ('contradictory', 'unit_conflict', 'censoring_conflict')),
    reconciliation_status TEXT NOT NULL CHECK (reconciliation_status IN ('unreconciled', 'reviewed')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contradiction_observations_run
    ON contradiction_observations(analysis_run_id, status);
CREATE INDEX IF NOT EXISTS idx_contradiction_observations_compound
    ON contradiction_observations(compound_id, created_at);
