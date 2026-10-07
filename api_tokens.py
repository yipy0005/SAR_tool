"""Per-user API tokens for non-interactive clients (the MCP server).

A token stands for one user and carries exactly that user's project memberships; it is never
broader than the person who created it. Only a SHA-256 hash is stored (tokens are 256-bit random
values, so a fast hash is sufficient and lookups are exact). The plaintext is returned once, at
creation, and is never logged or stored.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from database import read_connection, transaction

TOKEN_PREFIX = "sarpat_"
DEFAULT_LIFETIME_DAYS = 30
MAX_LIFETIME_DAYS = 90
MAX_ACTIVE_TOKENS_PER_USER = 5
MAX_NAME_LENGTH = 60
# last_used_at is a convenience for the owner, so it is refreshed at most this often. Writing on
# every request would add a SQLite write (single writer) to each API call.
LAST_USED_RESOLUTION_SECONDS = 300


class TokenError(ValueError):
    """A token request that cannot be honoured. The message is safe to show to the user."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_token(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX) and 20 <= len(value) <= 200 and value.isascii() and not any(c.isspace() for c in value)


def _active_count(connection: Any, user_id: str, now: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?",
        (user_id, now),
    ).fetchone()
    return int(row["n"])


def create_token(database_path: str, user_id: str, name: str, lifetime_days: int | None = None) -> tuple[dict[str, Any], str]:
    """Create a token for ``user_id``. Returns (public record, plaintext token)."""
    label = " ".join(str(name or "").split())
    if not label:
        raise TokenError("Give the token a name so you can recognise it later, for example 'Kiro on my laptop'.")
    if len(label) > MAX_NAME_LENGTH:
        raise TokenError(f"Keep the token name to {MAX_NAME_LENGTH} characters or fewer.")
    days = DEFAULT_LIFETIME_DAYS if lifetime_days is None else lifetime_days
    if not 1 <= days <= MAX_LIFETIME_DAYS:
        raise TokenError(f"Choose a lifetime between 1 and {MAX_LIFETIME_DAYS} days.")
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    record = {
        "id": f"tok_{uuid.uuid4().hex}",
        "name": label,
        "token_prefix": plaintext[: len(TOKEN_PREFIX) + 5],
        "created_at": _stamp(now),
        "expires_at": _stamp(now + timedelta(days=days)),
    }
    with transaction(database_path) as connection:
        if _active_count(connection, user_id, record["created_at"]) >= MAX_ACTIVE_TOKENS_PER_USER:
            raise TokenError(
                f"You already have {MAX_ACTIVE_TOKENS_PER_USER} active tokens. Revoke one you no longer use, then try again."
            )
        connection.execute(
            """
            INSERT INTO api_tokens (id, user_id, name, token_prefix, token_hash, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (record["id"], user_id, label, record["token_prefix"], hash_token(plaintext), record["created_at"], record["expires_at"]),
        )
    return record, plaintext


def list_tokens(database_path: str, user_id: str) -> list[dict[str, Any]]:
    now = _stamp(_now())
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, name, token_prefix, created_at, expires_at, last_used_at, revoked_at
            FROM api_tokens WHERE user_id = ? ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["status"] = "revoked" if item["revoked_at"] else ("expired" if item["expires_at"] <= now else "active")
        result.append(item)
    return result


def revoke_token(database_path: str, user_id: str, token_id: str) -> bool:
    """Revoke one of the user's own tokens. Returns False when there is nothing to revoke."""
    with transaction(database_path) as connection:
        cursor = connection.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (_stamp(_now()), token_id, user_id),
        )
        return bool(cursor.rowcount)


def authenticate_token(database_path: str, token: str) -> str | None:
    """Return the owner's email when ``token`` is valid, else None. Inactive users never authenticate."""
    if not looks_like_token(token):
        return None
    now = _now()
    with read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT t.id, t.expires_at, t.revoked_at, t.last_used_at, u.email, u.is_active
            FROM api_tokens AS t JOIN users AS u ON u.id = t.user_id
            WHERE t.token_hash = ?
            """,
            (hash_token(token),),
        ).fetchone()
    if row is None or row["revoked_at"] or not row["is_active"] or row["expires_at"] <= _stamp(now):
        return None
    stale_before = _stamp(now - timedelta(seconds=LAST_USED_RESOLUTION_SECONDS))
    if not row["last_used_at"] or row["last_used_at"] < stale_before:
        with transaction(database_path) as connection:
            connection.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (_stamp(now), row["id"]))
    return str(row["email"]).strip().lower()
