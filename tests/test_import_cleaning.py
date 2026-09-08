from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app import create_app
from database import read_connection
from import_formats import SheetSelectionRequired, UploadFormatError, profile_upload, read_tabular_upload
from import_pipeline import preview_upload


def _xlsx_bytes(*, second_visible: bool = False, include_formula: bool = True, metadata_formula: bool = False) -> bytes:
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="6" uniqueCount="6">'
        '<si><t>compound_id</t></si><si><t>smiles</t></si><si><t>assay</t></si>'
        '<si><t>result</t></si><si><t>unit</t></si><si><t>date</t></si></sst>'
    )
    second_state = "" if second_visible else ' state="hidden"'
    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        '<sheet name="Data" sheetId="1" r:id="rId1"/>'
        f'<sheet name="Notes" sheetId="2"{second_state} r:id="rId2"/>'
        '</sheets></workbook>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="worksheet" Target="worksheets/sheet2.xml"/>'
        '</Relationships>'
    )
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/xl/workbook.xml" ContentType="workbook"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="worksheet"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="worksheet"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="sharedStrings"/>'
        '</Types>'
    )
    result_cell = '<c r="D2"><f>10</f><v>10</v></c>' if include_formula else '<c r="D2"><v>10</v></c>'
    metadata_cell = '<c r="F2"><f>10</f><v>45000</v></c>' if metadata_formula else ''
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
        '<c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c><c r="F1" t="s"><v>5</v></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>CMP-1</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>CCO</t></is></c>'
        '<c r="C2" t="inlineStr"><is><t>IC50</t></is></c>'
        f'{result_cell}<c r="E2" t="inlineStr"><is><t>nM</t></is></c>{metadata_cell}</row>'
        '</sheetData><mergeCells count="1"><mergeCell ref="A1:A1"/></mergeCells></worksheet>'
    )
    empty = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in {
            "[Content_Types].xml": content_types,
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": rels,
            "xl/sharedStrings.xml": shared,
            "xl/worksheets/sheet1.xml": sheet,
            "xl/worksheets/sheet2.xml": empty,
        }.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def test_xlsx_profile_requires_selection_for_multiple_visible_sheets():
    content = _xlsx_bytes(second_visible=True)
    profile = profile_upload(content, "measurements.xlsx")
    assert profile["file_format"] == "xlsx"
    assert profile["requires_sheet_selection"] is True
    assert [sheet["name"] for sheet in profile["sheets"]] == ["Data", "Notes"]
    with pytest.raises(SheetSelectionRequired):
        read_tabular_upload(content, "measurements.xlsx")


def test_xlsx_preview_preserves_formula_warning_and_cell_locations():
    preview = preview_upload(_xlsx_bytes(), "measurements.xlsx", sheet_name="Data")
    assert preview["accepted_count"] == 0
    row = preview["rows"][0]
    assert row.source_location["sheet"] == "Data"
    assert row.source_location["cells"]["result"] == "D2"
    assert row.source_location["formula_cells"] == ["D2"]
    assert any(error["code"] == "formula_scientific_value" for error in row.errors)
    assert any("Formula cells detected" in warning for warning in row.warnings)
    assert any("merged cells detected" in warning for warning in preview["profile"]["warnings"])


def test_unsupported_excel_variants_and_unsafe_zip_fail_closed():
    with pytest.raises(UploadFormatError, match="Macro-enabled"):
        profile_upload(b"not a workbook", "measurements.xlsm")
    with pytest.raises(UploadFormatError, match="unsafe ZIP member"):
        with io.BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("../outside.xml", "bad")
            profile_upload(buffer.getvalue(), "measurements.xlsx")


