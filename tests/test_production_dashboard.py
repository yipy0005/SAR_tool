from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app


@pytest.fixture
def production_dashboard_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-dashboard",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def test_production_dashboard_exposes_focused_workspace_pages(production_dashboard_app):
    client = production_dashboard_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Dashboard fixture"}).get_json()["project"]["id"]
    query = f"?project_id={project_id}"

    overview = client.get(f"/{query}")
    assert overview.status_code == 200
    overview_html = overview.get_data(as_text=True)
    assert "START HERE · STEP 1" in overview_html
    assert "Start with one lab results file" in overview_html
    assert "Choose a file" in overview_html
    assert "productionImportForm" in overview_html
    assert "productionSummaryButton" in overview_html
    assert "productionAnalysisButton" in overview_html
    for marker in (
        "productionWorkbookProfile",
        "productionWorkbookSheet",
        "productionWorkbookHeaderRow",
        "productionWorkbookDataStartRow",
        "productionFormulaAcknowledgement",
        "productionPreviewSelected",
        "productionImportPreview",
        "productionCommitButton",
    ):
        assert marker in overview_html
    assert "production-workspace-nav__step" in overview_html
    assert "YOU ARE HERE · STEP 01" in overview_html
    assert "COMPOUNDS" not in overview_html
    assert "SAR SNAPSHOT" not in overview_html
    assert "STRUCTURE-FIRST SNAPSHOT" not in overview_html
    assert "/workspace/explore" in overview_html
    assert "Production data boundary · imported records only · no illustrative fallback" in overview_html
    assert '<a class="button button--secondary" href="/api/v1/projects">Project API</a>' in overview_html

    explore = client.get(f"/workspace/explore{query}")
    assert explore.status_code == 200
    explore_html = explore.get_data(as_text=True)
    assert "COMPARE WORKSPACE" in explore_html
    assert "Test/result A" in explore_html
    assert "productionEndpointA" in explore_html
    assert "productionEndpointB" in explore_html
    assert "productionExplorerCanvas" in explore_html
    assert "productionExplorerSessionName" in explore_html
    assert "productionExplorerSessionList" in explore_html
    assert "productionExplorerExportBundle" in explore_html
    assert '"activityCliffs"' in explore_html
    assert '"mmp"' in explore_html
    assert '"designCandidates"' in explore_html
    assert '"recommendations"' in explore_html
    assert "/static/production_explorer.js" in explore_html

    evidence = client.get(f"/workspace/evidence{query}")
    assert evidence.status_code == 200
    evidence_html = evidence.get_data(as_text=True)
    assert "CHECK RESULTS · SOURCE EVIDENCE" in evidence_html
    assert "UPLOADED RESULTS" in evidence_html
    assert "CALCULATED SUMMARIES" in evidence_html
    assert "What did we actually upload?" in evidence_html

    analysis = client.get(f"/workspace/analysis{query}")
    assert analysis.status_code == 200
    analysis_html = analysis.get_data(as_text=True)
    assert "FIND PATTERNS · CALCULATED VIEWS" in analysis_html
    assert "What changed between two compounds?" in analysis_html
    assert "MMP" in analysis_html
    assert "CELLULAR TRANSLATION" in analysis_html
    assert "PREDICTION QUALIFICATION" in analysis_html
    assert "predictionQualificationForm" in analysis_html
    assert "/static/production_prediction.js" in analysis_html

    designs = client.get(f"/workspace/designs{query}")
    assert designs.status_code == 200
    designs_html = designs.get_data(as_text=True)
    assert "CHOOSE NEXT · REVIEW QUEUE" in designs_html
    assert "productionHypothesisForm" in designs_html
    assert "productionDesignForm" in designs_html
    assert "PROPOSED EXPERIMENTS" in designs_html
    assert "Every item here remains untested." in designs_html

    chemistry_markers = {
        "overview": "Starting from the raw export preserves compound identity",
        "evidence": "Structure identity, assay context, units, and qualifiers",
        "explore": "Comparing matched compounds across tests",
        "analysis": "Structure–activity reasoning is strongest",
        "designs": "Medicinal-chemistry decisions are safer",
    }
    for page, step, label in (
        ("overview", "01", "Start"),
        ("evidence", "02", "Check results"),
        ("explore", "03", "Compare"),
        ("analysis", "04", "Find patterns"),
        ("designs", "05", "Choose next"),
    ):
        page_response = client.get(f"/workspace/{page}{query}")
        assert page_response.status_code == 200
        page_html = page_response.get_data(as_text=True)
        assert f"YOU ARE HERE · STEP {step}" in page_html
        assert f'<span class="production-workspace-nav__label">{label}</span>' in page_html
        assert "Original lab values stay separate from calculated summaries" in page_html
        assert "Why in chemistry" in page_html
        assert chemistry_markers[page] in page_html


def test_new_project_form_creates_isolated_blank_workspace(production_dashboard_app):
    client = production_dashboard_app.test_client()

    form = client.get("/workspace/new")
    assert form.status_code == 200
    form_html = form.get_data(as_text=True)
    assert "Start a blank project" in form_html
    assert "Create blank project" in form_html
    assert "Existing projects, uploaded files, calculations, and review items stay unchanged." in form_html
    assert "Separate project boundaries prevent compounds" in form_html

    created = client.post(
        "/workspace/new",
        data={"name": "Fresh upload session", "description": "A separate lab results set."},
    )
    assert created.status_code == 302
    location = created.headers["Location"]
    assert "project_id=" in location

    workspace = client.get(location)
    assert workspace.status_code == 200
    workspace_html = workspace.get_data(as_text=True)
    assert "Fresh upload session" in workspace_html
    assert "Start with one lab results file" in workspace_html
    assert "No results saved yet" in workspace_html


def test_explorer_client_exposes_linked_analysis_and_reproducibility_workflows():
    explorer_path = Path(__file__).parents[1] / "static" / "production_explorer.js"
    source = explorer_path.read_text(encoding="utf-8")

    for marker in (
        "renderAnalysisWorkspace",
        "rgroupAssignments",
        "activityCliffs",
        "mmp",
        "selectivityObservations",
        "translationObservations",
        "admeObservations",
        "propertyProfiles",
        "exportBundle",
        "sessionsStorageKey",
        "/api/v1/hypotheses",
        "/api/v1/designs",
        "compoundEvidenceIds",
    ):
        assert marker in source


def test_import_review_is_preserved_when_pattern_checks_finish():
    source = Path(__file__).parents[1].joinpath("static", "production.js").read_text(encoding="utf-8")

    assert "const hasPendingImportState = () => Boolean(pendingFile || pendingImportId || pendingPreview);" in source
    assert "if (hasPendingImportState())" in source
    assert 'if (!requireNoPendingImport("finding patterns")) return;' in source
    assert "setPendingImportActionsDisabled(Boolean(pendingFile));" in source
    assert "nothing has been saved yet." in source
    assert "Your selected or checked file is still open above." in source
    assert "window.sessionStorage.setItem(pendingImportStorageKey, importId);" in source
    assert "Your checked file was restored." in source
    assert "window.location.reload()" in source


def test_workspace_navigation_is_visible_and_styled():
    styles = Path(__file__).parents[1].joinpath("static", "styles.css").read_text(encoding="utf-8")
    assert ".production-workspace-nav {" in styles
    assert ".production-workspace-nav__link.is-active" in styles
