"""Deterministic heuristic information-gap scoring.

This module ranks evidence gaps. It does not estimate biological efficacy,
probability of success, or model-based expected information gain.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from database import read_connection, transaction


ANALYSIS_VERSION = "information-gap-heuristic-v1"
EVIDENCE_CLASS = "heuristic_evidence_gap"
MAX_CONTEXTS = 32
DEFAULT_MIN_REPLICATES = 2
WEIGHTS = {
    "missingness_gap": 0.35,
    "censoring_gap": 0.20,
    "contradiction_burden": 0.20,
    "replicate_uncertainty": 0.15,
    "context_priority": 0.10,
}


class InformationGainAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _bounded_number(value: Any, field: str, default: float) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InformationGainAnalysisError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise InformationGainAnalysisError(f"{field} must be finite and between 0 and 1")
    return number


def validate_contexts(contexts: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(contexts, Sequence) or isinstance(contexts, (str, bytes)) or not contexts:
        raise InformationGainAnalysisError("At least one target context is required")
    if len(contexts) > MAX_CONTEXTS:
        raise InformationGainAnalysisError(f"At most {MAX_CONTEXTS} target contexts may be requested")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in contexts:
        if isinstance(item, str):
            key = item.strip()
            priority = 0.5
            minimum_replicates = DEFAULT_MIN_REPLICATES
        elif isinstance(item, Mapping):
            key = str(item.get("compatibility_key", item.get("target_key", ""))).strip()
            priority = _bounded_number(item.get("priority", 0.5), "context priority", 0.5)
            try:
                minimum_replicates = int(item.get("minimum_replicates", DEFAULT_MIN_REPLICATES))
            except (TypeError, ValueError) as exc:
                raise InformationGainAnalysisError("minimum_replicates must be an integer") from exc
        else:
            raise InformationGainAnalysisError("Each target context must be a string or object")
        if not key:
            raise InformationGainAnalysisError("Each target context needs a compatibility_key")
        if key in seen:
            raise InformationGainAnalysisError(f"Duplicate target context: {key}")
        if not 1 <= minimum_replicates <= 16:
            raise InformationGainAnalysisError("minimum_replicates must be between 1 and 16")
        seen.add(key)
        result.append({
            "compatibility_key": key,
            "priority": priority,
            "minimum_replicates": minimum_replicates,
        })
    return tuple(result)


def _summary_is_missing(summary: Mapping[str, Any] | None) -> bool:
    if summary is None:
        return True
    state = str(summary.get("summary_state", ""))
    try:
        missing_count = int(summary.get("missing_measurement_count") or 0)
    except (TypeError, ValueError):
        missing_count = 0
    return missing_count > 0 or "missing" in state or summary.get("summary_value") is None and state == "missing"


def _summary_is_censored(summary: Mapping[str, Any] | None) -> bool:
    if summary is None:
        return False
    state = str(summary.get("summary_state", ""))
    try:
        censored_count = int(summary.get("censored_measurement_count") or 0)
    except (TypeError, ValueError):
        censored_count = 0
    return censored_count > 0 or "censored" in state or str(summary.get("summary_qualifier", "")) in {"<", "<=", ">", ">="}


def _latest_summaries(summaries: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in summaries:
        item = dict(raw)
        compound_id = str(item.get("compound_id", "")).strip()
        key = str(item.get("compatibility_key", "")).strip()
        if not compound_id or not key:
            continue
        identity = (compound_id, key)
        current = selected.get(identity)
        candidate_order = (str(item.get("created_at", "")), str(item.get("id", "")))
        current_order = (str(current.get("created_at", "")), str(current.get("id", ""))) if current else ("", "")
        if current is None or candidate_order >= current_order:
            selected[identity] = item
    return selected


def calculate_information_gain(
    compounds: Iterable[Mapping[str, Any]],
    summaries: Iterable[Mapping[str, Any]],
    contradictions: Iterable[Mapping[str, Any]],
    contexts: Any,
) -> list[dict[str, Any]]:
    target_contexts = validate_contexts(contexts)
    latest = _latest_summaries(summaries)
    contradiction_ids: dict[tuple[str, str], list[str]] = {}
    for raw in contradictions:
        item = dict(raw)
        if str(item.get("status", "unreconciled")) != "unreconciled":
            continue
        identity = (str(item.get("compound_id", "")), str(item.get("compatibility_key", "")))
        contradiction_ids.setdefault(identity, []).append(str(item.get("id", "")))

    observations: list[dict[str, Any]] = []
    for compound in compounds:
        compound_id = str(compound.get("id", compound.get("compound_id", ""))).strip()
        registration_id = str(compound.get("registration_id", compound_id)).strip()
        if not compound_id:
            continue
        for context in target_contexts:
            key = context["compatibility_key"]
            summary = latest.get((compound_id, key))
            missingness_raw = 1.0 if _summary_is_missing(summary) else 0.0
            censoring_raw = 1.0 if _summary_is_censored(summary) else 0.0
            contradiction_list = sorted(set(contradiction_ids.get((compound_id, key), [])))
            contradiction_raw = min(len(contradiction_list), 1)
            try:
                eligible_count = int((summary or {}).get("eligible_measurement_count") or 0)
            except (TypeError, ValueError):
                eligible_count = 0
            minimum_replicates = int(context["minimum_replicates"])
            replicate_raw = min(max(minimum_replicates - eligible_count, 0) / minimum_replicates, 1.0)
            if summary is None or summary.get("summary_value") is None:
                dispersion_raw = 1.0
            elif summary.get("dispersion") is None:
                dispersion_raw = 0.5 if eligible_count >= 2 else 1.0
            else:
                try:
                    value = abs(float(summary.get("summary_value")))
                    dispersion = abs(float(summary.get("dispersion")))
                    dispersion_raw = min(dispersion / max(value, 1e-9), 1.0)
                except (TypeError, ValueError):
                    dispersion_raw = 1.0
            replicate_uncertainty_raw = max(replicate_raw, dispersion_raw)
            raw_components = {
                "missingness_gap": missingness_raw,
                "censoring_gap": censoring_raw,
                "contradiction_burden": contradiction_raw,
                "replicate_uncertainty": replicate_uncertainty_raw,
                "context_priority": float(context["priority"]),
            }
            weighted_components = {
                name: round(raw_components[name] * WEIGHTS[name], 6)
                for name in WEIGHTS
            }
            score = round(min(sum(weighted_components.values()), 1.0), 6)
            if missingness_raw:
                gap_type = "missing_context"
            elif contradiction_raw:
                gap_type = "contradictory_context"
            elif censoring_raw:
                gap_type = "censored_context"
            else:
                gap_type = "replicate_uncertainty"
            evidence_ids: list[str] = []
            if summary and summary.get("id"):
                evidence_ids.append(str(summary["id"]))
            evidence_ids.extend(contradiction_list)
            observations.append({
                "compound_id": compound_id,
                "registration_id": registration_id,
                "gap_type": gap_type,
                "target_key": key,
                "priority_score": score,
                "score_components": {
                    "raw": raw_components,
                    "weighted": weighted_components,
                    "weights": WEIGHTS,
                },
                "uncertainty": {
                    "classification": "heuristic_not_predictive",
                    "confidence": "assumption_bound",
                    "limitations": [
                        "Ranks evidence gaps rather than predicting assay outcomes.",
                        "Weights and replicate thresholds are declared heuristics.",
                        "No model-based expected information gain is claimed.",
                    ],
                    "assumptions": {
                        "minimum_replicates": minimum_replicates,
                        "target_context_priority": context["priority"],
                    },
                },
                "evidence_ids": sorted(set(evidence_ids)),
                "source_summary_id": summary.get("id") if summary else None,
                "status": "open",
                "evidence_class": EVIDENCE_CLASS,
            })
    observations.sort(key=lambda item: (-item["priority_score"], item["registration_id"], item["target_key"]))
    return observations


def _project_inputs(connection: Any, project_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    compounds = [dict(row) for row in connection.execute(
        "SELECT id, registration_id FROM compounds WHERE project_id = ? ORDER BY registration_id, id",
        (project_id,),
    )]
    summaries = [dict(row) for row in connection.execute(
        """
        SELECT ms.*
        FROM measurement_summaries AS ms
        JOIN compounds AS c ON c.id = ms.compound_id
        WHERE c.project_id = ?
        """,
        (project_id,),
    )]
    contradictions = [dict(row) for row in connection.execute(
        """
        SELECT co.*
        FROM contradiction_observations AS co
        JOIN analysis_runs AS ar ON ar.id = co.analysis_run_id
        WHERE ar.project_id = ?
        """,
        (project_id,),
    )]
    return compounds, summaries, contradictions


def run_information_gain_analysis(
    database_path: str,
    project_id: str,
    contexts: Any,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    target_contexts = validate_contexts(contexts)
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        if connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise InformationGainAnalysisError("The target project does not exist")
        compounds, summaries, contradictions = _project_inputs(connection, project_id)
        observations = calculate_information_gain(compounds, summaries, contradictions, target_contexts)
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'information_gain', ?, ?, 'completed', ?)
            """,
            (run_id, project_id, json.dumps({"contexts": list(target_contexts), "weights": WEIGHTS}, sort_keys=True), ANALYSIS_VERSION, now),
        )
        for observation in observations:
            connection.execute(
                """
                INSERT INTO information_gain_observations
                    (id, analysis_run_id, compound_id, gap_type, target_key, priority_score,
                     evidence_class, score_components_json, uncertainty_json, evidence_ids_json,
                     status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("ig"), run_id, observation["compound_id"], observation["gap_type"], observation["target_key"],
                    observation["priority_score"], EVIDENCE_CLASS, json.dumps(observation["score_components"], sort_keys=True),
                    json.dumps(observation["uncertainty"], sort_keys=True), json.dumps(observation["evidence_ids"]),
                    observation["status"], now,
                ),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'information_gain_analysis', 'analysis_run', ?, 'success', ?, ?)
            """,
            (_id("audit"), actor_user_id, project_id, run_id, json.dumps({"observation_count": len(observations), "algorithm_version": ANALYSIS_VERSION}, sort_keys=True), now),
        )
    return get_information_gain_run(database_path, run_id) or {}


def get_information_gain_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'information_gain'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        rows = connection.execute(
            """
            SELECT igo.*, c.registration_id
            FROM information_gain_observations AS igo
            JOIN compounds AS c ON c.id = igo.compound_id
            WHERE igo.analysis_run_id = ?
            ORDER BY igo.priority_score DESC, c.registration_id, igo.target_key
            """,
            (run_id,),
        ).fetchall()
    observations = []
    for row in rows:
        item = dict(row)
        item["score_components"] = json.loads(item.pop("score_components_json"))
        item["uncertainty"] = json.loads(item.pop("uncertainty_json"))
        item["evidence_ids"] = json.loads(item.pop("evidence_ids_json"))
        item["data_origin"] = "derived"
        observations.append(item)
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["observations"] = observations
    result["observation_count"] = len(observations)
    result["evidence_class"] = EVIDENCE_CLASS
    result["data_origin"] = "derived"
    return result
