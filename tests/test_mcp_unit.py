"""MCP server: protocol, schema validation, output shaping, settings and permission tiers (no web app)."""
from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.request

import pytest

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
    assert frames[0]["error"]["code"] == -32700 and len(frames[2]["result"]["tools"]) == 19


@pytest.mark.parametrize("mode,count", [("read-only", 16), ("analyze", 19), ("full", 22)])
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
        assert descriptor["annotations"]["readOnlyHint"] is (tool.tier == "read")
        if tool.tier != "read":
            assert descriptor["annotations"]["idempotentHint"] is False


def test_invalid_arguments_come_back_as_tool_errors_the_model_can_fix():
    server = McpServer(settings(), FakeClient())
    result = call(server, "sar_search_compounds", {"project_id": "../etc", "limit": 9999, "extra": 1})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "project_id" in text and "limit" in text and "not an accepted argument" in text


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


def test_settings_validate_url_mode_and_bounds():
    assert settings().base_url == "http://127.0.0.1:5001" and settings().mode == "analyze"
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


def test_commit_requires_explicit_confirmation():
    fake = FakeClient()
    server = McpServer(settings(SAR_MCP_MODE="full"), fake)
    args = {"project_id": "p1", "import_id": "imp_1", "user_confirmed": False}
    result = call(server, "sar_import_commit", args)
    assert result["isError"] and "user_confirmed" in result["content"][0]["text"] and fake.calls == []
    call(server, "sar_import_commit", {**args, "user_confirmed": True})
    assert fake.calls == [("POST", "/api/v1/imports/imp_1/commit", {"project_id": "p1"})]


def test_kiro_config_has_no_secrets_and_auto_approves_only_read_tools():
    entry = kiro_config("analyze")["mcpServers"]["sar-workbench"]
    assert set(entry["env"]) == {"SAR_MCP_BASE_URL", "SAR_MCP_MODE"}
    approved = set(entry["autoApprove"])
    by_name = {tool.name: tool for tool in all_tools()}
    assert approved and all(by_name[name].tier == "read" for name in approved)
    assert "sar_export_overview" not in approved and not {"sar_run_analysis", "sar_import_commit", "sar_create_project"} & approved
    assert os.path.isabs(entry["command"]) and os.path.isabs(entry["args"][0]) and entry["args"][0].endswith("sar_mcp_server.py")
    assert "password" not in json.dumps(entry).lower()


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


def test_links_use_the_browser_address_when_one_is_configured():
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
