from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from statistics import mean, median, stdev
from typing import Any

from database import read_connection, transaction


DEFAULT_ANALYSIS_VERSION = "measurement-summary-v1"
OBSERVED_QUALIFIERS = frozenset({"=", "~"})
CENSORED_QUALIFIERS = frozenset({"<", "<=", ">", ">="})
VALID_QUALIFIERS = OBSERVED_QUALIFIERS | CENSORED_QUALIFIERS
DISPERSION_METHOD = "sample_standard_deviation"


class MeasurementEngineError(ValueError):
    """Base error for invalid or non-aggregable measurement data."""


class IncompatibleAssayContextError(MeasurementEngineError):
    """Raised when measurements do not share an assay compatibility context."""


# A descriptive alias makes the boundary easy to discover for callers that use
# the domain term rather than the implementation term.
AssayCompatibilityError = IncompatibleAssayContextError


class MeasurementSummaryError(MeasurementEngineError):
    """Backward-compatible domain-specific name for summary failures."""


@dataclass(frozen=True)
class MeasurementSummary:
    """Immutable, provenance-preserving result for one compatible measurement set."""

    compound_id: str | None
    compatibility_key: str
    canonical_unit: str | None
    summary_value: float | None
    summary_qualifier: str | None
    lower_bound: float | None
    upper_bound: float | None
    summary_state: str
    technical_replicate_count: int
    biological_replicate_count: int
    unspecified_replicate_count: int
    technical_replicate_group_count: int
    biological_replicate_group_count: int
    eligible_measurement_count: int
    censored_measurement_count: int
    missing_measurement_count: int
    source_measurement_ids: tuple[str, ...]
    missing_reasons: tuple[str, ...]
    assay_definition_ids: tuple[str, ...]
    assay_run_ids: tuple[str, ...]
    replicate_group_id: str | None
    aggregation_method: str
    dispersion: float | None
    dispersion_method: str
    analysis_version: str

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the summary."""
        result = {
            "compound_id": self.compound_id,
            "compatibility_key": self.compatibility_key,
            "canonical_unit": self.canonical_unit,
            "summary_value": self.summary_value,
            "summary_qualifier": self.summary_qualifier,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "summary_state": self.summary_state,
            "technical_replicate_count": self.technical_replicate_count,
            "biological_replicate_count": self.biological_replicate_count,
            "unspecified_replicate_count": self.unspecified_replicate_count,
            "technical_replicate_group_count": self.technical_replicate_group_count,
            "biological_replicate_group_count": self.biological_replicate_group_count,
            "eligible_measurement_count": self.eligible_measurement_count,
            "censored_measurement_count": self.censored_measurement_count,
            "missing_measurement_count": self.missing_measurement_count,
            "source_measurement_ids": list(self.source_measurement_ids),
            "missing_reasons": list(self.missing_reasons),
            "assay_definition_ids": list(self.assay_definition_ids),
            "assay_run_ids": list(self.assay_run_ids),
            "replicate_group_id": self.replicate_group_id,
            "aggregation_method": self.aggregation_method,
            "dispersion": self.dispersion,
            "dispersion_method": self.dispersion_method,
            "analysis_version": self.analysis_version,
        }
        # These aliases keep the result convenient for consumers that refer to
        # the aggregate as simply value/unit while retaining explicit columns.
        result["value"] = self.summary_value
        result["unit"] = self.canonical_unit
        return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _row_dict(row: Mapping[str, Any] | sqlite3.Row | Any) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError) as exc:
        raise MeasurementEngineError("Each measurement must be a mapping or sqlite row") from exc


def _first_value(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            value = row[name]
            if not isinstance(value, str) or value.strip():
                return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any, field: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MeasurementEngineError(f"{field} must be numeric or null") from exc
    if not math.isfinite(number):
        raise MeasurementEngineError(f"{field} must be finite")
    return number


def _measurement_id(row: Mapping[str, Any]) -> str:
    value = _first_value(row, ("id", "measurement_id", "source_measurement_id", "source_row_id"))
    if value is None:
        raise MeasurementEngineError("Every measurement needs a source measurement ID")
    return str(value)


def _compound_id(row: Mapping[str, Any]) -> str | None:
    value = _first_value(row, ("compound_id", "compound"))
    return None if value is None else str(value)


def _canonical_unit(row: Mapping[str, Any]) -> str | None:
    value = _first_value(row, ("canonical_unit", "summary_unit", "unit_ucum", "unit"))
    return _text(value)


def _assay_context(row: Mapping[str, Any]) -> dict[str, str | None]:
    compatibility_key = _first_value(
        row,
        ("compatibility_key", "assay_compatibility_key", "assay_context_key"),
    )
    if compatibility_key is None:
        compatibility_key = _first_value(
            row,
            ("assay_definition_id", "assay_run_id", "assay_id", "assay", "endpoint_code"),
        )
    compatibility_key = _text(compatibility_key)
    if compatibility_key is None:
        raise MeasurementEngineError("Every measurement needs an assay compatibility key")
    return {
        "compatibility_key": compatibility_key,
        "canonical_unit": _canonical_unit(row),
    }


def assert_assay_compatible(
    measurements: Iterable[Mapping[str, Any] | sqlite3.Row],
) -> dict[str, str | None]:
    """Validate and return the shared assay context for a measurement set.

    The persisted ``compatibility_key`` is authoritative. Canonical units are
    checked as a second dimension so values in different units cannot be
    silently averaged even when a caller supplied an over-broad key.
    """
    rows = [_row_dict(measurement) for measurement in measurements]
    if not rows:
        raise MeasurementEngineError("At least one measurement is required")
    contexts = [_assay_context(row) for row in rows]
    first = contexts[0]
    incompatible = [context for context in contexts[1:] if context != first]
    if incompatible:
        details = ", ".join(
            f"{context['compatibility_key']}/{context['canonical_unit'] or 'unitless'}"
            for context in contexts
        )
        raise IncompatibleAssayContextError(
            f"Measurements are not assay-compatible: {details}"
        )
    return first


def check_assay_compatibility(
    measurements: Iterable[Mapping[str, Any] | sqlite3.Row],
) -> dict[str, str | None]:
    """Public synonym for :func:`assert_assay_compatible`."""
    return assert_assay_compatible(measurements)


def are_assay_compatible(
    measurements: Iterable[Mapping[str, Any] | sqlite3.Row],
) -> bool:
    """Return a boolean compatibility result without hiding validation errors."""
    try:
        assert_assay_compatible(measurements)
    except MeasurementEngineError:
        return False
    return True


def _replicate_type(row: Mapping[str, Any]) -> str:
    value = _text(_first_value(row, ("replicate_type", "replicate_kind")))
    if value is None:
        return "unspecified"
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "tech": "technical",
        "technical_replicate": "technical",
        "bio": "biological",
        "biological_replicate": "biological",
        "unknown": "unspecified",
        "": "unspecified",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"technical", "biological", "unspecified"}:
        raise MeasurementEngineError(f"Unsupported replicate type: {value!r}")
    return normalized


def _group_id(row: Mapping[str, Any], measurement_id: str) -> str | None:
    value = _first_value(
        row,
        (
            "biological_replicate_id",
            "biological_group_id",
            "biological_replicate_group_id",
            "replicate_group_id",
        ),
    )
    return None if value is None else str(value)


def _qualifier(row: Mapping[str, Any], value: float | None) -> str | None:
    raw = _text(row.get("qualifier"))
    if raw is None and value is not None:
        return "="
    if raw is not None and raw not in VALID_QUALIFIERS:
        raise MeasurementEngineError(f"Unsupported measurement qualifier: {raw!r}")
    return raw


def _state(exact_count: int, censored_count: int, missing_count: int) -> str:
    if exact_count:
        suffix = []
        if censored_count:
            suffix.append("censored")
        if missing_count:
            suffix.append("missing")
        return "observed" if not suffix else "observed_with_" + "_and_".join(suffix)
    if censored_count:
        return "censored" if not missing_count else "censored_with_missing"
    return "missing"


def _aggregate(values: Sequence[float], method: str) -> float:
    if not values:
        raise MeasurementEngineError("At least one eligible numeric value is required")
    if method == "mean":
        return float(mean(values))
    if method == "median":
        return float(median(values))
    raise MeasurementEngineError(f"Unsupported aggregation method: {method!r}")


def _summary_values(prepared: Sequence[dict[str, Any]], method: str) -> list[float]:
    eligible = [item for item in prepared if item["eligible"]]
    values = [item["value"] for item in eligible]
    if not values:
        return []

    # Technical replicates sharing a biological group contribute one group
    # estimate before biological groups are combined. This prevents a group
    # with more wells from receiving extra weight in mixed designs. Purely
    # technical or unspecified data retain the direct aggregate semantics.
    if not any(item["replicate_type"] == "biological" for item in eligible):
        return values
    groups: dict[str, list[float]] = defaultdict(list)
    for item in eligible:
        group = item["group_id"] or f"measurement:{item['measurement_id']}"
        groups[group].append(item["value"])
    return [_aggregate(group_values, method) for group_values in groups.values()]


def summarize_measurements(
    measurements: Iterable[Mapping[str, Any] | sqlite3.Row],
    *,
    aggregation_method: str = "mean",
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
) -> MeasurementSummary:
    """Create a deterministic replicate-aware summary without database I/O.

    Exact (``=``) and approximate (``~``) numeric values are eligible for the
    aggregate. Censored and missing rows remain in the provenance and state
    counters but never contribute to the numeric aggregate.
    """
    rows = [_row_dict(measurement) for measurement in measurements]
    context = assert_assay_compatible(rows)
    method = str(aggregation_method).strip().lower()
    if method not in {"mean", "median"}:
        raise MeasurementEngineError(f"Unsupported aggregation method: {aggregation_method!r}")
    version = _text(analysis_version)
    if version is None:
        raise MeasurementEngineError("analysis_version is required")

    prepared: list[dict[str, Any]] = []
    for row in rows:
        measurement_id = _measurement_id(row)
        value = _number(row.get("canonical_value"), "canonical_value")
        qualifier = _qualifier(row, value)
        missing_reason = _text(row.get("missing_reason"))
        is_censored = qualifier in CENSORED_QUALIFIERS
        is_eligible = value is not None and qualifier in OBSERVED_QUALIFIERS and not missing_reason
        is_missing = not is_censored and (value is None or bool(missing_reason) or qualifier is None)
        prepared.append(
            {
                "measurement_id": measurement_id,
                "compound_id": _compound_id(row),
                "replicate_type": _replicate_type(row),
                "group_id": _group_id(row, measurement_id),
                "value": value,
                "qualifier": qualifier,
                "missing_reason": missing_reason or ("not_reported" if is_missing else None),
                "lower_bound": _number(row.get("lower_bound"), "lower_bound"),
                "upper_bound": _number(row.get("upper_bound"), "upper_bound"),
                "eligible": is_eligible,
                "censored": is_censored,
                "missing": is_missing,
                "assay_definition_id": _text(row.get("assay_definition_id")),
                "assay_run_id": _text(row.get("assay_run_id")),
            }
        )

    compound_ids = {item["compound_id"] for item in prepared if item["compound_id"] is not None}
    if len(compound_ids) > 1:
        raise MeasurementEngineError("A summary cannot combine multiple compounds")

    eligible = [item for item in prepared if item["eligible"]]
    censored = [item for item in prepared if item["censored"]]
    missing = [item for item in prepared if item["missing"]]
    aggregate_input = _summary_values(prepared, method)
    summary_value = _aggregate(aggregate_input, method) if aggregate_input else None

    # Sample SD is undefined for one eligible value and intentionally remains
    # null rather than manufacturing a zero-precision claim.
    dispersion = float(stdev([item["value"] for item in eligible])) if len(eligible) >= 2 else None
    qualifiers = [item["qualifier"] for item in (*eligible, *censored) if item["qualifier"]]
    summary_qualifier = qualifiers[0] if qualifiers and all(item == qualifiers[0] for item in qualifiers) else None
    lower_bounds = [item["lower_bound"] for item in censored if item["lower_bound"] is not None]
    upper_bounds = [item["upper_bound"] for item in censored if item["upper_bound"] is not None]
    lower_bound = max(lower_bounds) if lower_bounds else None
    upper_bound = min(upper_bounds) if upper_bounds else None

    source_ids = tuple(sorted(item["measurement_id"] for item in prepared))
    missing_reasons = tuple(sorted({item["missing_reason"] for item in missing if item["missing_reason"]}))
    assay_definition_ids = tuple(
        sorted({item["assay_definition_id"] for item in prepared if item["assay_definition_id"]})
    )
    assay_run_ids = tuple(sorted({item["assay_run_id"] for item in prepared if item["assay_run_id"]}))

    def count_type(replicate_type: str) -> int:
        return sum(item["replicate_type"] == replicate_type for item in prepared)

    def group_count(replicate_type: str) -> int:
        typed = [item for item in prepared if item["replicate_type"] == replicate_type]
        groups = {item["group_id"] for item in typed if item["group_id"] is not None}
        return len(groups) if groups else len(typed)

    group_ids = {item["group_id"] for item in prepared}
    common_group_id = next(iter(group_ids)) if len(group_ids) == 1 else None
    return MeasurementSummary(
        compound_id=next(iter(compound_ids), None),
        compatibility_key=context["compatibility_key"] or "",
        canonical_unit=context["canonical_unit"],
        summary_value=summary_value,
        summary_qualifier=summary_qualifier,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        summary_state=_state(len(eligible), len(censored), len(missing)),
        technical_replicate_count=count_type("technical"),
        biological_replicate_count=count_type("biological"),
        unspecified_replicate_count=count_type("unspecified"),
        technical_replicate_group_count=group_count("technical"),
        biological_replicate_group_count=group_count("biological"),
        eligible_measurement_count=len(eligible),
        censored_measurement_count=len(censored),
        missing_measurement_count=len(missing),
        source_measurement_ids=source_ids,
        missing_reasons=missing_reasons,
        assay_definition_ids=assay_definition_ids,
        assay_run_ids=assay_run_ids,
        replicate_group_id=common_group_id,
        aggregation_method=method,
        dispersion=dispersion,
        dispersion_method=DISPERSION_METHOD,
        analysis_version=version,
    )


def summarize_replicates(
    measurements: Iterable[Mapping[str, Any] | sqlite3.Row],
    *,
    aggregation_method: str = "mean",
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
) -> MeasurementSummary:
    """Public synonym emphasizing the replicate semantics of the operation."""
    return summarize_measurements(
        measurements,
        aggregation_method=aggregation_method,
        analysis_version=analysis_version,
    )


def aggregate_measurements(
    measurements: Iterable[Mapping[str, Any] | sqlite3.Row],
    *,
    aggregation_method: str = "mean",
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
) -> MeasurementSummary:
    """Compatibility synonym for callers that use aggregate terminology."""
    return summarize_measurements(
        measurements,
        aggregation_method=aggregation_method,
        analysis_version=analysis_version,
    )


def _persisted_result(summary_id: str, summary: MeasurementSummary, created_at: str) -> dict[str, Any]:
    result = summary.as_dict()
    result.update({"id": summary_id, "created_at": created_at, "data_origin": "derived"})
    return result


def persist_measurement_summary(
    database_path: str,
    summary: MeasurementSummary,
    *,
    summary_id: str | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Persist one summary snapshot and return its JSON-friendly representation."""
    if summary.compound_id is None:
        raise MeasurementEngineError("A persisted summary requires a compound_id")
    if not summary.source_measurement_ids:
        raise MeasurementEngineError("A persisted summary requires source measurement IDs")
    summary_id = summary_id or _id("msum")
    created_at = _now()
    source_ids = list(summary.source_measurement_ids)
    placeholders = ", ".join("?" for _ in source_ids)
    with transaction(database_path) as connection:
        existing = connection.execute(
            f"SELECT COUNT(*) AS count FROM measurements WHERE id IN ({placeholders})",
            source_ids,
        ).fetchone()
        if int(existing["count"]) != len(source_ids):
            raise MeasurementEngineError("Every source measurement must exist before persistence")
        connection.execute(
            """
            INSERT INTO measurement_summaries
                (id, compound_id, compatibility_key, canonical_unit, replicate_group_id,
                 summary_state, summary_value, summary_qualifier, lower_bound, upper_bound,
                 technical_replicate_count, biological_replicate_count, unspecified_replicate_count,
                 technical_replicate_group_count, biological_replicate_group_count,
                 eligible_measurement_count, censored_measurement_count, missing_measurement_count,
                 missing_reasons_json, source_measurement_ids_json, assay_definition_ids_json,
                 assay_run_ids_json, aggregation_method, dispersion, dispersion_method,
                 analysis_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary_id,
                summary.compound_id,
                summary.compatibility_key,
                summary.canonical_unit,
                summary.replicate_group_id,
                summary.summary_state,
                summary.summary_value,
                summary.summary_qualifier,
                summary.lower_bound,
                summary.upper_bound,
                summary.technical_replicate_count,
                summary.biological_replicate_count,
                summary.unspecified_replicate_count,
                summary.technical_replicate_group_count,
                summary.biological_replicate_group_count,
                summary.eligible_measurement_count,
                summary.censored_measurement_count,
                summary.missing_measurement_count,
                json.dumps(list(summary.missing_reasons), sort_keys=True),
                json.dumps(source_ids, sort_keys=True),
                json.dumps(list(summary.assay_definition_ids), sort_keys=True),
                json.dumps(list(summary.assay_run_ids), sort_keys=True),
                summary.aggregation_method,
                summary.dispersion,
                summary.dispersion_method,
                summary.analysis_version,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            SELECT ?, ?, c.project_id, 'measurement_summary_create', 'measurement_summary', ?, 'success', ?, ?
            FROM compounds AS c WHERE c.id = ?
            """,
            (
                _id("audit"),
                actor_user_id,
                summary_id,
                json.dumps({"source_measurement_count": len(source_ids), "analysis_version": summary.analysis_version}, sort_keys=True),
                created_at,
                summary.compound_id,
            ),
        )
    return _persisted_result(summary_id, summary, created_at)


