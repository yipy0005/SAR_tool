"""HTTP client for the SAR Workbench API. It signs in the way a browser does (form login, session
cookie, CSRF header), so the app's own project membership and audit rules apply to every call."""
from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__
from .config import Settings

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_LOGIN_FAILURES = 3
_CSRF_PATTERNS = (
    re.compile(r'name="_csrf_token"\s+value="([^"]+)"'),
    re.compile(r'name="csrf-token"\s+content="([^"]+)"'),
)


class SarError(Exception):
    """Base class. Messages are written for the model and the user, and never contain secrets."""


class SarConnectionError(SarError):
    pass


class SarAuthError(SarError):
    pass


class SarApiError(SarError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"HTTP {status} {code}: {message}" if message else f"HTTP {status} {code}")
        self.status = status
        self.code = code
        self.message = message


class _SameOriginRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the configured origin, so cookies and tokens never travel elsewhere."""

    def __init__(self, origin: str) -> None:
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        target = urllib.parse.urlsplit(resolved)
        if f"{target.scheme}://{target.netloc}" != self._origin:
            raise urllib.error.HTTPError(req.full_url, code, "Redirect to another origin refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def seg(value: Any) -> str:
    """One URL path segment, percent-encoded so an identifier can never add '/' or '..' to the path."""
    return urllib.parse.quote(str(value), safe="")


class SarClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar), _SameOriginRedirects(settings.base_url)
        )
        self._csrf = ""
        self._auth_required: bool | None = None
        self._signed_in = False
        self._login_failures = 0
        self.user_email = ""

    # -- low level -------------------------------------------------------------------------
    def _send(self, method: str, path: str, *, query=None, body=None, form=None, timeout=None) -> tuple[int, str, str]:
        problem = self.settings.transport_problem()
        if problem:
            raise SarConnectionError(problem)
        url = self.settings.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        headers = {"Accept": "application/json, text/html;q=0.5", "User-Agent": f"sar-mcp/{__version__}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if method != "GET" and self._csrf:
            headers["X-CSRF-Token"] = self._csrf
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout or self.settings.timeout) as response:
                status, kind, raw = response.status, response.headers.get("Content-Type", ""), response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status, kind, raw = error.code, error.headers.get("Content-Type", "") if error.headers else "", error.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.URLError as error:
            raise SarConnectionError(
                f"Cannot reach the SAR Workbench at {self.settings.base_url} ({error.reason}). "
                "Start it with `pixi run start-local`, or set SAR_MCP_BASE_URL."
            ) from error
        except TimeoutError as error:
            raise SarConnectionError(f"The SAR Workbench did not answer within {timeout or self.settings.timeout:g} s.") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SarApiError(status, "response_too_large", "The response is larger than 16 MiB; narrow the request.")
        return status, kind, raw.decode("utf-8", errors="replace")

    # -- session ---------------------------------------------------------------------------
    @staticmethod
    def _find_token(html: str) -> str:
        for pattern in _CSRF_PATTERNS:
            match = pattern.search(html)
            if match:
                return match.group(1)
        return ""

    def _probe_auth(self) -> None:
        """Production mode serves a login form; local/test mode redirects /login straight to the app."""
        status, _kind, text = self._send("GET", "/login")
        self._auth_required = status == 200 and 'name="password"' in text and bool(self._find_token(text))

    def _login(self) -> None:
        if not self.settings.has_credentials:
            raise SarAuthError(
                "The SAR Workbench requires sign-in but no credentials are configured. "
                "Run `pixi run mcp-credentials` once (stores them in a private file), then restart the MCP server."
            )
        if self._login_failures >= MAX_LOGIN_FAILURES:
            raise SarAuthError("Sign-in failed repeatedly, so the MCP server stopped trying. Fix the credentials and restart it.")
        self._csrf = ""
        _status, _kind, page = self._send("GET", "/login")
        token = self._find_token(page)
        if not token:
            raise SarAuthError("The sign-in page did not provide a CSRF token; is SAR_MCP_BASE_URL the SAR Workbench?")
        status, _kind, _text = self._send(
            "POST", "/login", form={"email": self.settings.email, "password": self.settings.password, "_csrf_token": token}
        )
        if status >= 400:
            self._login_failures += 1
            raise SarAuthError("Email or password was not accepted by the SAR Workbench.")
        # Signing in rotates the session, so read the new token from an authenticated page.
        _status, _kind, page = self._send("GET", "/workspace/new")
        self._csrf = self._find_token(page)
        if not self._csrf:
            self._login_failures += 1
            raise SarAuthError("Signed in, but the session did not provide a CSRF token.")
        self._login_failures = 0
        self._signed_in = True
        self.user_email = self.settings.email

    def ensure_session(self) -> None:
        if self._auth_required is None:
            self._probe_auth()
        if self._auth_required and not self._signed_in:
            self._login()

    # -- public API ------------------------------------------------------------------------
    def request(self, method: str, path: str, *, query=None, body=None, long: bool = False) -> Any:
        """Call the API and return parsed JSON. Raises SarApiError for HTTP errors (status >= 400)."""
        timeout = max(self.settings.timeout, 180.0) if long else self.settings.timeout
        for attempt in (1, 2):
            self.ensure_session()
            status, kind, text = self._send(method, path, query=query, body=body, timeout=timeout)
            payload: Any = None
            if "json" in kind:
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = None
            expired = (status == 401 and (payload or {}).get("error") == "authentication_required") or (
                status == 400 and (payload or {}).get("error") == "csrf_failed"
            )
            if expired and self._auth_required and attempt == 1:
                # The request was rejected before it ran, so signing in again and repeating it is safe.
                self._signed_in = False
                continue
            break
        if status >= 400:
            info = payload if isinstance(payload, dict) else {}
            raise SarApiError(status, str(info.get("error") or "http_error"), str(info.get("message") or ""))
        return payload if payload is not None else {"text": text}

    def get(self, path: str, **query: Any) -> Any:
        return self.request("GET", path, query=query or None)

    def post(self, path: str, body: dict, *, long: bool = False) -> Any:
        return self.request("POST", path, body=body, long=long)
