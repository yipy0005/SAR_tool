from __future__ import annotations

import sqlite3

import pytest

from app import create_app
from chemistry import StructureValidationError, render_scaffold_svg, standardize_structure
from database import apply_migrations, backup_database, read_connection, transaction, verify_backup
from scientific import normalize_measurement


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


def test_rdkit_identity_preserves_stereochemistry_and_renders_from_molecule():
    structure = standardize_structure("C[C@H](O)CC", "smiles")

    assert structure.isomeric_smiles != structure.canonical_smiles
    assert structure.stereochemistry_status == "defined_stereochemistry"
    assert structure.inchikey
    assert structure.rendered_svg.lstrip().startswith("<svg")


def test_scaffold_svg_uses_standard_rdkit_line_angle_rendering():
    svg = render_scaffold_svg("O=C(Nc1ccc([*:2])cn1)Cc1ccc([*:1])cc1")

    assert svg.lstrip().startswith("<svg")
    assert 'data-scaffold-positions="R1,R2,R3"' in svg
    assert 'data-scaffold-highlighted="true"' in svg
    assert svg.count("<path") >= 20
    for colour in ("#82C9FF", "#C9B6FF", "#8DE4BB", "#FF9D9D"):
        assert colour.lower() in svg.lower()


def test_invalid_structure_fails_closed():
    with pytest.raises(StructureValidationError) as error:
        standardize_structure("not-a-valid-smiles")

    assert error.value.code in {"parse_failed", "sanitize_failed"}


def test_censored_concentration_inverts_to_pic50_bound():
    measurement = normalize_measurement("10", "nM", ">", endpoint_code="IC50")

    assert measurement.canonical_unit == "pIC50"
    assert measurement.canonical_value == pytest.approx(8.0)
    assert measurement.qualifier == "<"
    assert measurement.lower_bound is None
    assert measurement.upper_bound == pytest.approx(8.0)


