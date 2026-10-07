"""Deep links into the web app: ?run=<analysis run id> pins a stored run, ?find= reruns a search."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app
from database import transaction

OLDER, NEWER = "2026-01-01T09:00:00+00:00", "2026-01-02T09:00:00+00:00"
CSV = (
    "compound_id,smiles,assay,result,unit,qualifier\n"
    "DL-001,O=C(Cc1ccccc1)Nc1ccccn1,IC50,1000,nM,=\n"
    "DL-002,Cc1ccc(CC(=O)Nc2ccccn2)cc1,IC50,500,nM,=\n"
    "DL-003,O=C(Cc1ccc(Cl)cc1)Nc1ccccn1,IC50,30,nM,=\n"
    "DL-004,O=C(Cc1ccc(C(F)(F)F)cc1)Nc1ccccn1,IC50,5,nM,=\n"
)
OLD_CORE = "O=C(Cc1ccccc1)Nc1ccccn1"
NEW_CORE = "c1ccccc1"


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True, "SAR_ENV": "test", "SAR_DEMO_MODE": False, "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-links", "DATABASE_PATH": str(tmp_path / "links.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"), "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def make_project(client, name="Links"):
    project = client.post("/api/v1/projects", json={"name": name}).get_json()["project"]["id"]
    preview = client.post("/api/v1/imports/preview", json={"csv_text": CSV, "project_id": project})
    assert client.post(f"/api/v1/imports/{preview.get_json()['import_id']}/commit", json={"project_id": project}).status_code == 200
    assert client.post("/api/v1/measurement-summaries/project", json={"project_id": project}).status_code == 201
    return project


def backdate(app, run_id, stamp):
    """Runs are timestamped to the second, so give each a distinct time to make 'latest' well defined."""
    with transaction(app.config["DATABASE_PATH"]) as connection:
        connection.execute("UPDATE analysis_runs SET created_at = ? WHERE id = ?", (stamp, run_id))


def rgroup_run(client, project, core, stamp=None):
    response = client.post("/api/v1/analysis/rgroup", json={"project_id": project, "scaffold_smarts": core})
    assert response.status_code == 201, response.get_json()
    run_id = response.get_json()["analysis_run_id"]
    if stamp:
        backdate(client.application, run_id, stamp)
    return run_id


def page(client, project, **params):
    query = "".join(f"&{key}={value}" for key, value in params.items())
    response = client.get(f"/workspace/analysis?project_id={project}{query}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_run_parameter_shows_an_older_run_and_says_so(app):
    client = app.test_client()
    project = make_project(client)
    old_run = rgroup_run(client, project, OLD_CORE, OLDER)
    new_run = rgroup_run(client, project, NEW_CORE, NEWER)

    latest = page(client, project)
    assert f"Declared core (SMARTS): {NEW_CORE}" in latest and "runPinNotice" not in latest

    pinned = page(client, project, run=old_run)
    assert f"Declared core (SMARTS): {OLD_CORE}" in pinned and f"Declared core (SMARTS): {NEW_CORE}" not in pinned
    assert "runPinNotice" in pinned and f"R-group run {old_run[-8:]}" in pinned
    assert "A newer r-group run exists" in pinned and "Everything else on this page is the latest result" in pinned
    assert f"/workspace/analysis?project_id={project}" in pinned  # the "Show latest results" link

    same = page(client, project, run=new_run)
    assert f"Declared core (SMARTS): {NEW_CORE}" in same and "This is the latest r-group run" in same


def test_other_pages_and_other_run_types_are_unaffected_by_a_pin(app):
    client = app.test_client()
    project = make_project(client)
    old_run = rgroup_run(client, project, OLD_CORE, OLDER)
    rgroup_run(client, project, NEW_CORE, NEWER)
    cliffs = client.post("/api/v1/analysis/activity-cliffs", json={"project_id": project}).get_json()["analysis_run_id"]
    # Pinning an R-group run leaves the activity-cliff view on its own latest run.
    assert f"Declared core (SMARTS): {OLD_CORE}" in page(client, project, run=old_run)
    assert "This is the latest activity cliffs run" in page(client, project, run=cliffs)
    # Any workspace page accepts the parameter and never leaks the pin into the next request.
    assert "runPinNotice" in client.get(f"/workspace/sar?project_id={project}&run={old_run}").get_data(as_text=True)
    assert f"Declared core (SMARTS): {NEW_CORE}" in page(client, project)


def test_unknown_foreign_and_unsupported_runs_fall_back_to_latest_with_a_notice(app):
    client = app.test_client()
    project = make_project(client, "Mine")
    other = make_project(client, "Someone else's")
    foreign_run = rgroup_run(client, other, OLD_CORE)
    rgroup_run(client, project, NEW_CORE)
    for bad in ("analysis_does_not_exist", foreign_run, "x" * 300, "%27%3B%20DROP%20TABLE%20analysis_runs%3B--"):
        html = page(client, project, run=bad)
        assert "Run not found" in html and f"Declared core (SMARTS): {NEW_CORE}" in html
        assert f"Declared core (SMARTS): {OLD_CORE}" not in html
    assert client.get(f"/workspace/analysis?project_id={project}").status_code == 200  # tables still intact
    gain = client.post("/api/v1/analysis/information-gain", json={"project_id": project, "contexts": [{"compatibility_key": "IC50:import-v1"}]})
    assert gain.status_code == 201, gain.get_json()
    html = page(client, project, run=gain.get_json()["id"])  # this endpoint names the run "id", not "analysis_run_id"
    assert "cannot be shown here" in html and "no view on the workspace pages" in html


def test_run_notice_text_is_escaped(app):
    client = app.test_client()
    project = make_project(client)
    html = page(client, project, run="%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>alert(1)" not in html and "Run not found" in html


def test_find_parameter_is_wired_into_the_search_dialog():
    source = (Path(__file__).parents[1] / "static" / "structure_search.js").read_text(encoding="utf-8")
    for marker in ('get("find")', 'get("find_format")', "DOMContentLoaded", "runLinkedSearch", "slice(0, 500)"):
        assert marker in source
    assert '["auto", "smiles", "smarts"].includes(format)' in source  # an unknown format cannot reach the server
