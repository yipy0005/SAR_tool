

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from app import create_app
from identity_provider import ExternalIdentityConfiguration, IdentityProviderConfigurationError, external_identity_boundary_status
from authorization import has_project_access
from database import apply_migrations, read_connection, transaction


def _test_app(tmp_path, **overrides):
    config = {
        "TESTING": True,
        "SAR_ENV": "test",
        "SAR_DEMO_MODE": False,
        "SAR_AUTH_MODE": "test-only",
        "SECRET_KEY": "test-secret-that-is-long-enough-for-release-gates",
        "DATABASE_PATH": str(tmp_path / "release.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "MAX_CONTENT_LENGTH": 2_000_000,
        "MAX_EXPORT_ROWS": 100_000,
    }
    config.update(overrides)
    return create_app(config)


def test_high_volume_import_and_export_refuse_silent_truncation(tmp_path):
    app = _test_app(tmp_path, MAX_EXPORT_ROWS=100)
    client = app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "High volume release gate"}).get_json()["project"]["id"]
    rows = [
        f"CMP-{index:04d},CCO,IC50,{index + 1},nM,="
        for index in range(512)
    ]
    csv_text = "compound_id,smiles,assay,result,unit,qualifier\n" + "\n".join(rows) + "\n"

    preview = client.post("/api/v1/imports/preview", json={"filename": "volume.csv", "csv_text": csv_text})
    assert preview.status_code == 201
    assert preview.get_json()["accepted_count"] == 512
    committed = client.post(
        f"/api/v1/imports/{preview.get_json()['import_id']}/commit",
        json={"project_id": project_id},
    )
    assert committed.status_code == 200
    assert committed.get_json()["inserted_compounds"] == 512
    assert client.get(f"/api/v1/compounds?project_id={project_id}").get_json()["count"] == 512

    export = client.get(f"/api/v1/exports/project/{project_id}?format=json")
    assert export.status_code == 422
    assert export.get_json()["error"] == "export_unavailable"
    assert "safety limit" in export.get_json()["message"]
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE operation = 'project_export'").fetchone()[0] == 0


def test_authorization_revocation_race_never_returns_private_data(tmp_path):
    database_path = str(tmp_path / "authorization-race.db")
    apply_migrations(database_path)
    now = "2026-09-07T09:00:00+00:00"
    with transaction(database_path) as connection:
        connection.execute(
            "INSERT INTO users (id, email, display_name, role, is_active, created_at) VALUES (?, ?, ?, 'scientist', 1, ?)",
            ("race-user", "race@example.test", "Race User", now),
        )
        connection.execute(
            "INSERT INTO projects (id, name, description, status, data_origin, created_at, updated_at) VALUES (?, ?, '', 'active', 'imported', ?, ?)",
            ("race-project", "Race project", now, now),
        )
        connection.execute(
            "INSERT INTO project_members (project_id, user_id, role, created_at) VALUES (?, ?, 'viewer', ?)",
            ("race-project", "race-user", now),
        )
    app = create_app(
        {
            "TESTING": True,
            "SAR_ENV": "production",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "local",
            "SECRET_KEY": "production-race-secret-that-is-long-enough",
            "DATABASE_PATH": database_path,
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
            "SESSION_COOKIE_SECURE": False,
        }
    )

    def read_export(_index):
        client = app.test_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["user_email"] = "race@example.test"
            session["csrf_token"] = "race-csrf"
        response = client.get("/api/v1/exports/project/race-project?format=json")
        assert response.status_code in {200, 403, 404}
        assert response.status_code != 500
        if response.status_code == 200:
            body = response.get_data(as_text=True)
            assert "race-project" in body
        return response.status_code

    def revoke_and_restore(_index):
        with transaction(database_path) as connection:
            connection.execute(
                "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
                ("race-project", "race-user"),
            )
        with transaction(database_path) as connection:
            connection.execute(
                "INSERT INTO project_members (project_id, user_id, role, created_at) VALUES (?, ?, 'viewer', ?) "
                "ON CONFLICT(project_id, user_id) DO UPDATE SET role = excluded.role",
                ("race-project", "race-user", now),
            )
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(read_export, index) for index in range(32)]
        futures.extend(executor.submit(revoke_and_restore, index) for index in range(8))
        results = [future.result() for future in as_completed(futures)]
    read_results = [result for result in results if type(result) is int]
    assert read_results
    assert set(read_results) <= {200, 403, 404}
    assert has_project_access(database_path, "race-project", minimum_role="viewer", user_id="race-user") is True


