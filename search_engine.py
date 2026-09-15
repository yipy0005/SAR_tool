from __future__ import annotations

from typing import Any

from database import read_connection


SEARCH_INDEX_VERSION = "project-compounds-v1"
MAX_SEARCH_LIMIT = 200


class SearchValidationError(ValueError):
    """Raised when a bounded search request is invalid."""


def _bounded_int(value: Any, field: str, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SearchValidationError(f"{field} must be an integer") from exc
    if parsed < 0 or parsed > maximum:
        raise SearchValidationError(f"{field} must be between 0 and {maximum}")
    return parsed


def search_compounds(
    database_path: str,
    project_id: str,
    query: Any = "",
    *,
    limit: Any = 50,
    offset: Any = 0,
) -> dict[str, Any]:
    text = str(query or "").strip()
    if len(text) > 200:
        raise SearchValidationError("query must be 200 characters or fewer")
    result_limit = _bounded_int(limit, "limit", 50, MAX_SEARCH_LIMIT)
    if result_limit == 0:
        raise SearchValidationError("limit must be at least 1")
    result_offset = _bounded_int(offset, "offset", 0, 1_000_000)
    prefix = f"{text}%"
    structure_pattern = f"%{text}%"
    with read_connection(database_path) as connection:
        total_row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM compounds c
            LEFT JOIN structure_records s ON s.compound_id = c.id
            WHERE c.project_id = ?
              AND (
                ? = '' OR c.registration_id LIKE ? OR c.preferred_name LIKE ?
                OR s.canonical_smiles LIKE ? OR s.isomeric_smiles LIKE ?
              )
            """,
            (project_id, text, prefix, prefix, structure_pattern, structure_pattern),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT c.id, c.registration_id, c.preferred_name, c.lifecycle_status,
                   s.canonical_smiles, s.isomeric_smiles, s.inchikey,
                   s.stereochemistry_status, s.rendered_svg
            FROM compounds c
            LEFT JOIN structure_records s ON s.compound_id = c.id
            WHERE c.project_id = ?
              AND (
                ? = '' OR c.registration_id LIKE ? OR c.preferred_name LIKE ?
                OR s.canonical_smiles LIKE ? OR s.isomeric_smiles LIKE ?
              )
            ORDER BY c.registration_id, c.id
            LIMIT ? OFFSET ?
            """,
            (project_id, text, prefix, prefix, structure_pattern, structure_pattern, result_limit, result_offset),
        ).fetchall()
    total = int(total_row["count"])
    return {
        "results": [dict(row) for row in rows],
        "count": total,
        "limit": result_limit,
        "offset": result_offset,
        "has_more": result_offset + len(rows) < total,
        "query": text,
        "search_index_version": SEARCH_INDEX_VERSION,
        "projection_status": "canonical",
        "data_origin": "imported",
    }
