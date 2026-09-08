from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import session

from database import read_connection, transaction


ROLE_RANK = {
    "viewer": 0,
    "scientist": 1,
    "editor": 2,
    "owner": 3,
    "admin": 4,
}
VALID_PROJECT_ROLES = frozenset(ROLE_RANK)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def session_email() -> str | None:
    email = str(session.get("user_email", "")).strip().lower()
    return email or None


def current_user_id(database_path: str) -> str | None:
    email = session_email()
    if not email:
        return None
    with read_connection(database_path) as connection:
        row = connection.execute(
            "SELECT id FROM users WHERE lower(email) = ? AND is_active = 1",
            (email,),
        ).fetchone()
    return row["id"] if row else None


def ensure_local_user(database_path: str, email: str) -> str | None:
    normalized_email = str(email).strip().lower()
    if not normalized_email:
        return None
    now = _now()
    with transaction(database_path) as connection:
        row = connection.execute(
            "SELECT id, is_active FROM users WHERE lower(email) = ?",
            (normalized_email,),
        ).fetchone()
        if row:
            return row["id"] if row["is_active"] else None
        user_id = _id("user")
        connection.execute(
            """
            INSERT INTO users (id, email, display_name, role, is_active, created_at)
            VALUES (?, ?, ?, 'scientist', 1, ?)
            """,
            (user_id, normalized_email, normalized_email.split("@", 1)[0], now),
        )
        return user_id


def project_role(database_path: str, project_id: str, user_id: str | None = None) -> str | None:
    resolved_user_id = user_id or current_user_id(database_path)
    if not resolved_user_id:
        return None
    with read_connection(database_path) as connection:
        row = connection.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, resolved_user_id),
        ).fetchone()
    return str(row["role"]).lower() if row else None


def has_project_access(
    database_path: str,
    project_id: str,
    *,
    minimum_role: str = "viewer",
    user_id: str | None = None,
) -> bool:
    required_rank = ROLE_RANK.get(minimum_role)
    if required_rank is None:
        raise ValueError(f"Unknown project role: {minimum_role}")
    role = project_role(database_path, project_id, user_id)
    return role in ROLE_RANK and ROLE_RANK[role] >= required_rank


def accessible_projects(database_path: str, user_id: str | None = None) -> list[dict[str, object]]:
    resolved_user_id = user_id or current_user_id(database_path)
    if not resolved_user_id:
        return []
    with read_connection(database_path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT p.*
                FROM projects AS p
                JOIN project_members AS pm ON pm.project_id = p.id
                WHERE pm.user_id = ?
                ORDER BY p.created_at DESC
                """,
                (resolved_user_id,),
            )
        ]


def add_project_member(
    database_path: str,
    project_id: str,
    email: str,
    *,
    display_name: str = "",
    role: str = "viewer",
) -> dict[str, object]:
    normalized_email = str(email).strip().lower()
    normalized_role = str(role).strip().lower()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("A valid member email is required")
    if normalized_role not in VALID_PROJECT_ROLES:
        raise ValueError(f"Unsupported project role: {normalized_role}")
    now = _now()
    with transaction(database_path) as connection:
        user = connection.execute(
            "SELECT id, is_active FROM users WHERE lower(email) = ?",
            (normalized_email,),
        ).fetchone()
        if user and not user["is_active"]:
            raise ValueError("The requested user is inactive")
        if user:
            user_id = user["id"]
        else:
            user_id = _id("user")
            connection.execute(
                """
                INSERT INTO users (id, email, display_name, role, is_active, created_at)
                VALUES (?, ?, ?, 'scientist', 1, ?)
                """,
                (user_id, normalized_email, display_name.strip() or normalized_email.split("@", 1)[0], now),
            )
        connection.execute(
            """
            INSERT INTO project_members (project_id, user_id, role, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, user_id) DO UPDATE SET role = excluded.role
            """,
            (project_id, user_id, normalized_role, now),
        )
        row = connection.execute(
            """
            SELECT u.id AS user_id, u.email, u.display_name, pm.role
            FROM users AS u
            JOIN project_members AS pm ON pm.user_id = u.id
            WHERE pm.project_id = ? AND pm.user_id = ?
            """,
            (project_id, user_id),
        ).fetchone()
    return dict(row)


def list_project_members(database_path: str, project_id: str) -> list[dict[str, object]]:
    with read_connection(database_path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT u.id AS user_id, u.email, u.display_name, pm.role, pm.created_at
                FROM users AS u
                JOIN project_members AS pm ON pm.user_id = u.id
                WHERE pm.project_id = ?
                ORDER BY pm.role DESC, lower(u.email)
                """,
                (project_id,),
            )
        ]
