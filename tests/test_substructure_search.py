"""Draw-to-search: query building, project substructure search and sketch loading."""
from __future__ import annotations

from pathlib import Path

import pytest
from rdkit import Chem

from app import create_app
from substructure_search import SubstructureQueryError, build_query, sketch_from_smiles

SERIES = {
    "SS-001": "O=C(Cc1ccccc1)Nc1ccccn1",
    "SS-002": "O=C(Cc1ccc(Cl)cc1)Nc1ccccn1",
    "SS-003": "O=C(Cc1ccc(Cl)cc1)Nc1cccnc1",
    "SS-004": "CN(C(=O)Cc1ccc(C(F)(F)F)cc1)c1ccccn1",
    "SS-005": "O=C(Cc1ccc(N2CCOCC2)cc1)Nc1ccccn1",
}
EVERY = sorted(SERIES)
RING6 = [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1)]


def _molblock(atoms, bonds, charges=()):
    """Minimal V2000 molfile, the same shape the sketcher exports."""
    lines = ["", "  test", "", f"{len(atoms):>3}{len(bonds):>3}  0  0  0  0  0  0  0  0999 V2000"]
    for index, symbol in enumerate(atoms):
        lines.append(f"{index * 1.5:>10.4f}{(index % 2) * 0.8:>10.4f}{0:>10.4f} {symbol:<3} 0  0  0  0  0  0  0  0  0  0  0  0")
    for first, second, order in bonds:
        lines.append(f"{first:>3}{second:>3}{order:>3}  0")
    for atom, charge in charges:
        lines.append(f"M  CHG  1 {atom:>3} {charge:>3}")
    lines.append("M  END")
    return "\n".join(lines)


def _hits(query):
    return sorted(name for name, smiles in SERIES.items() if Chem.MolFromSmiles(smiles).HasSubstructMatch(query["mol"]))


KEKULE_CHLOROBENZENE = _molblock(["C"] * 6 + ["Cl"], [(a, b, 2 if a % 2 else 1) for a, b in RING6] + [(1, 7, 1)])


@pytest.fixture
def search_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-search",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
        }
    )


def test_drawn_kekule_ring_is_aromatized_and_matches_aryl_chlorides():
    query = build_query(molblock=KEKULE_CHLOROBENZENE)
    assert query["source"] == "sketch"
    assert (query["atom_count"], query["bond_count"]) == (7, 7)
    assert _hits(query) == ["SS-002", "SS-003"]


def test_query_atoms_any_bond_charge_and_explicit_hydrogen():
    # A~C(=O)-N-H: an amide N that still carries an H, so the N-methyl SS-004 drops out.
    amide_nh = build_query(molblock=_molblock(["A", "C", "O", "N", "H"], [(1, 2, 8), (2, 3, 2), (2, 4, 1), (4, 5, 1)]))
    assert "!H0" in amide_nh["smarts"]
    assert _hits(amide_nh) == ["SS-001", "SS-002", "SS-003", "SS-005"]
    # An aromatic six-membered ring with a heteroatom (Q) anywhere: every pyridyl compound.
    pyridine_like = build_query(molblock=_molblock(["C"] * 5 + ["Q"], [(a, b, 4) for a, b in RING6]))
    assert _hits(pyridine_like) == EVERY
    # para-Disubstituted benzene with two attachment points (*).
    para = build_query(molblock=_molblock(["C"] * 6 + ["*", "*"], [(a, b, 4) for a, b in RING6] + [(1, 7, 1), (4, 8, 1)]))
    assert _hits(para) == ["SS-002", "SS-003", "SS-004", "SS-005"]
    # A drawn charge is required in the match.
    assert build_query(molblock=_molblock(["C", "N"], [(1, 2, 1)], charges=[(2, 1)]))["smarts"] == "[#6]-[#7+]"


