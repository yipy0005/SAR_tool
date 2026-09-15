from __future__ import annotations

import pytest

from app import create_app
from database import transaction
from prediction_engine import PredictionError, train_prediction_model


@pytest.fixture
def production_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-prediction",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def _seed_project(client) -> str:
    project_id = client.post("/api/v1/projects", json={"name": "Prediction fixture"}).get_json()["project"]["id"]
    rows = [
        ("CMP-001", "c1ccccc1", 8.0),
        ("CMP-002", "c1ccncc1", 8.2),
        ("CMP-003", "c1ccoc1", 7.8),
        ("CMP-004", "c1ccsc1", 7.6),
        ("CMP-005", "c1cnccc1", 7.4),
        ("CMP-006", "c1ccccc1C", 8.1),
    ]
    csv_text = "compound_id,smiles,assay,result,unit\n" + "\n".join(
        f"{compound_id},{smiles},IC50,{value},nM" for compound_id, smiles, _value in rows for value in [_value]
    ) + "\n"
    preview = client.post("/api/v1/imports/preview", json={"project_id": project_id, "csv_text": csv_text}).get_json()
    assert client.post(f"/api/v1/imports/{preview['import_id']}/commit", json={"project_id": project_id}).status_code == 200
    assert client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id}).status_code == 201
    return project_id


def test_prediction_training_persists_explicit_internal_validation(production_app):
    client = production_app.test_client()
    project_id = _seed_project(client)

    response = client.post(
        "/api/v1/predictions/train",
        json={
            "project_id": project_id,
            "compatibility_key": "IC50:import-v1",
            "min_training_compounds": 5,
            "min_validation_compounds": 3,
            "min_coverage": 0.1,
            "max_mae": 10.0,
        },
    )
    assert response.status_code == 201
    model = response.get_json()["model"]
    assert model["algorithm_version"] == "rdkit-morgan-similarity-knn-v1"
    assert model["validation_scope"] == "internal_leave_one_out"
    assert model["validation_metrics"]["validation_records"] == 6
    assert model["model_status"] in {"candidate", "internally_validated"}
    assert len(model["observations"]) == 6


def test_unqualified_prediction_is_blocked(production_app):
    client = production_app.test_client()
    project_id = _seed_project(client)
    response = client.post(
        "/api/v1/predictions/train",
        json={"project_id": project_id, "compatibility_key": "IC50:import-v1", "max_mae": 0.0},
    )
    assert response.status_code == 201
    model = response.get_json()["model"]
    blocked = client.post(
        f"/api/v1/predictions/{model['id']}/predict",
        json={"project_id": project_id, "compound_ids": []},
    )
    assert blocked.status_code == 422
    assert "not passed" in blocked.get_json()["message"] or "compound_ids" in blocked.get_json()["message"]


def test_prediction_engine_rejects_censored_or_missing_training_data(production_app):
    client = production_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Incomplete prediction"}).get_json()["project"]["id"]
    with pytest.raises(PredictionError, match="exact observed compounds"):
        train_prediction_model(
            client.application.config["DATABASE_PATH"],
            project_id,
            "IC50:import-v1",
        )