def load_measurements(
    database_path: str,
    measurement_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Load measurements with assay context needed for compatibility checks."""
    ids = list(dict.fromkeys(str(measurement_id) for measurement_id in measurement_ids))
    if not ids:
        raise MeasurementEngineError("At least one measurement ID is required")
    placeholders = ", ".join("?" for _ in ids)
    with read_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT m.*, ad.id AS assay_definition_id, ad.compatibility_key,
                   ad.endpoint_code, ad.modality, ad.protocol_version,
                   ar.id AS assay_run_id
            FROM measurements AS m
            JOIN assay_runs AS ar ON ar.id = m.assay_run_id
            JOIN assay_definitions AS ad ON ad.id = ar.assay_definition_id
            WHERE m.id IN ({placeholders})
            ORDER BY m.id
            """,
            ids,
        ).fetchall()
    found = {str(row["id"]) for row in rows}
    missing = [measurement_id for measurement_id in ids if measurement_id not in found]
    if missing:
        raise MeasurementEngineError(f"Unknown measurement IDs: {', '.join(missing)}")
    return [_row_dict(row) for row in rows]


def create_measurement_summary(
    database_path: str,
    measurement_ids: Iterable[str],
    *,
    aggregation_method: str = "mean",
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    summary_id: str | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Load, validate, aggregate, and persist a selected measurement set."""
    summary = summarize_measurements(
        load_measurements(database_path, measurement_ids),
        aggregation_method=aggregation_method,
        analysis_version=analysis_version,
    )
    return persist_measurement_summary(
        database_path,
        summary,
        summary_id=summary_id,
        actor_user_id=actor_user_id,
    )


# Explicit name for callers that prefer the selection/persistence wording.
summarize_measurement_ids = create_measurement_summary


def summarize_project_measurements(
    database_path: str,
    project_id: str,
    *,
    aggregation_method: str = "mean",
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    actor_user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Persist one summary per compound, compatible assay context, and unit."""
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT m.id, m.compound_id, m.canonical_unit, m.unit_ucum,
                   ad.compatibility_key
            FROM measurements AS m
            JOIN compounds AS c ON c.id = m.compound_id
            JOIN assay_runs AS ar ON ar.id = m.assay_run_id
            JOIN assay_definitions AS ad ON ad.id = ar.assay_definition_id
            WHERE c.project_id = ?
            ORDER BY m.compound_id, ad.compatibility_key, COALESCE(m.canonical_unit, m.unit_ucum), m.id
            """,
            (project_id,),
        ).fetchall()
    grouped: dict[tuple[str, str, str | None], list[str]] = defaultdict(list)
    for row in rows:
        unit = row["canonical_unit"] or row["unit_ucum"]
        grouped[(row["compound_id"], row["compatibility_key"], unit)].append(row["id"])
    return [
        create_measurement_summary(
            database_path,
            measurement_ids,
            aggregation_method=aggregation_method,
            analysis_version=analysis_version,
            actor_user_id=actor_user_id,
        )
        for _, measurement_ids in sorted(grouped.items())
    ]


run_measurement_summaries = summarize_project_measurements


def _decode_json_column(result: dict[str, Any], column: str, output: str) -> None:
    value = result.pop(column, "[]")
    try:
        result[output] = json.loads(value) if value else []
    except (TypeError, json.JSONDecodeError) as exc:
        raise MeasurementEngineError(f"Invalid JSON in {column}") from exc


def get_measurement_summary(database_path: str, summary_id: str) -> dict[str, Any] | None:
    """Read a persisted summary with decoded provenance arrays."""
    with read_connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM measurement_summaries WHERE id = ?",
            (summary_id,),
        ).fetchone()
    if row is None:
        return None
    result = _row_dict(row)
    _decode_json_column(result, "source_measurement_ids_json", "source_measurement_ids")
    _decode_json_column(result, "missing_reasons_json", "missing_reasons")
    _decode_json_column(result, "assay_definition_ids_json", "assay_definition_ids")
    _decode_json_column(result, "assay_run_ids_json", "assay_run_ids")
    result["value"] = result["summary_value"]
    result["unit"] = result["canonical_unit"]
    result["data_origin"] = "derived"
    return result
