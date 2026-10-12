from __future__ import annotations

import math
import unicodedata
from typing import Any


def canonical_unit(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _endpoint_metadata(key: str | None, summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not key:
        return None
    units = sorted({canonical_unit(item.get("canonical_unit")) for item in summaries if item.get("compatibility_key") == key and canonical_unit(item.get("canonical_unit"))})
    return {
        "endpoint_key": key,
        "name": key.split(":", 1)[0],
        "canonical_unit": units[0] if len(units) == 1 else None,
        "canonical_units": units,
        "unit_consistent": len(units) <= 1,
    }


def _summary_order(item: dict[str, Any]) -> tuple[int, str, str]:
    """Match production_explorer.js: observed records outrank newer non-observed records."""
    state = str(item.get("summary_state") or "").lower()
    qualifier = item.get("summary_qualifier")
    numeric = finite(item.get("summary_value"))
    missing = _is_missing(item)
    censored = state == "censored" or qualifier != "="
    browser_observed = not missing and not censored and numeric is not None
    return (2 if browser_observed else 1, str(item.get("created_at") or ""), str(item.get("id") or ""))


def _missing_reasons(item: dict[str, Any] | None) -> list[str]:
    if not item:
        return []
    reasons: list[str] = []
    stored = item.get("missing_reasons")
    if isinstance(stored, (list, tuple)):
        reasons.extend(str(reason) for reason in stored if str(reason))
    elif stored:
        reasons.append(str(stored))
    reason = item.get("missing_reason")
    if reason and str(reason) not in reasons:
        reasons.append(str(reason))
    return reasons


def _is_missing(item: dict[str, Any] | None) -> bool:
    if not item:
        return True
    state = str(item.get("summary_state") or "").lower()
    return state == "missing" or bool(_missing_reasons(item))


def _browser_status(item: dict[str, Any] | None) -> str:
    if not item:
        return "missing"
    state = str(item.get("summary_state") or "").lower()
    qualifier = item.get("summary_qualifier")
    if _is_missing(item):
        return "missing"
    if state == "censored" or qualifier != "=":
        return "censored"
    return "observed" if finite(item.get("summary_value")) is not None else "review"


def compare_rows(summaries: list[dict[str, Any]], compounds: list[dict[str, Any]], endpoint_a: str, endpoint_b: str | None, coverage: str = "all", q: str = "", offset: int = 0, limit: int = 200) -> dict[str, Any]:
    by_compound = {str(item.get("id")): item for item in compounds}
    endpoint_keys = {endpoint_a} | ({endpoint_b} if endpoint_b else set())
    grouped: dict[str, dict[str, dict[str, Any]]] = {compound_id: {} for compound_id in by_compound}
    # The route normally supplies this order, but selecting here as well keeps native/MCP callers deterministic.
    for item in sorted(summaries, key=_summary_order, reverse=True):
        key = item.get("compatibility_key")
        compound_id = str(item.get("compound_id"))
        if key not in endpoint_keys or compound_id not in grouped:
            continue
        grouped[compound_id].setdefault(str(key), item)
    selected_summaries = [item for records in grouped.values() for item in records.values()]
    query = str(q or "").strip().casefold()
    rows = []
    for compound_id, records in grouped.items():
        compound = by_compound.get(compound_id, {})
        registration = str(compound.get("registration_id") or "")
        if query and query not in registration.casefold():
            continue
        a = records.get(endpoint_a)
        b = records.get(endpoint_b) if endpoint_b else None
        a_value = finite(a.get("summary_value")) if a else None
        b_value = finite(b.get("summary_value")) if b else None
        a_unit = canonical_unit(a.get("canonical_unit")) if a else ""
        b_unit = canonical_unit(b.get("canonical_unit")) if b else ""
        a_exact = bool(a and _browser_status(a) == "observed" and a.get("summary_qualifier") == "=" and a_value is not None)
        b_exact = bool(b and _browser_status(b) == "observed" and b.get("summary_qualifier") == "=" and b_value is not None)
        measured_a = a is not None and _browser_status(a) != "missing"
        measured_b = b is not None and _browser_status(b) != "missing"
        review = not a_exact or (endpoint_b is not None and not b_exact)
        if coverage == "endpoint-a" and not measured_a:
            continue
        if coverage == "both" and (not measured_a or not measured_b):
            continue
        if coverage == "review" and not review:
            continue
        def missing(item: dict[str, Any] | None) -> bool:
            return _browser_status(item) == "missing"

        def invalid_numeric(item: dict[str, Any] | None, value: float | None) -> bool:
            if not item:
                return False
            raw = item.get("summary_value")
            # Numeric corruption takes precedence over a persisted missing/review state;
            # otherwise NaN/Infinity would be reported as ordinary missingness.
            return (raw is not None and value is None) or (str(item.get("summary_state") or "").startswith("observed") and raw is None)

        if invalid_numeric(a, a_value) or invalid_numeric(b, b_value):
            status = "invalid_numeric"
        elif missing(a):
            status = "missing_a"
        elif endpoint_b and missing(b):
            status = "missing_b"
        elif review:
            status = "review"
        elif not a_unit or (endpoint_b and not b_unit):
            status = "missing_unit"
        elif endpoint_b and a_unit != b_unit:
            status = "unit_mismatch"
        else:
            status = "comparable"

        def result(item: dict[str, Any] | None, unit: str) -> dict[str, Any]:
            return {
                "summary_id": item.get("id") if item else None,
                "value": finite(item.get("summary_value")) if item else None,
                "unit": unit,
                "original_unit": item.get("canonical_unit") if item else None,
                "qualifier": item.get("summary_qualifier") if item else None,
                "state": item.get("summary_state") if item else "missing",
                "missing": _is_missing(item),
                "missing_reason": (_missing_reasons(item) or [None])[0],
                "missing_reasons": _missing_reasons(item),
                "source_measurement_ids": item.get("source_measurement_ids", []) if item else [],
            }

        rows.append({
            "compound_id": compound_id,
            "registration_id": registration,
            "endpoint_a": result(a, a_unit),
            "endpoint_b": result(b, b_unit) if endpoint_b else None,
            "status": status,
            "comparability": "comparable" if status == "comparable" else "not_comparable",
            "data_origin": "derived",
        })
    rows.sort(key=lambda row: (row["registration_id"], row["compound_id"]))
    total = len(rows)
    start = max(0, int(offset))
    page = rows[start:start + max(1, int(limit))]
    return {
        "endpoint_a": endpoint_a,
        "endpoint_b": endpoint_b or "none",
        "endpoint_a_metadata": _endpoint_metadata(endpoint_a, selected_summaries),
        "endpoint_b_metadata": _endpoint_metadata(endpoint_b, selected_summaries),
        "canonical_units": {
            "endpoint_a": (_endpoint_metadata(endpoint_a, selected_summaries) or {}).get("canonical_units", []),
            "endpoint_b": (_endpoint_metadata(endpoint_b, selected_summaries) or {}).get("canonical_units", []) if endpoint_b else [],
        },
        "coverage": coverage,
        "q": str(q or "").strip(),
        "total": total,
        "offset": start,
        "returned": len(page),
        "has_more": start + len(page) < total,
        "next_offset": start + len(page) if start + len(page) < total else None,
        "stable_order": "registration_id,compound_id",
        "rows": page,
        "data_origin": "derived",
    }
