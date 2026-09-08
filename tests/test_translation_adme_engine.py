import pytest

from adme_engine import ADMEAnalysisError, calculate_adme_panel, get_adme_run, run_adme_analysis
from app import create_app
from cellular_translation_engine import TranslationAnalysisError, calculate_translation, get_translation_run, run_translation_analysis


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


def _seed_project(client):
    project_id = client.post("/api/v1/projects", json={"name": "Translation and ADME fixture"}).get_json()["project"]["id"]
    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier\n"
        "CMP-A,CCO,IC50,100,nM,=\n"
        "CMP-A,CCO,Cellular,6.5,pIC50,=\n"
        "CMP-B,CC,IC50,100,nM,=\n"
        "CMP-B,CC,Cellular,6.0,pIC50,>\n"
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
    return project_id


def test_translation_requires_explicit_compatible_observed_evidence():
    records = [
        {
            "id": "bio-a",
            "compound_id": "a",
            "registration_id": "CMP-A",
            "compatibility_key": "bio",
            "canonical_unit": "pIC50",
            "summary_state": "observed",
            "summary_value": 8.0,
            "summary_qualifier": "=",
            "direction": "higher_is_better",
            "created_at": "2026-01-01",
        },
        {
            "id": "cell-a",
            "compound_id": "a",
            "registration_id": "CMP-A",
            "compatibility_key": "cell",
            "canonical_unit": "pIC50",
            "summary_state": "observed",
            "summary_value": 7.5,
            "summary_qualifier": "=",
            "direction": "higher_is_better",
            "created_at": "2026-01-01",
        },
        {
            "id": "bio-b",
            "compound_id": "b",
            "registration_id": "CMP-B",
            "compatibility_key": "bio",
            "canonical_unit": "pIC50",
            "summary_state": "observed",
            "summary_value": 8.0,
            "summary_qualifier": "=",
            "direction": "higher_is_better",
            "created_at": "2026-01-01",
        },
        {
            "id": "cell-b",
            "compound_id": "b",
            "registration_id": "CMP-B",
            "compatibility_key": "cell",
            "canonical_unit": "pIC50",
            "summary_state": "censored",
            "summary_value": 7.0,
            "summary_qualifier": ">",
            "direction": "higher_is_better",
            "created_at": "2026-01-01",
        },
    ]

    observations = calculate_translation(records, "bio", "cell", translation_loss_threshold=0.6)

    assert observations[0]["status"] == "translated"
    assert observations[0]["translation_delta"] == pytest.approx(-0.5)
    assert observations[1]["status"] == "incomplete"
    assert observations[1]["reason"] == "summary_not_exact_observed"
    with pytest.raises(TranslationAnalysisError, match="must differ"):
        calculate_translation(records, "bio", "bio")


def test_adme_panel_preserves_censoring_and_missingness():
    records = [
        {
            "id": "clint-a",
            "compound_id": "a",
            "compatibility_key": "CLint",
            "canonical_unit": "uL/min/mg",
            "summary_state": "observed",
            "summary_value": 3.0,
            "summary_qualifier": "=",
            "created_at": "2026-01-01",
        },
        {
            "id": "papp-a",
            "compound_id": "a",
            "compatibility_key": "Papp",
            "canonical_unit": "10^-6 cm/s",
            "summary_state": "observed_with_censored",
            "summary_value": 1.0,
            "summary_qualifier": ">",
            "created_at": "2026-01-01",
        },
    ]
    compounds = [{"id": "a", "registration_id": "CMP-A"}, {"id": "b", "registration_id": "CMP-B"}]

    panel = calculate_adme_panel(records, compounds, ["CLint", "Papp"])

    assert panel[0]["status"] == "incomplete_censored"
    assert panel[0]["censored_contexts"] == ["Papp"]
    assert panel[1]["status"] == "incomplete_missing"
    assert panel[1]["missing_contexts"] == ["CLint", "Papp"]
    with pytest.raises(ADMEAnalysisError, match="At least one"):
        calculate_adme_panel(records, compounds, [])


def test_translation_and_adme_apis_persist_versioned_runs(production_app):
    client = production_app.test_client()
    project_id = _seed_project(client)

    translation = client.post(
        "/api/v1/analysis/cellular-translation",
        json={
            "project_id": project_id,
            "biochemical_compatibility_key": "IC50:import-v1",
            "cellular_compatibility_key": "Cellular:import-v1",
            "translation_loss_threshold": 0.6,
        },
    )
    assert translation.status_code == 201
    translation_payload = translation.get_json()
    assert translation_payload["data_origin"] == "derived"
    assert translation_payload["counts"]["translated"] == 1
    assert translation_payload["counts"]["incomplete"] == 1
    stored_translation = client.get(
        f"/api/v1/analysis/cellular-translation/{translation_payload['analysis_run_id']}"
    )
    assert stored_translation.status_code == 200
    assert get_translation_run(production_app.config["DATABASE_PATH"], translation_payload["analysis_run_id"])

    adme = client.post(
        "/api/v1/analysis/adme",
        json={
            "project_id": project_id,
            "compatibility_keys": ["IC50:import-v1", "Cellular:import-v1"],
        },
    )
    assert adme.status_code == 201
    adme_payload = adme.get_json()
    assert adme_payload["data_origin"] == "derived"
    assert adme_payload["counts"]["complete_observed"] == 1
    assert adme_payload["counts"]["incomplete_missing"] == 1
    stored_adme = client.get(f"/api/v1/analysis/adme/{adme_payload['analysis_run_id']}")
    assert stored_adme.status_code == 200
    assert stored_adme.get_json()["observations"][0]["context_values"]
    assert get_adme_run(production_app.config["DATABASE_PATH"], adme_payload["analysis_run_id"])

    dashboard = client.get(f"/workspace/analysis?project_id={project_id}")
    assert dashboard.status_code == 200
    dashboard_body = dashboard.get_data(as_text=True)
    assert "translated" in dashboard_body
    assert "complete_observed" in dashboard_body
    assert "No Pareto or contradiction observations are persisted yet." in dashboard_body
