CREATE TABLE IF NOT EXISTS selectivity_observations (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    primary_summary_id TEXT REFERENCES measurement_summaries(id) ON DELETE RESTRICT,
    comparator_summary_id TEXT REFERENCES measurement_summaries(id) ON DELETE RESTRICT,
    primary_value REAL,
    comparator_value REAL,
    selectivity_delta REAL,
    canonical_unit TEXT,
    status TEXT NOT NULL CHECK (status IN ('selective', 'non_selective', 'incomplete')),
    evidence_status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_selectivity_observations_run
    ON selectivity_observations(analysis_run_id, status);
CREATE INDEX IF NOT EXISTS idx_selectivity_observations_compound
    ON selectivity_observations(compound_id, created_at);
