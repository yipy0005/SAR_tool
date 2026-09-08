from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from database import read_connection, transaction


ANALYSIS_VERSION = "selectivity-summary-v1"
OBSERVED_QUALIFIERS = frozenset({"=", "~"})


class SelectivityAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _validate_inputs(primary_compatibility_key: str, comparator_compatibility_key: str, threshold: float) -> float:
    primary = str(primary_compatibility_key).strip()
    comparator = str(comparator_compatibility_key).strip()
    if not primary or not comparator:
        raise SelectivityAnalysisError("Primary and comparator compatibility keys are required")
    if primary == comparator:
        raise SelectivityAnalysisError("Primary and comparator compatibility keys must differ")
    try:
        numeric_threshold = float(threshold)
    except (TypeError, ValueError) as exc:
        raise SelectivityAnalysisError("selectivity_threshold must be numeric") from exc
    if not math.isfinite(numeric_threshold) or numeric_threshold < 0:
        raise SelectivityAnalysisError("selectivity_threshold must be finite and non-negative")
    return numeric_threshold


def _latest_by_compound(records: Iterable[Mapping[str, Any]], compatibility_key: str) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for raw in records:
        record = dict(raw)
        if str(record.get("compatibility_key", "")) != compatibility_key:
            continue
        compound_id = str(record.get("compound_id", "")).strip()
        if not compound_id:
            continue
        current = selected.get(compound_id)
        candidate_key = (str(record.get("created_at", "")), str(record.get("id", "")))
        current_key = (str(current.get("created_at", "")), str(current.get("id", ""))) if current else ("", "")
        if current is None or candidate_key > current_key:
            selected[compound_id] = record
    return selected


def _observed_value(record: Mapping[str, Any] | None) -> tuple[float | None, str | None]:
    if record is None:
        return None, "summary_missing"
    state = str(record.get("summary_state", ""))
    qualifier = str(record.get("summary_qualifier", ""))
    value = record.get("summary_value")
    if state == "missing" or value is None:
        return None, "summary_missing"
    if state != "observed" or qualifier not in OBSERVED_QUALIFIERS:
        return None, "summary_not_exact_observed"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None, "summary_value_invalid"
    if not math.isfinite(numeric):
        return None, "summary_value_invalid"
    return numeric, None


def calculate_selectivity(
    records: Iterable[Mapping[str, Any]],
    primary_compatibility_key: str,
    comparator_compatibility_key: str,
    *,
    selectivity_threshold: float = 1.0,
) -> list[dict[str, Any]]:
    threshold = _validate_inputs(primary_compatibility_key, comparator_compatibility_key, selectivity_threshold)
    records_list = [dict(record) for record in records]
    primary = _latest_by_compound(records_list, primary_compatibility_key)
    comparator = _latest_by_compound(records_list, comparator_compatibility_key)
    compound_ids = set(primary) | set(comparator)
    def sort_key(compound_id: str) -> tuple[str, str]:
        record = primary.get(compound_id) or comparator.get(compound_id) or {}
        return (str(record.get("registration_id", "")), compound_id)
    results: list[dict[str, Any]] = []
    for compound_id in sorted(compound_ids, key=sort_key):
        primary_record = primary.get(compound_id)
        comparator_record = comparator.get(compound_id)
        primary_value, primary_reason = _observed_value(primary_record)
        comparator_value, comparator_reason = _observed_value(comparator_record)
        primary_unit = primary_record.get("canonical_unit") if primary_record else None
        comparator_unit = comparator_record.get("canonical_unit") if comparator_record else None
        reason = primary_reason or comparator_reason
        delta = None
        status = "incomplete"
        evidence_status = "incomplete_summary_evidence"
        if primary_value is not None and comparator_value is not None:
            if primary_unit != comparator_unit:
                reason = "canonical_unit_mismatch"
            else:
                delta = primary_value - comparator_value
                status = "selective" if delta >= threshold else "non_selective"
                evidence_status = "observed_compatible_summaries"
                reason = None
        results.append(
            {
                "compound_id": compound_id,
                "registration_id": (primary_record or comparator_record or {}).get("registration_id"),
                "primary_summary_id": primary_record.get("id") if primary_record else None,
                "comparator_summary_id": comparator_record.get("id") if comparator_record else None,
                "primary_value": primary_value,
                "comparator_value": comparator_value,
                "selectivity_delta": delta,
                "canonical_unit": primary_unit or comparator_unit,
                "status": status,
                "evidence_status": evidence_status,
                "reason": reason,
            }
        )
    return results