def test_profile_and_preview_api_persist_cleaning_metadata(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-cleaning",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
        }
    )
    client = app.test_client()
    content = _xlsx_bytes()
    profile_response = client.post(
        "/api/v1/imports/profile",
        data={"file": (io.BytesIO(content), "measurements.xlsx")},
        content_type="multipart/form-data",
    )
    assert profile_response.status_code == 200
    assert profile_response.get_json()["profile"]["file_format"] == "xlsx"

    preview_response = client.post(
        "/api/v1/imports/preview",
        data={
            "file": (io.BytesIO(content), "measurements.xlsx"),
            "sheet_name": "Data",
            "header_row": "1",
            "data_start_row": "2",
        },
        content_type="multipart/form-data",
    )
    assert preview_response.status_code == 201
    payload = preview_response.get_json()
    assert payload["file_format"] == "xlsx"
    assert payload["formula_acknowledged"] is False
    assert payload["rows"][0]["source_location"]["cells"]["result"] == "D2"

    report_response = client.get(f"/api/v1/imports/{payload['import_id']}")
    assert report_response.status_code == 200
    report = report_response.get_json()
    assert report["formula_policy_version"] == "formula-safe-v1"
    assert report["mapping_issues"] == []
    assert report["rows"][0]["source_location"]["sheet"] == "Data"

    with read_connection(str(tmp_path / "sar.db")) as connection:
        source = connection.execute("SELECT file_format, profile_json FROM source_documents").fetchone()
        batch = connection.execute(
            "SELECT selected_sheet, header_row, data_start_row, cleaning_policy_version FROM import_batches"
        ).fetchone()
        row = connection.execute("SELECT source_location_json FROM import_rows").fetchone()
    assert source["file_format"] == "xlsx"
    assert '"sheet_name": "Data"' in source["profile_json"]
    assert batch["selected_sheet"] == "Data"
    assert batch["header_row"] == 1
    assert batch["data_start_row"] == 2
    assert batch["cleaning_policy_version"] == "import-clean-v1"
    assert '"sheet": "Data"' in row["source_location_json"]


def test_duplicate_required_headers_are_blocked_until_explicit_mapping():
    content = (
        b"compound_id,compound_id,smiles,assay,result,unit\n"
        b"CMP-1,CMP-1-shadow,CCO,IC50,10,nM\n"
    )
    blocked = preview_upload(content, "duplicate.csv")
    assert blocked["accepted_count"] == 0
    assert blocked["mapping"]["compound_id"] is None
    assert any(issue["severity"] == "blocking" for issue in blocked["mapping_issues"])
    assert blocked["rows"][0].raw["compound_id"] == "CMP-1"
    assert blocked["rows"][0].raw["compound_id__duplicate_2"] == "CMP-1-shadow"

    selected = preview_upload(
        content,
        "duplicate.csv",
        mapping_overrides={"compound_id": "compound_id__duplicate_2"},
    )
    assert selected["accepted_count"] == 1
    assert selected["mapping_confidence"]["compound_id"] == "explicit"
    assert any(issue["severity"] == "warning" for issue in selected["mapping_issues"])


def test_metadata_formula_requires_explicit_acknowledgment_without_evaluation():
    content = _xlsx_bytes(include_formula=False, metadata_formula=True)
    blocked = preview_upload(content, "metadata-formula.xlsx", sheet_name="Data")
    assert blocked["accepted_count"] == 0
    assert any(error["code"] == "formula_acknowledgment_required" for error in blocked["rows"][0].errors)

    acknowledged = preview_upload(
        content,
        "metadata-formula.xlsx",
        sheet_name="Data",
        formula_acknowledged=True,
    )
    assert acknowledged["accepted_count"] == 1
    assert any("acknowledged without evaluation" in warning for warning in acknowledged["rows"][0].warnings)


def test_staged_preview_can_be_revised_without_creating_duplicate_source(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-revision",
            "DATABASE_PATH": str(tmp_path / "sar.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
        }
    )
    client = app.test_client()
    csv_text = "compound_id,compound_id,smiles,assay,result,unit\nCMP-1,CMP-1-shadow,CCO,IC50,10,nM\n"
    first = client.post(
        "/api/v1/imports/preview",
        json={"filename": "revision.csv", "csv_text": csv_text},
    ).get_json()
    second_response = client.post(
        "/api/v1/imports/preview",
        json={
            "filename": "revision.csv",
            "csv_text": csv_text,
            "mapping_overrides": {"compound_id": "compound_id__duplicate_2"},
        },
    )
    assert second_response.status_code == 201
    second = second_response.get_json()
    assert second["import_id"] == first["import_id"]
    assert second["accepted_count"] == 1

    with read_connection(str(tmp_path / "sar.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_rows").fetchone()[0] == 1
        operations = [
            row["operation"]
            for row in connection.execute(
                "SELECT operation FROM audit_events WHERE resource_id = ?",
                (first["import_id"],),
            )
        ]
    assert "import_preview_revision" in operations
