from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "scripts" / "setup_local.py"


def test_setup_local_seeds_synthetic_workflow(tmp_path: Path) -> None:
    database_path = tmp_path / "local.db"
    upload_dir = tmp_path / "uploads"
    env_file = tmp_path / "local.env"
    environment = os.environ.copy()
    environment["SAR_SETUP_PASSWORD"] = "test-only-password"
    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_SCRIPT),
            "--non-interactive",
            "--seed-example",
            "--seed-contradiction",
            "--email",
            "scientist@example.org",
            "--database",
            str(database_path),
            "--upload-dir",
            str(upload_dir),
            "--env-file",
            str(env_file),
            "--project-id",
            "test_example_project",
            "--dataset",
            "compact",
            "--port",
            "5010",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    with sqlite3.connect(database_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "users",
                "projects",
                "project_members",
                "measurements",
                "measurement_summaries",
                "contradiction_observations",
            )
        }
        imports = connection.execute(
            "SELECT status, COUNT(*) FROM import_batches GROUP BY status"
        ).fetchall()

    assert counts == {
        "users": 1,
        "projects": 1,
        "project_members": 1,
        "measurements": 49,
        "measurement_summaries": 85,
        "contradiction_observations": 2,
    }
    assert imports == [("committed", 2)]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    env_text = env_file.read_text(encoding="utf-8")
    assert "SAR_AUTH_PASSWORD_HASH" in env_text
    assert "SAR_GUNICORN_PORT=5010" in env_text
    assert "test-only-password" not in env_text


def test_setup_local_druglike_series(tmp_path: Path) -> None:
    database_path = tmp_path / "druglike.db"
    upload_dir = tmp_path / "druglike-uploads"
    env_file = tmp_path / "druglike.env"
    environment = os.environ.copy()
    environment["SAR_SETUP_PASSWORD"] = "test-only-password"
    result = subprocess.run(
        [
            sys.executable,
            str(SETUP_SCRIPT),
            "--non-interactive",
            "--seed-example",
            "--no-seed-contradiction",
            "--dataset",
            "druglike",
            "--email",
            "scientist@example.org",
            "--database",
            str(database_path),
            "--upload-dir",
            str(upload_dir),
            "--env-file",
            str(env_file),
            "--project-id",
            "druglike_project",
            "--port",
            "5011",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    with sqlite3.connect(database_path) as connection:
        compound_count = connection.execute("SELECT COUNT(*) FROM compounds").fetchone()[0]
        structure_count = connection.execute("SELECT COUNT(*) FROM structure_records").fetchone()[0]
        measurement_count = connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        summary_count = connection.execute("SELECT COUNT(*) FROM measurement_summaries").fetchone()[0]
        project_name = connection.execute("SELECT name FROM projects WHERE id = ?", ("druglike_project",)).fetchone()[0]

    assert (compound_count, structure_count, measurement_count, summary_count) == (10, 10, 80, 70)
    assert project_name == "Synthetic drug-like SAR series"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_setup_local_blank_workspace_has_no_synthetic_records(tmp_path: Path) -> None:
    database_path = tmp_path / "blank.db"
    upload_dir = tmp_path / "blank-uploads"
    env_file = tmp_path / "blank.env"
    environment = os.environ.copy()
    environment["SAR_SETUP_PASSWORD"] = "test-only-password"
    command = [
        sys.executable,
        str(SETUP_SCRIPT),
        "--non-interactive",
        "--no-seed-example",
        "--email",
        "scientist@example.org",
        "--database",
        str(database_path),
        "--upload-dir",
        str(upload_dir),
        "--env-file",
        str(env_file),
        "--project-id",
        "blank_project",
        "--port",
        "5012",
    ]
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name FROM projects WHERE id = 'blank_project'").fetchone()[0] == "Local project"
        assert connection.execute("SELECT COUNT(*) FROM compounds").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 0

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO compounds (id, project_id, registration_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("compound-1", "blank_project", "CMP-001", "2026-09-16T00:00:00+00:00", "2026-09-16T00:00:00+00:00"),
        )
        connection.commit()

    reused = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    assert reused.returncode == 2
    assert "will not reuse a database" in reused.stderr
