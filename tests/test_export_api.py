import csv
import io
import json

import pytest

from app import create_app
from database import read_connection
from export_engine import ExportError, build_project_export, export_project_csv


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
            "MAX_EXPORT_ROWS": 100_000,
        }
    )


def _seed_project(client, name, compound_id):
    project = client.post("/api/v1/projects", json={"name": name}).get_json()["project"]
    project_id = project["id"]
    preview = client.post(
        "/api/v1/imports/preview",
        json={
            "csv_text": (
                "compound_id,smiles,assay,result,unit,qualifier\n"
                f"{compound_id},CCO,IC50,10,nM,=\n"
            ),
        },
    )
    assert preview.status_code == 201
    committed = client.post(
        f"/api/v1/imports/{preview.get_json()['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert committed.status_code == 200
    summaries = client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id})
    assert summaries.status_code == 201
    return project_id


def test_project_export_json_and_csv_preserve_provenance_and_audit(production_app):
    client = production_app.test_client()
    project_id = _seed_project(client, "Export fixture", "CMP-EXPORT")
    other_project_id = _seed_project(client, "Other export fixture", "CMP-OTHER")

    json_response = client.get(f"/api/v1/exports/project/{project_id}?format=json")
    assert json_response.status_code == 200
    assert json_response.mimetype == "application/json"
    assert json_response.headers["X-SAR-Export-Origin"] == "production_export"
    assert "attachment" in json_response.headers["Content-Disposition"]
    payload = json.loads(json_response.get_data(as_text=True))
    assert payload["project"]["id"] == project_id
    assert payload["data_origin"] == "production_export"
    assert payload["row_count"] >= 3
    assert {item["registration_id"] for item in payload["compounds"]} == {"CMP-EXPORT"}
    assert payload["structure_records"][0]["registration_id"] == "CMP-EXPORT"
    assert payload["structure_records"][0]["canonical_smiles"]
    assert all(item["data_origin"] == "raw" for item in payload["measurements"])
    assert all(item["data_origin"] == "derived" for item in payload["measurement_summaries"])
    assert "CMP-OTHER" not in json_response.get_data(as_text=True)

    csv_response = client.get(f"/api/v1/exports/project/{project_id}?format=csv")
    assert csv_response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_response.get_data(as_text=True))))
    assert {row["record_type"] for row in rows} >= {"compound", "measurement", "measurement_summary"}
    assert {row["registration_id"] for row in rows if row["registration_id"]} == {"CMP-EXPORT"}
    assert all(row["project_id"] == project_id for row in rows)
    payload["compounds"][0]["registration_id"] = "=HYPERLINK(\"https://example.test\")"
    export_rows = {**payload, "generated_recommendations": [{"id": "rec_1", "project_id": project_id, "title": "Review", "data_origin": "generated"}], "compound_relationships": [{"id": "rel_1", "project_id": project_id, "data_origin": "imported"}]}
    csv_text = export_project_csv(export_rows)
    assert {row["record_type"] for row in csv.DictReader(io.StringIO(csv_text))} >= {"generated_recommendation", "compound_relationship"}
    assert "'=HYPERLINK" in csv_text

    with read_connection(production_app.config["DATABASE_PATH"]) as connection:
        audits = connection.execute(
            "SELECT operation, project_id, outcome, metadata_json FROM audit_events WHERE operation = 'project_export' ORDER BY created_at"
        ).fetchall()
    assert len(audits) == 2
    assert {row["project_id"] for row in audits} == {project_id}
    assert all(row["outcome"] == "success" for row in audits)
    assert json.loads(audits[-1]["metadata_json"])["row_count"] >= 3
    assert other_project_id != project_id


