from __future__ import annotations

import pytest

from app import create_app
from database import read_connection, transaction
from property_engine import PropertyAnalysisError, calculate_properties, get_property_run, run_property_analysis


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


def _seed_project(client) -> tuple[str, str]:
    project_id = client.post("/api/v1/projects", json={"name": "Property fixture"}).get_json()["project"]["id"]
    preview = client.post(
        "/api/v1/imports/preview",
        json={
            "csv_text": "compound_id,smiles,assay,result,unit\nCMP-001,CCO,IC50,10,nM\n",
        },
    ).get_json()
    assert client.post(
        f"/api/v1/imports/{preview['import_id']}/commit",
        json={"project_id": project_id},
    ).status_code == 200
    compound_id = client.get(f"/api/v1/compounds?project_id={project_id}").get_json()["results"][0]["id"]
    return project_id, compound_id


def test_calculate_properties_is_deterministic_and_unit_explicit():
    first = calculate_properties("CCO")
    second = calculate_properties("CCO")

    assert first == second
    assert first["algorithm_version"] == "rdkit-properties-v1"
    assert first["data_origin"] == "derived"
    assert first["uncertainty"]["experimental"] is False
    assert first["descriptors"]["molecular_weight"]["value"] == pytest.approx(46.069)
    assert first["descriptors"]["molecular_weight"]["unit"] == "Da"
    assert first["descriptors"]["hbd"]["value"] == 1
    assert first["descriptors"]["hba"]["value"] == 1


def test_invalid_structure_fails_closed():
    with pytest.raises(PropertyAnalysisError, match="cannot be parsed"):
        calculate_properties("not-a-valid-smiles")


def test_property_analysis_persists_profiles_and_missing_structure_state(production_app):
    client = production_app.test_client()
    project_id, _compound_id = _seed_project(client)
    now = "2026-09-07T08:45:00+00:00"
    with transaction(production_app.config["DATABASE_PATH"]) as connection:
        connection.execute(
            """
            INSERT INTO compounds (id, project_id, registration_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("cmp_missing", project_id, "CMP-MISSING", now, now),
        )

    result = run_property_analysis(production_app.config["DATABASE_PATH"], project_id)

    assert result["analysis_type"] == "properties"
    assert result["counts"] == {"computed": 1, "missing_structure": 1, "invalid_structure": 0}
    computed = next(profile for profile in result["profiles"] if profile["status"] == "computed")
    assert computed["descriptors"]["logp"]["unit"] == "dimensionless"
    missing = next(profile for profile in result["profiles"] if profile["status"] == "missing_structure")
    assert missing["reason"] == "structure_record_missing"

    stored = get_property_run(production_app.config["DATABASE_PATH"], result["analysis_run_id"])
    assert stored is not None
    assert stored["algorithm_version"] == "rdkit-properties-v1"
    assert stored["input_selection"]["selection_policy"] == "latest_structure_record_per_compound"
    with read_connection(production_app.config["DATABASE_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM property_profiles").fetchone()[0] == 2


def test_property_analysis_rejects_foreign_compound_ids(production_app):
    client = production_app.test_client()
    project_id, _compound_id = _seed_project(client)

    with pytest.raises(PropertyAnalysisError, match="target project"):
        run_property_analysis(
            production_app.config["DATABASE_PATH"],
            project_id,
            ["compound-from-another-project"],
        )



def test_property_analysis_api_rejects_foreign_compounds(production_app):
    client = production_app.test_client()
    project_id, _compound_id = _seed_project(client)
    other_project_id = client.post("/api/v1/projects", json={"name": "Property foreign fixture"}).get_json()["project"]["id"]
    other_preview = client.post(
        "/api/v1/imports/preview",
        json={"csv_text": "compound_id,smiles,assay,result,unit\nCMP-002,CCN,IC50,10,nM\n"},
    )
    assert other_preview.status_code == 201
    assert client.post(
        f"/api/v1/imports/{other_preview.get_json()['import_id']}/commit",
        json={"project_id": other_project_id},
    ).status_code == 200
    foreign_compound_id = client.get(
        f"/api/v1/compounds?project_id={other_project_id}"
    ).get_json()["results"][0]["id"]

    response = client.post(
        "/api/v1/analysis/properties",
        json={"project_id": project_id, "compound_ids": [foreign_compound_id]},
    )

    assert response.status_code == 422
    assert response.get_json()["error"] == "property_analysis_unavailable"


def test_property_analysis_api_persists_versioned_profiles(production_app):
    client = production_app.test_client()
    project_id, _compound_id = _seed_project(client)

    response = client.post(
        "/api/v1/analysis/properties",
        json={"project_id": project_id},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["data_origin"] == "derived"
    assert payload["analysis_type"] == "properties"
    assert payload["counts"]["computed"] == 1
    assert payload["profiles"][0]["descriptors"]["tpsa"]["unit"] == "A2"

    stored = client.get(f"/api/v1/analysis/properties/{payload['analysis_run_id']}")
    assert stored.status_code == 200
    assert stored.get_json()["algorithm_version"] == "rdkit-properties-v1"
