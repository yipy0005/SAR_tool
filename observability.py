from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from flask import Flask, Request, g


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = "sar-workbench", environment: str | None = None):
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service,
        }
        if self.environment:
            payload["environment"] = self.environment
        for key in ("event", "request_id", "method", "path", "endpoint", "status", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _log_handler(app: Flask) -> logging.Handler:
    sink = str(app.config.get("LOG_SINK", "stdout")).strip() or "stdout"
    if sink == "stdout":
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
    else:
        path = Path(sink).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            path,
            when="midnight",
            interval=1,
            backupCount=int(app.config.get("LOG_RETENTION_DAYS", 14)),
            utc=True,
            encoding="utf-8",
        )
    handler.setFormatter(JsonFormatter(environment=str(app.config.get("SAR_ENV", "unknown"))))
    setattr(handler, "_sar_json_handler", True)
    setattr(handler, "_sar_sink", sink)
    return handler


def configure_logging(app: Flask) -> None:
    sink = str(app.config.get("LOG_SINK", "stdout")).strip() or "stdout"
    for existing in list(app.logger.handlers):
        app.logger.removeHandler(existing)
        try:
            existing.close()
        except Exception:
            pass
    app.logger.addHandler(_log_handler(app))
    app.logger.setLevel(str(app.config.get("LOG_LEVEL", "INFO")).upper())
    app.logger.propagate = False


def initialize_metrics(app: Flask) -> None:
    app.extensions["sar_metrics"] = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requests_total": 0,
        "requests_by_status": Counter(),
        "requests_by_endpoint": Counter(),
        "log_sink": str(app.config.get("LOG_SINK", "stdout")),
        "log_retention_days": int(app.config.get("LOG_RETENTION_DAYS", 14)),
    }


def record_request(app: Flask, request: Request, status_code: int) -> None:
    metrics = app.extensions["sar_metrics"]
    endpoint = request.endpoint or "unmatched"
    metrics["requests_total"] += 1
    metrics["requests_by_status"][str(status_code)] += 1
    metrics["requests_by_endpoint"][endpoint] += 1
    started_at = getattr(g, "request_started_at", time.monotonic())
    duration_ms = round((time.monotonic() - started_at) * 1000, 3)
    app.logger.info(
        "request_complete",
        extra={
            "event": "request_complete",
            "request_id": g.get("request_id", "unknown"),
            "method": request.method,
            "path": request.path,
            "endpoint": endpoint,
            "status": status_code,
            "duration_ms": duration_ms,
        },
    )


def metrics_snapshot(app: Flask) -> dict[str, Any]:
    metrics = app.extensions["sar_metrics"]
    return {
        "started_at": metrics["started_at"],
        "requests_total": metrics["requests_total"],
        "requests_by_status": dict(sorted(metrics["requests_by_status"].items())),
        "requests_by_endpoint": dict(sorted(metrics["requests_by_endpoint"].items())),
        "logging": {
            "sink": metrics["log_sink"] if metrics["log_sink"] == "stdout" else "file",
            "retention_days": metrics["log_retention_days"],
            "central_collection_required": metrics["log_sink"] == "stdout",
        },
    }
