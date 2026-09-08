from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app import create_app
from database import read_connection
from import_pipeline import commit_import


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


def _seed_project(client, name: str, compound_id: str, smiles: str, result: str) -> tuple[str, str, str]:
    project_response = client.post("/api/v1/projects", json={"name": name})
    assert project_response.status_code == 201
    project_id = project_response.get_json()["project"]["id"]
    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier\n"
        f"{compound_id},{smiles},IC50,{result},nM,=\n"
    )
    preview_response = client.post(
        "/api/v1/imports/preview",
        json={"filename": f"{compound_id}.csv", "csv_text": csv_text},
    )
    assert preview_response.status_code == 201
    import_id = preview_response.get_json()["import_id"]
    commit_response = client.post(
        f"/api/v1/imports/{import_id}/commit",
        json={"project_id": project_id},
    )
    assert commit_response.status_code == 200
    summaries_response = client.post(
        "/api/v1/measurement-summaries/project",
        json={"project_id": project_id},
    )
    assert summaries_response.status_code == 201
    summary_id = summaries_response.get_json()["summaries"][0]["id"]
    compound_id = client.get(f"/api/v1/compounds?project_id={project_id}").get_json()["results"][0]["id"]
    return project_id, summary_id, compound_id


def _candidate(evidence_id: str, parent_compound_id: str | None = None) -> dict[str, object]:
    return {
        "category": "test",
        "title": "Test a supported design direction",
        "rationale": "The candidate is explicitly tied to persisted evidence.",
        "hypothesis": "The structural change preserves the observed activity profile.",
        "expected_outcome": "The next assay will distinguish the competing explanations.",
        "uncertainty": "The proposed combination has not yet been experimentally measured.",
        "evidence_ids": [evidence_id],
        "novelty_score": 0.4,
        "feasibility_status": "review",
        "feasibility_reasons": ["Requires routine synthesis review"],
        "information_gain_score": 0.8,
        "objective_alignment_score": 0.7,
        "parent_compound_id": parent_compound_id,
    }


def test_api_malformed_json_shape_and_unknown_route_are_safe(production_app):
    client = production_app.test_client()

    malformed_shape = client.post("/api/v1/structure/standardize", json=["not", "an", "object"])
    assert malformed_shape.status_code == 422
    assert malformed_shape.is_json
    assert "Traceback" not in malformed_shape.get_data(as_text=True)

    missing_route = client.get("/api/v1/not-a-real-resource")
    assert missing_route.status_code == 404
    assert missing_route.is_json
    assert missing_route.get_json() == {
        "error": "not_found",
        "message": "Request could not be completed.",
    }
    assert "Traceback" not in missing_route.get_data(as_text=True)


def test_upload_limit_returns_safe_json_error(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-fixtures",
            "DATABASE_PATH": str(tmp_path / "large.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 128,
        }
    )
    response = app.test_client().post(
        "/api/v1/imports/preview",
        json={"csv_text": "compound_id,smiles,assay,result,unit\n" + ("A,CCO,IC50,10,nM\n" * 100)},
    )
    assert response.status_code == 413
    assert response.is_json
    assert response.get_json()["error"] == "upload_too_large"
    assert "Traceback" not in response.get_data(as_text=True)


def test_quarantine_filename_cannot_escape_upload_directory(production_app):
    client = production_app.test_client()
    response = client.post(
        "/api/v1/imports/preview",
        json={
            "filename": "../../../../outside.csv",
            "csv_text": "compound_id,smiles,assay,result,unit\nCMP-001,CCO,IC50,10,nM\n",
        },
    )
    assert response.status_code == 201
    upload_dir = Path(production_app.config["UPLOAD_DIR"]).resolve()
    with read_connection(production_app.config["DATABASE_PATH"]) as connection:
        storage_path = Path(
            connection.execute("SELECT storage_path FROM source_documents").fetchone()["storage_path"]
        ).resolve()
    assert storage_path.parent == upload_dir
    assert storage_path.name.endswith(".upload")
    assert "outside.csv" not in storage_path.name


