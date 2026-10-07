#!/usr/bin/env python3
"""Entry point for the SAR Workbench MCP server (stdio).

Run by an MCP client such as Kiro; see docs/MCP_GUIDE.md. Useful commands:
    python sar_mcp_server.py --check              test connection and sign-in
    python sar_mcp_server.py --save-credentials   store the web-app login privately
    python sar_mcp_server.py --print-kiro-config  print an mcp.json entry
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sar_mcp.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
