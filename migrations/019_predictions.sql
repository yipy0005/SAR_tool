CREATE TABLE IF NOT EXISTS prediction_models (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    compatibility_key TEXT NOT NULL,
    prediction_unit TEXT,
    feature_profile TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    model_status TEXT NOT NULL CHECK (model_status IN ('candidate', 'internally_validated', 'rejected')),
    validation_scope TEXT NOT NULL CHECK (validation_scope IN ('internal_leave_one_out', 'project_holdout')),
    input_selection_json TEXT NOT NULL,
    training_records_json TEXT NOT NULL,
    hyperparameters_json TEXT NOT NULL,
    validation_metrics_json TEXT NOT NULL,
    applicability_policy_json TEXT NOT NULL,
    review_note TEXT NOT NULL DEFAULT '',
    created_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    reviewed_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS prediction_observations (
    id TEXT PRIMARY KEY,
    prediction_model_id TEXT NOT NULL REFERENCES prediction_models(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    target_summary_id TEXT REFERENCES measurement_summaries(id) ON DELETE RESTRICT,
    prediction_value REAL,
    prediction_unit TEXT,
    observed_value REAL,
    observed_unit TEXT,
    error_value REAL,
    uncertainty_json TEXT NOT NULL DEFAULT '{}',
    applicability_status TEXT NOT NULL CHECK (applicability_status IN ('eligible', 'borderline', 'out_of_domain', 'not_predicted')),
    nearest_compound_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE (prediction_model_id, compound_id)
);

CREATE INDEX IF NOT EXISTS idx_prediction_models_project ON prediction_models(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_prediction_observations_model ON prediction_observations(prediction_model_id, applicability_status);
CREATE INDEX IF NOT EXISTS idx_prediction_observations_compound ON prediction_observations(compound_id);