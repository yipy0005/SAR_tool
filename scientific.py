from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


QUALIFIERS = {"=", "<", "<=", ">", ">=", "~"}
MISSING_REASONS = {
    "",
    "na",
    "n/a",
    "not run",
    "not_run",
    "not reported",
    "not_reported",
    "not detected",
    "not_detected",
    "invalid",
    "insufficient sample",
    "insufficient_sample",
    "not applicable",
    "not_applicable",
}
CONCENTRATION_TO_NM = {
    "m": 1_000_000_000,
    "mm": 1_000_000,
    "um": 1_000,
    "µm": 1_000,
    "μm": 1_000,
    "nm": 1,
    "pm": 0.001,
}


class MeasurementValidationError(ValueError):
    def __init__(self, message: str, code: str = "invalid_measurement") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NormalizedMeasurement:
    raw_value_text: str
    value_numeric: float | None
    unit_ucum: str | None
    qualifier: str | None
    lower_bound: float | None
    upper_bound: float | None
    canonical_value: float | None
    canonical_unit: str | None
    transform_id: str | None
    missing_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _inverse_qualifier(qualifier: str) -> str:
    return {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=", "~": "~"}[qualifier]


def _normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    value = unit.strip().replace("μ", "µ")
    aliases = {"microM": "µM", "uM": "µM", "micromolar": "µM", "nanomolar": "nM"}
    return aliases.get(value, value)


def normalize_measurement(
    raw_value: Any,
    unit: str | None,
    qualifier: str | None = "=",
    endpoint_code: str | None = None,
    canonical_unit: str | None = None,
) -> NormalizedMeasurement:
    raw_text = "" if raw_value is None else str(raw_value).strip()
    missing_key = raw_text.lower().replace("-", "_")
    normalized_unit = _normalize_unit(unit)
    if missing_key in MISSING_REASONS:
        return NormalizedMeasurement(
            raw_value_text=raw_text,
            value_numeric=None,
            unit_ucum=normalized_unit,
            qualifier=None,
            lower_bound=None,
            upper_bound=None,
            canonical_value=None,
            canonical_unit=canonical_unit or normalized_unit,
            transform_id=None,
            missing_reason=missing_key or "not_reported",
        )

    try:
        numeric = float(Decimal(raw_text))
    except (InvalidOperation, ValueError) as exc:
        raise MeasurementValidationError(
            f"Measurement value must be numeric or an explicit missing value: {raw_text!r}",
            "non_numeric_value",
        ) from exc
    if not math.isfinite(numeric):
        raise MeasurementValidationError("NaN and infinity are not valid measurements", "non_finite_value")

    relation = (qualifier or "=").strip()
    if relation not in QUALIFIERS:
        raise MeasurementValidationError(
            f"Unsupported measurement qualifier: {relation!r}", "invalid_qualifier"
        )
    if normalized_unit is None:
        raise MeasurementValidationError("A measurement unit is required", "missing_unit")

    endpoint = (endpoint_code or "").lower()
    concentration_endpoint = any(
        token in endpoint for token in ("ic50", "ec50", "ac50", "ki", "kd", "mic")
    )
    wants_pic50 = (canonical_unit or "").lower() == "pic50" or concentration_endpoint
    if wants_pic50:
        factor = CONCENTRATION_TO_NM.get(normalized_unit.lower())
        if factor is None:
            raise MeasurementValidationError(
                f"Unit {normalized_unit!r} cannot be converted to pIC50", "unsupported_concentration_unit"
            )
        if numeric <= 0:
            raise MeasurementValidationError(
                "Concentration measurements must be greater than zero", "non_positive_concentration"
            )
        pic50 = 9.0 - math.log10(numeric * factor)
        canonical_qualifier = _inverse_qualifier(relation)
        return NormalizedMeasurement(
            raw_value_text=raw_text,
            value_numeric=numeric,
            unit_ucum=normalized_unit,
            qualifier=canonical_qualifier,
            lower_bound=pic50 if canonical_qualifier in {">", ">="} else None,
            upper_bound=pic50 if canonical_qualifier in {"<", "<="} else None,
            canonical_value=pic50,
            canonical_unit="pIC50",
            transform_id="concentration-to-pIC50-v1",
            missing_reason=None,
        )

    return NormalizedMeasurement(
        raw_value_text=raw_text,
        value_numeric=numeric,
        unit_ucum=normalized_unit,
        qualifier=relation,
        lower_bound=numeric if relation in {">", ">="} else None,
        upper_bound=numeric if relation in {"<", "<="} else None,
        canonical_value=numeric,
        canonical_unit=canonical_unit or normalized_unit,
        transform_id=None,
        missing_reason=None,
    )
