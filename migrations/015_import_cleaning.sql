ALTER TABLE source_documents ADD COLUMN file_format TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE source_documents ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE import_batches ADD COLUMN selected_sheet TEXT;
ALTER TABLE import_batches ADD COLUMN header_row INTEGER NOT NULL DEFAULT 1;
ALTER TABLE import_batches ADD COLUMN data_start_row INTEGER NOT NULL DEFAULT 2;
ALTER TABLE import_batches ADD COLUMN cleaning_policy_version TEXT NOT NULL DEFAULT 'import-clean-v1';
ALTER TABLE import_batches ADD COLUMN mapping_confidence_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE import_rows ADD COLUMN source_location_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_import_batches_cleaning_state
    ON import_batches(status, cleaning_policy_version, created_at);
CREATE INDEX IF NOT EXISTS idx_import_rows_source_location
    ON import_rows(import_batch_id, source_row_id);
