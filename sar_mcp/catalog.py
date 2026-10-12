"""Derived MCP catalog metadata used by checks and generated configuration."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .toolkit import Tool

SCHEMA_VERSION = 22
EXPECTED_COUNTS = {"read-only": 17, "analyze": 22, "full": 28}
EXPECTED_FINGERPRINT = "109a86ce9b7ab797d24c45f0d6c7b5b2f37f91e5ca016f588ea82e94a614174b"


def catalog_counts(tools: Iterable[Tool]) -> dict[str, int]:
    rows = list(tools)
    return {mode: sum(tool.available_in(mode) for tool in rows) for mode in ("read-only", "analyze", "full")}


def catalog_fingerprint(tools: Iterable[Tool]) -> str:
    descriptors = [tool.descriptor() for tool in tools]
    payload = json.dumps(descriptors, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()
