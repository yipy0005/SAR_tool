import pytest

from app import create_app


@pytest.fixture
def production_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-fixtures",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def _seed_pair(client):
    project_response = client.post("/api/v1/projects", json={"name": "Analysis API fixture"})
    assert project_response.status_code == 201
    project_id = project_response.get_json()["project"]["id"]

    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier\n"
        "CMP-A,c1ccccc1,IC50,100,nM,=\n"
        "CMP-B,Cc1ccccc1,IC50,10,nM,=\n"
    )
    preview = client.post("/api/v1/imports/preview", json={"csv_text": csv_text})
    assert preview.status_code == 201
    commit = client.post(
        f"/api/v1/imports/{preview.get_json()['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert commit.status_code == 200

    measurements = client.get(f"/api/v1/measurements?project_id={project_id}")
    assert measurements.status_code == 200
    measurement_ids = [item["id"] for item in measurements.get_json()["results"]]
    assert len(measurement_ids) == 2
    return project_id, measurement_ids


def test_measurement_summary_creation_retrieval_and_project_listing(production_app):
    client = production_app.test_client()
    project_id, measurement_ids = _seed_pair(client)

    create_response = client.post(
        "/api/v1/measurement-summaries",
        json={
            "measurement_ids": [measurement_ids[0]],
            "analysis_version": "measurement-summary-api-v1",
        },
    )
    assert create_response.status_code == 201
    summary = create_response.get_json()
    assert summary["data_origin"] == "derived"
    assert summary["source_measurement_ids"] == [measurement_ids[0]]
    assert summary["analysis_version"] == "measurement-summary-api-v1"

    retrieved = client.get(f"/api/v1/measurement-summaries/{summary['id']}")
    assert retrieved.status_code == 200
    assert retrieved.get_json()["id"] == summary["id"]

    bulk_response = client.post(
        "/api/v1/measurement-summaries/project",
        json={"project_id": project_id, "analysis_version": "measurement-summary-api-v1"},
    )
    assert bulk_response.status_code == 201
    assert bulk_response.get_json()["count"] == 2

    listed = client.get(f"/api/v1/measurement-summaries?project_id={project_id}")
    assert listed.status_code == 200
    assert listed.get_json()["data_origin"] == "derived"
    assert len(listed.get_json()["summaries"]) == 3


def test_rgroup_activity_cliff_and_design_candidate_apis(production_app):
    client = production_app.test_client()
    project_id, _measurement_ids = _seed_pair(client)

    summaries = client.post(
        "/api/v1/measurement-summaries/project",
        json={"project_id": project_id},
    )
    assert summaries.status_code == 201

    rgroup = client.post(
        "/api/v1/analysis/rgroup",
        json={"project_id": project_id, "scaffold_smarts": "c1ccccc1"},
    )
    assert rgroup.status_code == 201
    rgroup_payload = rgroup.get_json()
    assert rgroup_payload["data_origin"] == "derived"
    assert rgroup_payload["analysis_type"] == "rgroup"
    assert any(item["status"] == "assigned" for item in rgroup_payload["assignments"])

    cliffs = client.post(
        "/api/v1/analysis/activity-cliffs",
        json={"project_id": project_id, "effect_threshold": 0.9},
    )
    assert cliffs.status_code == 201
    cliff_payload = cliffs.get_json()
    assert cliff_payload["data_origin"] == "derived"
    assert cliff_payload["cliff_count"] >= 1
    assert cliff_payload["cliffs"][0]["evidence_status"] == "eligible_observed_summary"

    candidate = {
        "category": "exploit",
        "title": "Combine supported potency and stability changes",
        "rationale": "Two observations support this design direction.",
        "hypothesis": "A less basic R3 preserves potency while improving clearance.",
        "expected_outcome": "Maintain potency and reduce clearance.",
        "uncertainty": "The exact combination has not been measured.",
        "evidence_ids": [cliff_payload["cliffs"][0]["summary_a_id"]],
        "novelty_score": 0.35,
        "feasibility_status": "tractable",
        "feasibility_reasons": ["Uses a precedent route"],
        "information_gain_score": 0.55,
        "objective_alignment_score": 0.9,
        "structure_smiles": "Cc1ccccc1",
    }
    designs = client.post(
        "/api/v1/designs",
        json={"project_id": project_id, "candidates": [candidate]},
    )
    assert designs.status_code == 201
    design_payload = designs.get_json()
    assert design_payload["data_origin"] == "curated"
    assert design_payload["candidates"][0]["score_components"]

    listed = client.get(f"/api/v1/designs?project_id={project_id}")
    assert listed.status_code == 200
    assert listed.get_json()["data_origin"] == "curated"
    assert len(listed.get_json()["candidates"]) == 1

    analysis_page = client.get(f"/workspace/analysis?project_id={project_id}")
    assert analysis_page.status_code == 200
    analysis_body = analysis_page.get_data(as_text=True)
    assert "VERSIONED ANALYSIS" in analysis_body
    assert "SCAFFOLD / R-GROUP" in analysis_body
    assert "ACTIVITY CLIFFS" in analysis_body

    design_page = client.get(f"/workspace/designs?project_id={project_id}")
    assert design_page.status_code == 200
    design_body = design_page.get_data(as_text=True)
    assert "CURATED DESIGN QUEUE" in design_body
    assert "curated" in design_body


def test_analysis_routes_reject_demo_data(tmp_path):
    demo_app = create_app(

        {
            "TESTING": True,
            "SAR_ENV": "demo",
            "SAR_DEMO_MODE": True,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "demo-secret-that-is-long-enough-for-fixtures",
            "DATABASE_PATH": str(tmp_path / "demo.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )
    client = demo_app.test_client()

    response = client.post(
        "/api/v1/analysis/rgroup",
        json={"project_id": "demo-project", "scaffold_smarts": "c1ccccc1"},
    )
    assert response.status_code == 409
    assert response.get_json()["error"] == "demo_mode"

    response = client.post(
        "/api/v1/designs",
        json={"project_id": "demo-project", "candidates": []},
    )
    assert response.status_code == 409
    assert response.get_json()["error"] == "demo_mode"


def test_operational_metrics_are_bounded_and_not_scientific_data(production_app):
    client = production_app.test_client()
    client.get("/healthz")
    client.get("/api/v1/metrics")
    response = client.get("/api/v1/metrics")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["data_origin"] == "operational"
    assert payload["metrics"]["requests_total"] >= 2
    assert "metrics_api" in payload["metrics"]["requests_by_endpoint"]

def test_selectivity_analysis_api_persists_observed_assay_margins(production_app):
    client = production_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Selectivity API fixture"}).get_json()["project"]["id"]
    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier\n"
        "CMP-A,c1ccccc1,IC50,100,nM,=\n"
        "CMP-A,c1ccccc1,OffTarget,6,pIC50,=\n"
        "CMP-B,Cc1ccccc1,IC50,10,nM,=\n"
        "CMP-B,Cc1ccccc1,OffTarget,6,pIC50,=\n"
    )
    preview = client.post("/api/v1/imports/preview", json={"csv_text": csv_text})
    assert preview.status_code == 201
    commit = client.post(
        f"/api/v1/imports/{preview.get_json()['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert commit.status_code == 200
    summaries = client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id})
    assert summaries.status_code == 201
    assert summaries.get_json()["count"] == 4

    response = client.post(
        "/api/v1/analysis/selectivity",
        json={
            "project_id": project_id,
            "primary_compatibility_key": "IC50:import-v1",
            "comparator_compatibility_key": "OffTarget:import-v1",
            "selectivity_threshold": 0.9,
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["data_origin"] == "derived"
    assert payload["counts"] == {"selective": 2, "non_selective": 0, "incomplete": 0}
    assert [item["selectivity_delta"] for item in payload["observations"]] == [1.0, 2.0]

    stored = client.get(f"/api/v1/analysis/selectivity/{payload['analysis_run_id']}")
    assert stored.status_code == 200
    assert stored.get_json()["input_selection"]["selection_policy"].startswith("latest_")
