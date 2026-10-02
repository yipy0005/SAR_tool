"""Find patterns: interactive explorer read model and page wiring."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import create_app
from chemistry import reference_site_map
from pattern_explorer import SHOWN_CLIFF_ROWS, _core_key, build_pattern_explorer

SERIES_CSV = (
    "compound_id,smiles,assay,result,unit,qualifier\n"
    "PX-001,O=C(Cc1ccccc1)Nc1ccccn1,IC50,1000,nM,=\n"
    "PX-002,Cc1ccc(CC(=O)Nc2ccccn2)cc1,IC50,500,nM,=\n"
    "PX-003,O=C(Cc1ccc(Cl)cc1)Nc1ccccn1,IC50,30,nM,=\n"
    "PX-004,O=C(Cc1ccc(Cl)cc1)Nc1cccnc1,IC50,400,nM,=\n"
    "PX-005,O=C(Cc1ccc(C(F)(F)F)cc1)Nc1ccccn1,IC50,5,nM,=\n"
    "PX-006,O=C(CCc1ccc(F)cc1)Nc1ccccn1,IC50,800,nM,=\n"
    "PX-001,O=C(Cc1ccccc1)Nc1ccccn1,Solubility,120,uM,=\n"
    "PX-003,O=C(Cc1ccc(Cl)cc1)Nc1ccccn1,Solubility,40,uM,=\n"
    "PX-005,O=C(Cc1ccc(C(F)(F)F)cc1)Nc1ccccn1,Solubility,25,uM,>\n"
)


@pytest.fixture
def explorer_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-explorer",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def _cell(value, state="exact", qualifier="="):
    if state == "exact":
        return {"state": "exact", "display": str(value), "value": value, "sort_value": value, "qualifier": "=", "n": 2, "summary_id": "msum"}
    if state == "censored":
        return {"state": "censored", "display": f"{qualifier} {value}", "value": None, "sort_value": value, "qualifier": qualifier, "n": 1, "summary_id": "msum"}
    return {"state": "not_tested", "display": "n.t."}


def test_builder_reads_scales_thresholds_pairs_and_saved_records():
    endpoints = [
        {"id": "IC50:v1|pIC50", "compatibility_key": "IC50:v1", "name": "IC50", "unit": "pIC50", "unit_label": "pIC50", "direction": "higher"},
        {"id": "Sol:v1|µM", "compatibility_key": "Sol:v1", "name": "Sol", "unit": "µM", "unit_label": "µM", "direction": "higher"},
        {"id": "Shift:v1|mV", "compatibility_key": "Shift:v1", "name": "Shift", "unit": "mV", "unit_label": "mV", "direction": "none"},
    ]
    structures = ["O=C(Cc1ccccc1)Nc1ccccn1", "Cc1ccc(CC(=O)Nc2ccccn2)cc1", "O=C(Cc1ccc(Cl)cc1)Nc1ccccn1", "O=C(Cc1ccc(Cl)cc1)Nc1cccnc1"]
    cells = [
        [_cell(6.0), _cell(120.0), _cell(-3.0)],
        [_cell(6.3), _cell(40.0), _cell(2.0)],
        [_cell(7.5), _cell(25, "censored", ">"), _cell(5.0)],
        [_cell(6.4), _cell(None, "not_tested"), _cell(1.0)],
    ]
    rows = [
        {"compound_id": f"c{index}", "registration_id": f"PX-00{index + 1}", "isomeric_smiles": smiles, "cells": row}
        for index, (smiles, row) in enumerate(zip(structures, cells))
    ]
    cliffs = [
        {"id": f"cliff_{number}", "compound_a_id": "c0", "compound_b_id": "c2", "assay_compatibility_key": "IC50:v1", "effect_unit": "pIC50", "similarity": 0.9}
        for number in range(SHOWN_CLIFF_ROWS + 2)
    ]
    data = build_pattern_explorer(
        sar_matrix={"endpoints": endpoints, "rows": rows},
        pharmacophore_rgroup=None,
        property_profiles=[],
        activity_cliffs=iter(cliffs),
        mmp_pairs=[{"compound_a_id": "c1", "compound_b_id": "c0", "similarity": 0.94}],
        fragment_svgs={},
    )

    assert [(item["scale"], item["potency"]) for item in data["endpoints"]] == [("log", True), ("fold", False), ("linear", False)]
    assert (data["endpoints"][1]["exact"], data["endpoints"][1]["censored"]) == (2, 1)
    assert data["default_endpoint"] == "IC50:v1|pIC50"
    assert data["compounds"][2]["values"][1] == {"v": 25.0, "s": "c", "q": ">", "d": "> 25", "sid": "msum", "n": 1}
    assert data["compounds"][3]["values"][1] is None
    assert data["compounds"][0]["symbol"] == "structure-PX-001"
    # Without a pharmacophore run nothing is placed on R-sites, but every other view still works.
    assert {item["status"] for item in data["compounds"]} == {"no_run"}
    assert data["sites"] == [] and data["reference"] is None
    assert all(item["props"]["hac"] > 0 and "clogp" in item["props"] for item in data["compounds"])
    assert data["provenance"]["properties"]["calculated"] == 4
    assert len(data["pairs"]) == 6 and all(i < j and 0 <= similarity <= 1 for i, j, similarity in data["pairs"])
    assert data["mcs"] == [[0, 1, 0.94], [0, 2, 0.9]]
    shown = [record["shown"] for record in data["saved_cliffs"]]
    assert shown.count(True) == SHOWN_CLIFF_ROWS and shown[-1] is False
    json.dumps(data, allow_nan=False)


def test_core_key_separates_a_ring_nitrogen_move_but_not_a_substituent_change():
    parent = "O=C(Cc1ccccc1)Nc1ccccn1"          # 2-pyridyl amide, core = all 15 atoms
    methyl = "O=C(Cc1ccc(C)cc1)Nc1ccccn1"       # same core; atom 7 is the new methyl
    moved = "O=C(Cc1ccccc1)Nc1cccnc1"           # 3-pyridyl: ring nitrogen one position over
    core = list(range(15))
    methyl_core = [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15]
    assert _core_key(parent, core) == _core_key(methyl, methyl_core)
    assert _core_key(parent, core) != _core_key(moved, core)
    assert _core_key(parent, []) is None
    assert _core_key(parent, [0, 99]) is None
    assert _core_key("not a smiles", core) is None


def test_reference_site_map_places_each_site_on_the_drawing():
    result = reference_site_map("O=C(Cc1ccc(Cl)cc1)Nc1ccccn1", "c1ccccc1", [(0, "R1"), (3, "R2")], width=400, height=200)
    assert (result["width"], result["height"]) == (400, 200)
    assert [site["label"] for site in result["sites"]] == ["R1", "R2"]
    assert all(0 < site["x"] < 1 and 0 < site["y"] < 1 for site in result["sites"])
    assert result["sites"][0] != result["sites"][1]
    assert 'aria-label="Reference structure with R-sites R1, R2"' in result["svg"]


def test_explorer_script_is_csp_safe_and_linked():
    source = (Path(__file__).parents[1] / "static" / "production_patterns.js").read_text(encoding="utf-8")
    for marker in (
        "patternExplorerData",
        "window.SARFormat",
        "data-pe-key",
        "aria-pressed",
        "renderStrip",
        "renderLandscape",
        "renderProperties",
        "applySelection",
        "sessionStorage",
        "#ev-",
        "LipE",
        "renderMatrix",
        "comboModel",
        "additive estimate",
    ):
        assert marker in source
    for forbidden in ("eval(", "new Function", "http://", "https://", "document.write", "onclick="):
        assert forbidden not in source


def test_analysis_page_renders_linked_pattern_explorer(explorer_app):
    client = explorer_app.test_client()
    project_id = client.post("/api/v1/projects", json={"name": "Explorer fixture"}).get_json()["project"]["id"]
    page = f"/workspace/analysis?project_id={project_id}"
    assert "data-pattern-explorer" not in client.get(page).get_data(as_text=True)

    preview = client.post("/api/v1/imports/preview", json={"csv_text": SERIES_CSV})
    assert preview.status_code == 201
    assert client.post(f"/api/v1/imports/{preview.get_json()['import_id']}/commit", json={"project_id": project_id}).status_code == 200
    assert client.post("/api/v1/measurement-summaries/project", json={"project_id": project_id}).status_code == 201
    assert client.post("/api/v1/analysis/properties", json={"project_id": project_id}).status_code == 201
    listed = client.get(f"/api/v1/compounds?project_id={project_id}").get_json()
    records = listed.get("compounds") or listed.get("results") or []
    reference = next(item for item in records if item["registration_id"] == "PX-001")
    run = client.post(
        "/api/v1/analysis/pharmacophore-rgroup",
        json={"project_id": project_id, "reference_compound_id": reference.get("id") or reference.get("compound_id"), "scaffold_smarts": ""},
    )
    assert run.status_code == 201, run.get_json()

    html = client.get(page).get_data(as_text=True)
    assert "PATTERN EXPLORER · INTERACTIVE" in html
    assert "/static/production_patterns.js" in html
    # Combine sites: rows × columns (optionally one grid per third site) beside the one-site strip.
    for marker in ("data-pattern-site-mode", "data-pattern-combo-rows", "data-pattern-combo-cols", "data-pattern-combo-split", "data-pattern-combo-loose", "data-pattern-matrix"):
        assert marker in html, marker
    # The shared structure sprite sits before the explorer, outside the collapsible results.
    assert html.index('class="structure-symbols"') < html.index("data-pattern-explorer") < html.index("production-analysis-results")
    payload = json.loads(re.search(r'<script id="patternExplorerData" type="application/json">(.*?)</script>', html, re.S).group(1))
    by_reg = {item["reg"]: item for item in payload["compounds"]}
    assert payload["reference"] == list(by_reg).index("PX-001")
    assert [site["label"] for site in payload["sites"]] == ["R1"]
    assert by_reg["PX-003"]["sites"]["R1"]["name"] == "Cl"
    assert "Halogen substituent" in by_reg["PX-003"]["sites"]["R1"]["features"]
    assert by_reg["PX-004"]["core"] != by_reg["PX-003"]["core"]
    assert "relocated" in by_reg["PX-004"]["core_note"]
    assert by_reg["PX-006"]["status"] == "unmatched"
    ic50, solubility = payload["endpoints"]
    assert (ic50["unit"], ic50["scale"], ic50["potency"]) == ("pIC50", "log", True)
    assert (solubility["scale"], solubility["censored"]) == ("fold", 1)
    assert by_reg["PX-005"]["values"][1]["s"] == "c" and by_reg["PX-005"]["values"][1]["q"] == ">"
    assert len(payload["pairs"]) == 15
    for compound in payload["compounds"]:
        assert f'<symbol id="{compound["symbol"]}"' in html
        for site in compound["sites"].values():
            if site["frag"]:
                assert f'<symbol id="{site["frag"]}"' in html