def test_production_import_is_quarantined_then_committed_with_provenance(production_app):
    client = production_app.test_client()
    project_response = client.post("/api/v1/projects", json={"name": "Production fixture"})
    assert project_response.status_code == 201
    project_id = project_response.get_json()["project"]["id"]

    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier\n"
        "CMP-001,CCO,IC50,10,nM,=\n"
        "CMP-002,not-a-valid-smiles,IC50,20,nM,=\n"
    )
    preview_response = client.post(
        "/api/v1/imports/preview",
        json={"filename": "fixture.csv", "csv_text": csv_text},
    )
    assert preview_response.status_code == 201
    preview = preview_response.get_json()
    assert preview["status"] == "preview"
    assert preview["data_origin"] == "quarantined_import"
    assert preview["accepted_count"] == 1
    assert preview["rejected_count"] == 1
    assert preview["rows"][1]["status"] == "rejected"

    commit_response = client.post(
        f"/api/v1/imports/{preview['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert commit_response.status_code == 200
    assert commit_response.get_json()["inserted_compounds"] == 1
    assert commit_response.get_json()["inserted_measurements"] == 1

    compounds = client.get(f"/api/v1/compounds?project_id={project_id}").get_json()
    assert compounds["data_origin"] == "production"
    assert compounds["count"] == 1
    assert compounds["results"][0]["registration_id"] == "CMP-001"
    assert compounds["results"][0]["rendered_svg"].startswith("<svg")

    measurements = client.get(f"/api/v1/measurements?project_id={project_id}").get_json()
    assert len(measurements["results"]) == 1
    assert measurements["results"][0]["canonical_unit"] == "pIC50"
    assert measurements["results"][0]["canonical_value"] == pytest.approx(8.0)
    assert measurements["results"][0]["source_row_id"] == "2"


def test_invalid_only_import_cannot_commit(production_app):
    client = production_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Invalid fixture"}).get_json()["project"]["id"]
    preview = client.post(
        "/api/v1/imports/preview",
        json={"csv_text": "compound_id,smiles,assay,result,unit\nBAD,not-a-valid-smiles,IC50,10,nM\n"},
    ).get_json()

    response = client.post(
        f"/api/v1/imports/{preview['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert response.status_code == 422
    assert client.get(f"/api/v1/compounds?project_id={project_id}").get_json()["count"] == 0


def test_migrations_are_idempotent_and_backup_is_restorable(tmp_path):
    database_path = str(tmp_path / "source.db")
    assert apply_migrations(database_path) == 22
    assert apply_migrations(database_path) == 22
    backup_path = str(tmp_path / "backup.db")
    backup_database(database_path, backup_path)
    verification = verify_backup(backup_path)
    assert verification == {"schema_version": 22, "scientific_tables": 20, "integrity": "ok"}

    connection = sqlite3.connect(backup_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 22
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='measurements'").fetchone()
    finally:
        connection.close()


def test_production_configuration_rejects_demo_fallback():
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        create_app(
            {
                "TESTING": False,
                "SAR_ENV": "production",
                "SAR_DEMO_MODE": True,
                "SAR_AUTH_MODE": "disabled",
                "DATABASE_PATH": "/tmp/should-not-be-used.db",
                "UPLOAD_DIR": "/tmp/uploads",
                "SECRET_KEY": "short",
                "MAX_CONTENT_LENGTH": 1_000_000,
                "HOST": "127.0.0.1",
                "PORT": 5001,
                "SESSION_COOKIE_SECURE": True,
            }
        )


def test_production_login_and_csrf_boundary(monkeypatch, tmp_path):
    from re import search
    from werkzeug.security import generate_password_hash

    monkeypatch.setenv("SAR_ENV", "production")
    monkeypatch.setenv("SAR_DEMO_MODE", "false")
    monkeypatch.setenv("SAR_DATABASE_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("SAR_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SAR_SECRET_KEY", "production-secret-that-is-long-enough-123456")
    monkeypatch.setenv("SAR_AUTH_MODE", "local")
    monkeypatch.setenv("SAR_AUTH_EMAIL", "scientist@example.test")
    monkeypatch.setenv("SAR_AUTH_PASSWORD_HASH", generate_password_hash("correct horse battery staple"))
    monkeypatch.setenv("SAR_SECURE_COOKIES", "false")

    production = create_app()
    client = production.test_client()
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/metrics").status_code == 401
    assert client.get("/api/v1/readyz").status_code == 200

    login_page = client.get("/login")
    token_match = search(r'name="_csrf_token" value="([^"]+)"', login_page.get_data(as_text=True))
    assert login_page.status_code == 200
    assert token_match
    token = token_match.group(1)

    login_response = client.post(
        "/login",
        data={
            "email": "scientist@example.test",
            "password": "correct horse battery staple",
            "_csrf_token": token,
        },
    )
    assert login_response.status_code == 302

    no_csrf = client.post("/api/v1/projects", json={"name": "Blocked"})
    assert no_csrf.status_code == 400
    assert no_csrf.get_json()["error"] == "csrf_failed"

    dashboard = client.get("/")
    dashboard_token = search(r'name="_csrf_token" value="([^"]+)"', dashboard.get_data(as_text=True)).group(1)
    create_response = client.post(
        "/api/v1/projects",
        json={"name": "Authenticated production project"},
        headers={"X-CSRF-Token": dashboard_token},
    )
    assert create_response.status_code == 201
    project_id = create_response.get_json()["project"]["id"]
    members = client.get(f"/api/v1/projects/{project_id}/members")
    assert members.status_code == 200
    assert members.get_json()["members"][0]["role"] == "owner"

    add_member = client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"email": "reviewer@example.test", "display_name": "Reviewer", "role": "viewer"},
        headers={"X-CSRF-Token": dashboard_token},
    )
    assert add_member.status_code == 201
    assert add_member.get_json()["member"]["role"] == "viewer"
    assert len(client.get(f"/api/v1/projects/{project_id}/members").get_json()["members"]) == 2
    with transaction(production.config["DATABASE_PATH"]) as connection:
        connection.execute(
            """
            UPDATE project_members SET role = 'viewer'
            WHERE project_id = ? AND user_id = (SELECT id FROM users WHERE email = 'scientist@example.test')
            """,
            (project_id,),
        )
    blocked_hypothesis = client.post(
        "/api/v1/hypotheses",
        json={"project_id": project_id, "statement": "Viewer cannot mutate this project.", "rationale": "Role test"},
        headers={"X-CSRF-Token": dashboard_token},
    )
    assert blocked_hypothesis.status_code == 403
    with read_connection(production.config["DATABASE_PATH"]) as connection:
        actor_events = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE actor_user_id IS NOT NULL"
        ).fetchone()[0]
    assert actor_events >= 2
    assert create_response.headers["Content-Security-Policy"].startswith("default-src 'self'")


def test_mmp_analysis_and_claims_are_versioned_and_evidence_linked(production_app):

    client = production_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "MMP fixture"}).get_json()["project"]["id"]
    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier\n"
        "CMP-A,c1ccccc1,IC50,100,nM,=\n"
        "CMP-B,Cc1ccccc1,IC50,10,nM,=\n"
    )
    preview = client.post("/api/v1/imports/preview", json={"csv_text": csv_text}).get_json()
    assert client.post(
        f"/api/v1/imports/{preview['import_id']}/commit", json={"project_id": project_id}
    ).status_code == 200

    analysis_response = client.post("/api/v1/analysis/mmp", json={"project_id": project_id})
    assert analysis_response.status_code == 201
    analysis = analysis_response.get_json()
    assert analysis["algorithm_version"] == "rdkit-mcs-mmp-v1"
    assert analysis["pair_count"] == 1
    assert analysis["pairs"][0]["effect_value"] == pytest.approx(1.0)
    run_id = analysis["analysis_run_id"]

    stored_run = client.get(f"/api/v1/analysis/mmp/{run_id}").get_json()
    assert stored_run["id"] == run_id
    assert stored_run["pairs"][0]["similarity"] >= 0.65

    claims_response = client.post(f"/api/v1/analysis/mmp/{run_id}/claims")
    assert claims_response.status_code == 201
    claims = claims_response.get_json()["claims"]
    assert len(claims) == 1
    assert claims[0]["claim_type"] == "observation"
    assert claims[0]["evidence_strength"] == "preliminary"
    assert claims[0]["n_pairs"] == 1

    listed = client.get(f"/api/v1/sar/claims?project_id={project_id}").get_json()
    assert listed["data_origin"] == "derived"
    assert listed["claims"][0]["analysis_run_id"] == run_id


