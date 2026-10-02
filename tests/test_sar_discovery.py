from sar_discovery import discover_series


def test_discover_series_groups_shared_scaffold_and_ranks_lower_ic50_as_better() -> None:
    compounds = [
        {"compound_id": "c1", "registration_id": "POS-001", "isomeric_smiles": "Cc1ccccc1"},
        {"compound_id": "c2", "registration_id": "POS-002", "isomeric_smiles": "CCc1ccccc1"},
        {"compound_id": "c3", "registration_id": "POS-003", "isomeric_smiles": "CCOc1ccccc1"},
    ]
    summaries = [
        {"id": "s1", "compound_id": "c1", "compatibility_key": "IC50:import-v1", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 100.0, "created_at": "2026-01-01"},
        {"id": "s2", "compound_id": "c2", "compatibility_key": "IC50:import-v1", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 10.0, "created_at": "2026-01-01"},
        {"id": "s3", "compound_id": "c3", "compatibility_key": "IC50:import-v1", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 30.0, "created_at": "2026-01-01"},
    ]

    result = discover_series(compounds, summaries, "IC50:import-v1")

    assert result["series_count"] == 1
    series = result["series"][0]
    assert series["member_count"] == 3
    assert series["direction"] == "lower"
    assert series["best_member"]["registration_id"] == "POS-002"
    assert series["min_value"] == 10.0
    assert series["max_value"] == 100.0
    assert series["membership_source"] == "analysis_derived"
    assert series["reference_registration_id"] == "POS-001"
    members = {member["registration_id"]: member for member in series["members"]}
    assert members["POS-001"]["structural_change"]["status"] == "assigned"
    assert members["POS-002"]["structural_change"]["status"] == "assigned"
    assert "CC" in members["POS-002"]["structural_change"]["label"]
    assert members["POS-002"]["endpoint_delta"] == -90.0
    assert members["POS-002"]["endpoint_effect"] == "better"


def test_selected_compound_returns_observed_local_series() -> None:
    compounds = [
        {"compound_id": "c1", "registration_id": "POS-001", "isomeric_smiles": "Cc1ccccc1"},
        {"compound_id": "c2", "registration_id": "POS-002", "isomeric_smiles": "CCc1ccccc1"},
    ]
    summaries = [
        {"id": "s1", "compound_id": "c1", "compatibility_key": "Cellular:import-v1", "canonical_unit": "pIC50", "summary_state": "observed", "summary_value": 5.4, "created_at": "2026-01-01"},
        {"id": "s2", "compound_id": "c2", "compatibility_key": "Cellular:import-v1", "canonical_unit": "pIC50", "summary_state": "observed", "summary_value": 6.2, "created_at": "2026-01-01"},
    ]

    result = discover_series(compounds, summaries, "Cellular:import-v1", mode="selected", selected_compound_id="c1")

    assert result["mode"] == "selected"
    assert result["series_count"] == 1
    assert result["series"][0]["selected_compound_id"] == "c1"
    assert result["series"][0]["direction"] == "higher"
    assert {member["registration_id"] for member in result["series"][0]["members"]} == {"POS-001", "POS-002"}