def test_duplicate_preview_is_rejected_without_duplicate_source_or_batch(production_app):
    client = production_app.test_client()
    payload = {
        "filename": "same.csv",
        "csv_text": "compound_id,smiles,assay,result,unit\nCMP-001,CCO,IC50,10,nM\n",
    }
    first = client.post("/api/v1/imports/preview", json=payload)
    second = client.post("/api/v1/imports/preview", json=payload)
    assert first.status_code == 201
    assert second.status_code == 422
    assert second.get_json()["error"] == "duplicate_import"
    assert second.get_json()["import_id"] == first.get_json()["import_id"]
    with read_connection(production_app.config["DATABASE_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 1


def test_same_source_can_be_imported_into_a_separate_project(production_app):
    client = production_app.test_client()
    project_a = client.post("/api/v1/projects", json={"name": "Source project"}).get_json()["project"]["id"]
    project_b = client.post("/api/v1/projects", json={"name": "Separate project"}).get_json()["project"]["id"]
    payload = {
        "filename": "shared-fixture.csv",
        "csv_text": "compound_id,smiles,assay,result,unit\nCMP-001,CCO,IC50,10,nM\n",
    }

    first = client.post("/api/v1/imports/preview", json={**payload, "project_id": project_a})
    assert first.status_code == 201
    first_payload = first.get_json()
    committed = client.post(
        f"/api/v1/imports/{first_payload['import_id']}/commit",
        json={"project_id": project_a},
    )
    assert committed.status_code == 200

    second = client.post("/api/v1/imports/preview", json={**payload, "project_id": project_b})
    assert second.status_code == 201
    second_payload = second.get_json()
    assert second_payload["import_id"] != first_payload["import_id"]
    assert second_payload["source_document_id"] == first_payload["source_document_id"]

    committed_second = client.post(
        f"/api/v1/imports/{second_payload['import_id']}/commit",
        json={"project_id": project_b},
    )
    assert committed_second.status_code == 200

    with read_connection(production_app.config["DATABASE_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM compounds WHERE project_id = ?", (project_a,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM compounds WHERE project_id = ?", (project_b,)).fetchone()[0] == 1


def test_concurrent_commit_is_idempotent(production_app):
    client = production_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Concurrent import"}).get_json()["project"]["id"]
    preview = client.post(
        "/api/v1/imports/preview",
        json={"csv_text": "compound_id,smiles,assay,result,unit\nCMP-001,CCO,IC50,10,nM\n"},
    ).get_json()

    def commit_once():
        return commit_import(
            production_app.config["DATABASE_PATH"],
            preview["import_id"],
            project_id,
        )["status"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _index: commit_once(), range(2)))

    assert statuses == ["already_committed", "committed"]
    with read_connection(production_app.config["DATABASE_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM import_batches").fetchone()[0] == "committed"


def test_design_evidence_and_parent_compound_are_project_scoped(production_app):
    client = production_app.test_client()
    project_a, summary_a, compound_a = _seed_project(client, "Project A", "A-001", "CCO", "10")
    project_b, summary_b, compound_b = _seed_project(client, "Project B", "B-001", "CCN", "20")

    foreign_evidence = client.post(
        "/api/v1/designs",
        json={"project_id": project_a, "candidates": [_candidate(summary_b)]},
    )
    assert foreign_evidence.status_code == 422
    assert "evidence" in foreign_evidence.get_json()["message"].lower()

    foreign_parent = client.post(
        "/api/v1/designs",
        json={"project_id": project_a, "candidates": [_candidate(summary_a, compound_b)]},
    )
    assert foreign_parent.status_code == 422
    assert "project" in foreign_parent.get_json()["message"].lower()

    valid = client.post(
        "/api/v1/designs",
        json={"project_id": project_a, "candidates": [_candidate(summary_a, compound_a)]},
    )
    assert valid.status_code == 201
    assert valid.get_json()["count"] == 1
