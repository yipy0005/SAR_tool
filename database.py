from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import PROJECT_ROOT


MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
POSTGRES_PREFIXES = ("postgresql://", "postgres://")
REQUIRED_SCIENTIFIC_TABLES = (
    "projects",
    "measurements",
    "measurement_summaries",
    "activity_cliffs",
    "selectivity_observations",
    "translation_observations",
    "adme_observations",
    "pareto_observations",
    "contradiction_observations",
    "design_candidates",
    "property_profiles",
    "information_gain_observations",
    "generated_recommendations",
    "series",
    "series_versions",
    "series_memberships",
    "prediction_models",
    "prediction_observations",
    "compound_relationships",
    "pharmacophore_rgroup_assignments",
)
POSTGRES_MIGRATION_LOCK_KEY = 753104534946


def is_postgres_database(database_path: str) -> bool:
    return str(database_path).strip().lower().startswith(POSTGRES_PREFIXES)


class _PostgresConnection:
    """Minimal DB-API surface used by the application’s SQL layer."""

    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, statement: str, parameters: tuple[Any, ...] | list[Any] = ()):
        translated = statement.replace("?", "%s")
        if parameters:
            return self._connection.execute(translated, tuple(parameters))
        return self._connection.execute(translated)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect(database_path: str) -> Any:
    if is_postgres_database(database_path):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL support requires the Pixi-managed psycopg dependency"
            ) from exc
        return _PostgresConnection(
            psycopg.connect(database_path, row_factory=dict_row, autocommit=True)
        )

    if database_path != ":memory:":
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        database_path,
        timeout=5.0,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if database_path != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _begin_statement(database_path: str) -> str:
    return "BEGIN" if is_postgres_database(database_path) else "BEGIN IMMEDIATE"


def _statements(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def latest_migration_version(migrations_dir: Path = MIGRATIONS_DIR) -> int:
    versions = []
    for migration_file in Path(migrations_dir).glob("*.sql"):
        try:
            versions.append(int(migration_file.name.split("_", 1)[0]))
        except (IndexError, ValueError):
            continue
    return max(versions, default=0)


def apply_migrations(database_path: str, migrations_dir: Path = MIGRATIONS_DIR) -> int:
    connection = connect(database_path)
    migration_lock_acquired = False
    try:
        if is_postgres_database(database_path):
            connection.execute(
                "SELECT pg_advisory_lock(?)",
                (POSTGRES_MIGRATION_LOCK_KEY,),
            )
            migration_lock_acquired = True
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        migration_files = sorted(Path(migrations_dir).glob("*.sql"))
        for migration_file in migration_files:
            version = int(migration_file.name.split("_", 1)[0])
            if version in applied:
                continue
            connection.execute(_begin_statement(database_path))
            try:
                for statement in _statements(migration_file.read_text(encoding="utf-8")):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, migration_file.name),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0)
    finally:
        if migration_lock_acquired:
            try:
                connection.execute(
                    "SELECT pg_advisory_unlock(?)",
                    (POSTGRES_MIGRATION_LOCK_KEY,),
                )
            except Exception:
                pass
        connection.close()


@contextmanager
def transaction(database_path: str) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    connection.execute(_begin_statement(database_path))
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify_backup(backup_path: str) -> dict[str, int | str]:
    """Verify a SQLite backup without mutating it or applying migrations."""
    if is_postgres_database(backup_path):
        raise RuntimeError("PostgreSQL backups must be verified with pg_verifybackup or a restore rehearsal")
    path = Path(backup_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Backup does not exist: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        version = int(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0)
        expected_version = latest_migration_version()
        if version != expected_version:
            raise RuntimeError(f"Backup schema version {version} does not match current version {expected_version}")
        placeholders = ",".join("?" for _ in REQUIRED_SCIENTIFIC_TABLES)
        count = connection.execute(
            f"SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
            REQUIRED_SCIENTIFIC_TABLES,
        ).fetchone()[0]
        if count != len(REQUIRED_SCIENTIFIC_TABLES):
            raise RuntimeError(
                f"Backup is missing scientific tables ({count}/{len(REQUIRED_SCIENTIFIC_TABLES)})"
            )
        return {
            "schema_version": version,
            "scientific_tables": int(count),
            "integrity": integrity,
        }
    finally:
        connection.close()


@contextmanager
def read_connection(database_path: str) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        yield connection
    finally:
        connection.close()


def database_ready(database_path: str) -> bool:
    """Return true only when the current schema and scientific tables are complete."""
    try:
        with read_connection(database_path) as connection:
            migration = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()
            if migration is None or int(migration["version"] or 0) != latest_migration_version():
                return False
            placeholders = ",".join("?" for _ in REQUIRED_SCIENTIFIC_TABLES)
            if is_postgres_database(database_path):
                rows = connection.execute(
                    f"""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema() AND table_name IN ({placeholders})
                    """,
                    REQUIRED_SCIENTIFIC_TABLES,
                )
                present = {row["table_name"] for row in rows}
            else:
                rows = connection.execute(
                    f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
                    REQUIRED_SCIENTIFIC_TABLES,
                )
                present = {row["name"] for row in rows}
            return set(REQUIRED_SCIENTIFIC_TABLES) <= present
    except Exception:
        return False


def backup_database(database_path: str, backup_path: str) -> None:
    """Create a consistent SQLite backup without copying a live WAL file manually."""
    if is_postgres_database(database_path):
        raise RuntimeError("PostgreSQL backups must use pg_dump and a restore rehearsal")
    Path(backup_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    source = connect(database_path)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()



def restore_rehearsal(backup_path: str) -> dict[str, int | str | bool]:
    """Restore a SQLite backup into a temporary database and verify readiness."""
    if is_postgres_database(backup_path):
        raise RuntimeError("PostgreSQL restore rehearsals must use pg_restore and an isolated database")
    source = Path(backup_path).expanduser().resolve()
    verification = verify_backup(str(source))
    with tempfile.TemporaryDirectory(prefix="sar-restore-") as directory:
        restored = Path(directory) / "restored.db"
        shutil.copy2(source, restored)
        restored_version = apply_migrations(str(restored))
        restored_ready = database_ready(str(restored))
        restored_verification = verify_backup(str(restored))
    return {
        "schema_version": restored_version,
        "scientific_tables": int(restored_verification["scientific_tables"]),
        "integrity": str(restored_verification["integrity"]),
        "database_ready": restored_ready,
        "source_schema_version": int(verification["schema_version"]),
    }
