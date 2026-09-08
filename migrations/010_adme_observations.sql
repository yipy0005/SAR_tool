CREATE TABLE IF NOT EXISTS adme_observations (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    required_contexts_json TEXT NOT NULL,
    context_values_json TEXT NOT NULL,
    observed_contexts_json TEXT NOT NULL DEFAULT '[]',
    censored_contexts_json TEXT NOT NULL DEFAULT '[]',
    missing_contexts_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('complete_observed', 'incomplete_censored', 'incomplete_missing', 'incomplete_mixed')
    ),
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_adme_observations_run
    ON adme_observations(analysis_run_id, status);
CREATE INDEX IF NOT EXISTS idx_adme_observations_compound
    ON adme_observations(compound_id, created_at);
