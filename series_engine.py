from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database import read_connection, transaction


MEMBERSHIP_SOURCES = {"curated", "rule_derived", "analysis_derived", "import_derived"}
SERIES_STATUSES = {"active", "archived"}
MEMBERSHIP_STATUSES = {"included", "excluded", "needs_review"}


class SeriesError(ValueError):
    """Base error for project-scoped series operations."""


class SeriesNotFoundError(SeriesError):
    """Raised when a series is not available in the requested project."""


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _text(value: Any, field: str, *, required: bool = False, maximum: int = 2000) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise SeriesError(f"{field} is required")
    if len(result) > maximum:
        raise SeriesError(f"{field} is too long")
    return result


def _source(value: Any) -> str:
    result = _text(value, "membership_source", required=True, maximum=40).lower()
    if result not in MEMBERSHIP_SOURCES:
        raise SeriesError(f"membership_source must be one of {sorted(MEMBERSHIP_SOURCES)}")
    return result


def _status(value: Any) -> str:
    result = _text(value, "membership_status", required=True, maximum=40).lower()
    if result not in MEMBERSHIP_STATUSES:
        raise SeriesError(f"membership_status must be one of {sorted(MEMBERSHIP_STATUSES)}")
    return result


def _compound_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SeriesError("compound_ids must be a list")
    result = []
    seen = set()
    for item in value:
        compound_id = _text(item, "compound_id", required=True, maximum=200)
        if compound_id not in seen:
            result.append(compound_id)
            seen.add(compound_id)
    if len(result) > 5000:
        raise SeriesError("A series version cannot contain more than 5000 compounds")
    return result


def _validate_compounds(connection: Any, project_id: str, compound_ids: list[str]) -> None:
    if not compound_ids:
        return
    placeholders = ",".join("?" for _ in compound_ids)
    rows = connection.execute(
        f"SELECT id FROM compounds WHERE project_id = ? AND id IN ({placeholders})",
        [project_id, *compound_ids],
    ).fetchall()
    found = {row["id"] for row in rows}
    missing = [compound_id for compound_id in compound_ids if compound_id not in found]
    if missing:
        raise SeriesError(f"Compound IDs are not in the target project: {', '.join(missing[:10])}")


