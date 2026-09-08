CREATE TABLE IF NOT EXISTS information_gain_observations (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE RESTRICT,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    gap_type TEXT NOT NULL CHECK (gap_type IN ('missing_context', 'censored_context', 'contradictory_context', 'replicate_uncertainty')),
    target_key TEXT NOT NULL,
    priority_score REAL NOT NULL CHECK (priority_score >= 0 AND priority_score <= 1),
    evidence_class TEXT NOT NULL CHECK (evidence_class = 'heuristic_evidence_gap'),
    score_components_json TEXT NOT NULL,
    uncertainty_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'addressed', 'not_actionable')),
    created_at TEXT NOT NULL,
    UNIQUE (analysis_run_id, compound_id, gap_type, target_key)
);

CREATE INDEX IF NOT EXISTS idx_information_gain_run_score
    ON information_gain_observations(analysis_run_id, priority_score DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_information_gain_compound
    ON information_gain_observations(compound_id, target_key, status);
