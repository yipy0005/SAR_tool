CREATE TABLE IF NOT EXISTS generated_recommendations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    information_gain_observation_id TEXT NOT NULL REFERENCES information_gain_observations(id) ON DELETE RESTRICT,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    recommendation_type TEXT NOT NULL CHECK (recommendation_type IN ('evidence_gap_experiment')),
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    expected_outcome TEXT NOT NULL,
    uncertainty TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    score_components_json TEXT NOT NULL,
    information_gain_score REAL NOT NULL CHECK (information_gain_score >= 0 AND information_gain_score <= 1),
    evidence_class TEXT NOT NULL CHECK (evidence_class = 'heuristic_evidence_gap'),
    status TEXT NOT NULL DEFAULT 'generated_review' CHECK (status IN ('generated_review', 'approved', 'rejected')),
    generated_algorithm_version TEXT NOT NULL,
    review_note TEXT,
    reviewed_by TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    UNIQUE (information_gain_observation_id, generated_algorithm_version)
);

CREATE INDEX IF NOT EXISTS idx_generated_recommendations_project
    ON generated_recommendations(project_id, status, information_gain_score DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_generated_recommendations_review
    ON generated_recommendations(status, created_at);
