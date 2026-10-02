from __future__ import annotations

from app import create_app


def _app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-prodrug",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def _seed_project(client):
    project_id = client.post("/api/v1/projects", json={"name": "Bulk prodrug fixture"}).get_json()["project"]["id"]
    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier,replicate,date\n"
        "PRO-001,CCOc1ccccc1,IC50,100,nM,=,1,2026-01-01\n"
        "ACT-001,c1ccccc1,IC50,10,nM,=,1,2026-01-01\n"
        "PRO-002,CCc1ccccc1,IC50,50,nM,=,1,2026-01-01\n"
        "ACT-002,Cc1ccccc1,IC50,5,nM,=,1,2026-01-01\n"
    )
    preview = client.post(
        "/api/v1/imports/preview",
        json={"project_id": project_id, "filename": "forms.csv", "csv_text": csv_text},
    )
    assert preview.status_code == 201
    import_id = preview.get_json()["import_id"]
    committed = client.post(f"/api/v1/imports/{import_id}/commit", json={"project_id": project_id})
    assert committed.status_code == 200
    summaries = client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id})
    assert summaries.status_code == 201
    return project_id


def test_bulk_relationship_preview_save_and_compare_all_pairs(tmp_path):
    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    pair_text = "prodrug_id,active_id\nPRO-001,ACT-001\nPRO-002,ACT-002\n"

    preview = client.post(
        "/api/v1/compound-relationships/preview",
        json={"project_id": project_id, "pair_text": pair_text},
    )
    assert preview.status_code == 200
    assert preview.get_json()["valid_count"] == 2
    assert preview.get_json()["invalid_count"] == 0

    saved = client.post(
        "/api/v1/compound-relationships",
        json={"project_id": project_id, "pair_text": pair_text},
    )
    assert saved.status_code == 201
    assert saved.get_json()["saved_count"] == 2
    assert all(item["review_status"] == "needs_review" for item in saved.get_json()["relationships"])

    listed = client.get(f"/api/v1/compound-relationships?project_id={project_id}")
    assert listed.status_code == 200
    assert {item["prodrug"]["registration_id"] for item in listed.get_json()["relationships"]} == {"PRO-001", "PRO-002"}

    compared = client.post(
        "/api/v1/analysis/prodrug-comparison",
        json={"project_id": project_id, "endpoint_key": "IC50:import-v1"},
    )
    assert compared.status_code == 201
    payload = compared.get_json()
    assert payload["pair_count"] == 2
    assert payload["complete_count"] == 2
    assert payload["algorithm_version"] == "prodrug-active-pair-compare-v1"
    assert all(pair["comparison"]["status"] == "complete" for pair in payload["pairs"])
    assert all(pair["comparison"]["delta"] is not None for pair in payload["pairs"])


def test_bulk_relationship_preview_flags_duplicates_and_unknown_compounds(tmp_path):
    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    preview = client.post(
        "/api/v1/compound-relationships/preview",
        json={
            "project_id": project_id,
            "pair_text": "prodrug_id,active_id\nPRO-001,ACT-001\nPRO-001,ACT-001\nPRO-999,ACT-001\n",
        },
    )
    assert preview.status_code == 200
    payload = preview.get_json()
    assert payload["valid_count"] == 1
    assert payload["invalid_count"] == 2
    assert any("more than once" in error for row in payload["invalid"] for error in row["errors"])
    assert any("not found" in error for row in payload["invalid"] for error in row["errors"])


def test_relationships_are_project_scoped_and_sar_page_exposes_bulk_mode(tmp_path):
    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    other_project = client.post("/api/v1/projects", json={"name": "Other project"}).get_json()["project"]["id"]
    saved = client.post(
        "/api/v1/compound-relationships",
        json={"project_id": project_id, "pairs": [{"prodrug_id": "PRO-001", "active_id": "ACT-001"}]},
    )
    assert saved.status_code == 201
    assert client.get(f"/api/v1/compound-relationships?project_id={other_project}").get_json()["relationships"] == []

    page = client.get(f"/workspace/sar?project_id={project_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for marker in (
        "Compare prodrug–active pairs",
        "productionSarPairField",
        "productionSarPairText",
        "/api/v1/compound-relationships/preview",
        "/api/v1/analysis/prodrug-comparison",
        "Save mapping and compare all pairs",
    ):
        assert marker in html or marker in client.get("/static/production_sar.js").get_data(as_text=True)