def test_project_and_import_access_are_membership_scoped(monkeypatch, tmp_path):
    from re import search
    from werkzeug.security import generate_password_hash

    monkeypatch.setenv("SAR_ENV", "production")
    monkeypatch.setenv("SAR_DEMO_MODE", "false")
    monkeypatch.setenv("SAR_DATABASE_PATH", str(tmp_path / "membership.db"))
    monkeypatch.setenv("SAR_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SAR_SECRET_KEY", "production-secret-that-is-long-enough-123456")
    monkeypatch.setenv("SAR_AUTH_MODE", "local")
    monkeypatch.setenv("SAR_AUTH_EMAIL", "scientist@example.test")
    monkeypatch.setenv("SAR_AUTH_PASSWORD_HASH", generate_password_hash("correct horse battery staple"))
    monkeypatch.setenv("SAR_SECURE_COOKIES", "false")

    production = create_app()
    client = production.test_client()
    login_page = client.get("/login")
    token = search(r'name="_csrf_token" value="([^"]+)"', login_page.get_data(as_text=True)).group(1)
    assert client.post(
        "/login",
        data={
            "email": "scientist@example.test",
            "password": "correct horse battery staple",
            "_csrf_token": token,
        },
    ).status_code == 302
    csrf = search(r'name="_csrf_token" value="([^"]+)"', client.get("/").get_data(as_text=True)).group(1)
    own = client.post(
        "/api/v1/projects",
        json={"name": "Visible project"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["project"]["id"]

    now = "2026-09-07T08:00:00+00:00"
    with transaction(production.config["DATABASE_PATH"]) as connection:
        connection.execute(
            "INSERT INTO users (id, email, display_name, role, is_active, created_at) VALUES (?, ?, ?, 'scientist', 1, ?)",
            ("user-other", "other@example.test", "Other", now),
        )
        connection.execute(
            "INSERT INTO projects (id, name, description, status, data_origin, created_at, updated_at) VALUES (?, ?, '', 'active', 'imported', ?, ?)",
            ("private-project", "Private project", now, now),
        )
        connection.execute(
            "INSERT INTO project_members (project_id, user_id, role, created_at) VALUES (?, ?, 'owner', ?)",
            ("private-project", "user-other", now),
        )

    projects = client.get("/api/v1/projects").get_json()["projects"]
    assert [project["id"] for project in projects] == [own]
    assert client.get("/api/v1/compounds?project_id=private-project").status_code == 400
    assert client.get("/api/v1/projects/private-project/members").status_code == 403

    preview = client.post(
        "/api/v1/imports/preview",
        json={"csv_text": "compound_id,smiles,assay,result,unit\nCMP-001,CCO,IC50,10,nM\n"},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 201
    with transaction(production.config["DATABASE_PATH"]) as connection:
        connection.execute(
            "UPDATE import_batches SET created_by_user_id = 'user-other' WHERE id = ?",
            (preview.get_json()["import_id"],),
        )
    assert client.get(f"/api/v1/imports/{preview.get_json()['import_id']}").status_code == 403
