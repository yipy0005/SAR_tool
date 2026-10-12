"""MCP server: protocol, schema validation, output shaping, settings and permission tiers (no web app)."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app import create_app
from config import base64_wire_bytes
from sar_mcp import cli as mcp_cli
from sar_mcp.cli import kiro_config
from sar_mcp.client import SarApiError, _SameOriginRedirects
from sar_mcp.config import ConfigError, Settings, read_credentials_file
from sar_mcp.schema import ID_PATTERN, validate
from sar_mcp.server import SUPPORTED_VERSIONS, McpServer
from sar_mcp.shaping import fit, strip_drawings
from sar_mcp.tools import all_tools

NO_FILE = {"SAR_MCP_CREDENTIALS_FILE": "/nonexistent/sar-mcp.env"}


def settings(**env):
    return Settings.from_env({**NO_FILE, **env})


class FakeClient:
    """Stands in for SarClient: records calls and returns canned payloads."""

    def __init__(self, payloads=None):
        self.calls = []
        self.payloads = payloads or {}
        self._auth_required = False
        self.user_email = ""

    def get(self, path, **query):
        self.calls.append(("GET", path, query))
        return self.payloads.get(path, {})

    def post(self, path, body, long=False):
        self.calls.append(("POST", path, body))
        return self.payloads.get(path, {})

    def ensure_session(self):
        pass


def rpc(server, method, params=None, id=1):
    message = {"jsonrpc": "2.0", "method": method, **({"id": id} if id is not None else {}), **({"params": params} if params is not None else {})}
    return server.handle(message)


def call(server, name, arguments):
    reply = rpc(server, "tools/call", {"name": name, "arguments": arguments})
    return reply["error"] if "error" in reply else reply["result"]


def test_initialize_negotiates_version_and_declares_only_tools():
    server = McpServer(settings(), FakeClient())
    newest = rpc(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "kiro"}})["result"]
    assert newest["protocolVersion"] == "2025-06-18" and set(newest["capabilities"]) == {"tools"}
    assert newest["serverInfo"]["name"] == "sar-workbench" and "causation" in newest["instructions"]
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == SUPPORTED_VERSIONS[0]
    assert rpc(server, "ping")["result"] == {}


def test_notifications_get_no_reply_unknown_methods_error_and_batches_work():
    server = McpServer(settings(), FakeClient())
    assert rpc(server, "notifications/initialized", id=None) is None
    assert rpc(server, "notifications/cancelled", {"requestId": 3}, id=None) is None
    assert rpc(server, "no/such")["error"]["code"] == -32601
    assert server.handle({"jsonrpc": "2.0", "id": 5})["error"]["code"] == -32600
    assert rpc(server, "prompts/list")["result"] == {"prompts": []}
    batch = server.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert len(batch) == 1 and batch[0]["id"] == 1


def test_stdio_loop_survives_bad_lines_and_keeps_stdout_clean():
    server = McpServer(settings(), FakeClient())
    lines = 'not json\n\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    out = io.StringIO()
    server.serve(io.StringIO(lines), out)
    frames = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [frame.get("id") for frame in frames] == [None, 1, 2]
    assert frames[0]["error"]["code"] == -32700 and len(frames[2]["result"]["tools"]) == 22


@pytest.mark.parametrize("mode,count", [("read-only", 17), ("analyze", 22), ("full", 28)])
def test_modes_show_only_the_permitted_tools(mode, count):
    server = McpServer(settings(SAR_MCP_MODE=mode), FakeClient())
    listed = {tool["name"] for tool in rpc(server, "tools/list")["result"]["tools"]}
    assert len(listed) == count
    assert ("sar_run_analysis" in listed) == (mode != "read-only")
    assert ("sar_import_commit" in listed) == (mode == "full")
    hidden = next(iter(server.hidden), None)
    if hidden:
        error = call(server, hidden, {})
        assert error["code"] == -32602 and mode in error["message"]
    assert call(server, "sar_nonexistent", {})["code"] == -32602


def test_review_gates_are_never_exposed():
    names = {tool.name for tool in all_tools()}
    tokens = {part for name in names for part in name.split("_")}
    # Whole words only: "preview" is fine, "review" (approving generated recommendations) is not.
    assert not tokens & {"review", "approve", "reject", "claim", "claims", "member", "members", "design", "designs", "hypothesis"}
    assert "sar_list_records" in names  # reading saved claims, designs and recommendations is allowed; changing them is not
    train = next(tool for tool in all_tools() if tool.name == "sar_train_prediction_model")
    assert set(train.properties) == {"project_id", "endpoint_key", "holdout_compounds"}  # no qualification thresholds


def test_descriptors_are_well_formed():
    for tool in all_tools():
        descriptor = tool.descriptor()
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", descriptor["name"])
        schema = descriptor["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema.get("required", [])) <= set(schema["properties"])
        assert len(descriptor["description"]) > 40
        assert descriptor["annotations"]["readOnlyHint"] is (tool.tier == "read" and tool.side_effect == "none")
        if tool.tier != "read":
            assert descriptor["annotations"]["idempotentHint"] is False


def test_invalid_arguments_come_back_as_tool_errors_the_model_can_fix():
    server = McpServer(settings(), FakeClient())
    result = call(server, "sar_search_compounds", {"project_id": "../etc", "limit": 9999, "extra": 1})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "project_id" in text and "limit" in text and "not an accepted argument" in text


def test_import_upload_contract_is_rejected_before_http():
    fake = FakeClient()
    server = McpServer(settings(SAR_MCP_MODE="full"), fake)
    cases = [
        ("sar_import_preview", {"project_id": "p1", "csv_text": "a,b\n1,2\n"}),
        ("sar_import_preview", {"project_id": "p1", "filename": "x.csv"}),
        ("sar_import_preview", {"project_id": "p1", "filename": "x.csv", "csv_text": "a", "content_base64": "YQ=="}),
        ("sar_import_profile", {"project_id": "p1", "filename": "x.csv"}),
        ("sar_import_profile", {"project_id": "p1", "filename": "x.csv", "csv_text": "a", "content_base64": "YQ=="}),
    ]
    for name, arguments in cases:
        result = call(server, name, arguments)
        assert result["isError"] is True
    assert fake.calls == []


def test_prodrug_mapping_contract_rejects_invalid_forms_before_http():
    fake = FakeClient()
    server = McpServer(settings(SAR_MCP_MODE="full"), fake)
    valid_pair = {"prodrug_id": "PRO-001", "active_id": "ACT-001"}
    cases = [
        ("sar_preview_prodrug_relationships", {"project_id": "p1", "relationship_type": "prodrug_of"}),
        ("sar_save_prodrug_relationships", {"project_id": "p1", "relationship_type": "prodrug_of", "user_confirmed": True}),
        ("sar_preview_prodrug_relationships", {"project_id": "p1", "relationship_type": "prodrug_of", "pairs": [valid_pair], "pair_text": "PRO-001,ACT-001"}),
        ("sar_save_prodrug_relationships", {"project_id": "p1", "relationship_type": "prodrug_of", "pairs": [valid_pair], "pair_text": "PRO-001,ACT-001", "user_confirmed": True}),
        ("sar_preview_prodrug_relationships", {"project_id": "p1", "relationship_type": "prodrug_of", "pairs": [{**valid_pair, "unexpected": "x"}]}),
        ("sar_save_prodrug_relationships", {"project_id": "p1", "relationship_type": "prodrug_of", "pairs": [{**valid_pair, "unexpected": "x"}], "user_confirmed": True}),
        ("sar_preview_prodrug_relationships", {"project_id": "p1", "relationship_type": "related_to", "pairs": [valid_pair]}),
        ("sar_save_prodrug_relationships", {"project_id": "p1", "relationship_type": "related_to", "pairs": [valid_pair], "user_confirmed": True}),
    ]
    for name, arguments in cases:
        result = call(server, name, arguments)
        assert result["isError"] is True
    late_invalid = [valid_pair] * 200 + [{"prodrug_id": "PRO-001"}]
    for name, extra in (("sar_preview_prodrug_relationships", {}), ("sar_save_prodrug_relationships", {"user_confirmed": True})):
        result = call(server, name, {"project_id": "p1", "relationship_type": "prodrug_of", "pairs": late_invalid, **extra})
        assert result["isError"] is True
    assert fake.calls == []


def test_scalar_overflow_is_parseable_and_has_no_fake_continuation():
    payload = {"status": "ok", "detail": "x" * 10000}
    server = McpServer(settings(SAR_MCP_MAX_CHARS="2000"), FakeClient({"/api/v1/readyz": payload}))
    result = call(server, "sar_status", {})
    assert result["isError"] is True
    error = json.loads(result["content"][0]["text"])
    assert error["error"] == "response_too_large" and error["tool"] == "sar_status"
    assert "same_tool" not in result["content"][0]["text"]


def test_project_allowlist_blocks_other_projects():
    fake = FakeClient({"/api/v1/projects": {"projects": [{"id": "a_proj", "name": "A"}, {"id": "b_proj", "name": "B"}]}})
    server = McpServer(settings(SAR_MCP_PROJECT_IDS="a_proj"), fake)
    listed = json.loads(call(server, "sar_list_projects", {})["content"][0]["text"])
    assert [p["id"] for p in listed["projects"]] == ["a_proj"]
    blocked = call(server, "sar_search_compounds", {"project_id": "b_proj"})
    assert blocked["isError"] and "restricted" in blocked["content"][0]["text"] and not any("search" in c[1] for c in fake.calls)


def test_schema_validator_rules():
    schema = {"type": "object", "additionalProperties": False, "required": ["a"],
              "properties": {"a": {"type": "integer", "minimum": 1}, "b": {"type": "string", "enum": ["x", "y"]}, "id": {"type": "string", "pattern": ID_PATTERN}}}
    assert validate({"a": 1, "b": "x", "id": "prj_1-2.3"}, schema) == []
    assert any("integer" in p for p in validate({"a": True}, schema))
    assert any("required" in p for p in validate({}, schema))
    assert any("one of" in p for p in validate({"a": 1, "b": "z"}, schema))
    assert any("format" in p for p in validate({"a": 1, "id": "a/../b"}, schema))
    assert any("not an accepted" in p for p in validate({"a": 1, "c": 1}, schema))


def test_drawings_are_stripped_and_long_lists_are_cut_with_a_note():
    payload = {"results": [{"id": i, "rendered_svg": "<svg>" + "x" * 500, "smiles": "C" * 40, "other": "<svg xmlns='x'/>"} for i in range(200)]}
    clean = strip_drawings(payload)
    assert set(clean["results"][0]) == {"id", "smiles"}
    small, notes = fit(clean, 1500)
    assert len(json.dumps(small)) <= 1700 and notes[0]["total"] == 200 and 1 <= notes[0]["shown"] < 200 and notes[0]["path"] == ["results"]
    assert len(clean["results"]) == 200  # the caller's object is untouched
    top, top_notes = fit(list(range(5000)), 300)
    assert isinstance(top, list) and top_notes and len(top) < 5000


def test_oversized_results_are_cut_and_announced():
    server = McpServer(settings(SAR_MCP_MAX_CHARS="2000"), FakeClient({"/api/v1/projects": {"projects": [{"id": f"p{i}", "name": "n" * 40} for i in range(300)]}}))
    result = call(server, "sar_list_projects", {})
    text = result["content"][0]["text"]
    assert len(text) <= 2100 and "_truncated" in text and result["isError"] is False


def test_paged_fit_repairs_return_metadata_and_continuation():
    server = McpServer(settings(SAR_MCP_MAX_CHARS="2000"), FakeClient())
    data = {"offset": 0, "returned": 200, "total": 200, "has_more": False, "next_offset": None, "rows": [{"id": index, "detail": "x" * 80} for index in range(200)]}
    result = server._success("sar_compare_endpoints", {"project_id": "p1", "limit": 200}, data)
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["returned"] == len(payload["rows"]) < 200
    assert payload["has_more"] is True and payload["next_offset"] == payload["returned"]
    assert payload["continuation"]["arguments"]["offset"] == payload["next_offset"]


def test_paged_fit_continuations_retain_safe_request_context():
    server = McpServer(settings(SAR_MCP_MAX_CHARS="2000"), FakeClient())
    endpoint_data = {
        "offset": 0, "returned": 200, "total": 200, "has_more": False, "next_offset": None,
        "rows": [{"id": index, "detail": "x" * 80} for index in range(200)],
    }
    endpoint_result = server._success(
        "sar_compare_endpoints",
        {"project_id": "p1", "endpoint_a": "IC50:import-v1", "endpoint_b": "none", "coverage": "all", "q": "MC", "limit": 200},
        endpoint_data,
    )
    endpoint_payload = json.loads(endpoint_result["content"][0]["text"])
    assert endpoint_payload["continuation"] == {
        "tool": "sar_compare_endpoints",
        "arguments": {
            "project_id": "p1", "endpoint_a": "IC50:import-v1", "endpoint_b": "none",
            "coverage": "all", "q": "MC", "offset": endpoint_payload["next_offset"], "limit": 200,
        },
    }

    import_data = {
        "import_id": "imp_1", "offset": 0, "returned": 200, "total": 200, "has_more": False, "next_offset": None,
        "rows": [{"id": index, "detail": "x" * 80} for index in range(200)],
    }
    import_result = server._success(
        "sar_import_preview",
        {"project_id": "p1", "filename": "measurements.csv", "csv_text": "secret", "limit": 200},
        import_data,
    )
    import_payload = json.loads(import_result["content"][0]["text"])
    assert import_payload["continuation"] == {
        "tool": "sar_get_import_preview",
        "arguments": {"project_id": "p1", "import_id": "imp_1", "offset": import_payload["next_offset"], "limit": 200},
    }
    assert settings(SAR_MCP_BASE_URL="https://sar.example.org/").base_url == "https://sar.example.org"
    for bad in ("ftp://x", "http://user:pw@127.0.0.1", "http://127.0.0.1/app", "127.0.0.1:5001", "http://127.0.0.1?x=1"):
        with pytest.raises(ConfigError):
            settings(SAR_MCP_BASE_URL=bad)
    with pytest.raises(ConfigError):
        settings(SAR_MCP_MODE="root")
    with pytest.raises(ConfigError):
        settings(SAR_MCP_TIMEOUT="0")
    with pytest.raises(ConfigError):
        settings(SAR_MCP_MAX_CHARS="abc")


def test_credentials_never_travel_over_plain_http_to_another_host():
    remote = settings(SAR_MCP_BASE_URL="http://sar.example.org:5001", SAR_MCP_EMAIL="a@b.c", SAR_MCP_PASSWORD="pw")
    assert "plain HTTP" in remote.transport_problem()
    assert settings(SAR_MCP_BASE_URL="https://sar.example.org").transport_problem() is None
    assert settings(SAR_MCP_BASE_URL="http://localhost:5001").transport_problem() is None
    assert settings(SAR_MCP_BASE_URL="http://sar.example.org", SAR_MCP_ALLOW_INSECURE_HTTP="true").transport_problem() is None
    # The guard applies to every call, not just sign-in.
    server = McpServer(remote)
    assert "plain HTTP" in call(server, "sar_list_projects", {})["content"][0]["text"]


def test_credentials_file_must_be_private(tmp_path):
    path = tmp_path / "mcp.env"
    path.write_text('# comment\nexport SAR_MCP_EMAIL="Me@Example.org"\nSAR_MCP_PASSWORD=\'s3cret value\'\n')
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        read_credentials_file(path)
    os.chmod(path, 0o600)
    assert read_credentials_file(path) == {"SAR_MCP_EMAIL": "Me@Example.org", "SAR_MCP_PASSWORD": "s3cret value"}
    loaded = Settings.from_env({"SAR_MCP_CREDENTIALS_FILE": str(path)})
    assert loaded.email == "me@example.org" and loaded.password == "s3cret value" and loaded.has_credentials
    # Environment variables win over the file.
    assert Settings.from_env({"SAR_MCP_CREDENTIALS_FILE": str(path), "SAR_MCP_EMAIL": "x@y.z", "SAR_MCP_PASSWORD": "pw"}).email == "x@y.z"
    assert read_credentials_file(tmp_path / "missing.env") == {}


def test_credentials_reader_rejects_decoded_and_physical_line_breaks(tmp_path):
    path = tmp_path / "newline.env"
    os.chmod(path, 0o600) if path.exists() else None
    path.write_text('SAR_MCP_PASSWORD="secret\\nvalue"\n', encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError, match="line breaks"):
        read_credentials_file(path)
    path.write_text('SAR_MCP_PASSWORD="secret\nvalue"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        read_credentials_file(path)


def test_redirects_to_another_origin_are_refused():
    handler = _SameOriginRedirects("http://127.0.0.1:5001")
    request = urllib.request.Request("http://127.0.0.1:5001/login", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, io.BytesIO(b""), 302, "Found", {}, "http://evil.example/steal")
    same = handler.redirect_request(request, io.BytesIO(b""), 302, "Found", {}, "/workspace")
    assert same is not None and same.full_url == "http://127.0.0.1:5001/workspace"


def test_analysis_parameters_are_checked_before_anything_is_sent():
    fake = FakeClient()
    server = McpServer(settings(), fake)
    bad = call(server, "sar_run_analysis", {"project_id": "p1", "analysis": "rgroup", "parameters": {}})
    assert bad["isError"] and "scaffold_smarts" in bad["content"][0]["text"]
    unknown = call(server, "sar_run_analysis", {"project_id": "p1", "analysis": "rgroup", "parameters": {"scaffold_smarts": "c1ccccc1", "project_id": "other"}})
    assert unknown["isError"] and "not an accepted argument" in unknown["content"][0]["text"]
    assert call(server, "sar_run_analysis", {"project_id": "p1", "analysis": "drop_tables"})["isError"]
    assert fake.calls == []


def test_commit_requires_explicit_confirmation_and_complete_review_attestation():
    fake = FakeClient()
    server = McpServer(settings(SAR_MCP_MODE="full"), fake)
    args = {"project_id": "p1", "import_id": "imp_1", "user_confirmed": False, "reviewed_all_rows": True, "reviewed_row_count": 0}
    result = call(server, "sar_import_commit", args)
    assert result["isError"] and "user_confirmed" in result["content"][0]["text"] and fake.calls == []

    negative_review = call(server, "sar_import_commit", {**args, "user_confirmed": True, "reviewed_all_rows": False})
    assert negative_review["isError"] and "reviewed_all_rows" in negative_review["content"][0]["text"] and fake.calls == []

    committed = call(server, "sar_import_commit", {**args, "user_confirmed": True})
    committed_payload = json.loads(committed["content"][0]["text"])
    assert committed_payload["review_attestation_unverified"] is True
    assert fake.calls == [("POST", "/api/v1/imports/imp_1/commit", {"project_id": "p1", "mcp_request": True, "user_confirmed": True, "reviewed_all_rows": True, "reviewed_row_count": 0})]


def test_kiro_config_has_no_secrets_and_auto_approves_only_read_tools():
    entry = kiro_config("analyze")["mcpServers"]["sar-workbench"]
    assert set(entry["env"]) == {"SAR_MCP_BASE_URL", "SAR_MCP_MODE"}
    approved = set(entry["autoApprove"])
    by_name = {tool.name: tool for tool in all_tools()}
    assert approved and all(by_name[name].tier == "read" for name in approved)
    assert "sar_export_overview" not in approved and not {"sar_run_analysis", "sar_import_commit", "sar_create_project"} & approved
    assert os.path.isabs(entry["command"]) and os.path.isabs(entry["args"][0]) and entry["args"][0].endswith("sar_mcp_server.py")
    assert "password" not in json.dumps(entry).lower()


def test_kiro_config_rejects_secret_bearing_or_non_origin_base_urls(capsys):
    for bad_url in (
        "https://user:secret@example.test:5001",
        "https://example.test/workspace",
        "https://example.test?token=secret",
        "https://example.test#secret",
    ):
        with pytest.raises(ConfigError):
            kiro_config("analyze", bad_url)
    assert mcp_cli.main(["--print-kiro-config", "--base-url", "https://user:secret@example.test:5001"]) == 2
    assert "secret" not in capsys.readouterr().err


def test_prodrug_preview_overflow_without_continuation_is_actionable_size_error():
    server = McpServer(settings(SAR_MCP_MAX_CHARS="2000"), FakeClient())
    data = {
        "offset": 0, "returned": 200, "total": 200, "has_more": False, "next_offset": None,
        "valid": [{"row": index, "notes": "x" * 80} for index in range(200)], "invalid": [],
    }
    result = server._success("sar_preview_prodrug_relationships", {"project_id": "p1", "pair_text": "secret"}, data)
    assert result["isError"] is True
    error = json.loads(result["content"][0]["text"])
    assert error["error"] == "response_too_large"
    assert "continuation" not in error
    assert "secret" not in result["content"][0]["text"]


def test_api_errors_are_reported_with_status_and_hint():
    class Failing(FakeClient):
        def get(self, path, **query):
            raise SarApiError(403, "project_forbidden", "Viewer access is required for this project.")

    result = call(McpServer(settings(), Failing()), "sar_list_endpoints", {"project_id": "p1"})
    text = result["content"][0]["text"]
    assert result["isError"] and "403" in text and "project_forbidden" in text and "role" in text


def test_links_encode_everything_and_only_point_at_real_pages():
    from urllib.parse import parse_qs, urlsplit

    from sar_mcp.links import PAGES, Links, run_id_of

    links = Links("http://127.0.0.1:5001/")
    url = links.search("prj_1", "[F,Cl]c1ccc(C(=O)N)cc1&x=1#y", "smarts")
    parts = urlsplit(url)
    assert parts.path == "/workspace/analysis" and parts.fragment == "pattern-explorer"
    assert parse_qs(parts.query) == {"project_id": ["prj_1"], "find": ["[F,Cl]c1ccc(C(=O)N)cc1&x=1#y"], "find_format": ["smarts"]}
    assert "find_format" not in links.search("prj_1", "c1ccccc1")  # auto is the default, so it is left out
    assert links.page("sar", "prj_1", reference="cmp_9").endswith("/workspace/sar?project_id=prj_1&reference=cmp_9")
    assert links.page("overview", "a b/c").endswith("project_id=a%20b%2Fc")  # an odd id cannot add path or query parts
    with pytest.raises(ValueError):
        links.page("admin", "prj_1")
    assert set(PAGES) == {"overview", "evidence", "summaries", "explore", "sar", "analysis", "designs"}
    assert links.run("prj_1", "analysis_abc123").endswith("project_id=prj_1&run=analysis_abc123")
    assert links.run("prj_1", "../etc") is None and links.run("prj_1", "") is None
    assert run_id_of({"analysis_run_id": "analysis_a"}) == "analysis_a" and run_id_of({"id": "analysis_b"}) == "analysis_b" and run_id_of([]) == ""
    assert "password" not in url.lower() and "csrf" not in url.lower()


def test_endpoint_comparison_links_use_browser_ids_and_safe_fallbacks():
    comparison = {
        "endpoint_a": "IC50:import-v1",
        "endpoint_b": "Solubility:import-v1",
        "endpoint_a_metadata": {"endpoint_key": "IC50:import-v1", "canonical_unit": "pIC50", "unit_consistent": True},
        "endpoint_b_metadata": {"endpoint_key": "Solubility:import-v1", "canonical_unit": "uM", "unit_consistent": True},
        "rows": [], "total": 0, "returned": 0, "has_more": False, "next_offset": None,
    }
    server = McpServer(settings(), FakeClient({"/api/v1/endpoint-comparison": comparison}))
    payload = json.loads(call(server, "sar_compare_endpoints", {"project_id": "p1", "endpoint_a": "IC50:import-v1", "endpoint_b": "Solubility:import-v1"})["content"][0]["text"])
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(payload["web_link"]).query)
    assert query["endpoint_a"] == ["summary|IC50:import-v1|pIC50"]
    assert query["endpoint_b"] == ["summary|Solubility:import-v1|uM"]

    ambiguous = {**comparison, "endpoint_a_metadata": {"endpoint_key": "IC50:import-v1", "canonical_unit": None, "unit_consistent": False}}
    safe_server = McpServer(settings(), FakeClient({"/api/v1/endpoint-comparison": ambiguous}))
    safe_payload = json.loads(call(safe_server, "sar_compare_endpoints", {"project_id": "p1", "endpoint_a": "IC50:import-v1"})["content"][0]["text"])
    safe_query = parse_qs(urlsplit(safe_payload["web_link"]).query)
    assert "endpoint_a" not in safe_query and "IC50:import-v1" not in safe_payload["web_link"]


def test_sectioned_export_keeps_bounded_handoff_metadata():
    bundle = {
        "export_id": "export_1", "created_at": "2026-10-07T12:00:00Z", "row_count": 3,
        "provenance_policy": {"raw_measurements": "raw", "summaries_and_analyses": "derived"},
        "compounds": [{"id": "cmp_1"}, {"id": "cmp_2"}, {"id": "cmp_3"}],
    }
    server = McpServer(settings(), FakeClient({"/api/v1/exports/project/p1": bundle}))
    payload = json.loads(call(server, "sar_export_overview", {"project_id": "p1", "section": "compounds", "limit": 2})["content"][0]["text"])
    assert payload["returned"] == 2 and payload["has_more"] is True and payload["next_offset"] == 2
    assert payload["download_link"].endswith("/api/v1/exports/project/p1?format=csv")
    assert payload["provenance_policy"] == bundle["provenance_policy"]
    assert "audit" in payload["audit_side_effect"]
    assert len(payload["rows"]) == 2 and "secret" not in json.dumps(payload).lower()
    overview = json.loads(call(server, "sar_export_overview", {"project_id": "p1"})["content"][0]["text"])
    assert overview["returned"] == 0 and overview["has_more"] is False and overview["next_offset"] is None


    plain = settings(SAR_MCP_BASE_URL="http://127.0.0.1:5001")
    assert plain.link_origin == "http://127.0.0.1:5001"
    shown = settings(SAR_MCP_BASE_URL="http://127.0.0.1:5001", SAR_MCP_PUBLIC_URL="https://sar.example.org/")
    assert shown.link_origin == "https://sar.example.org" and shown.base_url == "http://127.0.0.1:5001"
    with pytest.raises(ConfigError):
        settings(SAR_MCP_PUBLIC_URL="https://sar.example.org/app")
    fake = FakeClient({"/api/v1/projects": {"projects": [{"id": "prj_1", "name": "One"}]}})
    body = json.loads(call(McpServer(shown, fake), "sar_list_projects", {})["content"][0]["text"])
    assert body["projects"][0]["web_link"] == "https://sar.example.org/workspace/overview?project_id=prj_1"


def test_open_in_workbench_builds_only_valid_combinations():
    fake = FakeClient({
        "/api/v1/projects": {"projects": [{"id": "prj_1", "name": "One"}]},
        "/api/v1/search/compounds": {"results": [{"id": "cmp_1", "registration_id": "POS-001"}]},
        "/api/v1/measurement-summaries": {"summaries": [{"compound_id": "cmp_1", "compatibility_key": "IC50:import-v1", "canonical_unit": "pIC50"}]},
    })
    server = McpServer(settings(), fake)

    def open_link(**arguments):
        result = call(server, "sar_open_in_workbench", {"project_id": "prj_1", **arguments})
        return result["isError"], result["content"][0]["text"]

    failed, text = open_link(page="analysis", compound="POS-001", run_id="analysis_x")
    link = json.loads(text)
    assert not failed and "compare=cmp_1" in link["web_link"] and "run=analysis_x" in link["web_link"] and link["web_link"].endswith("#pattern-explorer")
    assert "reference=cmp_1" in json.loads(open_link(page="sar", compound="POS-001")[1])["web_link"]
    assert "endpoint_a=summary%7CIC50%3Aimport-v1%7CpIC50" in json.loads(open_link(page="explore", endpoint_a="IC50:import-v1")[1])["web_link"]
    assert open_link(page="evidence", compound="POS-001")[0]  # a compound only makes sense on sar or analysis
    assert open_link(page="sar", endpoint_a="IC50:import-v1")[0]  # assays only make sense on the Compare page
    failed, text = open_link(page="explore", endpoint_a="Nope:import-v1")
    assert failed and "IC50:import-v1" in text  # unknown assay: the message lists the real ones
    assert open_link(page="admin")[0] and open_link(page="sar", run_id="../x")[0]
    failed, text = call(server, "sar_open_in_workbench", {"project_id": "prj_other", "page": "sar"})["isError"], ""
    assert failed  # a project you cannot see never gets a link


def _body_at_size(payload, target):
    for padding_size in range(target):
        candidate = {**payload, "padding": "x" * padding_size}
        body = json.dumps(candidate, separators=(",", ":")).encode("utf-8")
        if len(body) == target:
            return body
    raise AssertionError("could not construct the requested JSON boundary")


def test_upload_boundaries_cover_json_wire_decoded_and_multipart_without_batch(tmp_path):
    decoded_limit = 5
    wire_limit = base64_wire_bytes(decoded_limit)
    app = create_app({
        "TESTING": True,
        "SAR_ENV": "test",
        "SAR_DEMO_MODE": False,
        "SAR_AUTH_MODE": "test-only",
        "SECRET_KEY": "boundary-secret-that-is-long-enough",
        "DATABASE_PATH": str(tmp_path / "boundary.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "MAX_UPLOAD_BYTES": decoded_limit,
        "MAX_CONTENT_LENGTH": wire_limit,
    })
    client = app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "boundary project"}).get_json()["project"]["id"]
    exact = base64.b64encode(b"a,b\n1").decode("ascii")
    exact_body = _body_at_size({"project_id": project_id, "filename": "x.csv", "content_base64": exact}, wire_limit)
    exact_response = client.post("/api/v1/imports/profile", data=exact_body, content_type="application/json")
    assert exact_response.status_code == 200
    wire_over = client.post("/api/v1/imports/profile", data=exact_body + b"x", content_type="application/json")
    assert wire_over.status_code == 413

    decoded_over = base64.b64encode(b"a,b\n12").decode("ascii")
    rejected = client.post("/api/v1/imports/preview", json={"project_id": project_id, "filename": "x.csv", "content_base64": decoded_over})
    assert rejected.status_code == 413
    import sqlite3
    with sqlite3.connect(app.config["DATABASE_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 0

    multipart_exact = client.post("/api/v1/imports/profile", data={"file": (io.BytesIO(b"a,b\n1"), "x.csv")}, content_type="multipart/form-data")
    multipart_over = client.post("/api/v1/imports/profile", data={"file": (io.BytesIO(b"a,b\n12"), "x.csv")}, content_type="multipart/form-data")
    assert multipart_exact.status_code == 200 and multipart_over.status_code == 413


def test_save_credentials_round_trips_quotes_and_backslashes(tmp_path, monkeypatch, capsys):
    target = tmp_path / "mcp.env"
    monkeypatch.setenv("SAR_MCP_CREDENTIALS_FILE", str(target))
    monkeypatch.setattr("builtins.input", lambda _prompt: "chemist@example.org")
    password = r'p\q"ssword'
    monkeypatch.setattr(mcp_cli.getpass, "getpass", lambda _prompt: password)
    monkeypatch.setattr(mcp_cli, "check", lambda: 0)
    assert mcp_cli.save_credentials() == 0
    assert target.stat().st_mode & 0o777 == 0o600
    loaded = read_credentials_file(target)
    assert loaded == {"SAR_MCP_EMAIL": "chemist@example.org", "SAR_MCP_PASSWORD": password}
    assert password not in capsys.readouterr().out
