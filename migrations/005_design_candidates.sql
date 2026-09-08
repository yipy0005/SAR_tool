CREATE TABLE IF NOT EXISTS design_candidates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    category TEXT NOT NULL CHECK (category IN ('exploit', 'test', 'fill', 'resolve', 'explore', 'rescue', 'challenge')),
    title TEXT NOT NULL,
    parent_compound_id TEXT REFERENCES compounds(id) ON DELETE RESTRICT,
    structure_smiles TEXT,
    hypothesis TEXT NOT NULL,
    rationale TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    expected_outcome TEXT NOT NULL,
    uncertainty TEXT NOT NULL,
    novelty_score REAL NOT NULL CHECK (novelty_score >= 0 AND novelty_score <= 1),
    feasibility_status TEXT NOT NULL CHECK (feasibility_status IN ('tractable', 'review', 'unreviewed', 'rejected')),
    feasibility_reasons_json TEXT NOT NULL DEFAULT '[]',
    information_gain_score REAL NOT NULL CHECK (information_gain_score >= 0 AND information_gain_score <= 1),
    objective_alignment_score REAL NOT NULL CHECK (objective_alignment_score >= 0 AND objective_alignment_score <= 1),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'selected', 'tested', 'rejected')),
    ranking_score REAL NOT NULL,
    ranking_components_json TEXT NOT NULL,
    ranking_version TEXT NOT NULL,
    data_origin TEXT NOT NULL DEFAULT 'curated',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_design_candidates_project_rank
    ON design_candidates(project_id, ranking_score DESC, created_at DESC);
