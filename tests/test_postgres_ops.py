import subprocess
from pathlib import Path

import pytest

import postgres_ops
from postgres_ops import PostgreSQLOperationError


def test_postgres_backup_requires_postgres_and_does_not_put_password_in_argv(tmp_path, monkeypatch):
    with pytest.raises(PostgreSQLOperationError, match="PostgreSQL URL"):
        postgres_ops.postgres_backup(str(tmp_path / "sqlite.db"), str(tmp_path / "backup.dump"))

    calls = []

    def fake_run(command, *, env, check, capture_output, text):
        calls.append((command, env))
        output = Path(command[command.index("--file") + 1])
        output.write_bytes(b"custom archive")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(postgres_ops.subprocess, "run", fake_run)
    output = tmp_path / "backup.dump"
    result = postgres_ops.postgres_backup(
        "postgresql://backup_user:secret%40word@db.example:5432/sar",
        str(output),
        pg_dump_bin="pg_dump-test",
    )

    assert result == {"status": "backed_up", "format": "custom", "output": str(output.resolve())}
    assert output.read_bytes() == b"custom archive"
    command, environment = calls[0]
    assert "secret%40word" not in command
    assert "secret@word" not in command
    assert environment["PGPASSWORD"] == "secret@word"
    assert environment["PGDATABASE"] == "sar"


def test_postgres_backup_refuses_overwrite_without_explicit_flag(tmp_path):
    output = tmp_path / "existing.dump"
    output.write_bytes(b"existing")
    with pytest.raises(PostgreSQLOperationError, match="already exists"):
        postgres_ops.postgres_backup(
            "postgresql://user:password@db.example/sar",
            str(output),
        )


def test_postgres_archive_verification_uses_pg_restore_list(tmp_path, monkeypatch):
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"archive")
    calls = []

    def fake_run(command, *, env, check, capture_output, text):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="TABLE public.projects\nTABLE public.measurements\n", stderr="")

    monkeypatch.setattr(postgres_ops.subprocess, "run", fake_run)
    result = postgres_ops.verify_postgres_backup(str(archive), pg_restore_bin="pg_restore-test")

    assert result["status"] == "verified"
    assert result["backup_type"] == "logical_custom"
    assert result["entry_count"] == 2
    assert calls == [["pg_restore-test", "--list", str(archive.resolve())]]


def test_physical_backup_verification_uses_pg_verifybackup(tmp_path, monkeypatch):
    backup_directory = tmp_path / "physical"
    backup_directory.mkdir()
    calls = []

    def fake_run(command, *, env, check, capture_output, text):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(postgres_ops.subprocess, "run", fake_run)
    result = postgres_ops.verify_postgres_backup(
        str(backup_directory),
        pg_verifybackup_bin="pg_verifybackup-test",
    )

    assert result["backup_type"] == "physical"
    assert calls == [["pg_verifybackup-test", str(backup_directory.resolve())]]


def test_restore_rehearsal_targets_explicit_database_without_password_argv(tmp_path, monkeypatch):
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"archive")
    calls = []

    def fake_run(command, *, env, check, capture_output, text):
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(postgres_ops.subprocess, "run", fake_run)
    result = postgres_ops.postgres_restore_rehearsal(
        "postgresql://restore_user:restore-secret@db.example:5432/sar_restore",
        str(archive),
        pg_restore_bin="pg_restore-test",
    )

    assert result["status"] == "restore_verified"
    command, environment = calls[0]
    assert command == [
        "pg_restore-test",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--dbname",
        "sar_restore",
        str(archive.resolve()),
    ]
    assert "restore-secret" not in command
    assert environment["PGPASSWORD"] == "restore-secret"
