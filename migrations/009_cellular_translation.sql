CREATE TABLE IF NOT EXISTS translation_observations (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    biochemical_summary_id TEXT REFERENCES measurement_summaries(id) ON DELETE RESTRICT,
    cellular_summary_id TEXT REFERENCES measurement_summaries(id) ON DELETE RESTRICT,
    biochemical_value REAL,
    cellular_value REAL,
    translation_delta REAL,
    translation_loss REAL,
    canonical_unit TEXT,
    direction TEXT,
    status TEXT NOT NULL CHECK (status IN ('translated', 'attenuated', 'incomplete')),
    evidence_status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_translation_observations_run
    ON translation_observations(analysis_run_id, status);
CREATE INDEX IF NOT EXISTS idx_translation_observations_compound
    ON translation_observations(compound_id, created_at);
