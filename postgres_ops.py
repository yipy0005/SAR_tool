"""Operational PostgreSQL backup and restore helpers.

Commands deliberately receive connection details through libpq environment
variables rather than putting the database URL or password in argv.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from database import is_postgres_database


class PostgreSQLOperationError(RuntimeError):
    pass


def _connection_environment(database_url: str) -> tuple[dict[str, str], str]:
    if not is_postgres_database(database_url):
        raise PostgreSQLOperationError("A PostgreSQL URL is required for this operation")
    parsed = urlsplit(database_url)
    if not parsed.hostname:
        raise PostgreSQLOperationError("PostgreSQL URL must include a host or socket path")
    database_name = parsed.path.lstrip("/")
    if not database_name:
        raise PostgreSQLOperationError("PostgreSQL URL must include a database name")

    environment = os.environ.copy()
    environment["PGHOST"] = parsed.hostname
    if parsed.port:
        environment["PGPORT"] = str(parsed.port)
    if parsed.username:
        environment["PGUSER"] = unquote(parsed.username)
    if parsed.password is not None:
        environment["PGPASSWORD"] = unquote(parsed.password)
    environment["PGDATABASE"] = unquote(database_name)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in {"sslmode", "sslcert", "sslkey", "sslrootcert", "channel_binding"}:
            environment[f"PG{key.upper()}"] = value
    return environment, unquote(database_name)


def _run(command: list[str], environment: dict[str, str], *, label: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PostgreSQLOperationError(f"{label} executable was not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()[-1:]
        suffix = f": {detail[0]}" if detail else ""
        raise PostgreSQLOperationError(f"{label} failed{suffix}") from exc


def postgres_backup(
    database_url: str,
    output_path: str,
    *,
    overwrite: bool = False,
    pg_dump_bin: str = "pg_dump",
) -> dict[str, Any]:
    environment, _database_name = _connection_environment(database_url)
    output = Path(output_path).expanduser().resolve()
    if output.exists() and not overwrite:
        raise PostgreSQLOperationError(f"Backup already exists; pass overwrite explicitly: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        _run(
            [pg_dump_bin, "--format=custom", "--no-owner", "--file", str(partial)],
            environment,
            label="pg_dump",
        )
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise PostgreSQLOperationError("pg_dump completed without producing a non-empty archive")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return {"status": "backed_up", "format": "custom", "output": str(output)}


def verify_postgres_backup(
    input_path: str,
    *,
    pg_verifybackup_bin: str = "pg_verifybackup",
    pg_restore_bin: str = "pg_restore",
) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Backup does not exist: {source}")
    environment = os.environ.copy()
    if source.is_dir():
        _run([pg_verifybackup_bin, str(source)], environment, label="pg_verifybackup")
        return {"status": "verified", "backup_type": "physical", "input": str(source)}
    if not source.is_file():
        raise PostgreSQLOperationError("Backup input must be a regular file or physical-backup directory")
    result = _run([pg_restore_bin, "--list", str(source)], environment, label="pg_restore archive verification")
    entry_count = len([line for line in result.stdout.splitlines() if line.strip() and not line.startswith(";")])
    if entry_count < 1:
        raise PostgreSQLOperationError("pg_restore archive listing was empty")
    return {
        "status": "verified",
        "backup_type": "logical_custom",
        "input": str(source),
        "entry_count": entry_count,
    }


def postgres_restore_rehearsal(
    database_url: str,
    input_path: str,
    *,
    pg_restore_bin: str = "pg_restore",
) -> dict[str, Any]:
    environment, database_name = _connection_environment(database_url)
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise PostgreSQLOperationError("Logical restore rehearsal requires a custom-format archive file")
    _run(
        [
            pg_restore_bin,
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--dbname",
            database_name,
            str(source),
        ],
        environment,
        label="pg_restore",
    )
    return {"status": "restore_verified", "backup_type": "logical_custom", "input": str(source), "database": database_name}
