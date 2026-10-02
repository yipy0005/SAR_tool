CREATE TABLE IF NOT EXISTS compound_relationships (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    relationship_type TEXT NOT NULL CHECK (relationship_type IN ('prodrug_of')),
    prodrug_compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    active_compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    review_status TEXT NOT NULL DEFAULT 'needs_review' CHECK (review_status IN ('needs_review', 'reviewed', 'rejected')),
    relationship_source TEXT NOT NULL DEFAULT 'bulk_mapping' CHECK (relationship_source IN ('bulk_mapping', 'manual', 'imported')),
    activation_context_json TEXT NOT NULL DEFAULT '{}',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    created_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, relationship_type, prodrug_compound_id, active_compound_id),
    CHECK (prodrug_compound_id <> active_compound_id)
);

CREATE INDEX IF NOT EXISTS idx_compound_relationships_project
    ON compound_relationships(project_id, relationship_type, review_status);
CREATE INDEX IF NOT EXISTS idx_compound_relationships_prodrug
    ON compound_relationships(prodrug_compound_id);
CREATE INDEX IF NOT EXISTS idx_compound_relationships_active
    ON compound_relationships(active_compound_id);