def test_durable_comparison_export_preserves_provenance_in_json_and_csv(production_app):
    client = production_app.test_client()
    project_response = client.post("/api/v1/projects", json={"name": "Durable comparison export"})
    project_id = project_response.get_json()["project"]["id"]
    imported = client.post(
        "/api/v1/imports/preview",
        json={
            "project_id": project_id,
            "filename": "comparison.csv",
            "csv_text": (
                "compound_id,smiles,assay,result,unit,qualifier\n"
                "PRO-EXPORT,CCOc1ccccc1,IC50,100,nM,=\n"
                "ACT-EXPORT,c1ccccc1,IC50,10,nM,=\n"
            ),
        },
    )
    assert imported.status_code == 201
    import_id = imported.get_json()["import_id"]
    assert client.post(f"/api/v1/imports/{import_id}/commit", json={"project_id": project_id}).status_code == 200
    assert client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id}).status_code == 201
    saved = client.post(
        "/api/v1/compound-relationships",
        json={"project_id": project_id, "pair_text": "PRO-EXPORT,ACT-EXPORT,explicit export mapping"},
    )
    assert saved.status_code == 201
    relationship_id = saved.get_json()["relationship_ids"][0]
    comparison = client.post(
        "/api/v1/analysis/prodrug-comparison",
        json={"project_id": project_id, "endpoint_key": "IC50:import-v1", "persist": True},
    )
    assert comparison.status_code == 201
    comparison_payload = comparison.get_json()
    run_id = comparison_payload["analysis_run_id"]
    audit_id = comparison_payload["audit_event_id"]

    with read_connection(production_app.config["DATABASE_PATH"]) as connection:
        audit_row = connection.execute(
            "SELECT metadata_json FROM audit_events WHERE id = ? AND project_id = ? AND resource_type = 'analysis_run' AND resource_id = ?",
            (audit_id, project_id, run_id),
        ).fetchone()
    assert audit_row is not None
    audit_metadata = json.loads(audit_row["metadata_json"])
    assert audit_metadata["summary_ids"]
    assert audit_metadata["source_measurement_ids"]
    assert audit_metadata["evidence_boundary"] == comparison_payload["evidence_boundary"]

    json_payload = client.get(f"/api/v1/exports/project/{project_id}?format=json").get_json()
    analysis = next(row for row in json_payload["analysis_runs"] if row["id"] == run_id)
    selection = analysis["input_selection"]
    pair = selection["pairs"][0]
    summary_ids = [pair["prodrug"]["result"]["summary_id"], pair["active"]["result"]["summary_id"]]
    source_measurement_ids = sorted(
        pair["prodrug"]["result"]["source_measurement_ids"] + pair["active"]["result"]["source_measurement_ids"]
    )
    assert analysis["analysis_run_id"] == run_id
    assert analysis["audit_event_id"] == audit_id
    assert analysis["relationship_ids"] == [relationship_id]
    assert sorted(analysis["summary_ids"]) == sorted(summary_ids)
    assert analysis["source_measurement_ids"] == source_measurement_ids
    assert audit_metadata["summary_ids"] == sorted(summary_ids)
    assert audit_metadata["source_measurement_ids"] == source_measurement_ids
    assert sorted(summary_ids) == sorted(
        [pair["prodrug"]["result"]["summary_id"], pair["active"]["result"]["summary_id"]]
    )
    assert source_measurement_ids and analysis["endpoint_metadata"]["endpoint_key"] == "IC50:import-v1"
    assert analysis["input_selection"]["evidence_boundary"] == comparison_payload["evidence_boundary"]

    csv_rows = list(csv.DictReader(io.StringIO(client.get(f"/api/v1/exports/project/{project_id}?format=csv").get_data(as_text=True))))
    csv_analysis = next(row for row in csv_rows if row["record_type"] == "analysis_run" and row["id"] == run_id)
    csv_metadata = json.loads(csv_analysis["metadata_json"])
    assert csv_metadata["analysis_run_id"] == run_id
    assert csv_metadata["audit_event_id"] == audit_id
    assert csv_metadata["relationship_ids"] == [relationship_id]
    assert sorted(csv_metadata["summary_ids"]) == sorted(summary_ids)
    assert csv_metadata["source_measurement_ids"] == source_measurement_ids
    assert csv_metadata["evidence_boundary"] == analysis["evidence_boundary"]
    csv_selection = csv_metadata["input_selection"]
    csv_pair = csv_selection["pairs"][0]
    assert sorted([csv_pair["prodrug"]["result"]["summary_id"], csv_pair["active"]["result"]["summary_id"]]) == sorted(summary_ids)
    assert sorted(csv_pair["prodrug"]["result"]["source_measurement_ids"] + csv_pair["active"]["result"]["source_measurement_ids"]) == source_measurement_ids
    assert csv_metadata["endpoint_metadata"] == analysis["endpoint_metadata"]
    csv_relationship = next(row for row in csv_rows if row["record_type"] == "compound_relationship" and row["id"] == relationship_id)
    assert csv_relationship["project_id"] == project_id and csv_relationship["data_origin"] == "imported"

    other_project = _seed_project(client, "Foreign comparison export", "CMP-FOREIGN")
    foreign_csv = client.get(f"/api/v1/exports/project/{other_project}?format=csv").get_data(as_text=True)
    assert run_id not in foreign_csv and audit_id not in foreign_csv and relationship_id not in foreign_csv


def test_export_is_bounded_and_rejects_invalid_format(production_app):
    client = production_app.test_client()
    project_id = _seed_project(client, "Bounded export fixture", "CMP-BOUND")

    invalid = client.get(f"/api/v1/exports/project/{project_id}?format=xml")
    assert invalid.status_code == 422
    assert invalid.get_json()["error"] == "invalid_export_format"

    with pytest.raises(ExportError, match="safety limit"):
        build_project_export(
            production_app.config["DATABASE_PATH"],
            project_id,
            max_rows=1,
        )

    missing = client.get("/api/v1/exports/project/not-a-project")
    assert missing.status_code == 404


def test_export_route_remains_separate_from_demo_data(tmp_path):
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
    response = demo_app.test_client().get("/api/v1/exports/project/demo-project")
    assert response.status_code == 409
    assert response.get_json()["error"] == "demo_mode"