def _fetch_series(connection: Any, where: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT s.id, s.project_id, s.name, s.description, s.status, s.current_version,
               s.created_by, s.created_at, s.updated_at,
               sv.id AS version_id, sv.version, sv.membership_source,
               sv.rationale AS version_rationale, sv.created_by AS version_created_by,
               sv.created_at AS version_created_at,
               (SELECT COUNT(*) FROM series_memberships sm
                WHERE sm.series_version_id = sv.id AND sm.membership_status = 'included') AS member_count
        FROM series s
        JOIN series_versions sv
          ON sv.series_id = s.id AND sv.version = s.current_version
        {where}
        ORDER BY s.updated_at DESC, s.name
        """,
        parameters,
    ).fetchall()
    return [_row_dict(row) for row in rows]


def list_series(database_path: str, project_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    where = "WHERE s.project_id = ?"
    parameters: tuple[Any, ...] = (project_id,)
    if not include_archived:
        where += " AND s.status = 'active'"
    with read_connection(database_path) as connection:
        return _fetch_series(connection, where, parameters)


def get_series(database_path: str, project_id: str, series_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        rows = _fetch_series(connection, "WHERE s.project_id = ? AND s.id = ?", (project_id, series_id))
        if not rows:
            return None
        result = rows[0]
        members = connection.execute(
            """
            SELECT sm.id, sm.compound_id, c.registration_id, c.preferred_name,
                   sm.membership_source, sm.rationale, sm.membership_status, sm.created_at
            FROM series_memberships sm
            JOIN series_versions sv ON sv.id = sm.series_version_id
            JOIN compounds c ON c.id = sm.compound_id
            WHERE sv.series_id = ? AND sv.version = ?
            ORDER BY c.registration_id
            """,
            (series_id, result["current_version"]),
        ).fetchall()
        result["members"] = [_row_dict(row) for row in members]
        result["data_origin"] = "curated"
        return result


def series_context(database_path: str, project_id: str) -> dict[str, Any]:
    with read_connection(database_path) as connection:
        series = _fetch_series(connection, "WHERE s.project_id = ? AND s.status = 'active'", (project_id,))
        if not series:
            return {"series": [], "compound_series": {}, "data_origin": "curated"}
        series_by_id = {item["id"]: item for item in series}
        for item in series:
            item["members"] = []
        placeholders = ",".join("?" for _ in series)
        rows = connection.execute(
            f"""
            SELECT sv.series_id, sm.id, sm.compound_id, c.registration_id, c.preferred_name,
                   sm.membership_source, sm.rationale, sm.membership_status, sm.created_at
            FROM series_versions sv
            JOIN series_memberships sm ON sm.series_version_id = sv.id
            JOIN compounds c ON c.id = sm.compound_id
            WHERE sv.version = (SELECT current_version FROM series WHERE id = sv.series_id)
              AND sv.series_id IN ({placeholders})
            ORDER BY c.registration_id, sm.id
            """,
            tuple(series_by_id),
        ).fetchall()
    by_compound: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        member = _row_dict(row)
        series_id = member.pop("series_id")
        series_by_id[series_id]["members"].append(member)
        if member["membership_status"] == "excluded":
            continue
        series_item = series_by_id[series_id]
        by_compound.setdefault(member["compound_id"], []).append(
            {
                "series_id": series_id,
                "series_name": series_item["name"],
                "series_version": series_item["current_version"],
                "membership_source": member["membership_source"],
                "membership_status": member["membership_status"],
                "rationale": member["rationale"],
            }
        )
    return {"series": series, "compound_series": by_compound, "data_origin": "curated"}


def create_series(
    database_path: str,
    project_id: str,
    name: Any,
    *,
    description: Any = "",
    membership_source: Any = "curated",
    rationale: Any = "",
    compound_ids: Any = None,
    membership_status: Any = "included",
    created_by: str | None = None,
) -> dict[str, Any]:
    name_text = _text(name, "name", required=True, maximum=160)
    description_text = _text(description, "description", maximum=1000)
    source = _source(membership_source)
    rationale_text = _text(rationale, "rationale", maximum=2000)
    status = _status(membership_status)
    members = _compound_ids(compound_ids)
    now = _now()
    series_id = _id("series")
    version_id = _id("seriesv")
    with transaction(database_path) as connection:
        if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise SeriesError("Project does not exist")
        _validate_compounds(connection, project_id, members)
        try:
            connection.execute(
                """
                INSERT INTO series (id, project_id, name, description, status, current_version, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?)
                """,
                (series_id, project_id, name_text, description_text, created_by, now, now),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise SeriesError("A series with this name already exists in the project") from exc
            raise
        connection.execute(
            """
            INSERT INTO series_versions
                (id, series_id, version, membership_source, rationale, created_by, created_at)
            VALUES (?, ?, 1, ?, ?, ?, ?)
            """,
            (version_id, series_id, source, rationale_text, created_by, now),
        )
        for compound_id in members:
            connection.execute(
                """
                INSERT INTO series_memberships
                    (id, series_version_id, compound_id, membership_source, rationale, membership_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (_id("seriesm"), version_id, compound_id, source, rationale_text, status, now),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, request_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'series_create', 'series', ?, NULL, 'success', ?, ?)
            """,
            (_id("audit"), created_by, project_id, series_id, json.dumps({"version": 1, "membership_source": source, "member_count": len(members)}), now),
        )
    result = get_series(database_path, project_id, series_id)
    if result is None:
        raise SeriesError("Created series could not be reloaded")
    return result


def create_series_version(
    database_path: str,
    project_id: str,
    series_id: str,
    *,
    membership_source: Any,
    rationale: Any = "",
    compound_ids: Any = None,
    membership_status: Any = "included",
    created_by: str | None = None,
) -> dict[str, Any]:
    source = _source(membership_source)
    rationale_text = _text(rationale, "rationale", maximum=2000)
    status = _status(membership_status)
    members = _compound_ids(compound_ids)
    now = _now()
    with transaction(database_path) as connection:
        row = connection.execute(
            "SELECT current_version FROM series WHERE id = ? AND project_id = ?",
            (series_id, project_id),
        ).fetchone()
        if row is None:
            raise SeriesNotFoundError("Series does not exist in the target project")
        _validate_compounds(connection, project_id, members)
        version = int(row["current_version"]) + 1
        version_id = _id("seriesv")
        connection.execute(
            """
            INSERT INTO series_versions
                (id, series_id, version, membership_source, rationale, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, series_id, version, source, rationale_text, created_by, now),
        )
        for compound_id in members:
            connection.execute(
                """
                INSERT INTO series_memberships
                    (id, series_version_id, compound_id, membership_source, rationale, membership_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (_id("seriesm"), version_id, compound_id, source, rationale_text, status, now),
            )
        connection.execute(
            "UPDATE series SET current_version = ?, updated_at = ? WHERE id = ? AND project_id = ?",
            (version, now, series_id, project_id),
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, request_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'series_version_create', 'series_version', ?, NULL, 'success', ?, ?)
            """,
            (_id("audit"), created_by, project_id, version_id, json.dumps({"series_id": series_id, "version": version, "membership_source": source, "member_count": len(members)}), now),
        )
    result = get_series(database_path, project_id, series_id)
    if result is None:
        raise SeriesNotFoundError("Series version could not be reloaded")
    return result