def run_selectivity_analysis(
    database_path: str,
    project_id: str,
    primary_compatibility_key: str,
    comparator_compatibility_key: str,
    *,
    selectivity_threshold: float = 1.0,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    threshold = _validate_inputs(primary_compatibility_key, comparator_compatibility_key, selectivity_threshold)
    with read_connection(database_path) as connection:
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ms.id, ms.compound_id, c.registration_id, ms.compatibility_key, ms.canonical_unit,
                       ms.summary_state, ms.summary_value, ms.summary_qualifier, ms.created_at
                FROM measurement_summaries AS ms
                JOIN compounds AS c ON c.id = ms.compound_id
                WHERE c.project_id = ?
                ORDER BY ms.created_at, ms.id
                """,
                (project_id,),
            )
        ]
    observations = calculate_selectivity(
        records,
        primary_compatibility_key,
        comparator_compatibility_key,
        selectivity_threshold=threshold,
    )
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'selectivity', ?, ?, 'completed', ?)
            """,
            (
                run_id,
                project_id,
                json.dumps(
                    {
                        "primary_compatibility_key": primary_compatibility_key,
                        "comparator_compatibility_key": comparator_compatibility_key,
                        "selectivity_threshold": threshold,
                        "selection_policy": "latest_created_at_then_id_per_compound_and_context",
                    },
                    sort_keys=True,
                ),
                ANALYSIS_VERSION,
                now,
            ),
        )
        for observation in observations:
            connection.execute(
                """
                INSERT INTO selectivity_observations
                    (id, analysis_run_id, compound_id, primary_summary_id, comparator_summary_id,
                     primary_value, comparator_value, selectivity_delta, canonical_unit, status,
                     evidence_status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("selectivity"),
                    run_id,
                    observation["compound_id"],
                    observation["primary_summary_id"],
                    observation["comparator_summary_id"],
                    observation["primary_value"],
                    observation["comparator_value"],
                    observation["selectivity_delta"],
                    observation["canonical_unit"],
                    observation["status"],
                    observation["evidence_status"],
                    observation["reason"],
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'analysis_run', 'analysis_run', ?, 'success', ?, ?)
            """,
            (
                _id("audit"),
                actor_user_id,
                project_id,
                run_id,
                json.dumps({"type": "selectivity", "observation_count": len(observations), "analysis_version": ANALYSIS_VERSION}, sort_keys=True),
                now,
            ),
        )
    counts = {status: sum(1 for item in observations if item["status"] == status) for status in ("selective", "non_selective", "incomplete")}
    return {
        "analysis_run_id": run_id,
        "analysis_type": "selectivity",
        "algorithm_version": ANALYSIS_VERSION,
        "primary_compatibility_key": primary_compatibility_key,
        "comparator_compatibility_key": comparator_compatibility_key,
        "selectivity_threshold": threshold,
        "counts": counts,
        "observations": observations,
        "data_origin": "derived",
    }


def get_selectivity_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'selectivity'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        observations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT so.*, c.registration_id
                FROM selectivity_observations AS so
                JOIN compounds AS c ON c.id = so.compound_id
                WHERE so.analysis_run_id = ?
                ORDER BY c.registration_id
                """,
                (run_id,),
            )
        ]
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["observations"] = observations
    result["data_origin"] = "derived"
    result["counts"] = {
        status: sum(1 for item in observations if item["status"] == status)
        for status in ("selective", "non_selective", "incomplete")
    }
    return result
