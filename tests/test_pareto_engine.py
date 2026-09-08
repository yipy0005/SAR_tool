import pytest

from app import create_app
from pareto_engine import ParetoAnalysisError, calculate_pareto, get_pareto_run, run_pareto_analysis


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


def test_pareto_front_is_deterministic_and_excludes_incomplete_evidence():
    compounds = [
        {"id": "a", "registration_id": "CMP-A"},
        {"id": "b", "registration_id": "CMP-B"},
        {"id": "c", "registration_id": "CMP-C"},
    ]
    summaries = [
        {"id": "s-a", "compound_id": "a", "compatibility_key": "potency", "canonical_unit": "pIC50", "summary_state": "observed", "summary_value": 8.0, "summary_qualifier": "=", "created_at": "2026-01-01"},
        {"id": "s-b", "compound_id": "b", "compatibility_key": "potency", "canonical_unit": "pIC50", "summary_state": "observed", "summary_value": 7.0, "summary_qualifier": "=", "created_at": "2026-01-01"},
    ]
    properties = [
        {"id": "p-a", "compound_id": "a", "property_key": "logp", "status": "computed", "descriptors_json": '{"logp": {"value": 2.0}}', "created_at": "2026-01-01"},
        {"id": "p-b", "compound_id": "b", "property_key": "logp", "status": "computed", "descriptors_json": '{"logp": {"value": 1.0}}', "created_at": "2026-01-01"},
    ]

    result = calculate_pareto(
        compounds,
        summaries,
        properties,
        [
            {"source": "summary", "key": "potency", "direction": "maximize"},
            {"source": "property", "key": "logp", "direction": "minimize"},
        ],
    )

    assert [item["status"] for item in result] == ["pareto", "pareto", "incomplete"]
    assert {item["registration_id"] for item in result if item["is_pareto"]} == {"CMP-A", "CMP-B"}
    assert result[-1]["reason"] == "summary:potency:summary_missing;property:logp:property_profile_missing"
    with pytest.raises(ParetoAnalysisError, match="Duplicate"):
        calculate_pareto(compounds, summaries, properties, [{"source": "property", "key": "logp", "direction": "minimize"}, {"source": "property", "key": "logp", "direction": "maximize"}])


def _seed_project(client):
    project_id = client.post("/api/v1/projects", json={"name": "Pareto fixture"}).get_json()["project"]["id"]
    preview = client.post(
        "/api/v1/imports/preview",
        json={"csv_text": "compound_id,smiles,assay,result,unit,qualifier\nCMP-A,CCO,IC50,10,nM,=\n"},
    )
    assert preview.status_code == 201
    assert client.post(
        f"/api/v1/imports/{preview.get_json()['import_id']}/commit",
        json={"project_id": project_id},
    ).status_code == 200
    assert client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id}).status_code == 201
    assert client.post("/api/v1/analysis/properties", json={"project_id": project_id}).status_code == 201
    return project_id


def test_pareto_api_persists_explicit_objective_provenance(production_app):
    client = production_app.test_client()
    project_id = _seed_project(client)
    response = client.post(
        "/api/v1/analysis/pareto",
        json={
            "project_id": project_id,
            "objectives": [
                {"source": "summary", "key": "IC50:import-v1", "direction": "maximize", "label": "Observed potency"},
                {"source": "property", "key": "logp", "direction": "minimize", "label": "Derived logP"},
            ],
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["data_origin"] == "derived"
    assert payload["counts"] == {"pareto": 1, "complete_non_pareto": 0, "incomplete": 0}
    objective = payload["observations"][0]["objective_values"]["summary:IC50:import-v1"]
    assert objective["evidence_class"] == "experimental"
    assert objective["evidence_id"]
    stored = client.get(f"/api/v1/analysis/pareto/{payload['analysis_run_id']}")
    assert stored.status_code == 200
    assert stored.get_json()["objectives"][1]["source"] == "property"
    assert get_pareto_run(production_app.config["DATABASE_PATH"], payload["analysis_run_id"])
