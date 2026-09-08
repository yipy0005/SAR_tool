CREATE TABLE IF NOT EXISTS pareto_observations (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    objective_values_json TEXT NOT NULL,
    objective_status TEXT NOT NULL CHECK (objective_status IN ('complete', 'incomplete')),
    is_pareto INTEGER NOT NULL DEFAULT 0 CHECK (is_pareto IN (0, 1)),
    domination_count INTEGER,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pareto_observations_run
    ON pareto_observations(analysis_run_id, is_pareto, objective_status);
CREATE INDEX IF NOT EXISTS idx_pareto_observations_compound
    ON pareto_observations(compound_id, created_at);
