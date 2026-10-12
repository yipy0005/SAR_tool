"""Settings for the MCP server: environment variables plus an optional credentials file."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

MODES = ("read-only", "analyze", "full")
DEFAULT_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_MAX_UPLOAD_BYTES = 5_000_000
JSON_ENVELOPE_BYTES = 65_536

def json_wire_limit(decoded_bytes: int) -> int:
    return 4 * ((int(decoded_bytes) + 2) // 3) + JSON_ENVELOPE_BYTES
DEFAULT_CREDENTIALS_FILE = "~/.config/sar-workbench/mcp.env"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ConfigError(ValueError):
    """Unusable configuration. The message is safe to show to the user (it never holds a secret)."""


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def read_credentials_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Refuses a file that other users can read or that someone else owns."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return {}
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{path} can be read by other users. Run: chmod 600 {path}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ConfigError(f"{path} is owned by another user; refusing to read credentials from it.")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            raise ConfigError("credential file contains a malformed line")
        key, _, value = text.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if any(character in value for character in "\r\n"):
            raise ConfigError("credential values may not contain line breaks")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError("credential file contains malformed quoted values")
            if value[0] == '"':
                try:
                    value = json.loads(value)
                except (TypeError, ValueError) as exc:
                    raise ConfigError("credential file contains malformed quoted values") from exc
            else:
                value = value[1:-1].replace("\\'", "'").replace("\\\\", "\\")
            if not isinstance(value, str) or any(character in value for character in "\r\n"):
                raise ConfigError("credential values may not contain line breaks")
        values[key] = value
    return values


def _origin(value: str, name: str):
    """Parse an address that must be an origin only; returns the split result."""
    parts = urlsplit(value.strip().rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigError(f"{name} must look like http://127.0.0.1:5001")
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ConfigError(f"{name} must be an origin only (scheme, host and port), with no login, path or query.")
    return parts


def _bounded(environ: Mapping[str, str], name: str, default: float, low: float, high: float, cast=float):
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number.") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low:g} and {high:g}.")
    return value


@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    mode: str = "analyze"
    email: str = ""
    password: str = ""
    timeout: float = 60.0
    max_chars: int = 40_000
    allow_insecure_http: bool = False
    project_ids: frozenset = frozenset()
    log_level: str = "warning"
    public_url: str = ""
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    json_request_bytes: int = 0

    @property
    def link_origin(self) -> str:
        """Address put into links: what your browser uses, which may differ from the address this server calls."""
        return self.public_url or self.base_url

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()

    @property
    def has_credentials(self) -> bool:
        return bool(self.email and self.password)

    def transport_problem(self) -> str | None:
        """Why requests must not be sent, or None. Patient data and passwords never cross plain HTTP off-host."""
        if urlsplit(self.base_url).scheme == "http" and self.host not in LOOPBACK_HOSTS and not self.allow_insecure_http:
            return (
                f"Refusing to talk to {self.base_url} over plain HTTP: credentials and project data would be sent "
                "unencrypted. Use an https:// URL, or set SAR_MCP_ALLOW_INSECURE_HTTP=true on a trusted network."
            )
        return None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        parts = _origin(env.get("SAR_MCP_BASE_URL") or DEFAULT_BASE_URL, "SAR_MCP_BASE_URL")
        public = env.get("SAR_MCP_PUBLIC_URL")
        public_url = ""
        if public and public.strip():
            shown = _origin(public, "SAR_MCP_PUBLIC_URL")
            public_url = f"{shown.scheme}://{shown.netloc}"
        mode = (env.get("SAR_MCP_MODE") or "analyze").strip().lower()
        if mode not in MODES:
            raise ConfigError(f"SAR_MCP_MODE must be one of: {', '.join(MODES)}.")
        email = (env.get("SAR_MCP_EMAIL") or "").strip().lower()
        password = env.get("SAR_MCP_PASSWORD") or ""
        if not (email and password):
            location = Path(env.get("SAR_MCP_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser()
            stored = read_credentials_file(location)
            email = email or stored.get("SAR_MCP_EMAIL", "").strip().lower()
            password = password or stored.get("SAR_MCP_PASSWORD", "")
        projects = frozenset(item.strip() for item in (env.get("SAR_MCP_PROJECT_IDS") or "").split(",") if item.strip())
        max_upload = int(env.get("SAR_MAX_UPLOAD_BYTES") or DEFAULT_MAX_UPLOAD_BYTES)
        wire_limit = int(env.get("SAR_MAX_JSON_REQUEST_BYTES") or json_wire_limit(max_upload))
        return cls(
            base_url=f"{parts.scheme}://{parts.netloc}",
            mode=mode,
            email=email,
            password=password,
            timeout=_bounded(env, "SAR_MCP_TIMEOUT", 60.0, 5.0, 600.0),
            max_chars=_bounded(env, "SAR_MCP_MAX_CHARS", 40_000, 2_000, 200_000, cast=int),
            allow_insecure_http=_truthy(env.get("SAR_MCP_ALLOW_INSECURE_HTTP")),
            project_ids=projects,
            log_level=(env.get("SAR_MCP_LOG_LEVEL") or "warning").strip().lower(),
            public_url=public_url,
            max_upload_bytes=max_upload,
            json_request_bytes=wire_limit,
        )
