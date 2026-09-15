from __future__ import annotations

import pytest

from app import create_app
from database import transaction


@pytest.fixture
def production_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-series",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def _seed_project(client, name: str) -> tuple[str, list[str]]:
    project_id = client.post("/api/v1/projects", json={"name": name}).get_json()["project"]["id"]
    now = "2026-09-15T22:00:00+00:00"
    database_path = client.application.config["DATABASE_PATH"]
    compound_ids = []
    with transaction(database_path) as connection:
        for index in range(2):
            compound_id = f"cmp_{name.replace(' ', '_').lower()}_{index}"
            compound_ids.append(compound_id)
            connection.execute(
                """
                INSERT INTO compounds (id, project_id, registration_id, preferred_name, created_at, updated_at)
                VALUES (?, ?, ?, '', ?, ?)
                """,
                (compound_id, project_id, f"CMP-{index + 1:03d}", now, now),
            )
    return project_id, compound_ids


def test_series_api_persists_versioned_membership_and_context(production_app):
    client = production_app.test_client()
    project_id, compound_ids = _seed_project(client, "Series project")

    created = client.post(
        "/api/v1/series",
        json={
            "project_id": project_id,
            "name": "Core analogues",
            "description": "Curated R1 series",
            "membership_source": "curated",
            "rationale": "Shared scaffold reviewed by the team",
            "compound_ids": compound_ids,
        },
    )
    assert created.status_code == 201
    series = created.get_json()["series"]
    assert series["current_version"] == 1
    assert series["member_count"] == 2
    assert {member["compound_id"] for member in series["members"]} == set(compound_ids)

    updated = client.post(
        f"/api/v1/series/{series['id']}/versions",
        json={
            "project_id": project_id,
            "membership_source": "curated",
            "rationale": "Removed one unresolved structure",
            "compound_ids": compound_ids[:1],
            "membership_status": "included",
        },
    )
    assert updated.status_code == 201
    current = updated.get_json()["series"]
    assert current["current_version"] == 2
    assert current["member_count"] == 1
    assert current["members"][0]["compound_id"] == compound_ids[0]

    with transaction(production_app.config["DATABASE_PATH"]) as connection:
        audit_operations = [row["operation"] for row in connection.execute(
            "SELECT operation FROM audit_events WHERE project_id = ? ORDER BY created_at, id",
            (project_id,),
        )]
    assert set(audit_operations) >= {"project_create", "series_create", "series_version_create"}
    assert audit_operations.count("series_create") == 1
    assert audit_operations.count("series_version_create") == 1

    context = client.get(f"/api/v1/series?project_id={project_id}")
    assert context.status_code == 200
    payload = context.get_json()
    assert payload["series"][0]["version"] == 2
    assert payload["compound_series"][compound_ids[0]][0]["series_name"] == "Core analogues"
    assert compound_ids[1] not in payload["compound_series"]


def test_series_api_rejects_foreign_compounds(production_app):
    client = production_app.test_client()
    project_id, _ = _seed_project(client, "Series project A")
    _other_project_id, other_compounds = _seed_project(client, "Series project B")

    response = client.post(
        "/api/v1/series",
        json={"project_id": project_id, "name": "Foreign series", "compound_ids": other_compounds},
    )
    assert response.status_code == 422
    assert "not in the target project" in response.get_json()["message"]


def test_series_export_preserves_curated_membership_history(production_app):
    client = production_app.test_client()
    project_id, compound_ids = _seed_project(client, "Exported series")
    created = client.post(
        "/api/v1/series",
        json={"project_id": project_id, "name": "Export series", "rationale": "Shared scaffold", "compound_ids": compound_ids},
    )
    assert created.status_code == 201

    response = client.get(f"/api/v1/exports/project/{project_id}?format=json")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["series"][0]["name"] == "Export series"
    assert payload["series_versions"][0]["data_origin"] == "curated"
    assert {item["registration_id"] for item in payload["series_memberships"]} == {"CMP-001", "CMP-002"}
