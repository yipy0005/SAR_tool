ALTER TABLE pharmacophore_rgroup_assignments
    ADD COLUMN core_feature_delta_json TEXT NOT NULL DEFAULT '{}';
