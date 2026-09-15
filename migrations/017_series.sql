CREATE TABLE IF NOT EXISTS series (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    current_version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS series_versions (
    id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    membership_source TEXT NOT NULL CHECK (membership_source IN ('curated', 'rule_derived', 'analysis_derived', 'import_derived')),
    rationale TEXT NOT NULL DEFAULT '',
    created_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE (series_id, version)
);

CREATE TABLE IF NOT EXISTS series_memberships (
    id TEXT PRIMARY KEY,
    series_version_id TEXT NOT NULL REFERENCES series_versions(id) ON DELETE CASCADE,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    membership_source TEXT NOT NULL CHECK (membership_source IN ('curated', 'rule_derived', 'analysis_derived', 'import_derived')),
    rationale TEXT NOT NULL DEFAULT '',
    membership_status TEXT NOT NULL DEFAULT 'included' CHECK (membership_status IN ('included', 'excluded', 'needs_review')),
    created_at TEXT NOT NULL,
    UNIQUE (series_version_id, compound_id)
);

CREATE INDEX IF NOT EXISTS idx_series_project ON series(project_id, status);
CREATE INDEX IF NOT EXISTS idx_series_versions_series ON series_versions(series_id, version);
CREATE INDEX IF NOT EXISTS idx_series_memberships_version ON series_memberships(series_version_id, membership_status);
CREATE INDEX IF NOT EXISTS idx_series_memberships_compound ON series_memberships(compound_id);