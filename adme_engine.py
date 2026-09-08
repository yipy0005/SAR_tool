import json
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from database import read_connection, transaction


ANALYSIS_VERSION = "adme-evidence-panel-v1"
OBSERVED_QUALIFIERS = frozenset({"=", "~"})


class ADMEAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _record(record: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    return {key: record[key] for key in record.keys()}


def _validate_contexts(compatibility_keys: Sequence[str] | Iterable[str]) -> list[str]:
    keys = list(dict.fromkeys(str(key).strip() for key in compatibility_keys if str(key).strip()))
    if not keys:
        raise ADMEAnalysisError("At least one ADME compatibility key is required")
    if len(keys) > 32:
        raise ADMEAnalysisError("At most 32 ADME compatibility keys may be requested")
    return keys


def _latest_by_compound_context(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in records:
        record = _record(raw)
        compound_id = str(record.get("compound_id", "")).strip()
        context = str(record.get("compatibility_key", "")).strip()
        if not compound_id or not context:
            continue
        key = (compound_id, context)
        current = selected.get(key)
        candidate_key = (str(record.get("created_at", "")), str(record.get("id", "")))
        current_key = (str(current.get("created_at", "")), str(current.get("id", ""))) if current else ("", "")
        if current is None or candidate_key > current_key:
            selected[key] = record
    return selected


def _context_observation(record: Mapping[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {
            "summary_id": None,
            "value": None,
            "unit": None,
            "summary_state": None,
            "summary_qualifier": None,
            "status": "missing",
            "reason": "summary_missing",
        }
    state = str(record.get("summary_state", ""))
    qualifier = str(record.get("summary_qualifier", ""))
    value = record.get("summary_value")
    if state == "observed" and qualifier in OBSERVED_QUALIFIERS and value is not None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = math.nan
        if math.isfinite(numeric):
            return {
                "summary_id": record.get("id"),
                "value": numeric,
                "unit": record.get("canonical_unit"),
                "summary_state": state,
                "summary_qualifier": qualifier,
                "status": "observed",
                "reason": None,
            }
        return {
            "summary_id": record.get("id"),
            "value": None,
            "unit": record.get("canonical_unit"),
            "summary_state": state,
            "summary_qualifier": qualifier,
            "status": "missing",
            "reason": "summary_value_invalid",
        }
    if state == "missing" or value is None:
        reason = "summary_missing"
        try:
            reasons = json.loads(record.get("missing_reasons_json") or "[]")
            if reasons:
                reason = str(reasons[0])
        except (TypeError, json.JSONDecodeError):
            pass
        return {
            "summary_id": record.get("id"),
            "value": None,
            "unit": record.get("canonical_unit"),
            "summary_state": state or None,
            "summary_qualifier": qualifier or None,
            "status": "missing",
            "reason": reason,
        }
    return {
        "summary_id": record.get("id"),
        "value": None,
        "unit": record.get("canonical_unit"),
        "summary_state": state or None,
        "summary_qualifier": qualifier or None,
        "status": "censored",
        "reason": "summary_not_exact_observed",
    }


def calculate_adme_panel(
    records: Iterable[Mapping[str, Any]],
    compounds: Iterable[Mapping[str, Any]],
    compatibility_keys: Sequence[str] | Iterable[str],
) -> list[dict[str, Any]]:
    contexts = _validate_contexts(compatibility_keys)
    selected = _latest_by_compound_context(records)
    compound_records = [_record(compound) for compound in compounds]
    compound_records.sort(key=lambda item: (str(item.get("registration_id", "")), str(item.get("id", ""))))
    results: list[dict[str, Any]] = []
    for compound in compound_records:
        compound_id = str(compound.get("id", "")).strip()
        context_values = {
            context: _context_observation(selected.get((compound_id, context)))
            for context in contexts
        }
        observed = [context for context, item in context_values.items() if item["status"] == "observed"]
        censored = [context for context, item in context_values.items() if item["status"] == "censored"]
        missing = [context for context, item in context_values.items() if item["status"] == "missing"]
        if not censored and not missing:
            status = "complete_observed"
            reason = None
        elif censored and missing:
            status = "incomplete_mixed"
            reason = "censored_and_missing_contexts"
        elif censored:
            status = "incomplete_censored"
            reason = "censored_contexts"
        else:
            status = "incomplete_missing"
            reason = "missing_contexts"
        results.append(
            {
                "compound_id": compound_id,
                "registration_id": compound.get("registration_id"),
                "required_contexts": contexts,
                "context_values": context_values,
                "observed_contexts": observed,
                "censored_contexts": censored,
                "missing_contexts": missing,
                "status": status,
                "reason": reason,
            }
        )
    return results


def run_adme_analysis(
    database_path: str,
    project_id: str,
    compatibility_keys: Sequence[str] | Iterable[str],
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    contexts = _validate_contexts(compatibility_keys)
    with read_connection(database_path) as connection:
        compounds = [
            dict(row)
            for row in connection.execute(
                "SELECT id, registration_id FROM compounds WHERE project_id = ?",
                (project_id,),
            )
        ]
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ms.id, ms.compound_id, ms.compatibility_key, ms.canonical_unit,
                       ms.summary_state, ms.summary_value, ms.summary_qualifier,
                       ms.missing_reasons_json, ms.created_at
                FROM measurement_summaries AS ms
                JOIN compounds AS c ON c.id = ms.compound_id
                WHERE c.project_id = ?
                ORDER BY ms.created_at, ms.id
                """,
                (project_id,),
            )
        ]
    observations = calculate_adme_panel(records, compounds, contexts)
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'adme', ?, ?, 'completed', ?)
            """,
            (
                run_id,
                project_id,
                json.dumps(
                    {
                        "compatibility_keys": contexts,
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
                INSERT INTO adme_observations
                    (id, analysis_run_id, compound_id, required_contexts_json, context_values_json,
                     observed_contexts_json, censored_contexts_json, missing_contexts_json,
                     status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("adme"),
                    run_id,
                    observation["compound_id"],
                    json.dumps(observation["required_contexts"], sort_keys=True),
                    json.dumps(observation["context_values"], sort_keys=True),
                    json.dumps(observation["observed_contexts"], sort_keys=True),
                    json.dumps(observation["censored_contexts"], sort_keys=True),
                    json.dumps(observation["missing_contexts"], sort_keys=True),
                    observation["status"],
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
                    {"type": "adme", "observation_count": len(observations), "analysis_version": ANALYSIS_VERSION},
                    sort_keys=True,
                ),
                now,
            ),
        )
    counts = {
        status: sum(1 for item in observations if item["status"] == status)
        for status in ("complete_observed", "incomplete_censored", "incomplete_missing", "incomplete_mixed")
    }
    return {
        "analysis_run_id": run_id,
        "analysis_type": "adme",
        "algorithm_version": ANALYSIS_VERSION,
        "compatibility_keys": contexts,
        "counts": counts,
        "observations": observations,
        "data_origin": "derived",
    }


def _decode(result: dict[str, Any], column: str, output: str) -> None:
    try:
        result[output] = json.loads(result.pop(column) or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ADMEAnalysisError(f"Invalid JSON in {column}") from exc


def get_adme_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'adme'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ao.*, c.registration_id
                FROM adme_observations AS ao
                JOIN compounds AS c ON c.id = ao.compound_id
                WHERE ao.analysis_run_id = ?
                ORDER BY c.registration_id
                """,
                (run_id,),
            )
        ]
    observations: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        _decode(item, "required_contexts_json", "required_contexts")
        _decode(item, "context_values_json", "context_values")
        _decode(item, "observed_contexts_json", "observed_contexts")
        _decode(item, "censored_contexts_json", "censored_contexts")
        _decode(item, "missing_contexts_json", "missing_contexts")
        observations.append(item)
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["observations"] = observations
    result["counts"] = {
        status: sum(1 for item in observations if item["status"] == status)
        for status in ("complete_observed", "incomplete_censored", "incomplete_missing", "incomplete_mixed")
    }
    result["data_origin"] = "derived"
    return result
