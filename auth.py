from __future__ import annotations

import hmac
import os
import secrets
from typing import Any

from flask import Request, session
from werkzeug.security import check_password_hash


class AuthenticationError(ValueError):
    pass


class CSRFError(ValueError):
    pass


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf(request: Request) -> None:
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token")
    if not supplied and request.is_json:
        payload: Any = request.get_json(silent=True) or {}
        supplied = payload.get("csrf_token") if isinstance(payload, dict) else None
    if not expected or not supplied or not hmac.compare_digest(str(expected), str(supplied)):
        raise CSRFError("Invalid or missing CSRF token")


def configured_credentials() -> tuple[str, str]:
    email = os.environ.get("SAR_AUTH_EMAIL", "").strip().lower()
    password_hash = os.environ.get("SAR_AUTH_PASSWORD_HASH", "").strip()
    return email, password_hash


def authenticate(email: str, password: str) -> bool:
    configured_email, password_hash = configured_credentials()
    if not configured_email or not password_hash:
        return False
    if not hmac.compare_digest(email.strip().lower(), configured_email):
        return False
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def login(email: str) -> None:
    session.clear()
    session["authenticated"] = True
    session["user_email"] = email.strip().lower()
    csrf_token()


def logout() -> None:
    session.clear()


def is_authenticated() -> bool:
    return bool(session.get("authenticated") and session.get("user_email"))
