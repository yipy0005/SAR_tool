from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from config import Settings
from database import (
    _PostgresConnection,
    _begin_statement,
    apply_migrations,
    backup_database,
    is_postgres_database,
    restore_rehearsal,
    verify_backup,
)


class _FakePostgresConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None):
        self.calls.append((statement, parameters))
        return "cursor"

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def test_postgres_database_detection_and_transaction_mode():
    assert is_postgres_database("postgresql://user:password@db.example/sar")
    assert is_postgres_database("postgres://user:password@db.example/sar")
    assert not is_postgres_database("/secure/path/sar.db")
    assert _begin_statement("postgresql://db/sar") == "BEGIN"
    assert _begin_statement("/secure/path/sar.db") == "BEGIN IMMEDIATE"


def test_postgres_adapter_translates_sqlite_placeholders_without_leaking_parameters():
    raw = _FakePostgresConnection()
    connection = _PostgresConnection(raw)

    assert connection.execute("SELECT * FROM projects WHERE id = ?", ("project-1",)) == "cursor"
    assert raw.calls == [("SELECT * FROM projects WHERE id = %s", ("project-1",))]


def test_postgres_runtime_directories_only_create_upload_path(tmp_path):
    upload_dir = tmp_path / "uploads"
    settings = Settings(
        environment="production",
        demo_mode=False,
        database_path="postgresql://user:password@db.example/sar",
        upload_dir=str(upload_dir),
        secret_key="x" * 40,
        auth_mode="local",
        max_upload_bytes=1_000_000,
        host="127.0.0.1",
        port=5001,
        secure_cookies=True,
    )

    settings.ensure_runtime_directories()

    assert upload_dir.is_dir()
    assert not (tmp_path / "postgresql:").exists()


def test_sqlite_backup_commands_reject_postgres_urls_before_filesystem_access(tmp_path):
    with pytest.raises(RuntimeError, match="pg_dump"):
        backup_database("postgresql://user:password@db.example/sar", str(tmp_path / "backup.db"))
    with pytest.raises(RuntimeError, match="pg_verifybackup"):
        verify_backup("postgresql://user:password@db.example/sar")
    assert not any(Path(tmp_path).iterdir())



def test_restore_rehearsal_isolated_copy_is_ready(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    apply_migrations(str(source))
    backup_database(str(source), str(backup))
    before = backup.read_bytes()

    result = restore_rehearsal(str(backup))

    assert result == {
        "schema_version": 23,
        "scientific_tables": 20,
        "integrity": "ok",
        "database_ready": True,
        "source_schema_version": 23,
    }
    assert backup.read_bytes() == before



def test_database_ready_requires_current_schema_and_scientific_tables(tmp_path):
    from database import database_ready

    database_path = tmp_path / "ready.db"
    apply_migrations(str(database_path))
    assert database_ready(str(database_path)) is True

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE contradiction_observations")
        connection.commit()
    finally:
        connection.close()
    assert database_ready(str(database_path)) is False


def test_database_ready_rejects_stale_schema(tmp_path):
    from database import database_ready

    database_path = tmp_path / "stale.db"
    apply_migrations(str(database_path))
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DELETE FROM schema_migrations WHERE version = 23")
        connection.commit()
    finally:
        connection.close()
    assert database_ready(str(database_path)) is False


def test_gunicorn_config_rejects_sqlite_multiworker(monkeypatch):
    import runpy

    config_path = Path(__file__).resolve().parents[1] / "deploy" / "gunicorn.conf.py"
    monkeypatch.setenv("SAR_DATABASE_PATH", "/var/lib/sar-workbench/sar.db")
    monkeypatch.setenv("SAR_GUNICORN_WORKERS", "2")
    with pytest.raises(RuntimeError, match="requires a PostgreSQL"):
        runpy.run_path(str(config_path))


def test_gunicorn_config_allows_explicit_postgres_multiworker(monkeypatch):
    import runpy

    config_path = Path(__file__).resolve().parents[1] / "deploy" / "gunicorn.conf.py"
    monkeypatch.setenv("SAR_DATABASE_PATH", "postgresql://user:password@db.example/sar")
    monkeypatch.setenv("SAR_GUNICORN_WORKERS", "2")
    config = runpy.run_path(str(config_path))
    assert config["workers"] == 2
    assert config["threads"] == 4



def test_postgres_migrations_use_advisory_lock(monkeypatch, tmp_path):
    import database as database_module

    class Result(list):
        def fetchone(self):
            return self[0] if self else None

    class FakePostgresConnection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=()):
            self.calls.append((statement, parameters))
            if statement.startswith("SELECT version FROM schema_migrations"):
                return Result()
            if statement.startswith("SELECT MAX(version)"):
                return Result([{"version": 1}])
            return Result()

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    migration = tmp_path / "001_test.sql"
    migration.write_text("CREATE TABLE IF NOT EXISTS advisory_probe (id TEXT);", encoding="utf-8")
    connection = FakePostgresConnection()
    monkeypatch.setattr(database_module, "connect", lambda _database_path: connection)

    assert database_module.apply_migrations("postgresql://user:password@db.example/sar", tmp_path) == 1
    statements = [statement for statement, _parameters in connection.calls]
    assert statements[0].startswith("SELECT pg_advisory_lock")
    assert any(statement.startswith("SELECT pg_advisory_unlock") for statement in statements)