@pytest.mark.parametrize(
    ("text", "source", "expected"),
    [
        ("Clc1ccccc1", "smiles", ["SS-002", "SS-003"]),
        ("C1COCCN1", "smiles", ["SS-005"]),
        ("c1ccncc1", "smiles", EVERY),
        # Atomic-number atoms are SMARTS; read as SMILES they would silently match nothing.
        ("[#6]1:[#6]:[#7]:[#6]:[#6]:[#6]:1", "smarts", EVERY),
        ("[N;H1]C=O", "smarts", ["SS-001", "SS-002", "SS-003", "SS-005"]),
        ("[$(c1ccccc1)]C(F)(F)F", "smarts", ["SS-004"]),
    ],
)
def test_typed_queries_detect_smiles_or_smarts(text, source, expected):
    query = build_query(text=text)
    assert query["source"] == source
    assert _hits(query) == expected


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({}, "empty_query"),
        ({"text": "   "}, "empty_query"),
        ({"text": "C(", "text_format": "smiles"}, "invalid_text_query"),
        ({"text": "not a structure((", "text_format": "auto"}, "invalid_text_query"),
        ({"molblock": "garbage that is not a molfile"}, "invalid_drawing"),
        ({"text": "C" * 501}, "query_too_large"),
        ({"text": "C" * 81}, "query_too_large"),
        ({"text": "CC", "text_format": "inchi"}, "invalid_query_format"),
    ],
)
def test_unusable_queries_are_rejected_with_a_reason(kwargs, code):
    with pytest.raises(SubstructureQueryError) as error:
        build_query(**kwargs)
    assert error.value.code == code


def test_sketch_round_trips_a_molecule_and_its_ring_scaffold():
    drawn = sketch_from_smiles(SERIES["SS-002"])
    back = Chem.MolFromMolBlock(drawn["molblock"])
    assert Chem.MolToSmiles(back) == Chem.MolToSmiles(Chem.MolFromSmiles(SERIES["SS-002"]))
    counts = drawn["molblock"].splitlines()[3]
    atoms, bonds = int(counts[:3]), int(counts[3:6])
    bond_types = {line[6:9].strip() for line in drawn["molblock"].splitlines()[4 + atoms:4 + atoms + bonds]}
    assert bond_types <= {"1", "2"}  # Kekulé bonds: the sketcher draws what a chemist would draw
    scaffold = sketch_from_smiles(SERIES["SS-002"], "scaffold")
    assert scaffold["smiles"] == Chem.MolToSmiles(Chem.MolFromSmiles(SERIES["SS-001"]))
    with pytest.raises(SubstructureQueryError) as error:
        sketch_from_smiles("CCO", "scaffold")
    assert error.value.code == "no_scaffold"


def _seed(client):
    project_id = client.post("/api/v1/projects", json={"name": "Substructure fixture"}).get_json()["project"]["id"]
    rows = "".join(f"{name},{smiles},IC50,{100 + index * 10},nM,=\n" for index, (name, smiles) in enumerate(SERIES.items()))
    preview = client.post("/api/v1/imports/preview", json={"csv_text": "compound_id,smiles,assay,result,unit,qualifier\n" + rows})
    assert preview.status_code == 201
    assert client.post(f"/api/v1/imports/{preview.get_json()['import_id']}/commit", json={"project_id": project_id}).status_code == 200
    listed = client.get(f"/api/v1/compounds?project_id={project_id}").get_json()
    records = listed.get("compounds") or listed.get("results") or []
    return project_id, {item["registration_id"]: item.get("id") or item.get("compound_id") for item in records}


