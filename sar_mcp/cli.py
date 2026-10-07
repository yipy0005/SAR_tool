"""Command line: serve over stdio (default), --check, --save-credentials, --print-kiro-config."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .client import SarClient, SarError
from .config import DEFAULT_BASE_URL, DEFAULT_CREDENTIALS_FILE, MODES, ConfigError, Settings, valid_token_format
from .server import McpServer
from .tools import all_tools

SCRIPT = Path(__file__).resolve().parent.parent / "sar_mcp_server.py"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sar_mcp_server.py", description="MCP server for the SAR Workbench (stdio).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="test the connection, sign-in and permissions, then exit")
    group.add_argument("--save-credentials", action="store_true", help="store the web-app login in a private (chmod 600) file")
    group.add_argument("--save-token", action="store_true", help="store a SAR Workbench API token in a private (chmod 600) file")
    group.add_argument("--print-kiro-config", action="store_true", help="print an mcp.json entry for Kiro with absolute paths")
    parser.add_argument("--mode", choices=MODES, help="permission mode for --print-kiro-config (default analyze)")
    parser.add_argument("--ca-bundle", help="path to the server certificate file, for --print-kiro-config (self-signed servers)")
    parser.add_argument("--base-url", help="web app address for --print-kiro-config (default %s)" % DEFAULT_BASE_URL)
    return parser


def kiro_config(mode: str = "analyze", base_url: str = DEFAULT_BASE_URL, ca_bundle: str = "") -> dict:
    """An mcpServers entry. Secrets are never written here; credentials come from the private file."""
    # Read tools can be approved once; anything that stores or changes data (or writes an audit record) asks each time.
    env = {"SAR_MCP_BASE_URL": base_url, "SAR_MCP_MODE": mode}
    if ca_bundle:
        env["SAR_MCP_CA_BUNDLE"] = str(Path(ca_bundle).expanduser().resolve())
    quiet = [tool.name for tool in all_tools() if tool.tier == "read" and tool.name != "sar_export_overview"]
    return {
        "mcpServers": {
            "sar-workbench": {
                "command": sys.executable,
                "args": [str(SCRIPT)],
                "env": env,
                "autoApprove": quiet,
                "disabled": False,
            }
        }
    }


def check() -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as error:
        print(f"Configuration problem: {error}")
        return 2
    print(f"SAR Workbench MCP server {__version__}")
    print(f"  web app     {settings.base_url}")
    print(f"  mode        {settings.mode}")
    if settings.has_token:
        sign_in = "API token"
    elif settings.has_credentials:
        sign_in = "found for " + settings.email
    else:
        sign_in = "not configured"
    print(f"  credentials {sign_in}")
    if settings.ca_bundle:
        print(f"  certificate {settings.ca_bundle}")
    print(f"  projects    {', '.join(sorted(settings.project_ids)) if settings.project_ids else 'all you can access'}")
    client = SarClient(settings)
    try:
        ready = client.get("/api/v1/readyz")
        client.ensure_session()
        projects = client.get("/api/v1/projects").get("projects") or []
    except SarError as error:
        print(f"  PROBLEM: {error}")
        return 1
    print(f"  app         ready (schema {ready.get('schema_version')}), sign-in {'required' if client._auth_required else 'not required'}")
    print(f"  signed in   {client.user_email or 'n/a'}; {len(projects)} project(s) visible")
    print("  OK")
    return 0


def save_credentials() -> int:
    target = Path(os.environ.get("SAR_MCP_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser()
    email = input("SAR Workbench login email: ").strip().lower()
    password = getpass.getpass("Password (not shown, stored only in the private file): ")
    if not email or not password:
        print("Nothing saved: both email and password are needed.")
        return 2
    if any(ch in email + password for ch in "\r\n"):
        print("Nothing saved: line breaks cannot be stored in the credentials file.")
        return 2
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f'SAR_MCP_EMAIL="{email}"\nSAR_MCP_PASSWORD="{password}"\n')
    os.chmod(target, 0o600)
    print(f"Saved to {target} (owner-only). Checking the sign-in...")
    return check()


def save_token() -> int:
    target = Path(os.environ.get("SAR_MCP_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser()
    token = getpass.getpass("SAR Workbench API token (not shown, stored only in the private file): ").strip()
    if not valid_token_format(token):
        print("Nothing saved: that does not look like a SAR Workbench API token (it starts with sarpat_).")
        return 2
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f'SAR_MCP_TOKEN="{token}"\n')
    os.chmod(target, 0o600)
    if not (os.environ.get("SAR_MCP_BASE_URL") or "").strip():
        # The server address is chosen in the next step; checking the default local address now would
        # only report a misleading problem.
        print(f"Saved to {target} (owner-only). Next: print your Kiro entry with --print-kiro-config (see the setup guide).")
        return 0
    print(f"Saved to {target} (owner-only). Checking the connection...")
    return check()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.print_kiro_config:
        print(json.dumps(kiro_config(args.mode or "analyze", args.base_url or DEFAULT_BASE_URL, args.ca_bundle or ""), indent=2))
        return 0
    if args.save_token:
        return save_token()
    if args.save_credentials:
        return save_credentials()
    if args.check:
        return check()
    try:
        settings = Settings.from_env()
    except ConfigError as error:
        print(f"sar-mcp configuration problem: {error}", file=sys.stderr)
        return 2
    level = getattr(logging, settings.log_level.upper(), logging.WARNING)
    logging.basicConfig(stream=sys.stderr, level=level, format="%(asctime)s sar-mcp %(levelname)s %(message)s")
    McpServer(settings).serve()
    return 0
