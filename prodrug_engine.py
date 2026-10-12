from __future__ import annotations

import csv
import io
import json
import math
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from database import read_connection, transaction


RELATIONSHIP_TYPE = "prodrug_of"
MAX_PAIR_ROWS = 5_000
PAIR_TEXT_GRAMMAR_VERSION = "prodrug-pairs-v1"


class ProdrugRelationshipError(ValueError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).lstrip("\ufeff").strip()


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _compound_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(row.get("id")),
        "registration_id": _text(row.get("registration_id")),
        "preferred_name": _text(row.get("preferred_name")),
        "isomeric_smiles": _text(row.get("isomeric_smiles")),
        "rendered_svg": _text(row.get("rendered_svg")),
    }


def _load_compounds(database_path: str, project_id: str, connection: Any | None = None) -> list[dict[str, Any]]:
    def read(source: Any) -> list[dict[str, Any]]:
        rows = source.execute(
            """
            SELECT c.id, c.registration_id, c.preferred_name,
                   sr.isomeric_smiles, sr.rendered_svg
            FROM compounds AS c
            LEFT JOIN structure_records AS sr ON sr.id = (
                SELECT latest.id
                FROM structure_records AS latest
                WHERE latest.compound_id = c.id
                ORDER BY latest.created_at DESC, latest.id DESC
                LIMIT 1
            )
            WHERE c.project_id = ?
            ORDER BY c.registration_id, c.id
            """,
            (project_id,),
        ).fetchall()
        return [_compound_payload(_row_dict(row)) for row in rows]

    if connection is not None:
        return read(connection)
    with read_connection(database_path) as source:
        return read(source)


def _pair_rows_from_text(pair_text: Any) -> list[dict[str, Any]]:
    text = _text(pair_text)
    if not text:
        return []
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = "\t" if "\t" in first_line and "," not in first_line else ","
    rows: list[dict[str, Any]] = []
    first_nonblank = True
    for line_number, raw_fields in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter), start=1):
        fields = [_text(value) for value in raw_fields]
        if not any(fields):
            continue
        normalized = [field.lower().replace(" ", "_").replace("-", "_") for field in fields[:2]]
        aliases = ({"prodrug", "prodrug_id"}, {"active", "active_id"})
        if first_nonblank and len(normalized) >= 2 and normalized[0] in aliases[0] and normalized[1] in aliases[1]:
            first_nonblank = False
            continue
        first_nonblank = False
        if len(fields) < 2:
            rows.append({"line": line_number, "prodrug": fields[0] if fields else "", "active": "", "notes": "", "error": "Each row must contain prodrug and active columns."})
            continue
        rows.append({
            "line": line_number,
            "prodrug": fields[0],
            "active": fields[1],
            "notes": fields[2] if len(fields) > 2 else "",
            "source_columns": ["prodrug", "active", "notes"][:min(3, len(fields))],
        })
    return rows


def _pair_rows_from_payload(pair_text: Any = None, pairs: Any = None) -> list[dict[str, Any]]:
    if pairs is None:
        return _pair_rows_from_text(pair_text)
    if not isinstance(pairs, list):
        raise ProdrugRelationshipError("pairs must be a list of prodrug and active-form mappings.")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(pairs, start=1):
        if not isinstance(raw, Mapping):
            rows.append({"line": index, "prodrug": "", "active": "", "notes": "", "error": "Each pair must be an object."})
            continue
        prodrug = raw.get("prodrug_id", raw.get("prodrug", raw.get("prodrug_registration_id", "")))
        active = raw.get("active_id", raw.get("active", raw.get("active_form_id", raw.get("active_registration_id", ""))))
        rows.append(
            {
                "line": index,
                "prodrug": _text(prodrug),
                "active": _text(active),
                "notes": _text(raw.get("notes")),
                "source_columns": ["prodrug", "active", "notes"],
                "source_format": "structured_pairs",
            }
        )
    return rows


