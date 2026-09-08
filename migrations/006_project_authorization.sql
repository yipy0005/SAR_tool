ALTER TABLE import_batches
    ADD COLUMN created_by_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_import_batches_owner
    ON import_batches(created_by_user_id, created_at);
