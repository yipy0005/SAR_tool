from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    demo_mode: bool
    database_path: str
    upload_dir: str
    secret_key: str
    auth_mode: str
    max_upload_bytes: int
    host: str
    port: int
    secure_cookies: bool
    log_sink: str = "stdout"
    log_level: str = "INFO"
    log_retention_days: int = 14

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.environ.get("SAR_ENV", "local").strip().lower()
        demo_mode = _env_bool("SAR_DEMO_MODE", environment == "demo")
        default_database = PROJECT_ROOT / "instance" / "workbench" / "sar.db"
        default_upload_dir = PROJECT_ROOT / "instance" / "workbench" / "uploads"
        database_path = os.environ.get(
            "SAR_DATABASE_PATH", str(default_database)
        )
        upload_dir = os.environ.get(
            "SAR_UPLOAD_DIR", str(default_upload_dir)
        )
        return cls(
            environment=environment,
            demo_mode=demo_mode,
            database_path=database_path,
            upload_dir=upload_dir,
            secret_key=os.environ.get("SAR_SECRET_KEY", ""),
            auth_mode=os.environ.get("SAR_AUTH_MODE", "disabled").strip().lower(),
            max_upload_bytes=int(os.environ.get("SAR_MAX_UPLOAD_BYTES", "5000000")),
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5002")),
            secure_cookies=_env_bool("SAR_SECURE_COOKIES", environment == "production"),
            log_sink=os.environ.get("SAR_LOG_SINK", "stdout").strip(),
            log_level=os.environ.get("SAR_LOG_LEVEL", "INFO").strip().upper(),
            log_retention_days=int(os.environ.get("SAR_LOG_RETENTION_DAYS", "14")),
        )

    def validate_startup(self) -> None:
        """Fail closed for production instead of silently falling back to demo data."""
        if self.environment != "production":
            return
        errors: list[str] = []
        if self.demo_mode:
            errors.append("SAR_DEMO_MODE must be false in production")
        if not os.environ.get("SAR_DATABASE_PATH"):
            errors.append("SAR_DATABASE_PATH must be explicitly configured in production")
        if len(self.secret_key) < 32:
            errors.append("SAR_SECRET_KEY must be at least 32 characters in production")
        if self.auth_mode == "disabled":
            errors.append("SAR_AUTH_MODE must enable an external or local identity boundary")
        if self.auth_mode != "local":
            errors.append("The first production release supports SAR_AUTH_MODE=local only")
        if not os.environ.get("SAR_AUTH_EMAIL"):
            errors.append("SAR_AUTH_EMAIL must be configured for local production authentication")
        if not os.environ.get("SAR_AUTH_PASSWORD_HASH"):
            errors.append("SAR_AUTH_PASSWORD_HASH must be configured for local production authentication")
        if self.max_upload_bytes <= 0:
            errors.append("SAR_MAX_UPLOAD_BYTES must be positive")
        if self.log_sink != "stdout" and not Path(self.log_sink).expanduser().is_absolute():
            errors.append("SAR_LOG_SINK must be stdout or an absolute file path")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("SAR_LOG_LEVEL must be a standard logging level")
        if self.log_retention_days < 1:
            errors.append("SAR_LOG_RETENTION_DAYS must be at least one")
        if errors:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))

    def ensure_runtime_directories(self) -> None:
        database_value = self.database_path.strip().lower()
        is_postgres = database_value.startswith(("postgresql://", "postgres://"))
        if not is_postgres and self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        Path(self.upload_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
