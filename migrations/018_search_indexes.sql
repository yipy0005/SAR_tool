CREATE INDEX IF NOT EXISTS idx_compounds_project_registration ON compounds(project_id, registration_id);
CREATE INDEX IF NOT EXISTS idx_compounds_project_name ON compounds(project_id, preferred_name);
CREATE INDEX IF NOT EXISTS idx_measurements_compound_created ON measurements(compound_id, created_at);
CREATE INDEX IF NOT EXISTS idx_assay_runs_qc_status ON assay_runs(qc_status, created_at);