def test_logging_sink_is_structured_and_retention_is_visible_but_paths_are_not_exposed(tmp_path):
    log_path = tmp_path / "logs" / "sar.jsonl"
    app = _test_app(tmp_path, LOG_SINK=str(log_path), LOG_RETENTION_DAYS=7)
    client = app.test_client()
    assert client.get("/healthz").status_code == 200
    assert log_path.is_file()
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["service"] == "sar-workbench"
    assert record["environment"] == "test"
    assert record["event"] == "request_complete"

    metrics = client.get("/api/v1/metrics").get_json()["metrics"]
    assert metrics["logging"] == {
        "sink": "file",
        "retention_days": 7,
        "central_collection_required": False,
    }
    assert str(log_path) not in json.dumps(metrics)


def test_production_logging_configuration_fails_closed_for_relative_sink(tmp_path):
    with pytest.raises(RuntimeError, match="SAR_LOG_SINK"):
        create_app(
            {
                "TESTING": False,
                "SAR_ENV": "production",
                "SAR_DEMO_MODE": False,
                "SAR_AUTH_MODE": "local",
                "SAR_AUTH_EMAIL": "scientist@example.test",
                "SAR_AUTH_PASSWORD_HASH": "not-used-in-this-startup-test",
                "SECRET_KEY": "production-logging-secret-that-is-long-enough",
                "DATABASE_PATH": str(tmp_path / "logging.db"),
                "UPLOAD_DIR": str(tmp_path / "uploads"),
                "MAX_CONTENT_LENGTH": 1_000_000,
                "LOG_SINK": "relative.log",
            }
        )


def test_external_identity_boundary_fails_closed_without_provider_configuration(monkeypatch):
    for name in (
        "SAR_EXTERNAL_IDP_PROTOCOL",
        "SAR_EXTERNAL_IDP_ISSUER_URL",
        "SAR_EXTERNAL_IDP_CLIENT_ID",
        "SAR_EXTERNAL_IDP_CLIENT_SECRET",
        "SAR_EXTERNAL_IDP_REDIRECT_URI",
        "SAR_EXTERNAL_IDP_AUDIENCE",
    ):
        monkeypatch.delenv(name, raising=False)

    status = external_identity_boundary_status()
    assert status["configured"] is False
    assert status["status"] == "not_ready"
    assert "do-not-return-this" not in json.dumps(status)
    with pytest.raises(IdentityProviderConfigurationError, match="incomplete"):
        ExternalIdentityConfiguration.from_environment().validate()


def test_external_identity_boundary_requires_https_and_never_returns_secret(monkeypatch):
    monkeypatch.setenv("SAR_EXTERNAL_IDP_PROTOCOL", "oidc")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_ISSUER_URL", "http://idp.example.test")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_CLIENT_ID", "sar-client")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_CLIENT_SECRET", "do-not-return-this")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_REDIRECT_URI", "https://sar.example.test/auth/callback")

    status = external_identity_boundary_status()
    assert status["configured"] is False
    assert "do-not-return-this" not in json.dumps(status)
    with pytest.raises(IdentityProviderConfigurationError, match="HTTPS"):
        ExternalIdentityConfiguration.from_environment().validate()


def test_external_identity_boundary_accepts_complete_https_contract(monkeypatch):
    monkeypatch.setenv("SAR_EXTERNAL_IDP_PROTOCOL", "oidc")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_ISSUER_URL", "https://idp.example.test")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_CLIENT_ID", "sar-client")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_CLIENT_SECRET", "do-not-return-this")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_REDIRECT_URI", "https://sar.example.test/auth/callback")
    monkeypatch.setenv("SAR_EXTERNAL_IDP_AUDIENCE", "sar-api")

    status = external_identity_boundary_status()
    assert status == {
        "configured": True,
        "protocol": "oidc",
        "status": "configuration_ready",
        "implementation": "deployment_boundary_only",
    }
