"""ChemBioCatalyst portal single sign-on boundary (SAR_AUTH_MODE=portal).

The portal authenticates the user and gates which apps they may open. This
module performs the two server-to-server calls the workbench needs before it
creates a local session:

1. ``POST {PORTAL_URL}/sso/api/validate`` exchanges the single-use token from
   the redirect for the portal user's identity.
2. ``POST {PORTAL_URL}/sso/api/check-access`` confirms the user is on the
   portal's access list for this app. Portal tokens are not bound to a target
   app, so without this check a token issued for another app could be replayed
   here.

Both calls fail closed: any transport error, non-200 status, or malformed body
denies access. Stdlib HTTP is used so no extra dependency is pinned; TLS trust
comes from ``REQUESTS_CA_BUNDLE`` / ``SSL_CERT_FILE`` (the platform's
self-signed certificate in production) or the system store otherwise.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urlparse

DEFAULT_APP_NAME = "sar"
REQUEST_TIMEOUT_SECONDS = 5


class PortalUnavailableError(RuntimeError):
    """The portal could not be reached or returned an unusable response."""


def portal_url() -> str:
    return os.environ.get("PORTAL_URL", "").strip().rstrip("/")


def portal_app_name() -> str:
    return os.environ.get("SAR_PORTAL_APP_NAME", DEFAULT_APP_NAME).strip().lower() or DEFAULT_APP_NAME


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or None
    return ssl.create_default_context(cafile=cafile)


def _post_json(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    base = portal_url()
    if not base:
        raise PortalUnavailableError("PORTAL_URL is not configured")
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    context = _ssl_context() if base.startswith("https://") else None
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS, context=context) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PortalUnavailableError(f"Portal request failed: {exc.__class__.__name__}") from exc
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortalUnavailableError("Portal returned a non-JSON response") from exc
    return status, data if isinstance(data, dict) else {}


def validate_token(token: str) -> str | None:
    """Return the portal user's normalised email for a valid token, else None."""
    if not token or len(token) > 512:
        return None
    status, data = _post_json("/sso/api/validate", {"token": token})
    if status != 200 or data.get("success") is not True:
        return None
    user = data.get("user")
    email = str(user.get("email", "")).strip().lower() if isinstance(user, dict) else ""
    return email if "@" in email else None


def has_app_access(email: str) -> bool:
    """True only when the portal explicitly allows this email for this app."""
    status, data = _post_json("/sso/api/check-access", {"app_name": portal_app_name(), "email": email})
    return status == 200 and data.get("allowed") is True


_ACCESS_CACHE: dict[str, float] = {}
_ACCESS_CACHE_LOCK = threading.Lock()


def access_recheck_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("SAR_TOKEN_PORTAL_RECHECK_SECONDS", "300")))
    except ValueError:
        return 300.0


def has_app_access_cached(email: str) -> bool:
    """Portal access check for API-token requests, remembering an *allowed* answer briefly.

    Tokens outlive a browser session, so the portal access list is re-checked while they are used;
    removing someone from the list stops their tokens within ``SAR_TOKEN_PORTAL_RECHECK_SECONDS``
    (default five minutes). Denials and portal errors are never cached, so a failure cannot lock
    anyone in. Raises PortalUnavailableError when the portal cannot answer.
    """
    key = email.strip().lower()
    now = time.monotonic()
    with _ACCESS_CACHE_LOCK:
        if _ACCESS_CACHE.get(key, 0.0) > now:
            return True
    allowed = has_app_access(key)
    with _ACCESS_CACHE_LOCK:
        if allowed:
            _ACCESS_CACHE[key] = now + access_recheck_seconds()
        else:
            _ACCESS_CACHE.pop(key, None)
    return allowed


def clear_access_cache() -> None:
    with _ACCESS_CACHE_LOCK:
        _ACCESS_CACHE.clear()


def safe_next_path(value: str | None) -> str:
    """Accept only same-origin relative paths for post-login redirects."""
    candidate = str(value or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/"
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"
    return candidate


def portal_login_url(next_path: str = "/") -> str:
    """Portal entry point that issues a fresh token and returns the user here."""
    return f"{portal_url()}/sso/generate/{portal_app_name()}?next={quote(safe_next_path(next_path), safe='')}"


def portal_logout_url() -> str:
    return f"{portal_url()}/auth/global-logout"
