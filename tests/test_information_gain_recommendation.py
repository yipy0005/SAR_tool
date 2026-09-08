import pytest

from app import create_app
from information_gain_engine import InformationGainAnalysisError, calculate_information_gain


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


def test_information_gain_is_deterministic_and_explicitly_heuristic():
    compounds = [{"id": "cmp-1", "registration_id": "CMP-1"}]
    summaries = [
        {
            "id": "sum-1",
            "compound_id": "cmp-1",
            "compatibility_key": "Biochemical",
            "summary_state": "observed",
            "summary_value": 7.0,
            "summary_qualifier": "=",
            "eligible_measurement_count": 1,
            "dispersion": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": "sum-2",
            "compound_id": "cmp-1",
            "compatibility_key": "Cellular",
            "summary_state": "observed",
            "summary_value": 6.5,
            "summary_qualifier": "=",
            "eligible_measurement_count": 2,
            "dispersion": 0.1,
            "created_at": "2026-01-02T00:00:00+00:00",
        },
    ]
    contradictions = [
        {
            "id": "contradiction-1",
            "compound_id": "cmp-1",
            "compatibility_key": "Cellular",
            "status": "unreconciled",
        }
    ]
    contexts = [{"compatibility_key": "Cellular", "priority": 1.0, "minimum_replicates": 2}]

    first = calculate_information_gain(compounds, summaries, contradictions, contexts)
    second = calculate_information_gain(compounds, summaries, contradictions, contexts)

    assert first == second
    assert first[0]["gap_type"] == "contradictory_context"
    assert first[0]["evidence_class"] == "heuristic_evidence_gap"
    assert first[0]["uncertainty"]["classification"] == "heuristic_not_predictive"
    assert first[0]["priority_score"] <= 1


def test_information_gain_preserves_censored_and_replicate_uncertainty():
    compounds = [{"id": "cmp-1", "registration_id": "CMP-1"}]
    summaries = [
        {
            "id": "sum-censored",
            "compound_id": "cmp-1",
            "compatibility_key": "ADME",
            "summary_state": "censored",
            "summary_value": None,
            "summary_qualifier": ">",
            "censored_measurement_count": 1,
            "missing_measurement_count": 0,
            "eligible_measurement_count": 0,
            "dispersion": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    result = calculate_information_gain(
        compounds,
        summaries,
        [],
        [{"compatibility_key": "ADME", "minimum_replicates": 2}],
    )

    assert result[0]["gap_type"] == "censored_context"
    assert result[0]["score_components"]["raw"]["censoring_gap"] == 1.0
    assert "sum-censored" in result[0]["evidence_ids"]


def test_information_gain_requires_explicit_contexts():
    with pytest.raises(InformationGainAnalysisError, match="target context"):
        calculate_information_gain([], [], [], [])


def _seed_project(client):
    project = client.post("/api/v1/projects", json={"name": "Information gain fixture"})
    assert project.status_code == 201
    project_id = project.get_json()["project"]["id"]
    preview = client.post(
        "/api/v1/imports/preview",
        json={
            "filename": "information-gain.csv",
            "csv_text": "compound_id,smiles,assay,result,unit,qualifier\nCMP-1,CCO,Biochemical,10,nM,=\n",
        },
    )
    assert preview.status_code == 201
    committed = client.post(
        f"/api/v1/imports/{preview.get_json()['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert committed.status_code == 200
    summaries = client.post(
        "/api/v1/measurement-summaries/project",
        json={"project_id": project_id},
    )
    assert summaries.status_code == 201
    return project_id


def test_information_gain_and_generated_recommendation_api_require_review(production_app):
    client = production_app.test_client()
    project_id = _seed_project(client)

    analysis = client.post(
        "/api/v1/analysis/information-gain",
        json={
            "project_id": project_id,
            "contexts": [{"compatibility_key": "Cellular", "priority": 1.0}],
        },
    )
    assert analysis.status_code == 201
    payload = analysis.get_json()
    assert payload["data_origin"] == "derived"
    assert payload["evidence_class"] == "heuristic_evidence_gap"
    assert payload["observation_count"] == 1
    run_id = payload["id"]
    observation_id = payload["observations"][0]["id"]
    assert payload["observations"][0]["gap_type"] == "missing_context"

    retrieved = client.get(f"/api/v1/analysis/information-gain/{run_id}")
    assert retrieved.status_code == 200
    assert retrieved.get_json()["id"] == run_id

    generated = client.post(
        "/api/v1/recommendations/generate",
        json={"project_id": project_id, "information_gain_run_id": run_id},
    )
    assert generated.status_code == 201
    generated_payload = generated.get_json()
    assert generated_payload["data_origin"] == "generated"
    assert generated_payload["review_required"] is True
    recommendation = generated_payload["recommendations"][0]
    recommendation_id = recommendation["id"]
    assert observation_id in recommendation["evidence_ids"]
    assert recommendation["status"] == "generated_review"
    assert recommendation["experimentally_confirmed"] is False

    listed = client.get(f"/api/v1/recommendations?project_id={project_id}")
    assert listed.status_code == 200
    assert listed.get_json()["recommendations"][0]["data_origin"] == "generated"

    reviewed = client.post(
        f"/api/v1/recommendations/{recommendation_id}/review",
        json={"status": "approved", "review_note": "Qualified review approves this experiment."},
    )
    assert reviewed.status_code == 200
    reviewed_item = reviewed.get_json()["recommendation"]
    assert reviewed_item["status"] == "approved"
    assert reviewed_item["experimentally_confirmed"] is False

    second_review = client.post(
        f"/api/v1/recommendations/{recommendation_id}/review",
        json={"status": "rejected", "review_note": "A second review must not overwrite approval."},
    )
    assert second_review.status_code == 422


def test_generated_recommendation_and_information_gain_are_project_scoped(production_app):
    client = production_app.test_client()
    project_a = _seed_project(client)
    project_b = client.post("/api/v1/projects", json={"name": "Other project"}).get_json()["project"]["id"]
    analysis = client.post(
        "/api/v1/analysis/information-gain",
        json={"project_id": project_a, "contexts": ["Cellular"]},
    ).get_json()

    foreign_generation = client.post(
        "/api/v1/recommendations/generate",
        json={"project_id": project_b, "information_gain_run_id": analysis["id"]},
    )
    assert foreign_generation.status_code == 422

    foreign_retrieval = client.get(f"/api/v1/analysis/information-gain/{analysis['id']}")
    assert foreign_retrieval.status_code == 200
    assert foreign_retrieval.get_json()["project_id"] == project_a
