"""MCP server against a real SAR Workbench: production-mode sign-in, CSRF, sessions, and real analyses."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from app import create_app
from sar_mcp.client import SarClient
from sar_mcp.config import Settings
from sar_mcp.server import McpServer

ROOT = Path(__file__).resolve().parents[1]
EMAIL, PASSWORD = "scientist@example.test", "correct horse battery staple"
CSV = (
    "compound_id,smiles,assay,result,unit,qualifier\n"
    "MC-001,O=C(Cc1ccccc1)Nc1ccccn1,IC50,1000,nM,=\n"
    "MC-002,Cc1ccc(CC(=O)Nc2ccccn2)cc1,IC50,500,nM,=\n"
    "MC-003,O=C(Cc1ccc(Cl)cc1)Nc1ccccn1,IC50,30,nM,=\n"
    "MC-004,O=C(Cc1ccc(C(F)(F)F)cc1)Nc1ccccn1,IC50,5,nM,=\n"
    "MC-001,O=C(Cc1ccccc1)Nc1ccccn1,Solubility,120,uM,=\n"
    "MC-003,O=C(Cc1ccc(Cl)cc1)Nc1ccccn1,Solubility,25,uM,>\n"
)


@pytest.fixture
def live(monkeypatch, tmp_path):
    """A production-mode app (login required, CSRF enforced) on a real local port."""
    env = {"SAR_ENV": "production", "SAR_DEMO_MODE": "false", "SAR_DATABASE_PATH": str(tmp_path / "mcp.db"),
           "SAR_UPLOAD_DIR": str(tmp_path / "uploads"), "SAR_SECRET_KEY": "production-secret-that-is-long-enough-123456",
           "SAR_AUTH_MODE": "local", "SAR_AUTH_EMAIL": EMAIL, "SAR_AUTH_PASSWORD_HASH": generate_password_hash(PASSWORD),
           "SAR_SECURE_COOKIES": "false"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def make_server_for(url, mode="full", password=PASSWORD, **extra):
    env = {"SAR_MCP_BASE_URL": url, "SAR_MCP_MODE": mode, "SAR_MCP_EMAIL": EMAIL, "SAR_MCP_PASSWORD": password,
           "SAR_MCP_CREDENTIALS_FILE": "/nonexistent", **extra}
    return McpServer(Settings.from_env(env))


def run(server, tool, **arguments):
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
    result = reply["result"]
    text = result["content"][0]["text"]
    assert "<svg" not in text, "drawings must never reach the model"
    return (result["isError"], text) if result["isError"] else (False, json.loads(text))


def ok(server, tool, **arguments):
    failed, payload = run(server, tool, **arguments)
    assert not failed, payload
    return payload


def test_signs_in_with_csrf_and_runs_the_whole_workflow(live):
    server = make_server_for(live)
    status = ok(server, "sar_status")
    assert status["signed_in_as"] == EMAIL and status["sign_in_required"] is True and status["app"]["status"]
    project = ok(server, "sar_create_project", name="MCP workflow check")["project"]["id"]
    assert [p["id"] for p in ok(server, "sar_list_projects")["projects"]] == [project]

    preview = ok(server, "sar_import_preview", project_id=project, csv_text=CSV, filename="series.csv")
    assert preview["accepted_count"] == 6 and preview["rejected_count"] == 0 and "sar_import_commit" in preview["next"]
    refused, message = run(server, "sar_import_commit", project_id=project, import_id=preview["import_id"], user_confirmed=False)
    assert refused and "user_confirmed" in message
    assert ok(server, "sar_import_commit", project_id=project, import_id=preview["import_id"], user_confirmed=True)

    assert ok(server, "sar_list_endpoints", project_id=project)["count"] == 0  # nothing summarised yet
    assert ok(server, "sar_run_analysis", project_id=project, analysis="measurement_summaries")["stored"] is True
    endpoints = ok(server, "sar_list_endpoints", project_id=project)["endpoints"]
    assert {e["endpoint_key"] for e in endpoints} == {"IC50:import-v1", "Solubility:import-v1"}

    found = ok(server, "sar_search_compounds", project_id=project, q="MC-00")
    assert found["total_matches"] == 4 and all("rendered_svg" not in c for c in found["compounds"])
    compound = ok(server, "sar_get_compound", project_id=project, compound="mc-003")
    assert compound["compound"]["registration_id"] == "MC-003" and compound["measurement_count"] == 2
    censored = [m for m in compound["measurements"] if m["qualifier"] == ">"]
    assert len(censored) == 1 and censored[0]["value_numeric"] == 25.0  # the qualifier survives, so > 25 is not read as 25
    summaries = ok(server, "sar_list_result_summaries", project_id=project, endpoint_key="Solubility:import-v1", compound="MC-003")
    assert summaries["summaries"][0]["summary_qualifier"] == ">" and "censored" in summaries["reading_note"]
    assert ok(server, "sar_list_measurements", project_id=project, compound="MC-001", assay="IC50")["total_matches"] == 1

    hits = ok(server, "sar_substructure_search", project_id=project, query="c1ccncc1")
    assert hits["counts"]["matched"] == 4 and {m["registration_id"] for m in hits["matches"]} == {"MC-001", "MC-002", "MC-003", "MC-004"}
    assert ok(server, "sar_substructure_search", project_id=project, query="FC(F)F")["counts"]["matched"] == 1
    assert ok(server, "sar_standardize_structure", structure="OCC")["canonical_smiles"] == "CCO"
    core = ok(server, "sar_core_from_structure", query="*c1ccc(cc1)CC(=O)Nc1ccccn1")
    assert core["attachment_points"] == 1 and core["core_smarts"]

    rgroup = ok(server, "sar_run_analysis", project_id=project, analysis="rgroup", parameters={"scaffold_smarts": core["core_smarts"]})
    assert "not evidence of causation" in rgroup["interpretation"] and len(rgroup["result"]["assignments"]) == 4
    pharma = ok(server, "sar_run_analysis", project_id=project, analysis="pharmacophore_rgroup", parameters={"reference_compound": "MC-001"})
    run_id = pharma["result"]["analysis_run_id"]
    stored = ok(server, "sar_get_analysis_run", analysis="pharmacophore_rgroup", run_id=run_id)
    assert stored["id"] == run_id and stored["project_id"] == project
    fenced = make_server_for(live, SAR_MCP_PROJECT_IDS="prj_some_other_project")
    refused, why = run(fenced, "sar_get_analysis_run", analysis="pharmacophore_rgroup", run_id=run_id)
    assert refused and "not allowed" in why
    cliffs = ok(server, "sar_run_analysis", project_id=project, analysis="activity_cliffs", parameters={"effect_threshold": 0.5})
    assert "cliffs" in cliffs["result"]
    proposal = ok(server, "sar_propose_substituent", project_id=project, compound="MC-001", site="R1", replacement="*F")
    assert proposal["proposed"]["canonical_smiles"] and "nothing was saved" in proposal["note"]
    series = ok(server, "sar_discover_series", project_id=project, endpoint_key="IC50:import-v1")
    assert series["series"]
    assert ok(server, "sar_list_records", project_id=project, kind="series")
    overview = ok(server, "sar_export_overview", project_id=project)
    # Exactly the runs we stored: rgroup, pharmacophore_rgroup and activity_cliffs (summaries are not analysis runs).
    assert overview["sections"]["measurements"] == 6 and overview["sections"]["analysis_runs"] == 3
    assert ok(server, "sar_export_overview", project_id=project, section="compounds", limit=2)["returned"] == 2


def test_wrong_password_is_reported_without_echoing_it_and_stops_after_three_tries(live):
    server = make_server_for(live, password="definitely-wrong-password")
    for _ in range(3):
        failed, message = run(server, "sar_list_projects")
        assert failed and "not accepted" in message and "definitely-wrong-password" not in message
    failed, message = run(server, "sar_list_projects")
    assert failed and "stopped trying" in message


def test_missing_credentials_point_to_the_fix(live):
    server = McpServer(Settings.from_env({"SAR_MCP_BASE_URL": live, "SAR_MCP_CREDENTIALS_FILE": "/nonexistent"}))
    failed, message = run(server, "sar_list_projects")
    assert failed and "mcp-credentials" in message
    status = ok(server, "sar_status")
    assert "problem" in status and status["server"]["mode"] == "analyze"


def test_an_expired_session_signs_in_again_without_losing_the_call(live):
    server = make_server_for(live)
    project = ok(server, "sar_create_project", name="Session check")["project"]["id"]
    server.ctx.client._jar.clear()  # the server-side session is gone, as after a restart or timeout
    assert ok(server, "sar_create_project", name="After expiry")["project"]["id"] != project
    assert len(ok(server, "sar_list_projects")["projects"]) == 2


def test_read_only_mode_cannot_store_anything(live):
    writer = make_server_for(live)
    project = ok(writer, "sar_create_project", name="Read-only check")["project"]["id"]
    reader = make_server_for(live, mode="read-only")
    assert ok(reader, "sar_list_projects")["count"] == 1
    reply = reader.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "sar_run_analysis", "arguments": {"project_id": project, "analysis": "properties"}}})
    assert reply["error"]["code"] == -32602 and "read-only" in reply["error"]["message"]
    assert ok(writer, "sar_export_overview", project_id=project)["sections"]["analysis_runs"] == 0


def test_an_app_without_login_needs_no_credentials(tmp_path):
    app = create_app({"TESTING": True, "SAR_ENV": "test", "SAR_DEMO_MODE": False, "SAR_AUTH_MODE": "test-only",
                      "SECRET_KEY": "test-secret-that-is-long-enough-for-mcp", "DATABASE_PATH": str(tmp_path / "open.db"),
                      "UPLOAD_DIR": str(tmp_path / "up")})
    http = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        server = McpServer(Settings.from_env({"SAR_MCP_BASE_URL": f"http://127.0.0.1:{http.server_port}", "SAR_MCP_MODE": "full", "SAR_MCP_CREDENTIALS_FILE": "/nonexistent"}))
        status = ok(server, "sar_status")
        assert status["sign_in_required"] is False and status["signed_in_as"] is None
        assert ok(server, "sar_create_project", name="Open mode")["project"]["id"]
    finally:
        http.shutdown()


def test_unreachable_app_gives_an_actionable_message():
    server = McpServer(Settings.from_env({"SAR_MCP_BASE_URL": "http://127.0.0.1:9", "SAR_MCP_CREDENTIALS_FILE": "/nonexistent", "SAR_MCP_TIMEOUT": "5"}))
    failed, message = run(server, "sar_list_projects")
    assert failed and "Cannot reach the SAR Workbench" in message and "start-local" in message
    assert "problem" in ok(server, "sar_status")


def test_real_stdio_process_speaks_clean_protocol(live):
    """The actual entry point over pipes: nothing but JSON-RPC frames on stdout, secrets never echoed."""
    env = {**os.environ, "SAR_MCP_BASE_URL": live, "SAR_MCP_MODE": "analyze", "SAR_MCP_EMAIL": EMAIL,
           "SAR_MCP_PASSWORD": PASSWORD, "SAR_MCP_CREDENTIALS_FILE": "/nonexistent", "SAR_MCP_LOG_LEVEL": "info"}
    process = subprocess.Popen([sys.executable, str(ROOT / "sar_mcp_server.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env, cwd="/")
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sar_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "sar_standardize_structure", "arguments": {"structure": "c1ccccc1O"}}},
    ]
    stdout, stderr = process.communicate("\n".join(json.dumps(frame) for frame in frames) + "\n", timeout=60)
    replies = [json.loads(line) for line in stdout.splitlines()]  # every stdout line must be valid JSON
    assert [reply["id"] for reply in replies] == [1, 2, 3, 4] and process.returncode == 0
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert len(replies[1]["result"]["tools"]) == 19
    status = json.loads(replies[2]["result"]["content"][0]["text"])
    assert status["signed_in_as"] == EMAIL
    assert json.loads(replies[3]["result"]["content"][0]["text"])["canonical_smiles"] == "Oc1ccccc1"
    assert PASSWORD not in stdout and PASSWORD not in stderr
    assert "finished in" in stderr  # logging goes to stderr, not stdout


def open_link(server, link):
    """Open a link the way the browser would, in the signed-in session: (status, content type, body)."""
    from urllib.parse import urlsplit

    parts = urlsplit(link)
    assert f"{parts.scheme}://{parts.netloc}" == server.settings.base_url, "links must point at the configured address"
    return server.ctx.client._send("GET", parts.path + (f"?{parts.query}" if parts.query else ""))


def test_every_link_the_tools_return_opens_the_right_page_in_the_app(live):
    server = make_server_for(live)
    project = ok(server, "sar_create_project", name="Links check")
    found = {"create_project": project["web_link"]}
    project = project["project"]["id"]
    preview = ok(server, "sar_import_preview", project_id=project, csv_text=CSV)
    assert "web_link" not in preview  # nothing is saved yet, so there is nothing to look at
    found["import_commit"] = ok(server, "sar_import_commit", project_id=project, import_id=preview["import_id"], user_confirmed=True)["web_link"]
    found["summaries_run"] = ok(server, "sar_run_analysis", project_id=project, analysis="measurement_summaries")["web_link"]
    rgroup = ok(server, "sar_run_analysis", project_id=project, analysis="rgroup", parameters={"scaffold_smarts": "c1ccccc1"})
    found["rgroup_run"] = rgroup["web_link"]
    assert f"run={rgroup['result']['analysis_run_id']}" in rgroup["web_link"]
    cliffs = ok(server, "sar_run_analysis", project_id=project, analysis="activity_cliffs")
    assert f"run={cliffs['result']['analysis_run_id']}" in cliffs["web_link"]
    gap = ok(server, "sar_run_analysis", project_id=project, analysis="information_gap", parameters={"contexts": [{"compatibility_key": "IC50:import-v1"}]})
    assert "/workspace/designs" in gap["web_link"] and "run=" not in gap["web_link"]  # feeds recommendations, which live on Choose next

    found["status"] = ok(server, "sar_status")["web_link"]
    found["projects"] = ok(server, "sar_list_projects")["projects"][0]["web_link"]
    found["search"] = ok(server, "sar_search_compounds", project_id=project, q="MC-")["web_link"]
    compound = ok(server, "sar_get_compound", project_id=project, compound="MC-003")
    found["compound_source"] = compound["web_link"]
    found.update({f"compound_{name}": link for name, link in compound["related_links"].items()})
    found["endpoints"] = ok(server, "sar_list_endpoints", project_id=project)["web_link"]
    found["measurements"] = ok(server, "sar_list_measurements", project_id=project)["web_link"]
    found["summaries"] = ok(server, "sar_list_result_summaries", project_id=project)["web_link"]
    found["substructure"] = ok(server, "sar_substructure_search", project_id=project, query="[F,Cl]c1ccccc1", query_format="smarts")["web_link"]
    found["series"] = ok(server, "sar_discover_series", project_id=project, endpoint_key="IC50:import-v1")["web_link"]
    for kind in ("series", "recommendations", "prediction_models", "designs"):
        found[f"records_{kind}"] = ok(server, "sar_list_records", project_id=project, kind=kind)["web_link"]
    found["run_readback"] = ok(server, "sar_get_analysis_run", analysis="information_gain", run_id=gap["result"]["id"])["web_link"]
    found["open_pinned"] = ok(server, "sar_open_in_workbench", project_id=project, page="analysis", run_id=rgroup["result"]["analysis_run_id"])["web_link"]
    found["open_compare"] = ok(server, "sar_open_in_workbench", project_id=project, page="explore", endpoint_a="IC50:import-v1", endpoint_b="Solubility:import-v1")["web_link"]
    found["open_find"] = ok(server, "sar_open_in_workbench", project_id=project, page="sar", find="c1ccncc1")["web_link"]

    for name, link in found.items():
        status, kind, body = open_link(server, link)
        assert status == 200 and "html" in kind, (name, link, status)
        assert PASSWORD not in link and "csrf" not in link.lower(), name
        assert 'data-structure-search' in body or name in {"status"}, name  # the Find by structure dialog is on every project page

    # Specific pages show what the link promises.
    status, _kind, body = open_link(server, found["rgroup_run"])
    assert "runPinNotice" in body and "This is the latest r-group run" in body
    assert "Declared core (SMARTS): c1ccccc1" in body
    assert "find=%5BF%2CCl%5Dc1ccccc1" in found["substructure"] and found["substructure"].endswith("#pattern-explorer")
    # The export link downloads a CSV and is recorded in the audit trail like the tool call.
    download = ok(server, "sar_export_overview", project_id=project)["download_link"]
    status, kind, body = open_link(server, download)
    assert status == 200 and "csv" in kind and "MC-001" in body
    # A link made for one project does not reveal another project's data.
    other = ok(server, "sar_create_project", name="Someone else's")["project"]["id"]
    _status, _kind, body = open_link(server, found["rgroup_run"].replace(project, other))
    assert "Run not found" in body and "Declared core (SMARTS): c1ccccc1" not in body
