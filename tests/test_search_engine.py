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
            "SECRET_KEY": "test-secret-that-is-long-enough-for-search",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def _seed_compounds(client, name: str, count: int) -> tuple[str, list[str]]:
    project_id = client.post("/api/v1/projects", json={"name": name}).get_json()["project"]["id"]
    now = "2026-09-15T22:00:00+00:00"
    compound_ids = []
    with transaction(client.application.config["DATABASE_PATH"]) as connection:
        for index in range(count):
            compound_id = f"cmp_{name.replace(' ', '_').lower()}_{index}"
            compound_ids.append(compound_id)
            connection.execute(
                """
                INSERT INTO compounds (id, project_id, registration_id, preferred_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (compound_id, project_id, f"SAR-{index:03d}", f"Series {index % 3}", now, now),
            )
    return project_id, compound_ids


def test_compound_search_is_bounded_and_reports_projection_metadata(production_app):
    client = production_app.test_client()
    project_id, _ = _seed_compounds(client, "Search project", 25)

    response = client.get(f"/api/v1/search/compounds?project_id={project_id}&q=SAR-&limit=5&offset=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 25
    assert len(payload["results"]) == 5
    assert payload["offset"] == 5
    assert payload["limit"] == 5
    assert payload["has_more"] is True
    assert payload["search_index_version"] == "project-compounds-v1"
    assert payload["projection_status"] == "canonical"


def test_compound_search_does_not_cross_project_boundaries(production_app):
    client = production_app.test_client()
    project_a, _ = _seed_compounds(client, "Search project A", 2)
    project_b, _ = _seed_compounds(client, "Search project B", 2)

    response = client.get(f"/api/v1/search/compounds?project_id={project_a}&q=Search_project_B")
    assert response.status_code == 200
    assert response.get_json()["results"] == []

    invalid = client.get(f"/api/v1/search/compounds?project_id={project_b}&limit=201")
    assert invalid.status_code == 422
