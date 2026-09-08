import json
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from database import read_connection, transaction
from property_engine import PROPERTY_DEFINITIONS


ANALYSIS_VERSION = "pareto-evidence-v1"
OBSERVED_QUALIFIERS = frozenset({"=", "~"})
DIRECTIONS = frozenset({"maximize", "minimize"})
SOURCES = frozenset({"summary", "property"})
PROPERTY_KEYS = frozenset(name for name, _unit, _calculator in PROPERTY_DEFINITIONS)


class ParetoAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _record(record: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    return {key: record[key] for key in record.keys()}


def validate_objectives(objectives: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(objectives, (list, tuple)):
        objectives = list(objectives)
    if not objectives:
        raise ParetoAnalysisError("At least one Pareto objective is required")
    if len(objectives) > 16:
        raise ParetoAnalysisError("At most 16 Pareto objectives may be requested")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in objectives:
        if not isinstance(raw, Mapping):
            raise ParetoAnalysisError("Each Pareto objective must be an object")
        source = str(raw.get("source", "")).strip().lower()
        key = str(raw.get("key", "")).strip()
        direction = str(raw.get("direction", "")).strip().lower()
        label = str(raw.get("label", "")).strip()
        if source not in SOURCES:
            raise ParetoAnalysisError("Objective source must be summary or property")
        if not key:
            raise ParetoAnalysisError("Every Pareto objective needs a key")
        if direction not in DIRECTIONS:
            raise ParetoAnalysisError("Objective direction must be maximize or minimize")
        if source == "property" and key not in PROPERTY_KEYS:
            raise ParetoAnalysisError(f"Unsupported property objective: {key!r}")
        if len(label) > 120:
            raise ParetoAnalysisError("Objective labels must be at most 120 characters")
        identity = (source, key)
        if identity in seen:
            raise ParetoAnalysisError(f"Duplicate Pareto objective: {source}:{key}")
        seen.add(identity)
        normalized.append({"source": source, "key": key, "direction": direction, "label": label or f"{source}:{key}"})
    return normalized


def _latest(records: Iterable[Mapping[str, Any]], identity_fields: tuple[str, ...]) -> dict[tuple[str, ...], dict[str, Any]]:
    selected: dict[tuple[str, ...], dict[str, Any]] = {}
    for raw in records:
        record = _record(raw)
        identity = tuple(str(record.get(field, "")) for field in identity_fields)
        if any(not value for value in identity):
            continue
        current = selected.get(identity)
        candidate_key = (str(record.get("created_at", "")), str(record.get("id", "")))
        current_key = (str(current.get("created_at", "")), str(current.get("id", ""))) if current else ("", "")
        if current is None or candidate_key > current_key:
            selected[identity] = record
    return selected


def _summary_value(record: Mapping[str, Any] | None) -> tuple[float | None, str | None, str | None]:
    if record is None:
        return None, None, "summary_missing"
    state = str(record.get("summary_state", ""))
    qualifier = str(record.get("summary_qualifier", ""))
    value = record.get("summary_value")
    if state == "missing" or value is None:
        return None, None, "summary_missing"
    if state != "observed" or qualifier not in OBSERVED_QUALIFIERS:
        return None, qualifier or None, "summary_not_exact_observed"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None, qualifier or None, "summary_value_invalid"
    if not math.isfinite(numeric):
        return None, qualifier or None, "summary_value_invalid"
    return numeric, qualifier or None, None


def _property_value(record: Mapping[str, Any] | None, key: str) -> tuple[float | None, str | None]:
    if record is None:
        return None, "property_profile_missing"
    if str(record.get("status", "")) != "computed":
        return None, str(record.get("reason") or "property_profile_not_computed")
    try:
        descriptors = json.loads(record.get("descriptors_json") or "{}")
        descriptor = descriptors.get(key) or {}
        numeric = float(descriptor.get("value"))
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
        return None, "property_value_invalid"
    if not math.isfinite(numeric):
        return None, "property_value_invalid"
    return numeric, None


def calculate_pareto(
    compounds: Iterable[Mapping[str, Any]],
    summary_records: Iterable[Mapping[str, Any]],
    property_records: Iterable[Mapping[str, Any]],
    objectives: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized_objectives = validate_objectives(objectives)
    compound_rows = [_record(compound) for compound in compounds]
    compound_rows.sort(key=lambda item: (str(item.get("registration_id", "")), str(item.get("id", ""))))
    summaries = _latest(summary_records, ("compound_id", "compatibility_key"))
    properties = _latest(property_records, ("compound_id", "property_key"))
    rows: list[dict[str, Any]] = []

    for compound in compound_rows:
        compound_id = str(compound.get("id", "")).strip()
        objective_values: dict[str, dict[str, Any]] = {}
        incomplete_reasons: list[str] = []
        for objective in normalized_objectives:
            source = objective["source"]
            key = objective["key"]
            objective_id = f"{source}:{key}"
            if source == "summary":
                record = summaries.get((compound_id, key))
                value, qualifier, reason = _summary_value(record)
                item = {
                    "source": source,
                    "key": key,
                    "direction": objective["direction"],
                    "label": objective["label"],
                    "value": value,
                    "unit": record.get("canonical_unit") if record else None,
                    "qualifier": qualifier,
                    "evidence_id": record.get("id") if record else None,
                    "evidence_class": "experimental",
                    "status": "observed" if value is not None else "incomplete",
                    "reason": reason,
                }
                if value is not None and not item["unit"]:
                    item["value"] = None
                    item["status"] = "incomplete"
                    item["reason"] = "canonical_unit_missing"
            else:
                record = properties.get((compound_id, key))
                value, reason = _property_value(record, key)
                item = {
                    "source": source,
                    "key": key,
                    "direction": objective["direction"],
                    "label": objective["label"],
                    "value": value,
                    "unit": None,
                    "qualifier": None,
                    "evidence_id": record.get("id") if record else None,
                    "evidence_class": "derived",
                    "status": "derived" if value is not None else "incomplete",
                    "reason": reason,
                }
            objective_values[objective_id] = item
            if item["value"] is None:
                incomplete_reasons.append(f"{objective_id}:{item['reason']}")
        rows.append(
            {
                "compound_id": compound_id,
                "registration_id": compound.get("registration_id"),
                "objective_values": objective_values,
                "objective_status": "complete" if not incomplete_reasons else "incomplete",
                "is_pareto": False,
                "domination_count": None,
                "reason": ";".join(incomplete_reasons) if incomplete_reasons else None,
            }
        )

    complete = [row for row in rows if row["objective_status"] == "complete"]
    objective_ids = [f"{item['source']}:{item['key']}" for item in normalized_objectives]

    def dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        no_worse = True
        strictly_better = False
        for objective in normalized_objectives:
            objective_id = f"{objective['source']}:{objective['key']}"
            left_value = float(left["objective_values"][objective_id]["value"])
            right_value = float(right["objective_values"][objective_id]["value"])
            if objective["direction"] == "maximize":
                if left_value < right_value:
                    no_worse = False
                    break
                if left_value > right_value:
                    strictly_better = True
            else:
                if left_value > right_value:
                    no_worse = False
                    break
                if left_value < right_value:
                    strictly_better = True
        return no_worse and strictly_better

    for row in complete:
        row["domination_count"] = sum(1 for other in complete if other is not row and dominates(other, row))
        row["is_pareto"] = row["domination_count"] == 0
    for row in rows:
        if row["objective_status"] == "complete":
            row["status"] = "pareto" if row["is_pareto"] else "complete_non_pareto"
        else:
            row["status"] = "incomplete"
    return rows


def run_pareto_analysis(
    database_path: str,
    project_id: str,
    objectives: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    normalized_objectives = validate_objectives(objectives)
    with read_connection(database_path) as connection:
        compounds = [
            dict(row)
            for row in connection.execute(
                "SELECT id, registration_id FROM compounds WHERE project_id = ?",
                (project_id,),
            )
        ]
        summary_records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ms.id, ms.compound_id, ms.compatibility_key, ms.canonical_unit,
                       ms.summary_state, ms.summary_value, ms.summary_qualifier, ms.created_at
                FROM measurement_summaries AS ms
                JOIN compounds AS c ON c.id = ms.compound_id
                WHERE c.project_id = ?
                ORDER BY ms.created_at, ms.id
                """,
                (project_id,),
            )
        ]
        property_records: list[dict[str, Any]] = []
        property_keys = [objective["key"] for objective in normalized_objectives if objective["source"] == "property"]
        if property_keys:
            rows = connection.execute(
                """
                SELECT pp.id, pp.compound_id, pp.status, pp.reason, pp.descriptors_json, pp.created_at
                FROM property_profiles AS pp
                JOIN analysis_runs AS ar ON ar.id = pp.analysis_run_id
                JOIN compounds AS c ON c.id = pp.compound_id
                WHERE ar.project_id = ? AND ar.analysis_type = 'properties'
                ORDER BY pp.created_at, pp.id
                """,
                (project_id,),
            ).fetchall()
            for row in rows:
                item = dict(row)
                try:
                    descriptor_map = json.loads(item.get("descriptors_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    descriptor_map = {}
                for key in property_keys:
                    property_records.append({**item, "property_key": key, "descriptors_json": json.dumps({key: descriptor_map.get(key, {})})})

    observations = calculate_pareto(compounds, summary_records, property_records, normalized_objectives)
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'pareto', ?, ?, 'completed', ?)
            """,
            (
                run_id,
                project_id,
                json.dumps(
                    {
                        "objectives": normalized_objectives,
                        "selection_policy": "latest_created_at_then_id_per_compound_and_objective",
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
                INSERT INTO pareto_observations
                    (id, analysis_run_id, compound_id, objective_values_json,
                     objective_status, is_pareto, domination_count, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("pareto"),
                    run_id,
                    observation["compound_id"],
                    json.dumps(observation["objective_values"], sort_keys=True),
                    observation["objective_status"],
                    1 if observation["is_pareto"] else 0,
                    observation["domination_count"],
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
                json.dumps({"type": "pareto", "observation_count": len(observations), "analysis_version": ANALYSIS_VERSION}, sort_keys=True),
                now,
            ),
        )
    counts = {
        status: sum(1 for item in observations if item["status"] == status)
        for status in ("pareto", "complete_non_pareto", "incomplete")
    }
    return {
        "analysis_run_id": run_id,
        "analysis_type": "pareto",
        "algorithm_version": ANALYSIS_VERSION,
        "objectives": normalized_objectives,
        "counts": counts,
        "observations": observations,
        "data_origin": "derived",
    }


def get_pareto_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'pareto'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT po.*, c.registration_id
                FROM pareto_observations AS po
                JOIN compounds AS c ON c.id = po.compound_id
                WHERE po.analysis_run_id = ?
                ORDER BY c.registration_id
                """,
                (run_id,),
            )
        ]
    observations: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["objective_values"] = json.loads(item.pop("objective_values_json") or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ParetoAnalysisError("Invalid JSON in objective_values_json") from exc
        item["is_pareto"] = bool(item["is_pareto"])
        item["status"] = "pareto" if item["is_pareto"] else ("complete_non_pareto" if item["objective_status"] == "complete" else "incomplete")
        observations.append(item)
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["objectives"] = result["input_selection"]["objectives"]
    result["observations"] = observations
    result["counts"] = {
        status: sum(1 for item in observations if item["status"] == status)
        for status in ("pareto", "complete_non_pareto", "incomplete")
    }
    result["data_origin"] = "derived"
    return result