def test_substructure_search_api_returns_highlighted_matches(search_app):
    client = search_app.test_client()
    project_id, ids = _seed(client)
    url = "/api/v1/search/substructure"
    response = client.post(url, json={"project_id": project_id, "molblock": KEKULE_CHLOROBENZENE})
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert [item["registration_id"] for item in payload["results"]] == ["SS-002", "SS-003"]
    assert payload["matched_ids"] == [ids["SS-002"], ids["SS-003"]]
    assert payload["counts"] == {"total": 5, "scanned": 5, "matched": 2, "unreadable": 0, "drawn": 2}
    assert payload["complete"] is True and payload["stereo_compared"] is False
    assert payload["data_origin"] == "derived" and payload["query"]["source"] == "sketch" and payload["query"]["smarts"]
    first = payload["results"][0]
    assert first["svg"].startswith("<svg") and len(first["match_atoms"]) == 7 and first["match_count"] == 1

    typed = client.post(url, json={"project_id": project_id, "query": "C1COCCN1"}).get_json()
    assert [item["registration_id"] for item in typed["results"]] == ["SS-005"]
    # Every match is listed; only the first `limit` carry a drawing.
    limited = client.post(url, json={"project_id": project_id, "query": "c1ccncc1", "limit": 2}).get_json()
    assert len(limited["results"]) == 5 and sum("svg" in item for item in limited["results"]) == 2

    invalid = client.post(url, json={"project_id": project_id, "query": "C(", "query_format": "smiles"})
    assert invalid.status_code == 422 and invalid.get_json()["error"] == "invalid_text_query"
    assert client.post(url, json={"project_id": project_id, "query": "C", "limit": 0}).status_code == 422
    assert client.post(url, json={"project_id": project_id}).get_json()["error"] == "empty_query"
    assert client.post(url, json={"project_id": "prj_missing", "query": "C"}).status_code == 403


def test_sketch_api_loads_a_compound_scaffold_or_typed_smiles(search_app):
    client = search_app.test_client()
    project_id, ids = _seed(client)
    url = "/api/v1/structure/sketch"
    response = client.post(url, json={"project_id": project_id, "compound_id": ids["SS-002"], "part": "scaffold"})
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["result"]
    assert result["registration_id"] == "SS-002"
    assert Chem.MolToSmiles(Chem.MolFromMolBlock(result["molblock"])) == Chem.MolToSmiles(Chem.MolFromSmiles(SERIES["SS-001"]))
    assert client.post(url, json={"project_id": project_id, "compound_id": "cmp_missing"}).status_code == 404
    assert client.post(url, json={"project_id": "prj_missing", "compound_id": ids["SS-002"]}).status_code == 403
    typed = client.post(url, json={"smiles": "Clc1ccccc1"})
    assert typed.status_code == 200 and typed.get_json()["result"]["registration_id"] is None
    assert client.post(url, json={"smiles": "C("}).get_json()["error"] == "invalid_smiles"


def test_every_workspace_page_offers_find_by_structure(search_app):
    client = search_app.test_client()
    project_id, ids = _seed(client)
    for view in ("overview", "evidence", "summaries", "explore", "analysis", "designs", "sar"):
        html = client.get(f"/workspace/{view}?project_id={project_id}").get_data(as_text=True)
        assert 'id="structureSearchDialog"' in html, view
        assert "data-structure-search-open" in html, view
        assert "/static/structure_sketcher.js" in html and "/static/structure_search.js" in html, view
        assert f'<option value="{ids["SS-002"]}">SS-002</option>' in html, view
        assert f'"sar": "/workspace/sar?project_id={project_id}"' in html, view
    sar_html = client.get(f"/workspace/sar?project_id={project_id}").get_data(as_text=True)
    assert "Find one by structure" in sar_html
    analysis_html = client.get(f"/workspace/analysis?project_id={project_id}").get_data(as_text=True)
    assert "Find a reference by structure" in analysis_html
    new_project_html = client.get("/workspace/new").get_data(as_text=True)
    assert "structureSearchDialog" not in new_project_html


def test_sketcher_and_search_scripts_are_csp_safe_and_wired_to_pages():
    static = Path(__file__).parents[1] / "static"
    sketcher = (static / "structure_sketcher.js").read_text(encoding="utf-8")
    search = (static / "structure_search.js").read_text(encoding="utf-8")
    for marker in ("V2000", "M  CHG", "M  END", "getMolblock", "setMolblock", "window.SARSketcher", 'role="application"', "undo", "redo"):
        assert marker in sketcher
    for marker in ("/api/v1/search/substructure", "/api/v1/structure/sketch", "SARStructureActions", "X-CSRF-Token", "sar-structure-highlight", "showModal"):
        assert marker in search
    for source in (sketcher, search):
        for forbidden in ("eval(", "new Function", "http://", "https://", "document.write", "onclick="):
            assert forbidden not in source
    assert 'id: "sar-reference"' in (static / "production_sar.js").read_text(encoding="utf-8")
    patterns = (static / "production_patterns.js").read_text(encoding="utf-8")
    assert 'id: "explorer-compare"' in patterns and 'id: "explorer-highlight"' in patterns and "sar-structure-highlight" in patterns
    assert 'id: "pharmacophore-reference"' in (static / "production.js").read_text(encoding="utf-8")


