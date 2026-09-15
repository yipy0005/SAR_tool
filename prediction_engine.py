from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any

from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem

from database import read_connection, transaction


ALGORITHM_VERSION = "rdkit-morgan-similarity-knn-v1"
FEATURE_PROFILE = "morgan-radius2-2048-v1"
DEFAULT_NEIGHBORS = 5
DEFAULT_MIN_SIMILARITY = 0.35
DEFAULT_MIN_NEIGHBORS = 2
DEFAULT_MIN_TRAINING_COMPOUNDS = 5
DEFAULT_MIN_VALIDATION_COMPOUNDS = 8
DEFAULT_MIN_COVERAGE = 0.8
DEFAULT_MAX_MAE = 1.0
QUALIFIED_STATUSES = {"internally_validated"}
OBSERVED_STATES = {"observed"}
OBSERVED_QUALIFIERS = {"=", "~"}


class PredictionError(ValueError):
    """Base error for prediction training and inference."""


class PredictionNotFoundError(PredictionError):
    """Raised when a model is not available in the requested project."""


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _finite(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PredictionError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise PredictionError(f"{field} must be finite")
    return result


def _positive_int(value: Any, field: str, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PredictionError(f"{field} must be an integer") from exc
    if result < 1 or result > maximum:
        raise PredictionError(f"{field} must be between 1 and {maximum}")
    return result


def _smiles_fingerprint(smiles: str) -> Any:
    molecule = Chem.MolFromSmiles(str(smiles or ""))
    if molecule is None:
        raise PredictionError("A training or prediction structure could not be parsed")
    try:
        Chem.SanitizeMol(molecule)
    except Exception as exc:
        raise PredictionError("A training or prediction structure could not be sanitized") from exc
    return AllChem.GetMorganFingerprintAsBitVect(molecule, radius=2, nBits=2048)


def _latest_observed_records(database_path: str, project_id: str, compatibility_key: str) -> list[dict[str, Any]]:
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT ms.id AS summary_id, ms.compound_id, c.registration_id,
                   ms.compatibility_key, ms.canonical_unit, ms.summary_state,
                   ms.summary_value, ms.summary_qualifier, s.isomeric_smiles
            FROM measurement_summaries ms
            JOIN compounds c ON c.id = ms.compound_id
            JOIN structure_records s ON s.id = (
                SELECT latest.id
                FROM structure_records latest
                WHERE latest.compound_id = c.id
                ORDER BY latest.created_at DESC, latest.id DESC
                LIMIT 1
            )
            WHERE c.project_id = ?
              AND ms.compatibility_key = ?
              AND ms.id = (
                  SELECT latest.id
                  FROM measurement_summaries latest
                  WHERE latest.compound_id = ms.compound_id
                    AND latest.compatibility_key = ms.compatibility_key
                  ORDER BY latest.created_at DESC, latest.id DESC
                  LIMIT 1
              )
              AND ms.summary_state IN ('observed')
              AND ms.summary_qualifier IN ('=', '~')
              AND ms.summary_value IS NOT NULL
            ORDER BY c.registration_id, c.id
            """,
            (project_id, compatibility_key),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["value"] = _finite(item.pop("summary_value"), "summary_value")
        item["fingerprint"] = _smiles_fingerprint(item["isomeric_smiles"])
        result.append(item)
    return result


def _predict_record(record: Mapping[str, Any], training: Iterable[Mapping[str, Any]], policy: Mapping[str, Any]) -> dict[str, Any]:
    neighbors = []
    query_fp = record["fingerprint"]
    for candidate in training:
        if candidate["compound_id"] == record["compound_id"]:
            continue
        similarity = float(DataStructs.TanimotoSimilarity(query_fp, candidate["fingerprint"]))
        if similarity >= float(policy["min_similarity"]):
            neighbors.append((similarity, candidate))
    neighbors.sort(key=lambda item: (-item[0], str(item[1]["compound_id"])))
    selected = neighbors[: int(policy["neighbors"])]
    if not selected:
        return {
            "prediction_value": None,
            "applicability_status": "out_of_domain",
            "uncertainty": {
                "reason": "no_neighbor_above_similarity_threshold",
                "max_similarity": 0.0,
                "neighbor_count": 0,
                "effective_support": 0.0,
            },
            "nearest_compound_ids": [],
        }
    weights = [max(similarity, 1e-9) ** 2 for similarity, _candidate in selected]
    total_weight = sum(weights)
    prediction = sum(weight * float(candidate["value"]) for weight, (_similarity, candidate) in zip(weights, selected)) / total_weight
    residual_variance = sum(weight * (float(candidate["value"]) - prediction) ** 2 for weight, (_similarity, candidate) in zip(weights, selected)) / total_weight
    max_similarity = selected[0][0]
    neighbor_count = len(selected)
    status = "eligible"
    if neighbor_count < int(policy["min_neighbors"]) or max_similarity < float(policy["min_similarity"]) + 0.10:
        status = "borderline"
    return {
        "prediction_value": prediction,
        "applicability_status": status,
        "uncertainty": {
            "method": "similarity_weighted_neighbor_dispersion",
            "standard_deviation": math.sqrt(max(residual_variance, 0.0)),
            "max_similarity": max_similarity,
            "neighbor_count": neighbor_count,
            "effective_support": total_weight,
        },
        "nearest_compound_ids": [candidate["compound_id"] for _similarity, candidate in selected],
    }


def _metrics(results: list[dict[str, Any]], minimum_validation_compounds: int, minimum_coverage: float, maximum_mae: float) -> dict[str, Any]:
    total = len(results)
    predicted = [item for item in results if item["prediction_value"] is not None]
    eligible = [item for item in predicted if item["applicability_status"] == "eligible"]
    errors = [float(item["prediction_value"]) - float(item["observed_value"]) for item in eligible]
    absolute_errors = [abs(error) for error in errors]
    coverage = len(eligible) / total if total else 0.0
    mae = mean(absolute_errors) if absolute_errors else None
    rmse = math.sqrt(mean([error * error for error in errors])) if errors else None
    observed = [float(item["observed_value"]) for item in eligible]
    predicted_values = [float(item["prediction_value"]) for item in eligible]
    observed_mean = mean(observed) if observed else None
    ss_total = sum((value - observed_mean) ** 2 for value in observed) if observed_mean is not None else 0.0
    ss_residual = sum((actual - estimate) ** 2 for actual, estimate in zip(observed, predicted_values))
    r2 = 1.0 - ss_residual / ss_total if ss_total > 0 else None
    max_similarities = [float(item["uncertainty"].get("max_similarity", 0.0)) for item in eligible]
    passed = (
        total >= minimum_validation_compounds
        and len(eligible) >= minimum_validation_compounds
        and coverage >= minimum_coverage
        and mae is not None
        and mae <= maximum_mae
    )
    return {
        "validation_records": total,
        "eligible_predictions": len(eligible),
        "covered_predictions": len(predicted),
        "coverage": coverage,
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "median_max_similarity": median(max_similarities) if max_similarities else None,
        "minimum_validation_compounds": minimum_validation_compounds,
        "minimum_coverage": minimum_coverage,
        "maximum_mae": maximum_mae,
        "passed": passed,
    }


def _public_model(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    for field in ("input_selection_json", "training_records_json", "hyperparameters_json", "validation_metrics_json", "applicability_policy_json"):
        key = field.removesuffix("_json")
        result[key] = json.loads(result.pop(field) or ("[]" if field == "training_records_json" else "{}"))
    result["data_origin"] = "derived"
    return result


def get_prediction_model(database_path: str, project_id: str, model_id: str, *, include_observations: bool = True) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM prediction_models WHERE id = ? AND project_id = ?",
            (model_id, project_id),
        ).fetchone()
        if row is None:
            return None
        result = _public_model(dict(row))
        if include_observations:
            observations = []
            for observation in connection.execute(
                """
                SELECT po.*, c.registration_id
                FROM prediction_observations po
                JOIN compounds c ON c.id = po.compound_id
                WHERE po.prediction_model_id = ?
                ORDER BY c.registration_id
                """,
                (model_id,),
            ):
                item = dict(observation)
                item["uncertainty"] = json.loads(item.pop("uncertainty_json") or "{}")
                item["nearest_compound_ids"] = json.loads(item.pop("nearest_compound_ids_json") or "[]")
                observations.append(item)
            result["observations"] = observations
        return result


def list_prediction_models(database_path: str, project_id: str) -> list[dict[str, Any]]:
    with read_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM prediction_models WHERE project_id = ? ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
    return [_public_model(dict(row)) for row in rows]


def train_prediction_model(
    database_path: str,
    project_id: str,
    compatibility_key: Any,
    *,
    holdout_compound_ids: Iterable[str] | None = None,
    neighbors: Any = DEFAULT_NEIGHBORS,
    min_similarity: Any = DEFAULT_MIN_SIMILARITY,
    min_neighbors: Any = DEFAULT_MIN_NEIGHBORS,
    min_training_compounds: Any = DEFAULT_MIN_TRAINING_COMPOUNDS,
    min_validation_compounds: Any = DEFAULT_MIN_VALIDATION_COMPOUNDS,
    min_coverage: Any = DEFAULT_MIN_COVERAGE,
    max_mae: Any = DEFAULT_MAX_MAE,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    key = str(compatibility_key or "").strip()
    if not key:
        raise PredictionError("compatibility_key is required")
    policy = {
        "neighbors": _positive_int(neighbors, "neighbors", DEFAULT_NEIGHBORS, 50),
        "min_similarity": _finite(min_similarity if min_similarity not in (None, "") else DEFAULT_MIN_SIMILARITY, "min_similarity"),
        "min_neighbors": _positive_int(min_neighbors, "min_neighbors", DEFAULT_MIN_NEIGHBORS, 50),
    }
    if not 0.0 <= policy["min_similarity"] <= 1.0:
        raise PredictionError("min_similarity must be between 0 and 1")
    training_min = _positive_int(min_training_compounds, "min_training_compounds", DEFAULT_MIN_TRAINING_COMPOUNDS, 10_000)
    validation_min = _positive_int(min_validation_compounds, "min_validation_compounds", DEFAULT_MIN_VALIDATION_COMPOUNDS, 10_000)
    coverage_min = _finite(min_coverage if min_coverage not in (None, "") else DEFAULT_MIN_COVERAGE, "min_coverage")
    mae_max = _finite(max_mae if max_mae not in (None, "") else DEFAULT_MAX_MAE, "max_mae")
    if not 0.0 < coverage_min <= 1.0 or mae_max < 0.0:
        raise PredictionError("min_coverage must be in (0, 1] and max_mae must be non-negative")
    records = _latest_observed_records(database_path, project_id, key)
    if len(records) < training_min:
        raise PredictionError(f"At least {training_min} exact observed compounds are required; found {len(records)}")
    holdout = list(dict.fromkeys(str(item).strip() for item in (holdout_compound_ids or []) if str(item).strip()))
    available_ids = {record["compound_id"] for record in records}
    if holdout and not set(holdout) <= available_ids:
        missing = sorted(set(holdout) - available_ids)
        raise PredictionError(f"Holdout compounds are not eligible for this project/endpoint: {', '.join(missing)}")
    training_records = [record for record in records if record["compound_id"] not in set(holdout)]
    validation_records = [record for record in records if not holdout or record["compound_id"] in set(holdout)]
    if not holdout:
        validation_records = records
    if len(training_records) < training_min:
        raise PredictionError(f"At least {training_min} training compounds are required after holdout selection")
    validation_results = []
    for record in validation_records:
        validation_training = training_records if holdout else [candidate for candidate in records if candidate["compound_id"] != record["compound_id"]]
        prediction = _predict_record(record, validation_training, policy)
        validation_results.append({
            **prediction,
            "compound_id": record["compound_id"],
            "summary_id": record["summary_id"],
            "observed_value": record["value"],
            "observed_unit": record["canonical_unit"],
        })
    metrics = _metrics(validation_results, validation_min, coverage_min, mae_max)
    status = "internally_validated" if metrics["passed"] else "candidate"
    now = _now()
    model_id = _id("prediction")
    training_payload = [
        {
            "compound_id": record["compound_id"],
            "registration_id": record["registration_id"],
            "summary_id": record["summary_id"],
            "value": record["value"],
            "unit": record["canonical_unit"],
            "isomeric_smiles": record["isomeric_smiles"],
        }
        for record in training_records
    ]
    validation_scope = "project_holdout" if holdout else "internal_leave_one_out"
    with transaction(database_path) as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise PredictionError("The target project does not exist")
        connection.execute(
            """
            INSERT INTO prediction_models
                (id, project_id, compatibility_key, prediction_unit, feature_profile,
                 algorithm_version, model_status, validation_scope, input_selection_json,
                 training_records_json, hyperparameters_json, validation_metrics_json,
                 applicability_policy_json, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id, project_id, key, records[0]["canonical_unit"], FEATURE_PROFILE,
                ALGORITHM_VERSION, status, validation_scope,
                json.dumps({"compatibility_key": key, "validation_compound_ids": [item["compound_id"] for item in validation_records], "holdout_compound_ids": holdout}, sort_keys=True),
                json.dumps(training_payload, sort_keys=True),
                json.dumps({"neighbors": policy["neighbors"]}, sort_keys=True),
                json.dumps(metrics, sort_keys=True),
                json.dumps({**policy, "min_training_compounds": training_min}, sort_keys=True),
                actor_user_id, now,
            ),
        )
        for item in validation_results:
            connection.execute(
                """
                INSERT INTO prediction_observations
                    (id, prediction_model_id, compound_id, target_summary_id,
                     prediction_value, prediction_unit, observed_value, observed_unit,
                     error_value, uncertainty_json, applicability_status,
                     nearest_compound_ids_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("predobs"), model_id, item["compound_id"], item["summary_id"],
                    item["prediction_value"], records[0]["canonical_unit"], item["observed_value"], item["observed_unit"],
                    None if item["prediction_value"] is None else item["prediction_value"] - item["observed_value"],
                    json.dumps(item["uncertainty"], sort_keys=True), item["applicability_status"],
                    json.dumps(item["nearest_compound_ids"]), now,
                ),
            )
    result = get_prediction_model(database_path, project_id, model_id)
    if result is None:
        raise PredictionError("Prediction model could not be reloaded")
    return result


def predict_compounds(
    database_path: str,
    project_id: str,
    model_id: str,
    compound_ids: Iterable[str],
) -> dict[str, Any]:
    model = get_prediction_model(database_path, project_id, model_id, include_observations=False)
    if model is None:
        raise PredictionNotFoundError("Prediction model is not available in this project")
    if model["model_status"] not in QUALIFIED_STATUSES:
        raise PredictionError("This model has not passed its declared internal validation gate")
    selected = list(dict.fromkeys(str(item).strip() for item in compound_ids if str(item).strip()))
    if not selected:
        raise PredictionError("compound_ids must contain at least one compound")
    training_payload = model["training_records"]
    training = []
    for record in training_payload:
        training.append({**record, "fingerprint": _smiles_fingerprint(record["isomeric_smiles"])})
    policy = model["applicability_policy"]
    placeholders = ",".join("?" for _ in selected)
    with read_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT c.id AS compound_id, c.registration_id, s.isomeric_smiles
            FROM compounds c
            JOIN structure_records s ON s.id = (
                SELECT latest.id FROM structure_records latest
                WHERE latest.compound_id = c.id
                ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
            )
            WHERE c.project_id = ? AND c.id IN ({placeholders})
            """,
            (project_id, *selected),
        ).fetchall()
    if len(rows) != len(selected):
        raise PredictionError("Every compound ID must belong to the target project and have a structure")
    results = []
    now = _now()
    with transaction(database_path) as connection:
        for row in rows:
            record = {"compound_id": row["compound_id"], "isomeric_smiles": row["isomeric_smiles"], "fingerprint": _smiles_fingerprint(row["isomeric_smiles"])}
            prediction = _predict_record(record, training, policy)
            results.append({"compound_id": row["compound_id"], "registration_id": row["registration_id"], **{key: value for key, value in prediction.items() if key != "nearest_compound_ids"}})
            connection.execute(
                """
                INSERT INTO prediction_observations
                    (id, prediction_model_id, compound_id, prediction_value, prediction_unit,
                     uncertainty_json, applicability_status, nearest_compound_ids_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_model_id, compound_id) DO UPDATE SET
                    prediction_value = excluded.prediction_value,
                    prediction_unit = excluded.prediction_unit,
                    uncertainty_json = excluded.uncertainty_json,
                    applicability_status = excluded.applicability_status,
                    nearest_compound_ids_json = excluded.nearest_compound_ids_json,
                    created_at = excluded.created_at
                """,
                (
                    _id("predobs"), model_id, row["compound_id"], prediction["prediction_value"], model["prediction_unit"],
                    json.dumps(prediction["uncertainty"], sort_keys=True), prediction["applicability_status"],
                    json.dumps(prediction["nearest_compound_ids"]), now,
                ),
            )
    return {"model": model, "predictions": results, "data_origin": "derived"}
