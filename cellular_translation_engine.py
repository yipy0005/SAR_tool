import json
import math
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from database import read_connection, transaction


ANALYSIS_VERSION = "cellular-translation-v1"
OBSERVED_QUALIFIERS = frozenset({"=", "~"})
DIRECTIONS = frozenset({"higher_is_better", "lower_is_better"})


class TranslationAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _record(record: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    return {key: record[key] for key in record.keys()}


def _validate_inputs(biochemical_key: str, cellular_key: str, threshold: float) -> float:
    biochemical = str(biochemical_key).strip()
    cellular = str(cellular_key).strip()
    if not biochemical or not cellular:
        raise TranslationAnalysisError("Biochemical and cellular compatibility keys are required")
    if biochemical == cellular:
        raise TranslationAnalysisError("Biochemical and cellular compatibility keys must differ")
    try:
        numeric_threshold = float(threshold)
    except (TypeError, ValueError) as exc:
        raise TranslationAnalysisError("translation_loss_threshold must be numeric") from exc
    if not math.isfinite(numeric_threshold) or numeric_threshold < 0:
        raise TranslationAnalysisError("translation_loss_threshold must be finite and non-negative")
    return numeric_threshold


def _latest_by_compound(records: Iterable[Mapping[str, Any]], compatibility_key: str) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for raw in records:
        record = _record(raw)
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


def calculate_translation(
    records: Iterable[Mapping[str, Any]],
    biochemical_compatibility_key: str,
    cellular_compatibility_key: str,
    *,
    translation_loss_threshold: float = 1.0,
) -> list[dict[str, Any]]:
    threshold = _validate_inputs(
        biochemical_compatibility_key,
        cellular_compatibility_key,
        translation_loss_threshold,
    )
    records_list = [_record(record) for record in records]
    biochemical = _latest_by_compound(records_list, biochemical_compatibility_key)
    cellular = _latest_by_compound(records_list, cellular_compatibility_key)
    compound_ids = set(biochemical) | set(cellular)

    def sort_key(compound_id: str) -> tuple[str, str]:
        record = biochemical.get(compound_id) or cellular.get(compound_id) or {}
        return (str(record.get("registration_id", "")), compound_id)

    results: list[dict[str, Any]] = []
    for compound_id in sorted(compound_ids, key=sort_key):
        biochemical_record = biochemical.get(compound_id)
        cellular_record = cellular.get(compound_id)
        biochemical_value, biochemical_reason = _observed_value(biochemical_record)
        cellular_value, cellular_reason = _observed_value(cellular_record)
        biochemical_unit = biochemical_record.get("canonical_unit") if biochemical_record else None
        cellular_unit = cellular_record.get("canonical_unit") if cellular_record else None
        biochemical_direction = biochemical_record.get("direction") if biochemical_record else None
        cellular_direction = cellular_record.get("direction") if cellular_record else None
        reason = biochemical_reason or cellular_reason
        delta = None
        loss = None
        status = "incomplete"
        evidence_status = "incomplete_summary_evidence"

        if biochemical_value is not None and cellular_value is not None:
            if not biochemical_unit or not cellular_unit:
                reason = "canonical_unit_missing"
            elif biochemical_unit != cellular_unit:
                reason = "canonical_unit_mismatch"
            elif biochemical_direction not in DIRECTIONS or cellular_direction not in DIRECTIONS:
                reason = "assay_direction_missing"
            elif biochemical_direction != cellular_direction:
                reason = "assay_direction_mismatch"
            else:
                delta = cellular_value - biochemical_value
                loss = biochemical_value - cellular_value if biochemical_direction == "higher_is_better" else cellular_value - biochemical_value
                status = "translated" if loss <= threshold else "attenuated"
                evidence_status = "observed_compatible_summaries"
                reason = None

        results.append(
            {
                "compound_id": compound_id,
                "registration_id": (biochemical_record or cellular_record or {}).get("registration_id"),
                "biochemical_summary_id": biochemical_record.get("id") if biochemical_record else None,
                "cellular_summary_id": cellular_record.get("id") if cellular_record else None,
                "biochemical_value": biochemical_value,
                "cellular_value": cellular_value,
                "translation_delta": delta,
                "translation_loss": loss,
                "canonical_unit": biochemical_unit or cellular_unit,
                "direction": biochemical_direction or cellular_direction,
                "status": status,
                "evidence_status": evidence_status,
                "reason": reason,
            }
        )
    return results


def _summary_directions(connection: Any, project_id: str) -> dict[str, str | None]:
    rows = connection.execute(
        "SELECT id, direction FROM assay_definitions WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    return {str(row["id"]): row["direction"] for row in rows}


def _attach_directions(records: list[dict[str, Any]], directions: Mapping[str, str | None]) -> None:
    for record in records:
        try:
            definition_ids = json.loads(record.get("assay_definition_ids_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            definition_ids = []
        values = {directions.get(str(identifier)) for identifier in definition_ids}
        if not definition_ids or None in values or len(values) != 1:
            record["direction"] = None
        else:
            record["direction"] = next(iter(values))


def run_translation_analysis(
    database_path: str,
    project_id: str,
    biochemical_compatibility_key: str,
    cellular_compatibility_key: str,
    *,
    translation_loss_threshold: float = 1.0,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    threshold = _validate_inputs(
        biochemical_compatibility_key,
        cellular_compatibility_key,
        translation_loss_threshold,
    )
    with read_connection(database_path) as connection:
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ms.id, ms.compound_id, c.registration_id, ms.compatibility_key,
                       ms.canonical_unit, ms.summary_state, ms.summary_value,
                       ms.summary_qualifier, ms.assay_definition_ids_json, ms.created_at
                FROM measurement_summaries AS ms
                JOIN compounds AS c ON c.id = ms.compound_id
                WHERE c.project_id = ?
                ORDER BY ms.created_at, ms.id
                """,
                (project_id,),
            )
        ]
        _attach_directions(records, _summary_directions(connection, project_id))

    observations = calculate_translation(
        records,
        biochemical_compatibility_key,
        cellular_compatibility_key,
        translation_loss_threshold=threshold,
    )
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'cellular_translation', ?, ?, 'completed', ?)
            """,
            (
                run_id,
                project_id,
                json.dumps(
                    {
                        "biochemical_compatibility_key": biochemical_compatibility_key,
                        "cellular_compatibility_key": cellular_compatibility_key,
                        "translation_loss_threshold": threshold,
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
                INSERT INTO translation_observations
                    (id, analysis_run_id, compound_id, biochemical_summary_id, cellular_summary_id,
                     biochemical_value, cellular_value, translation_delta, translation_loss,
                     canonical_unit, direction, status, evidence_status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("translation"),
                    run_id,
                    observation["compound_id"],
                    observation["biochemical_summary_id"],
                    observation["cellular_summary_id"],
                    observation["biochemical_value"],
                    observation["cellular_value"],
                    observation["translation_delta"],
                    observation["translation_loss"],
                    observation["canonical_unit"],
                    observation["direction"],
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
                json.dumps(
                    {
                        "type": "cellular_translation",
                        "observation_count": len(observations),
                        "analysis_version": ANALYSIS_VERSION,
                    },
                    sort_keys=True,
                ),
                now,
            ),
        )
    counts = {
        status: sum(1 for item in observations if item["status"] == status)
        for status in ("translated", "attenuated", "incomplete")
    }
    return {
        "analysis_run_id": run_id,
        "analysis_type": "cellular_translation",
        "algorithm_version": ANALYSIS_VERSION,
        "biochemical_compatibility_key": biochemical_compatibility_key,
        "cellular_compatibility_key": cellular_compatibility_key,
        "translation_loss_threshold": threshold,
        "counts": counts,
        "observations": observations,
        "data_origin": "derived",
    }


def get_translation_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'cellular_translation'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        observations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT tobs.*, c.registration_id
                FROM translation_observations AS tobs
                JOIN compounds AS c ON c.id = tobs.compound_id
                WHERE tobs.analysis_run_id = ?
                ORDER BY c.registration_id
                """,
                (run_id,),
            )
        ]
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["observations"] = observations
    result["counts"] = {
        status: sum(1 for item in observations if item["status"] == status)
        for status in ("translated", "attenuated", "incomplete")
    }
    result["data_origin"] = "derived"
    return result
