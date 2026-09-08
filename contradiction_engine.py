import json
import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from database import read_connection, transaction


ANALYSIS_VERSION = "contradiction-reconciliation-v1"
OBSERVED_QUALIFIERS = frozenset({"=", "~"})


class ContradictionAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _record(record: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    return {key: record[key] for key in record.keys()}


def _validate_tolerance(value: float) -> float:
    try:
        tolerance = float(value)
    except (TypeError, ValueError) as exc:
        raise ContradictionAnalysisError("value_tolerance must be numeric") from exc
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ContradictionAnalysisError("value_tolerance must be finite and non-negative")
    return tolerance


def _numeric(record: Mapping[str, Any]) -> float | None:
    value = record.get("summary_value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def calculate_contradictions(
    records: Iterable[Mapping[str, Any]],
    *,
    value_tolerance: float = 0.5,
) -> list[dict[str, Any]]:
    tolerance = _validate_tolerance(value_tolerance)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        record = _record(raw)
        compound_id = str(record.get("compound_id", "")).strip()
        compatibility_key = str(record.get("compatibility_key", "")).strip()
        if compound_id and compatibility_key:
            groups[(compound_id, compatibility_key)].append(record)

    results: list[dict[str, Any]] = []
    for (compound_id, compatibility_key), group in sorted(groups.items()):
        group.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))))
        summary_ids = [str(item.get("id")) for item in group if item.get("id")]
        units = {item.get("canonical_unit") for item in group}
        non_null_units = {unit for unit in units if unit}
        exact = [item for item in group if str(item.get("summary_state", "")) == "observed" and str(item.get("summary_qualifier", "")) in OBSERVED_QUALIFIERS and _numeric(item) is not None]
        censored = [
            item
            for item in group
            if (
                str(item.get("summary_state", "")) not in {"observed", "missing"}
                and str(item.get("summary_state", ""))
            )
            or (
                str(item.get("summary_state", "")) == "observed"
                and str(item.get("summary_qualifier", "")) not in OBSERVED_QUALIFIERS
            )
        ]
        values = [_numeric(item) for item in group]
        values_by_id = {str(item.get("id")): value for item, value in zip(group, values) if item.get("id") and value is not None}
        status = None
        reason = None
        if len(non_null_units) > 1:
            status = "unit_conflict"
            reason = "multiple_canonical_units_for_compatibility_key"
        elif exact and censored:
            status = "censoring_conflict"
            reason = "exact_and_censored_summaries_coexist"
        elif len(exact) > 1:
            exact_values = [_numeric(item) for item in exact]
            if max(exact_values) - min(exact_values) > tolerance:
                status = "contradictory"
                reason = "exact_summary_values_exceed_tolerance"
        if status:
            results.append(
                {
                    "compound_id": compound_id,
                    "registration_id": (group[0] or {}).get("registration_id"),
                    "compatibility_key": compatibility_key,
                    "summary_ids": summary_ids,
                    "values": values_by_id,
                    "canonical_unit": next(iter(non_null_units), None),
                    "status": status,
                    "reconciliation_status": "unreconciled",
                    "reason": reason,
                }
            )
    return results


def run_contradiction_analysis(
    database_path: str,
    project_id: str,
    *,
    value_tolerance: float = 0.5,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    tolerance = _validate_tolerance(value_tolerance)
    with read_connection(database_path) as connection:
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ms.id, ms.compound_id, c.registration_id, ms.compatibility_key,
                       ms.canonical_unit, ms.summary_state, ms.summary_value,
                       ms.summary_qualifier, ms.created_at
                FROM measurement_summaries AS ms
                JOIN compounds AS c ON c.id = ms.compound_id
                WHERE c.project_id = ?
                ORDER BY ms.created_at, ms.id
                """,
                (project_id,),
            )
        ]
    observations = calculate_contradictions(records, value_tolerance=tolerance)
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'contradictions', ?, ?, 'completed', ?)
            """,
            (
                run_id,
                project_id,
                json.dumps(
                    {
                        "value_tolerance": tolerance,
                        "selection_policy": "all_persisted_summaries_per_compound_and_context",
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
                INSERT INTO contradiction_observations
                    (id, analysis_run_id, compound_id, compatibility_key, summary_ids_json,
                     values_json, canonical_unit, status, reconciliation_status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("contradiction"),
                    run_id,
                    observation["compound_id"],
                    observation["compatibility_key"],
                    json.dumps(observation["summary_ids"], sort_keys=True),
                    json.dumps(observation["values"], sort_keys=True),
                    observation["canonical_unit"],
                    observation["status"],
                    observation["reconciliation_status"],
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
                json.dumps({"type": "contradictions", "observation_count": len(observations), "analysis_version": ANALYSIS_VERSION}, sort_keys=True),
                now,
            ),
        )
    counts = {status: sum(1 for item in observations if item["status"] == status) for status in ("contradictory", "unit_conflict", "censoring_conflict")}
    return {
        "analysis_run_id": run_id,
        "analysis_type": "contradictions",
        "algorithm_version": ANALYSIS_VERSION,
        "value_tolerance": tolerance,
        "counts": counts,
        "observations": observations,
        "data_origin": "derived",
    }


def get_contradiction_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'contradictions'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT co.*, c.registration_id
                FROM contradiction_observations AS co
                JOIN compounds AS c ON c.id = co.compound_id
                WHERE co.analysis_run_id = ?
                ORDER BY c.registration_id, co.compatibility_key
                """,
                (run_id,),
            )
        ]
    observations: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["summary_ids"] = json.loads(item.pop("summary_ids_json") or "[]")
            item["values"] = json.loads(item.pop("values_json") or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ContradictionAnalysisError("Invalid contradiction JSON") from exc
        observations.append(item)
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["observations"] = observations
    result["counts"] = {status: sum(1 for item in observations if item["status"] == status) for status in ("contradictory", "unit_conflict", "censoring_conflict")}
    result["data_origin"] = "derived"
    return result