def preview_relationships(
    database_path: str,
    project_id: str,
    *,
    pair_text: Any = None,
    pairs: Any = None,
    relationship_type: str = RELATIONSHIP_TYPE,
    offset: int = 0,
    limit: int | None = None,
    connection: Any | None = None,
) -> dict[str, Any]:
    if relationship_type != RELATIONSHIP_TYPE:
        raise ProdrugRelationshipError("Only the prodrug_of relationship is supported in this workflow.")
    rows = _pair_rows_from_payload(pair_text, pairs)
    if len(rows) > MAX_PAIR_ROWS:
        raise ProdrugRelationshipError(f"Pair mapping exceeds the {MAX_PAIR_ROWS}-row safety limit.")
    compounds = _load_compounds(database_path, project_id, connection=connection)
    by_token: dict[str, list[dict[str, Any]]] = {}
    for compound in compounds:
        for token in {compound["id"], compound["registration_id"], compound["preferred_name"]}:
            if token:
                by_token.setdefault(token, []).append(compound)

    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def resolve(token: str, role: str) -> tuple[dict[str, Any] | None, str | None]:
        if not token:
            return None, f"{role} compound is blank."
        candidates = {item["id"]: item for item in by_token.get(token, [])}
        if not candidates:
            return None, f"{role} compound '{token}' was not found in this project."
        if len(candidates) > 1:
            labels = ", ".join(sorted(item["registration_id"] for item in candidates.values()))
            return None, f"{role} value '{token}' is ambiguous: {labels}."
        return next(iter(candidates.values())), None

    for row_index, row in enumerate(rows):
        prodrug, prodrug_error = resolve(_text(row.get("prodrug")), "Prodrug")
        active, active_error = resolve(_text(row.get("active")), "Active form")
        errors = [error for error in (row.get("error"), prodrug_error, active_error) if error]
        if not errors and prodrug and active and prodrug["id"] == active["id"]:
            errors.append("Prodrug and active form must be different compounds.")
        pair_key = (prodrug["id"], active["id"]) if prodrug and active else None
        if pair_key and pair_key in seen:
            errors.append("This prodrug–active pair appears more than once.")
        common = {
            "line": row.get("line"),
            "source_index": row_index,
            "source_line": row.get("line"),
            "source_row": row.get("line"),
            "source_columns": row.get("source_columns", ["prodrug", "active", "notes"]),
            "source_format": row.get("source_format", "pair_text"),
            "review_status": "needs_review",
        }
        if errors:
            invalid.append({
                **common,
                "prodrug_token": _text(row.get("prodrug")),
                "active_token": _text(row.get("active")),
                "notes": _text(row.get("notes")),
                "errors": errors,
            })
            continue
        assert prodrug is not None and active is not None
        seen.add(pair_key)
        valid.append({
            **common,
            "prodrug": prodrug,
            "active": active,
            "notes": _text(row.get("notes")),
            "relationship_type": relationship_type,
        })

    all_rows = sorted([*valid, *invalid], key=lambda item: (int(item.get("source_line") or 0), int(item.get("source_index") or 0)))
    start = max(0, int(offset))
    end = len(all_rows) if limit is None else min(len(all_rows), start + max(1, int(limit)))
    page = all_rows[start:end]
    page_valid = [item for item in page if "prodrug" in item and "active" in item]
    page_invalid = [item for item in page if "errors" in item]
    return {
        "relationship_type": relationship_type,
        "total_count": len(rows),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "offset": start,
        "returned": len(page),
        "has_more": end < len(all_rows),
        "next_offset": end if end < len(all_rows) else None,
        "stable_order": "source_line,source_index",
        "valid": page_valid,
        "invalid": page_invalid,
        "pair_text_grammar_version": PAIR_TEXT_GRAMMAR_VERSION,
        "saved": False,
        "nothing_saved": True,
        "review_status": "needs_review",
        "data_origin": "quarantined_import",
    }


