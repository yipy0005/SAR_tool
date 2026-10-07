"""MCP server that lets LLM clients (for example Kiro) use the SAR Workbench web app.

The server is a thin, permission-tiered client of the app's own HTTP API: it signs in
like a browser, so project membership, CSRF protection and audit logging all still apply.
Standard library only; no new dependencies.
"""
from __future__ import annotations

__version__ = "0.1.0"
SERVER_NAME = "sar-workbench"
