# Gunicorn configuration for the first SQLite-backed deployment.
import os


# SQLite remains one application process with one writer. Multi-worker mode is
# allowed only for an explicitly configured PostgreSQL URL after qualification.
database_path = os.environ.get("SAR_DATABASE_PATH", "").strip().lower()
try:
    workers = int(os.environ.get("SAR_GUNICORN_WORKERS", "1"))
except ValueError as exc:
    raise RuntimeError("SAR_GUNICORN_WORKERS must be a positive integer") from exc
if workers < 1:
    raise RuntimeError("SAR_GUNICORN_WORKERS must be a positive integer")
if workers > 1 and not database_path.startswith(("postgresql://", "postgres://")):
    raise RuntimeError(
        "SAR_GUNICORN_WORKERS greater than 1 requires a PostgreSQL SAR_DATABASE_PATH"
    )
try:
    port = int(os.environ.get("SAR_GUNICORN_PORT", "5001"))
except ValueError as exc:
    raise RuntimeError("SAR_GUNICORN_PORT must be an integer between 1 and 65535") from exc
if not 1 <= port <= 65535:
    raise RuntimeError("SAR_GUNICORN_PORT must be an integer between 1 and 65535")

bind = f"127.0.0.1:{port}"
threads = 4
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True
forwarded_allow_ips = "127.0.0.1"
