CREATE TABLE IF NOT EXISTS measurement_summaries (
    id TEXT PRIMARY KEY,
    compound_id TEXT NOT NULL REFERENCES compounds(id) ON DELETE RESTRICT,
    compatibility_key TEXT NOT NULL,
    canonical_unit TEXT,
    replicate_group_id TEXT,
    summary_state TEXT NOT NULL CHECK (
        summary_state IN (
            'observed',
            'observed_with_censored',
            'observed_with_missing',
            'observed_with_censored_and_missing',
            'censored',
            'censored_with_missing',
            'missing'
        )
    ),
    summary_value REAL,
    summary_qualifier TEXT CHECK (
        summary_qualifier IN ('=', '<', '<=', '>', '>=', '~')
        OR summary_qualifier IS NULL
    ),
    lower_bound REAL,
    upper_bound REAL,
    technical_replicate_count INTEGER NOT NULL DEFAULT 0 CHECK (technical_replicate_count >= 0),
    biological_replicate_count INTEGER NOT NULL DEFAULT 0 CHECK (biological_replicate_count >= 0),
    unspecified_replicate_count INTEGER NOT NULL DEFAULT 0 CHECK (unspecified_replicate_count >= 0),
    technical_replicate_group_count INTEGER NOT NULL DEFAULT 0 CHECK (technical_replicate_group_count >= 0),
    biological_replicate_group_count INTEGER NOT NULL DEFAULT 0 CHECK (biological_replicate_group_count >= 0),
    eligible_measurement_count INTEGER NOT NULL DEFAULT 0 CHECK (eligible_measurement_count >= 0),
    censored_measurement_count INTEGER NOT NULL DEFAULT 0 CHECK (censored_measurement_count >= 0),
    missing_measurement_count INTEGER NOT NULL DEFAULT 0 CHECK (missing_measurement_count >= 0),
    missing_reasons_json TEXT NOT NULL DEFAULT '[]',
    source_measurement_ids_json TEXT NOT NULL,
    assay_definition_ids_json TEXT NOT NULL DEFAULT '[]',
    assay_run_ids_json TEXT NOT NULL DEFAULT '[]',
    aggregation_method TEXT NOT NULL,
    dispersion REAL,
    dispersion_method TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_measurement_summaries_compound
    ON measurement_summaries(compound_id, compatibility_key, canonical_unit);
CREATE INDEX IF NOT EXISTS idx_measurement_summaries_version
    ON measurement_summaries(analysis_version, created_at);
