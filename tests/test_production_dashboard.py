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
    assert "productionSummaryButton" not in overview_html
    assert "productionAnalysisButton" not in overview_html
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
    assert 'aria-current="page" aria-label="Start:' in overview_html
    assert "YOU ARE HERE" not in overview_html
    assert "About this page" in overview_html
    assert "Other workflow questions" in overview_html
    assert "STEP 01 · NEXT ACTION" in overview_html
    assert "Open now" in overview_html
    assert "See all workflow tasks" not in overview_html
    assert "COMPOUNDS" not in overview_html
    assert "SAR SNAPSHOT" not in overview_html
    assert "STRUCTURE-FIRST SNAPSHOT" not in overview_html
    assert "/workspace/explore" in overview_html
    assert "Production data boundary · imported records only · no illustrative fallback" in overview_html
    assert '<a class="button button--secondary" href="/api/v1/projects">Project API</a>' in overview_html

    summaries = client.get(f"/workspace/summaries{query}")
    assert summaries.status_code == 200
    summaries_html = summaries.get_data(as_text=True)
    assert "BUILD SUMMARIES" in summaries_html
    assert "productionSummaryButton" in summaries_html
    assert "productionAnalysisButton" not in summaries_html
    assert "/static/production_summaries.js" in summaries_html
    assert "one action" in summaries_html.lower()

    explore = client.get(f"/workspace/explore{query}")
    assert explore.status_code == 200
    explore_html = explore.get_data(as_text=True)
    assert "COMPARE WORKSPACE" in explore_html
    assert "Assay A" in explore_html
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

    sar = client.get(f"/workspace/sar{query}")
    assert sar.status_code == 200
    sar_html = sar.get_data(as_text=True)
    assert "RUN SAR ANALYSIS" in sar_html
    assert "productionSarForm" in sar_html
    assert "Discover all series" in sar_html
    assert "Start from a selected SAR reference" in sar_html
    assert "/static/production_sar.js" in sar_html

    evidence = client.get(f"/workspace/evidence{query}")
    assert evidence.status_code == 200
    evidence_html = evidence.get_data(as_text=True)
    assert "CHECK RESULTS" in evidence_html
    assert "UPLOADED RESULTS" in evidence_html
    assert "CALCULATED SUMMARIES" in evidence_html
    assert "What did we actually upload?" in evidence_html

    analysis = client.get(f"/workspace/analysis{query}")
    assert analysis.status_code == 200
    analysis_html = analysis.get_data(as_text=True)
    assert "FIND PATTERNS" in analysis_html
    assert "What changed between two compounds?" in analysis_html
    assert "MMP" in analysis_html
    assert "CELLULAR TRANSLATION" in analysis_html
    assert "PREDICTION QUALIFICATION" in analysis_html
    assert "predictionQualificationForm" in analysis_html
    assert "/static/production_prediction.js" in analysis_html

    designs = client.get(f"/workspace/designs{query}")
    assert designs.status_code == 200
    designs_html = designs.get_data(as_text=True)
    assert "CHOOSE NEXT" in designs_html
    assert "productionHypothesisForm" in designs_html
    assert "productionDesignForm" in designs_html
    assert "PROPOSED EXPERIMENTS" in designs_html
    assert "Every item here remains untested." in designs_html

    chemistry_markers = {
        "overview": "Starting from the raw export preserves compound identity",
        "evidence": "Structure identity, assay context, units, and qualifiers",
        "summaries": "Replicate-aware summaries make compatible results easier to compare",
        "explore": "Side-by-side tests can reveal selectivity",
        "analysis": "Pattern analysis can prioritize a question",
        "designs": "Separating observations, suggestions, hypotheses",
    }
    for page, step, label in (
        ("overview", "01", "Start"),
        ("evidence", "02", "Check results"),
        ("summaries", "03", "Build summaries"),
        ("explore", "04", "Compare"),
        ("analysis", "06", "Find patterns"),
        ("designs", "07", "Choose next"),
    ):
        page_response = client.get(f"/workspace/{page}{query}")
        assert page_response.status_code == 200
        page_html = page_response.get_data(as_text=True)
        assert f'aria-current="page" aria-label="{label}:' in page_html
        assert f'<span class="production-workspace-nav__step">{step}</span>' in page_html
        # One collapsible explanation per page instead of inline callouts.
        assert page_html.count('class="chemistry-why"') == 1
        assert '<details class="production-about"' in page_html
        assert f'<span class="production-workspace-nav__label">{label}</span>' in page_html
        assert "Original lab values stay separate from calculated summaries" in page_html
        assert "Why in chemistry" not in page_html
        # The numbered step nav already shows the next page; no separate "Next:" link.
        assert "production-workspace-nav__next" not in page_html
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
    assert ".table-scroll {" in styles
    assert ".production-workspace textarea" in styles
    assert ".production-workspace code" in styles
    assert ".production-scaffold-potency-arrow { min-width: 0; }" in styles
    assert ".production-overview-workspace-card:focus-visible" in styles
    assert ".production-guided-home__tasks-heading { display: grid; gap: 3px; }" in styles
    assert ".production-guided-task--locked:last-child" in styles
    assert ".production-guided-home__tasks > summary::marker" in styles
    assert ".production-sar-heading" in styles
    assert ".production-sar-series-grid" in styles
    assert ".production-sar-series-card__actions" in styles
    assert ".production-sar-results__guide" in styles
    assert ".production-sar-ladder__header" in styles
    assert ".production-sar-ladder-row__change" in styles
    assert "outline: none" not in styles


def test_post_upload_overview_exposes_add_file_and_start_over_actions(production_dashboard_app):
    client = production_dashboard_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Post-upload action fixture"}).get_json()["project"]["id"]
    csv_text = (
        "compound_id,smiles,assay,result,unit,qualifier,replicate,date\n"
        "CMP-001,CCO,IC50,100,nM,=,1,2026-09-01\n"
    )
    preview = client.post(
        "/api/v1/imports/preview",
        json={"project_id": project_id, "filename": "first.csv", "csv_text": csv_text},
    )
    assert preview.status_code == 201
    import_id = preview.get_json()["import_id"]
    committed = client.post(f"/api/v1/imports/{import_id}/commit", json={"project_id": project_id})
    assert committed.status_code == 200

    overview = client.get(f"/?project_id={project_id}")
    assert overview.status_code == 200
    html = overview.get_data(as_text=True)
    for marker in (
        "CONTINUE OR START OVER",
        "Upload another file or begin a clean project",
        "Add another file",
        "Start over with a new project",
        "data-open-production-actions",
        'id="productionProjectTools"',
    ):
        assert marker in html

    production_js = client.get("/static/production.js").get_data(as_text=True)
    assert 'document.querySelector("[data-open-production-actions]")' in production_js
    assert 'document.querySelector("#productionProjectTools")' in production_js
