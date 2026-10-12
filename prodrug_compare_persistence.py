"""Durable prodrug comparison persistence kept separate from the browser's ephemeral path."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
import uuid

from database import transaction
from prodrug_engine import compare_relationships


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def persist_comparison(database_path: str, project_id: str, endpoint_key: str, *, relationship_ids: list[str] | None = None, actor_user_id: str | None = None, request_id: str | None = None, offset: int = 0, limit: int = 500) -> dict[str, Any]:
    """Read sources and write snapshot/run/audit while one write transaction holds the source boundary."""
    run_id = _id("analysis")
    audit_id = _id("audit")
    now = _now()
    with transaction(database_path, isolation_level="repeatable read") as connection:
        # SQLite's BEGIN IMMEDIATE and PostgreSQL's repeatable-read transaction keep all source reads
        # and the run/audit inserts inside one consistent snapshot. Serialization/lock conflicts
        # roll back this context, and the API translates them to a 409 with no stored run.
        result = compare_relationships(database_path, project_id, endpoint_key, relationship_ids=relationship_ids, offset=0, limit=None, connection=connection)
        snapshot = dict(result)
        snapshot["snapshot_version"] = "prodrug-comparison-snapshot-v1"
        snapshot["project_id"] = project_id
        snapshot["analysis_run_id"] = run_id
        snapshot["audit_event_id"] = audit_id
        snapshot["actor_user_id"] = actor_user_id
        snapshot["request_id"] = request_id
        snapshot["pairs"] = [{**pair, "prodrug": {k: v for k, v in pair["prodrug"].items() if k not in {"rendered_svg"}}, "active": {k: v for k, v in pair["active"].items() if k not in {"rendered_svg"}}} for pair in result["pairs"]]
        connection.execute(
            "INSERT INTO analysis_runs (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at) VALUES (?, ?, 'prodrug_comparison', ?, ?, 'completed', ?)",
            (run_id, project_id, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), result["algorithm_version"], now),
        )
        summary_ids = sorted({
            result_view.get("summary_id")
            for pair in result["pairs"]
            for result_view in (
                pair.get("prodrug", {}).get("result", {}),
                pair.get("active", {}).get("result", {}),
            )
            if result_view.get("summary_id")
        })
        source_measurement_ids = sorted({
            source_id
            for pair in result["pairs"]
            for result_view in (
                pair.get("prodrug", {}).get("result", {}),
                pair.get("active", {}).get("result", {}),
            )
            for source_id in result_view.get("source_measurement_ids", [])
        })
        audit_metadata = {
            "analysis_run_id": run_id,
            "audit_event_id": audit_id,
            "endpoint_key": endpoint_key,
            "endpoint_metadata": result.get("endpoint_metadata"),
            "relationship_ids": [p["id"] for p in result["pairs"]],
            "relationship_data_origin": "imported",
            "summary_ids": summary_ids,
            "source_measurement_ids": source_measurement_ids,
            "pair_statuses": [p.get("comparison", {}).get("status") for p in result["pairs"]],
            "evidence_boundary": result.get("evidence_boundary"),
            "algorithm_version": result["algorithm_version"],
        }
        connection.execute(
            "INSERT INTO audit_events (id, actor_user_id, project_id, operation, resource_type, resource_id, request_id, outcome, metadata_json, created_at) VALUES (?, ?, ?, 'prodrug_comparison', 'analysis_run', ?, ?, 'success', ?, ?)",
            (audit_id, actor_user_id, project_id, run_id, request_id, json.dumps(audit_metadata, sort_keys=True), now),
        )
    page = result["pairs"][offset:offset + limit]
    return {**result, "analysis_run_id": run_id, "audit_event_id": audit_id, "project_id": project_id, "persistence": "durable", "data_origin": "derived", "pairs": page, "offset": offset, "returned": len(page), "has_more": offset + len(page) < result["pair_count"], "next_offset": offset + len(page) if offset + len(page) < result["pair_count"] else None}
