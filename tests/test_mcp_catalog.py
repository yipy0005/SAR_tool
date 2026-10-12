from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from sar_mcp.catalog import EXPECTED_COUNTS, EXPECTED_FINGERPRINT, SCHEMA_VERSION, catalog_counts, catalog_fingerprint
from sar_mcp.tools import all_tools


def test_catalog_counts_fingerprint_and_schema_are_pinned():
    tools = all_tools()
    assert SCHEMA_VERSION == 22
    assert catalog_counts(tools) == EXPECTED_COUNTS == {"read-only": 17, "analyze": 22, "full": 28}
    assert catalog_fingerprint(tools) == EXPECTED_FINGERPRINT


def test_documented_catalog_contract_matches_runtime():
    root = Path(__file__).parents[1]
    documents = [(root / "README.md").read_text(encoding="utf-8"), (root / "docs" / "MCP_GUIDE.md").read_text(encoding="utf-8")]
    for document in documents:
        assert "schema 22" in document
        assert "17 read-only, 22 analyze, and 28 full" in document or "| `read-only` |" in document
        assert EXPECTED_FINGERPRINT in document
        assert "ea1141804f5a7f251b9a7409b1fe968380a1cccf66b700ca72717cf8caf5ffd0" not in document


def test_documented_test_count_matches_pytest_collection():
    root = Path(__file__).parents[1]
    documents = [(root / "README.md").read_text(encoding="utf-8"), (root / "docs" / "MCP_GUIDE.md").read_text(encoding="utf-8")]
    markers = [re.findall(r"<!-- sar-workbench-test-count: (\d+) -->", document) for document in documents]
    assert all(len(marker) == 1 for marker in markers) and markers[0] == markers[1]
    collection = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"(\d+) tests? collected", collection.stdout + collection.stderr)
    assert match and int(markers[0][0]) == int(match.group(1))


def test_side_effect_descriptors_match_approval_boundary():
    tools = {tool.name: tool for tool in all_tools()}
    assert tools["sar_export_overview"].descriptor()["_meta"]["sar.workbench"]["side_effect"] == "audit"
    assert tools["sar_compare_endpoints"].descriptor()["_meta"]["sar.workbench"]["side_effect"] == "none"
    assert tools["sar_export_overview"].descriptor()["annotations"]["readOnlyHint"] is False