def save_relationships(
    database_path: str,
    project_id: str,
    *,
    pair_text: Any = None,
    pairs: Any = None,
    actor_user_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    preview = preview_relationships(database_path, project_id, pair_text=pair_text, pairs=pairs)
    if preview["invalid_count"] or not preview["valid_count"]:
        raise ProdrugRelationshipError(
            "Review the pair mapping before saving it.",
            preview,
        )
    now = _now()
    relationship_ids: list[str] = []
    with transaction(database_path) as connection:
        for item in preview["valid"]:
            existing = connection.execute(
                """
                SELECT id FROM compound_relationships
                WHERE project_id = ? AND relationship_type = ?
                  AND prodrug_compound_id = ? AND active_compound_id = ?
                """,
                (project_id, RELATIONSHIP_TYPE, item["prodrug"]["id"], item["active"]["id"]),
            ).fetchone()
            if existing is not None:
                raise ProdrugRelationshipError(
                    "A relationship already exists for one or more selected pairs.",
                    {"error": "duplicate_relationship", "existing_relationship_id": existing["id"]},
                )
            relationship_id = _id("rel")
            connection.execute(
                """
                INSERT INTO compound_relationships
                    (id, project_id, relationship_type, prodrug_compound_id,
                     active_compound_id, review_status, relationship_source,
                     activation_context_json, evidence_ids_json, notes,
                     created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'needs_review', 'bulk_mapping', ?, '[]', ?, ?, ?, ?)
                """,
                (
                    relationship_id, project_id, RELATIONSHIP_TYPE,
                    item["prodrug"]["id"], item["active"]["id"],
                    json.dumps({"mapping_source": "mcp_explicit_mapping", "source_format": item.get("source_format", "structured_pairs"), "source_line": item.get("source_line", item.get("line")), "source_row": item.get("source_row", item.get("line")), "source_columns": item.get("source_columns", ["prodrug", "active", "notes"])}, sort_keys=True),
                    item["notes"], actor_user_id, now, now,
                ),
            )
            relationship_ids.append(relationship_id)
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id,
                 request_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'compound_relationship_bulk_save', 'compound_relationship_batch',
                    ?, ?, 'success', ?, ?)
            """,
            (
                _id("audit"),
                actor_user_id,
                project_id,
                ",".join(relationship_ids),
                request_id,
                json.dumps(
                    {
                        "relationship_type": RELATIONSHIP_TYPE,
                        "saved_count": len(relationship_ids),
                        "saved_relationship_ids": relationship_ids,
                        "source_formats": sorted({item.get("source_format", "structured_pairs") for item in preview["valid"]}),
                        "source_context": [
                            {
                                "relationship_id": relationship_id,
                                "source_format": item.get("source_format", "structured_pairs"),
                                "source_line": item.get("source_line", item.get("line")),
                                "source_row": item.get("source_row", item.get("line")),
                                "source_columns": item.get("source_columns", ["prodrug", "active", "notes"]),
                            }
                            for relationship_id, item in zip(relationship_ids, preview["valid"])
                        ],
                        "review_status": "needs_review",
                        "actor_user_id": actor_user_id,
                        "request_id": request_id,
                        "project_id": project_id,
                    },
                    sort_keys=True,
                ),
                now,
            ),
        )
    return {
        "saved_count": len(relationship_ids),
        "relationship_ids": relationship_ids,
        "relationships": list_relationships(database_path, project_id),
        "data_origin": "imported",
    }


def list_relationships(database_path: str, project_id: str, *, connection: Any | None = None) -> list[dict[str, Any]]:
    def read(source: Any) -> list[dict[str, Any]]:
        rows = source.execute(
            """
            SELECT r.id, r.project_id, r.relationship_type, r.review_status,
                   r.relationship_source, r.activation_context_json,
                   r.evidence_ids_json, r.notes, r.created_at, r.updated_at,
                   p.id AS prodrug_id, p.registration_id AS prodrug_registration_id,
                   p.preferred_name AS prodrug_preferred_name,
                   ps.isomeric_smiles AS prodrug_isomeric_smiles,
                   ps.rendered_svg AS prodrug_rendered_svg,
                   a.id AS active_id, a.registration_id AS active_registration_id,
                   a.preferred_name AS active_preferred_name,
                   ars.isomeric_smiles AS active_isomeric_smiles,
                   ars.rendered_svg AS active_rendered_svg
            FROM compound_relationships AS r
            JOIN compounds AS p ON p.id = r.prodrug_compound_id
            JOIN compounds AS a ON a.id = r.active_compound_id
            LEFT JOIN structure_records AS ps ON ps.id = (
                SELECT latest.id FROM structure_records AS latest
                WHERE latest.compound_id = p.id
                ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
            )
            LEFT JOIN structure_records AS ars ON ars.id = (
                SELECT latest.id FROM structure_records AS latest
                WHERE latest.compound_id = a.id
                ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
            )
            WHERE r.project_id = ? AND r.review_status != 'rejected'
            ORDER BY p.registration_id, a.registration_id, r.id
            """,
            (project_id,),
        ).fetchall()
        relationships: list[dict[str, Any]] = []
        for row in rows:
            item = _row_dict(row)
            relationships.append(
                {
                    "id": item["id"],
                    "project_id": item["project_id"],
                    "relationship_type": item["relationship_type"],
                    "review_status": item["review_status"],
                    "relationship_source": item["relationship_source"],
                    "activation_context": json.loads(item.pop("activation_context_json") or "{}"),
                    "evidence_ids": json.loads(item.pop("evidence_ids_json") or "[]"),
                    "notes": item["notes"] or "",
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "prodrug": {
                        "id": item["prodrug_id"],
                        "registration_id": item["prodrug_registration_id"],
                        "preferred_name": item["prodrug_preferred_name"] or "",
                        "isomeric_smiles": item["prodrug_isomeric_smiles"] or "",
                        "rendered_svg": item["prodrug_rendered_svg"] or "",
                    },
                    "active": {
                        "id": item["active_id"],
                        "registration_id": item["active_registration_id"],
                        "preferred_name": item["active_preferred_name"] or "",
                        "isomeric_smiles": item["active_isomeric_smiles"] or "",
                        "rendered_svg": item["active_rendered_svg"] or "",
                    },
                }
            )
        return relationships

    if connection is not None:
        return read(connection)
    with read_connection(database_path) as source:
        return read(source)


def _latest_summaries(database_path: str, project_id: str, endpoint_key: str, *, connection: Any | None = None) -> dict[str, dict[str, Any]]:
    def read(source: Any) -> dict[str, dict[str, Any]]:
        rows = source.execute(
            """
            SELECT ms.id, ms.compound_id, ms.compatibility_key,
                   ms.canonical_unit, ms.summary_state, ms.summary_value,
                   ms.summary_qualifier, ms.source_measurement_ids_json,
                   ms.created_at
            FROM measurement_summaries AS ms
            JOIN compounds AS c ON c.id = ms.compound_id
            WHERE c.project_id = ? AND ms.compatibility_key = ?
            ORDER BY ms.created_at DESC, ms.id DESC
            """,
            (project_id, endpoint_key),
        ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = _row_dict(row)
            item["source_measurement_ids"] = json.loads(item.pop("source_measurement_ids_json") or "[]")
            latest.setdefault(item["compound_id"], item)
        return latest

    if connection is not None:
        return read(connection)
    with read_connection(database_path) as source:
        return read(source)


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _invalid_numeric(summary: Mapping[str, Any] | None) -> bool:
    if not summary:
        return False
    raw_value = summary.get("summary_value")
    if raw_value is None:
        return _text(summary.get("summary_state")).startswith("observed")
    return _finite_number(raw_value) is None


def _exact_summary(summary: Mapping[str, Any] | None) -> bool:
    return bool(
        summary
        and _finite_number(summary.get("summary_value")) is not None
        and _text(summary.get("summary_state")).startswith("observed")
        and summary.get("summary_qualifier") in {"=", "~"}
    )


def _direction(endpoint_key: str, unit: str) -> str:
    key = _text(endpoint_key).lower()
    normalized_unit = _text(unit).lower()
    if "pic50" in key or normalized_unit == "pic50":
        return "higher"
    if any(token in key for token in ("ic50", "ec50", "ac50", "ki", "kd", "mic")):
        return "lower"
    return "unspecified"


def _summary_view(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    if summary is None:
        return {
            "summary_id": None,
            "value": None,
            "unit": "",
            "state": "missing",
            "qualifier": None,
            "source_measurement_ids": [],
        }
    return {
        "summary_id": summary.get("id"),
        # JSON cannot safely represent NaN or infinity. Invalid observed values
        # remain visible through the comparison status, while the value itself
        # is sanitized before it reaches Flask/CSV serialization.
        "value": _finite_number(summary.get("summary_value")),
        "unit": _text(summary.get("canonical_unit")),
        "state": _text(summary.get("summary_state")) or "missing",
        "qualifier": summary.get("summary_qualifier"),
        "source_measurement_ids": summary.get("source_measurement_ids", []),
    }


def _compare_pair(
    relationship: Mapping[str, Any],
    prodrug_summary: Mapping[str, Any] | None,
    active_summary: Mapping[str, Any] | None,
    endpoint_key: str,
) -> dict[str, Any]:
    prodrug_result = _summary_view(prodrug_summary)
    active_result = _summary_view(active_summary)
    comparison: dict[str, Any] = {
        "status": "incomplete",
        "delta": None,
        "unit": prodrug_result["unit"] or active_result["unit"],
        "direction": _direction(endpoint_key, prodrug_result["unit"] or active_result["unit"]),
        "effect": "not_comparable",
    }
    if prodrug_summary is None:
        comparison["status"] = "missing_prodrug"
    elif active_summary is None:
        comparison["status"] = "missing_active"
    elif _invalid_numeric(prodrug_summary) or _invalid_numeric(active_summary):
        comparison["status"] = "invalid_numeric"
    elif not _exact_summary(prodrug_summary):
        comparison["status"] = "prodrug_non_exact"
    elif not _exact_summary(active_summary):
        comparison["status"] = "active_non_exact"
    elif _finite_number(prodrug_result["value"]) is None or _finite_number(active_result["value"]) is None:
        comparison["status"] = "invalid_numeric"
    elif not prodrug_result["unit"] or not active_result["unit"] or prodrug_result["unit"] != active_result["unit"]:
        comparison["status"] = "unit_mismatch"
    else:
        prodrug_value = _finite_number(prodrug_result["value"])
        active_value = _finite_number(active_result["value"])
        delta = active_value - prodrug_value
        if not math.isfinite(delta):
            comparison["status"] = "invalid_numeric"
        else:
            comparison["status"] = "complete"
            comparison["delta"] = delta
            if abs(delta) < 1e-12:
                comparison["effect"] = "no_change"
            elif comparison["direction"] == "higher":
                comparison["effect"] = "active_better" if delta > 0 else "active_lower"
            elif comparison["direction"] == "lower":
                comparison["effect"] = "active_better" if delta < 0 else "active_lower"
            else:
                comparison["effect"] = "observed_change"
    return {
        "id": relationship["id"],
        "relationship_type": relationship["relationship_type"],
        "review_status": relationship["review_status"],
        "relationship_source": relationship["relationship_source"],
        "notes": relationship["notes"],
        "activation_context": relationship["activation_context"],
        "evidence_ids": relationship["evidence_ids"],
        "data_origin": "imported",
        "relationship_data_origin": "imported",
        "prodrug": {**relationship["prodrug"], "result": prodrug_result},
        "active": {**relationship["active"], "result": active_result},
        "comparison": comparison,
    }


def compare_relationships(
    database_path: str,
    project_id: str,
    endpoint_key: str,
    *,
    relationship_ids: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
    connection: Any | None = None,
) -> dict[str, Any]:
    endpoint = _text(endpoint_key)
    if not endpoint:
        raise ProdrugRelationshipError("Choose an endpoint before comparing prodrug–active pairs.")
    relationships = list_relationships(database_path, project_id, connection=connection)
    if relationship_ids is not None:
        if not relationship_ids:
            raise ProdrugRelationshipError("relationship_ids must contain at least one relationship.")
        if len(set(relationship_ids)) != len(relationship_ids):
            raise ProdrugRelationshipError("relationship_ids must not contain duplicates.")
        by_id = {str(item["id"]): item for item in relationships}
        foreign = [item for item in relationship_ids if item not in by_id]
        if foreign:
            raise ProdrugRelationshipError("One or more relationships are not available in this project.", {"relationship_ids": foreign})
        relationships = [by_id[item] for item in relationship_ids]
    relationships.sort(key=lambda item: (item["prodrug"].get("registration_id", ""), item["active"].get("registration_id", ""), item["id"]))
    summaries = _latest_summaries(database_path, project_id, endpoint, connection=connection)
    canonical_units = sorted({_text(item.get("canonical_unit")) for item in summaries.values() if _text(item.get("canonical_unit"))})
    endpoint_metadata = {
        "endpoint_key": endpoint,
        "name": endpoint.split(":", 1)[0],
        "canonical_unit": canonical_units[0] if len(canonical_units) == 1 else None,
        "canonical_units": canonical_units,
        "unit_consistent": len(canonical_units) <= 1,
    }
    pairs = [
        _compare_pair(
            relationship,
            summaries.get(relationship["prodrug"]["id"]),
            summaries.get(relationship["active"]["id"]),
            endpoint,
        )
        for relationship in relationships
    ]
    complete_count = sum(pair["comparison"]["status"] == "complete" for pair in pairs)
    total = len(pairs)
    start = max(0, int(offset))
    end = total if limit is None else min(total, start + max(1, int(limit)))
    page = pairs[start:end]
    return {
        "endpoint_key": endpoint,
        "endpoint_name": endpoint.split(":", 1)[0],
        "endpoint_metadata": endpoint_metadata,
        "canonical_unit": endpoint_metadata["canonical_unit"],
        "pair_count": total,
        "complete_count": complete_count,
        "incomplete_count": total - complete_count,
        "offset": start,
        "returned": len(page),
        "has_more": end < total,
        "next_offset": end if end < total else None,
        "stable_order": "prodrug_registration_id,active_registration_id,relationship_id",
        "pairs": page,
        "data_origin": "derived",
        "algorithm_version": "prodrug-active-pair-compare-v1",
        "evidence_boundary": "Pairs are project-scoped relationship mappings. Exact endpoint deltas require compatible units and exact observed summaries; cellular or enzymatic conversion evidence must be reviewed separately.",
    }
