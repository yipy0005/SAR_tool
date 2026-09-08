ALTER TABLE import_batches ADD COLUMN formula_acknowledged INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_batches ADD COLUMN formula_policy_version TEXT NOT NULL DEFAULT 'formula-safe-v1';
ALTER TABLE import_batches ADD COLUMN mapping_issues_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_import_batches_formula_policy
    ON import_batches(formula_policy_version, formula_acknowledged, created_at);
