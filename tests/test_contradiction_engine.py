import pytest

from app import create_app
from contradiction_engine import ContradictionAnalysisError, calculate_contradictions, get_contradiction_run


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


def test_contradictions_are_unreconciled_and_missingness_is_not_a_conflict():
    records = [
        {"id": "s1", "compound_id": "a", "registration_id": "CMP-A", "compatibility_key": "potency", "canonical_unit": "pIC50", "summary_state": "observed", "summary_value": 8.0, "summary_qualifier": "=", "created_at": "2026-01-01"},
        {"id": "s2", "compound_id": "a", "registration_id": "CMP-A", "compatibility_key": "potency", "canonical_unit": "pIC50", "summary_state": "observed", "summary_value": 6.0, "summary_qualifier": "=", "created_at": "2026-01-02"},
        {"id": "s3", "compound_id": "a", "registration_id": "CMP-A", "compatibility_key": "potency", "canonical_unit": "pIC50", "summary_state": "missing", "summary_value": None, "summary_qualifier": None, "created_at": "2026-01-03"},
        {"id": "s4", "compound_id": "b", "registration_id": "CMP-B", "compatibility_key": "potency", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 10.0, "summary_qualifier": "=", "created_at": "2026-01-01"},
        {"id": "s5", "compound_id": "b", "registration_id": "CMP-B", "compatibility_key": "potency", "canonical_unit": "pIC50", "summary_state": "observed", "summary_value": 7.0, "summary_qualifier": "=", "created_at": "2026-01-02"},
    ]

    result = calculate_contradictions(records, value_tolerance=0.5)

    assert [item["status"] for item in result] == ["contradictory", "unit_conflict"]
    assert all(item["reconciliation_status"] == "unreconciled" for item in result)
    with pytest.raises(ContradictionAnalysisError, match="non-negative"):
        calculate_contradictions(records, value_tolerance=-1)


def _commit(client, project_id, result):
    preview = client.post(
        "/api/v1/imports/preview",
        json={"csv_text": f"compound_id,smiles,assay,result,unit,qualifier\nCMP-A,CCO,IC50,{result},nM,=\n"},
    )
    assert preview.status_code == 201
    commit = client.post(
        f"/api/v1/imports/{preview.get_json()['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert commit.status_code == 200
    assert client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id}).status_code == 201


def test_contradiction_api_persists_review_required_warning(production_app):
    client = production_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Contradiction fixture"}).get_json()["project"]["id"]
    _commit(client, project_id, 10)
    _commit(client, project_id, 100)

    response = client.post(
        "/api/v1/analysis/contradictions",
        json={"project_id": project_id, "value_tolerance": 0.1},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["data_origin"] == "derived"
    assert payload["counts"]["contradictory"] == 1
    assert payload["observations"][0]["reconciliation_status"] == "unreconciled"
    stored = client.get(f"/api/v1/analysis/contradictions/{payload['analysis_run_id']}")
    assert stored.status_code == 200
    assert get_contradiction_run(production_app.config["DATABASE_PATH"], payload["analysis_run_id"])
