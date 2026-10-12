from __future__ import annotations

from app import create_app
from database import transaction


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
    assert payload["endpoint_metadata"]["endpoint_key"] == "IC50:import-v1"
    assert all(pair["relationship_data_origin"] == "imported" for pair in payload["pairs"])
    assert all(pair["comparison"]["status"] == "complete" for pair in payload["pairs"])
    assert all(pair["comparison"]["delta"] is not None for pair in payload["pairs"])


def test_comparison_rejects_foreign_relationship_selection_without_persistence(tmp_path):
    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    saved = client.post(
        "/api/v1/compound-relationships",
        json={"project_id": project_id, "pair_text": "PRO-001,ACT-001"},
    )
    assert saved.status_code == 201
    before = client.application.config["DATABASE_PATH"]
    with transaction(before) as connection:
        counts_before = connection.execute("SELECT (SELECT count(*) FROM analysis_runs), (SELECT count(*) FROM audit_events)").fetchone()
    for persist in (False, True):
        response = client.post(
            "/api/v1/analysis/prodrug-comparison",
            json={"project_id": project_id, "endpoint_key": "IC50:import-v1", "relationship_ids": ["foreign_relationship"], "persist": persist},
        )
        assert response.status_code == 422
        assert response.get_json()["error"] == "invalid_relationship_selection"
    with transaction(before) as connection:
        counts_after = connection.execute("SELECT (SELECT count(*) FROM analysis_runs), (SELECT count(*) FROM audit_events)").fetchone()
    assert tuple(counts_before) == tuple(counts_after)


def test_relationship_save_errors_omit_untrusted_notes(tmp_path):
    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    response = client.post(
        "/api/v1/compound-relationships",
        json={
            "project_id": project_id,
            "pair_text": "PRO-001,ACT-001,keep this note in preview\nUNKNOWN,ACT-001,do not echo this note",
        },
    )
    assert response.status_code == 422
    body = response.get_json()
    serialized = response.get_data(as_text=True)
    assert "keep this note in preview" not in serialized
    assert "do not echo this note" not in serialized
    assert body["details"]["invalid_count"] == 1
    assert body["details"]["invalid_rows"][0]["error_codes"] == ["unknown_compound"]
    assert "notes" not in body["details"]


def test_durable_comparison_requires_editor_and_marks_invalid_numeric(tmp_path, monkeypatch):
    import app as app_module

    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    saved = client.post(
        "/api/v1/compound-relationships",
        json={"project_id": project_id, "pair_text": "PRO-001,ACT-001"},
    )
    assert saved.status_code == 201
    monkeypatch.setattr(app_module, "_project_access", lambda _app, _project_id, minimum_role: minimum_role == "viewer")
    forbidden = client.post(
        "/api/v1/analysis/prodrug-comparison",
        json={"project_id": project_id, "endpoint_key": "IC50:import-v1", "persist": True},
    )
    assert forbidden.status_code == 403
    for summary_state, raw_value in (("observed", "NaN"), ("censored", "Infinity"), ("missing", "-Infinity")):
        with transaction(client.application.config["DATABASE_PATH"]) as connection:
            connection.execute("UPDATE measurement_summaries SET summary_state = ?, summary_value = ? WHERE compound_id IN (SELECT id FROM compounds WHERE project_id = ? AND registration_id = 'PRO-001')", (summary_state, raw_value, project_id))
        compared = client.post(
            "/api/v1/analysis/prodrug-comparison",
            json={"project_id": project_id, "endpoint_key": "IC50:import-v1"},
        )
        assert compared.status_code == 201
        pair = compared.get_json()["pairs"][0]
        assert pair["comparison"]["status"] == "invalid_numeric"
        assert pair["comparison"]["delta"] is None
    with transaction(client.application.config["DATABASE_PATH"]) as connection:
        connection.execute("UPDATE measurement_summaries SET summary_state = 'observed', summary_value = '-1e308' WHERE compound_id IN (SELECT id FROM compounds WHERE project_id = ? AND registration_id = 'PRO-001')", (project_id,))
        connection.execute("UPDATE measurement_summaries SET summary_value = '1e308' WHERE compound_id IN (SELECT id FROM compounds WHERE project_id = ? AND registration_id = 'ACT-001')", (project_id,))
    overflow = client.post(
        "/api/v1/analysis/prodrug-comparison",
        json={"project_id": project_id, "endpoint_key": "IC50:import-v1"},
    )
    assert overflow.status_code == 201
    overflow_pair = overflow.get_json()["pairs"][0]
    assert overflow_pair["comparison"]["status"] == "invalid_numeric" and overflow_pair["comparison"]["delta"] is None
    import json
    json.dumps(overflow.get_json(), allow_nan=False)



def test_missing_summary_qualifier_is_not_exact(tmp_path):
    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    saved = client.post(
        "/api/v1/compound-relationships",
        json={"project_id": project_id, "pair_text": "PRO-001,ACT-001"},
    )
    assert saved.status_code == 201
    with transaction(client.application.config["DATABASE_PATH"]) as connection:
        connection.execute("UPDATE measurement_summaries SET summary_qualifier = NULL WHERE compound_id IN (SELECT id FROM compounds WHERE project_id = ? AND registration_id = 'PRO-001')", (project_id,))
    response = client.post(
        "/api/v1/analysis/prodrug-comparison",
        json={"project_id": project_id, "endpoint_key": "IC50:import-v1"},
    )
    assert response.status_code == 201
    pair = response.get_json()["pairs"][0]
    assert pair["comparison"]["status"] == "prodrug_non_exact"
    assert pair["comparison"]["delta"] is None

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


def test_direct_save_rejects_non_prodrug_relationship_type(tmp_path):
    client = _app(tmp_path).test_client()
    project_id = _seed_project(client)
    response = client.post(
        "/api/v1/compound-relationships",
        json={"project_id": project_id, "relationship_type": "related_to", "pair_text": "PRO-001,ACT-001"},
    )
    assert response.status_code == 422
    assert response.get_json()["error"] == "invalid_relationship_type"


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