def test_sar_discovery_api_uses_project_summaries(tmp_path) -> None:
    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-sar",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )
    client = app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "SAR discovery fixture"}).get_json()["project"]["id"]
    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier,replicate,date\n"
        "POS-001,Cc1ccccc1,IC50,100,nM,=,1,2026-01-01\n"
        "POS-002,CCc1ccccc1,IC50,10,nM,=,1,2026-01-01\n"
    )
    preview = client.post(
        "/api/v1/imports/preview",
        json={"project_id": project_id, "filename": "sar.csv", "csv_text": csv_text},
    )
    assert preview.status_code == 201
    import_id = preview.get_json()["import_id"]
    committed = client.post("/api/v1/imports/%s/commit" % import_id, json={"project_id": project_id})
    assert committed.status_code == 200
    summaries = client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id})
    assert summaries.status_code == 201
    overview = client.get(f"/?project_id={project_id}")
    assert overview.status_code == 200
    overview_html = overview.get_data(as_text=True)
    assert "All compounds × all endpoints" in overview_html
    assert "data-sar-matrix" in overview_html
    assert "SAR series" in overview_html
    sar_page = client.get(f"/workspace/sar?project_id={project_id}")
    assert sar_page.status_code == 200
    assert "IC50" in sar_page.get_data(as_text=True)

    response = client.post(
        "/api/v1/sar/discover",
        json={"project_id": project_id, "endpoint_key": "IC50:import-v1", "mode": "discover"},
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["series_count"] == 1
    assert payload["series"][0]["best_member"]["registration_id"] == "POS-002"
    assert payload["series"][0]["reference_registration_id"] == "POS-001"
    assert payload["series"][0]["members"][0]["rendered_svg"].startswith("<svg")
    assert payload["series"][0]["members"][1]["structural_change"]["status"] == "assigned"


def test_wide_endpoint_preview_expands_common_columns_with_source_provenance() -> None:
    from import_pipeline import preview_upload

    csv_text = (
        "ID,SMILES,IC50_nM,Cellular_pIC50,LogP\n"
        "POS-001,Cc1ccccc1,100,5.4,2.1\n"
        "POS-002,CCc1ccccc1,10,6.2,2.8\n"
    )
    result = preview_upload(csv_text.encode("utf-8"), "wide.csv")

    assert result["missing_mapping_fields"] == []
    assert set(result["mapping"]["wide_endpoint_mapping"]) == {"IC50_nM", "Cellular_pIC50", "LogP"}
    assert result["accepted_count"] == 6
    assert result["rejected_count"] == 0
    assert {row.normalized["assay"] for row in result["rows"] if row.normalized} == {"IC50", "Cellular", "LogP"}
    assert all("wide_endpoint" in row.source_location for row in result["rows"])


def _site_groups(changes):
    return [
        {fragment["label"]: fragment["smiles"] for fragment in change["fragments"]}
        for change in changes
    ]


def test_consistent_substituents_uses_one_numbering_on_a_symmetric_core() -> None:
    from rdkit import Chem

    from sar_discovery import assign_position_labels, consistent_substituents

    # Benzene core: RDKit's first match would call the side chain R1 in one compound and R2 in another.
    smiles = [
        "O=C(Cc1ccccc1)Nc1ccccn1",
        "Cc1ccc(CC(=O)Nc2ccccn2)cc1",
        "O=C(Cc1ccc(F)cc1)Nc1ccccn1",
        "O=C(Cc1ccc(Cl)cc1)Nc1ccccn1",
    ]
    changes = consistent_substituents([Chem.MolFromSmiles(item) for item in smiles], Chem.MolFromSmarts("c1ccccc1"))
    sites = assign_position_labels(changes)
    groups = _site_groups(changes)

    assert [site["label"] for site in sites] == ["R1", "R2"]
    side_chain_sites = {label for group in groups for label, fragment in group.items() if "n" in fragment}
    assert len(side_chain_sites) == 1
    other = ({"R1", "R2"} - side_chain_sites).pop()
    assert [group.get(other) for group in groups] == [None, "C", "F", "Cl"]


def test_replace_substituent_swaps_only_the_chosen_site() -> None:
    from rdkit import Chem

    from sar_discovery import SarDiscoveryError, assign_position_labels, consistent_substituents, replace_substituent

    molecules = [Chem.MolFromSmiles(item) for item in ["O=C(Cc1ccc(F)cc1)Nc1ccccn1", "O=C(Cc1ccccc1)Nc1ccccn1"]]
    scaffold = Chem.MolFromSmarts("c1ccccc1")
    changes = consistent_substituents(molecules, scaffold)
    sites = {site["label"]: site["scaffold_atoms"][0] for site in assign_position_labels(changes)}
    halogen_site = next(label for label, group in _site_groups(changes)[0].items() if group == "F")

    to_chloro = replace_substituent(molecules[0], changes[0], sites[halogen_site], "*Cl")
    assert to_chloro == Chem.MolToSmiles(Chem.MolFromSmiles("O=C(Cc1ccc(Cl)cc1)Nc1ccccn1"))
    to_h = replace_substituent(molecules[0], changes[0], sites[halogen_site], "H")
    assert to_h == Chem.MolToSmiles(molecules[1])
    # A site that is empty on this compound gains the group.
    added = replace_substituent(molecules[1], changes[1], sites[halogen_site], "*OC")
    assert added == Chem.MolToSmiles(Chem.MolFromSmiles("COc1ccc(CC(=O)Nc2ccccn2)cc1"))

    try:
        replace_substituent(molecules[0], changes[0], sites[halogen_site], "Cl")
    except SarDiscoveryError:
        pass
    else:  # pragma: no cover - the missing attachment point must be rejected
        raise AssertionError("replacement without * should be rejected")
