"""Deterministic native fallback for the real user workbook journey."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from app import create_app


FIXTURE = Path(__file__).parents[1] / "test" / "CellLinePanel_pIC50s.xlsx"


def test_real_cell_line_workbook_explicit_prodrug_chain(tmp_path):
    app = create_app({"TESTING": True, "SAR_ENV": "test", "SAR_DEMO_MODE": False, "SAR_AUTH_MODE": "test-only", "SECRET_KEY": "native-smoke-secret-that-is-long-enough", "DATABASE_PATH": str(tmp_path / "smoke.db"), "UPLOAD_DIR": str(tmp_path / "uploads"), "MAX_CONTENT_LENGTH": 10_000_000, "MAX_UPLOAD_BYTES": 5_000_000})
    client = app.test_client()
    project = client.post("/api/v1/projects", json={"name": "real workbook smoke"}).get_json()["project"]["id"]
    encoded = base64.b64encode(FIXTURE.read_bytes()).decode("ascii")
    profile = client.post("/api/v1/imports/profile", json={"project_id": project, "filename": FIXTURE.name, "content_base64": encoded})
    assert profile.status_code == 200
    assert {sheet["name"] for sheet in profile.get_json()["profile"]["sheets"]} == {"Aggregated_Data", "Cell lines"}
    preview = client.post("/api/v1/imports/preview", json={"project_id": project, "filename": FIXTURE.name, "content_base64": encoded, "sheet_name": "Aggregated_Data", "header_row": 1, "data_start_row": 2, "mapping_overrides": {"compound_id": "Sample_ID", "structure": "SMILES"}})
    assert preview.status_code == 201, preview.get_json()
    staged = preview.get_json()
    assert staged["accepted_count"] == 168
    assert staged["rows"][0]["source_location"]["sheet"] == "Aggregated_Data"
    assert staged["rows"][0]["source_location"]["cells"]["Sample_ID"] == "A2"
    imported = client.get(f"/api/v1/imports/{staged['import_id']}?project_id={project}&offset=0&limit=500")
    assert imported.status_code == 200
    page = imported.get_json()
    assert page["total"] == 168 and page["returned"] == 168 and not page["has_more"]
    committed = client.post(f"/api/v1/imports/{staged['import_id']}/commit", json={"project_id": project, "user_confirmed": True, "reviewed_all_rows": True, "reviewed_row_count": 168})
    assert committed.status_code == 200
    summaries = client.post("/api/v1/measurement-summaries/project", json={"project_id": project})
    assert summaries.status_code == 201
    saved = client.post("/api/v1/compound-relationships", json={"project_id": project, "pair_text": "prodrug_id,active_id\nTFC-00000013,TFC-00000124\nTFC-00000020,TFC-00000196\n", "user_confirmed": True})
    assert saved.status_code == 201
    assert all("WuXi_ID_core" not in json.dumps(item) for item in saved.get_json()["relationships"])
    compared = client.post("/api/v1/analysis/prodrug-comparison", json={"project_id": project, "endpoint_key": "U2OS:import-v1", "persist": True})
    assert compared.status_code == 201, compared.get_json()
    result = compared.get_json()
    assert result["persistence"] == "durable" and result["analysis_run_id"] and result["audit_event_id"]
    assert {pair["prodrug"]["registration_id"] for pair in result["pairs"]} == {"TFC-00000013", "TFC-00000020"}
    readback = client.get(f"/api/v1/analysis/prodrug-comparison/{result['analysis_run_id']}?project_id={project}&limit=500")
    assert readback.status_code == 200
    assert readback.get_json()["audit_event_id"] == result["audit_event_id"]


def test_real_cell_line_workbook_through_mcp_handlers(tmp_path):
    from threading import Thread
    from werkzeug.serving import make_server
    from sar_mcp.config import Settings
    from sar_mcp.server import McpServer

    app = create_app({"TESTING": True, "SAR_ENV": "test", "SAR_DEMO_MODE": False, "SAR_AUTH_MODE": "test-only", "SECRET_KEY": "native-mcp-secret-that-is-long-enough", "DATABASE_PATH": str(tmp_path / "mcp-smoke.db"), "UPLOAD_DIR": str(tmp_path / "uploads"), "MAX_CONTENT_LENGTH": 10_000_000, "MAX_UPLOAD_BYTES": 5_000_000})
    http = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=http.serve_forever, daemon=True).start()
    try:
        server = McpServer(Settings.from_env({"SAR_MCP_BASE_URL": f"http://127.0.0.1:{http.server_port}", "SAR_MCP_MODE": "full", "SAR_MCP_CREDENTIALS_FILE": "/nonexistent"}))
        def call(tool_name, **arguments):
            reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}})["result"]
            assert not reply["isError"], reply
            return json.loads(reply["content"][0]["text"])

        project = call("sar_create_project", name="real workbook MCP smoke")["project"]["id"]
        encoded = base64.b64encode(FIXTURE.read_bytes()).decode("ascii")
        profile = call("sar_import_profile", project_id=project, filename=FIXTURE.name, content_base64=encoded)
        assert {sheet["name"] for sheet in profile["profile"]["sheets"]} == {"Aggregated_Data", "Cell lines"}
        preview = call("sar_import_preview", project_id=project, filename=FIXTURE.name, content_base64=encoded, sheet_name="Aggregated_Data", header_row=1, data_start_row=2, mapping_overrides={"compound_id": "Sample_ID", "structure": "SMILES"}, limit=50)
        assert preview["total"] == 168 and preview["returned"] == 50 and preview["data_origin"] == "quarantined_import"
        all_rows = []
        offset = 0
        while True:
            page = call("sar_get_import_preview", project_id=project, import_id=preview["import_id"], offset=offset, limit=50)
            all_rows.extend(page["rows"])
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        assert len(all_rows) == 168 and all_rows[0]["source_location"]["sheet"] == "Aggregated_Data"
        committed = call("sar_import_commit", project_id=project, import_id=preview["import_id"], user_confirmed=True, reviewed_all_rows=True, reviewed_row_count=168)
        assert committed["data_origin"] == "imported"
        call("sar_run_analysis", project_id=project, analysis="measurement_summaries")
        pair_text = "prodrug_id,active_id\nTFC-00000013,TFC-00000124\nTFC-00000020,TFC-00000196\n"
        pair_preview = call("sar_preview_prodrug_relationships", project_id=project, relationship_type="prodrug_of", pair_text=pair_text, limit=1)
        assert pair_preview["valid_count"] == 2 and pair_preview["returned"] == 1 and pair_preview["data_origin"] == "quarantined_import"
        assert pair_preview["review_status"] == "needs_review" and pair_preview["valid"][0]["review_status"] == "needs_review"
        continuation = pair_preview["continuation"]["arguments"]
        assert "pair_text" not in continuation and "pairs" not in continuation and continuation["continuation_token"]
        pair_page_two = call("sar_preview_prodrug_relationships", **continuation)
        assert pair_page_two["returned"] == 1 and not pair_page_two["has_more"]
        assert pair_page_two["valid"][0]["review_status"] == "needs_review"
        saved = call("sar_save_prodrug_relationships", project_id=project, relationship_type="prodrug_of", pair_text=pair_text, user_confirmed=True)
        assert saved["saved_count"] == 2 and all("WuXi_ID_core" not in json.dumps(item) for item in saved["relationships"])
        compared = call("sar_compare_prodrug_pairs", project_id=project, endpoint_key="U2OS:import-v1")
        assert compared["persistence"] == "durable" and compared["analysis_run_id"] and compared["audit_event_id"]
    finally:
        http.shutdown()