def test_drawn_core_drops_attachment_points_but_keeps_query_atoms():
    # Benzene with an R# label (Ketcher writes R# plus M  RGP) and a terminal * attachment point.
    drawn = _molblock(["C"] * 6 + ["R#", "*"], [(a, b, 2 if a % 2 else 1) for a, b in RING6] + [(1, 7, 1), (4, 8, 1)])
    drawn = drawn.replace("M  END", "M  RGP  1   7   1\nM  END")
    core = build_query(molblock=drawn)
    assert core["core_attachment_points"] == 2 and core["core_issue"] is None
    assert Chem.MolFromSmarts(core["core_smarts"]).GetNumAtoms() == 6
    # A and Q are real query atoms, not attachment points.
    kept = build_query(text="c1ccccc1[!#1]")
    assert kept["core_attachment_points"] == 0 and kept["core_smarts"] == "c1ccccc1[!#1]"
    assert build_query(text="c1ccccc1.C")["core_issue"].startswith("The core must be one connected piece")
    assert build_query(text="C*")["core_smarts"] is None


def test_core_from_a_structure_search_drives_the_rgroup_analysis(search_app):
    client = search_app.test_client()
    project_id, ids = _seed(client)
    found = client.post("/api/v1/search/substructure", json={"project_id": project_id, "query": "*c1ccc(cc1)CC(=O)Nc1ccccn1"}).get_json()
    assert found["query"]["core_attachment_points"] == 1
    core = found["query"]["core_smarts"]
    response = client.post("/api/v1/analysis/rgroup", json={"project_id": project_id, "scaffold_smarts": core})
    assert response.status_code in (200, 201), response.get_json()
    assignments = {item["compound_id"]: item for item in response.get_json()["assignments"]}
    assert assignments[ids["SS-002"]]["assignments"] == {"R1": "Cl"}
    assert assignments[ids["SS-003"]]["status"] == "unmatched"  # 3-pyridyl amide is outside the drawn core
    html = client.get(f"/workspace/analysis?project_id={project_id}").get_data(as_text=True)
    assert "data-draw-core" in html and 'data-structure-use="declared-core-drawing"' in html and "data-structure-use-drawing hidden" in html
    # The R-site map is drawn from the query core instead of disappearing.
    assert 'aria-label="Shared scaffold with positions R1' in html
    production = (Path(__file__).parents[1] / "static" / "production.js").read_text(encoding="utf-8")
    assert 'id: "declared-core"' in production and 'id: "declared-core-drawing"' in production and 'scope: "drawing"' in production
    assert "/api/v1/structure/core" in (Path(__file__).parents[1] / "static" / "structure_search.js").read_text(encoding="utf-8")


def test_core_api_turns_a_drawing_straight_into_core_smarts(search_app):
    client = search_app.test_client()
    url = "/api/v1/structure/core"
    drawn = _molblock(["C"] * 6 + ["R#"], [(a, b, 2 if a % 2 else 1) for a, b in RING6] + [(1, 7, 1)]).replace("M  END", "M  RGP  1   7   1\nM  END")
    response = client.post(url, json={"molblock": drawn})
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["result"]
    assert result["core_smarts"] == "[#6]1:[#6]:[#6]:[#6]:[#6]:[#6]:1" and result["attachment_points"] == 1
    assert response.get_json()["data_origin"] == "derived"
    typed = client.post(url, json={"query": "[N,C]1CCOCC1"}).get_json()["result"]
    assert "[N,C]" in typed["core_smarts"] or "[#7,#6]" in typed["core_smarts"]
    split = client.post(url, json={"query": "c1ccccc1.C"})
    assert split.status_code == 422 and split.get_json()["error"] == "invalid_core"
    assert client.post(url, json={}).get_json()["error"] == "empty_query"