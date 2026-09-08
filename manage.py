from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from config import Settings
from database import apply_migrations, backup_database, database_ready, restore_rehearsal, verify_backup
from postgres_ops import (
    PostgreSQLOperationError,
    postgres_backup,
    postgres_restore_rehearsal,
    verify_postgres_backup,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="SAR Workbench database operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Apply all forward-only migrations")
    subparsers.add_parser("check-db", help="Report migration and readiness state")
    backup_parser = subparsers.add_parser("backup-db", help="Create a consistent SQLite backup")
    backup_parser.add_argument("--output", required=True, help="Destination backup path")
    verify_parser = subparsers.add_parser("verify-backup", help="Verify a SQLite backup without modifying it")
    verify_parser.add_argument("--input", required=True, help="Backup path to verify")
    restore_parser = subparsers.add_parser("restore-check", help="Restore and verify a SQLite backup in an isolated temporary database")
    restore_parser.add_argument("--input", required=True, help="Backup path to restore and verify")
    postgres_backup_parser = subparsers.add_parser("postgres-backup", help="Create an atomic PostgreSQL custom-format backup")
    postgres_backup_parser.add_argument("--output", required=True, help="Destination custom-format archive")
    postgres_backup_parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing archive")
    postgres_verify_parser = subparsers.add_parser("postgres-verify-backup", help="Verify a PostgreSQL archive or physical-backup directory")
    postgres_verify_parser.add_argument("--input", required=True, help="Custom-format archive or physical-backup directory")
    postgres_restore_parser = subparsers.add_parser("postgres-restore-check", help="Restore a PostgreSQL archive into the database named by SAR_RESTORE_DATABASE_PATH")
    postgres_restore_parser.add_argument("--input", required=True, help="Custom-format archive to restore")
    args = parser.parse_args()

    if args.command == "postgres-verify-backup":
        result = verify_postgres_backup(
            args.input,
            pg_verifybackup_bin=os.environ.get("SAR_PG_VERIFYBACKUP_BIN", "pg_verifybackup"),
            pg_restore_bin=os.environ.get("SAR_PG_RESTORE_BIN", "pg_restore"),
        )
        print(json.dumps(result))
        return 0
    if args.command == "postgres-restore-check":
        restore_database = os.environ.get("SAR_RESTORE_DATABASE_PATH", "").strip()
        if not restore_database:
            raise PostgreSQLOperationError("SAR_RESTORE_DATABASE_PATH must identify an isolated restore target")
        result = postgres_restore_rehearsal(
            restore_database,
            args.input,
            pg_restore_bin=os.environ.get("SAR_PG_RESTORE_BIN", "pg_restore"),
        )
        print(json.dumps(result))
        return 0
    if args.command == "verify-backup":
        result = verify_backup(args.input)
        print(json.dumps({"status": "verified", **result}))
        return 0
    if args.command == "restore-check":
        result = restore_rehearsal(args.input)
        print(json.dumps({"status": "restore_verified", **result}))
        return 0

    settings = Settings.from_env()
    if settings.environment == "production":
        settings.validate_startup()
    settings.ensure_runtime_directories()
    if args.command == "postgres-backup":
        result = postgres_backup(
            settings.database_path,
            args.output,
            overwrite=args.overwrite,
            pg_dump_bin=os.environ.get("SAR_PG_DUMP_BIN", "pg_dump"),
        )
        print(json.dumps(result))
        return 0
    version = apply_migrations(settings.database_path)

    if args.command == "init-db":
        print(json.dumps({"status": "ready", "schema_version": version}))
        return 0
    if args.command == "check-db":
        print(json.dumps({"status": "ready" if database_ready(settings.database_path) else "not_ready", "schema_version": version}))
        return 0
    output = str(Path(args.output).expanduser().resolve())
    backup_database(settings.database_path, output)
    print(json.dumps({"status": "backed_up", "schema_version": version, "output": output}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
