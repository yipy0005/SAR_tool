# Deployment boundary

This directory contains examples, not a turnkey production deployment. Before use:

1. Install the exact Pixi environment from `/Users/yipyewmun/GitHub/SAR_tool/pixi.toml` and `pixi.lock`, including the approved WSGI server.
2. Create a non-privileged `sar-workbench` account and `/var/lib/sar-workbench` with restrictive permissions.
3. Provide `/etc/sar-workbench.env` through the host secret-management process. It must set `SAR_ENV=production`, `SAR_DEMO_MODE=false`, an explicit `SAR_DATABASE_PATH` or PostgreSQL URL, upload paths, a long secret, local credentials or the approved identity boundary, `SAR_SECURE_COOKIES=true`, `SAR_LOG_SINK=stdout`, and `SAR_LOG_RETENTION_DAYS` for the central collector's retention policy. Migration 006 does not guess ownership for pre-existing projects; assign an owner membership explicitly before exposing them.
4. Run `pixi run db-init`, then verify with `pixi run db-check`.
5. Run the WSGI entry point through `/Users/yipyewmun/GitHub/SAR_tool/deploy/gunicorn.conf.py`. It defaults to one worker. SQLite must remain single-worker; set `SAR_GUNICORN_WORKERS` above one only with a PostgreSQL URL after the isolated PostgreSQL migration, concurrency, failure-recovery, and restore qualification gates pass. Do not expose the loopback listener directly.
6. Terminate HTTPS at the reverse proxy, restrict the host name, configure upload limits consistently, and send structured stdout/stderr logs to the approved sink.
7. For SQLite, schedule backups and verify each artifact with:

```bash
pixi run python manage.py backup-db --output /var/backups/sar/sar-$(date +%Y%m%d).db
pixi run python manage.py verify-backup --input /var/backups/sar/sar-$(date +%Y%m%d).db
pixi run python manage.py restore-check --input /var/backups/sar/sar-$(date +%Y%m%d).db
```

`restore-check` operates on a temporary copy and verifies schema, integrity, and readiness without modifying the source backup or live database.

For PostgreSQL, configure `SAR_DATABASE_PATH` as a PostgreSQL URL and use the same Pixi migration/readiness commands. The backend adapter is available through the pinned `psycopg==3.3.4` dependency. An isolated synthetic PostgreSQL 16.4 qualification has passed schema 14 with 13 scientific tables, 73 indexes, 62 foreign keys, advisory-locked concurrent migrations (4/4), rollback, 12 concurrent project writes, 16 concurrent analysis writes, information-gap and generated-recommendation persistence/review, 8/8 two-worker `/api/v1/readyz` requests, and `pg_dump`/`pg_restore`. For PostgreSQL, the target host must provide approved `pg_dump`, `pg_restore`, and `pg_verifybackup` binaries. Set `SAR_PG_DUMP_BIN`, `SAR_PG_RESTORE_BIN`, and `SAR_PG_VERIFYBACKUP_BIN` when they are not on `PATH`; the pinned Pixi Python environment supplies the application adapter, while client binaries remain host-managed to avoid conflicting with the pinned RDKit/libpq solve.

Do not use `backup-db`, `verify-backup`, or `restore-check` for PostgreSQL. Use the Pixi-managed operational commands for logical archives:

```bash
export SAR_DATABASE_PATH='postgresql://<user>:<password>@<host>:5432/<database>'
pixi run python manage.py postgres-backup --output /var/backups/sar/sar-$(date +%Y%m%d).dump
pixi run python manage.py postgres-verify-backup --input /var/backups/sar/sar-$(date +%Y%m%d).dump

# Point this only at a disposable isolated restore target; credentials stay in environment-managed configuration.
export SAR_RESTORE_DATABASE_PATH='postgresql://<user>:<password>@<host>:5432/<isolated_restore_database>'
pixi run python manage.py postgres-restore-check --input /var/backups/sar/sar-$(date +%Y%m%d).dump
```

For physical PostgreSQL backups, run `pg_verifybackup` on the backup directory through `postgres-verify-backup`. Target-host failure recovery, physical-backup verification, deployment-level load, centralized operations, and restore rehearsal remain required.

The SQLite deployment is intentionally one controlled application process with one writer at a time. Never load proprietary data until identity, project membership, backup restoration, database qualification, adversarial testing, and qualified scientific review gates are complete.
