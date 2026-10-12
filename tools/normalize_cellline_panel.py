#!/usr/bin/env python3
"""Convert CellLinePanel_pIC50s.xlsx to SAR Workbench long-format CSV."""
from __future__ import annotations

import csv
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CELL_LINES = ["U2OS", "SJSA-1", "G292", "KNS42", "GBM2", "CHLA90"]


def excel_column(cell_ref: str) -> str:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        raise ValueError(f"Invalid cell reference: {cell_ref}")
    return match.group(1)


def read_sheet_rows(workbook: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(workbook) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(text.text or "" for text in item.findall(".//a:t", NS))
                for item in root.findall("a:si", NS)
            ]

        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows: list[dict[str, str]] = []
        for xml_row in sheet.findall(".//a:sheetData/a:row", NS):
            row: dict[str, str] = {}
            for cell in xml_row.findall("a:c", NS):
                value = cell.find("a:v", NS)
                text = "" if value is None else value.text or ""
                if cell.attrib.get("t") == "s" and text:
                    text = shared_strings[int(text)]
                row[excel_column(cell.attrib["r"])] = text
            if row:
                rows.append(row)
        return rows


def normalize(workbook: Path, output: Path) -> tuple[int, int]:
    rows = read_sheet_rows(workbook)
    if not rows or rows[0].get("A") != "Sample_ID":
        raise ValueError("Expected Sample_ID header in the first worksheet")

    output.parent.mkdir(parents=True, exist_ok=True)
    measurement_count = 0
    compound_ids: set[str] = set()
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "compound_id", "structure", "assay", "result", "unit", "core", "acid_ester"
        ])
        for row in rows[1:]:
            required = [row.get(column, "") for column in "ABCDEF"]
            if not all(required):
                continue
            sample_id, smiles, core, acid_ester = row["A"], row["B"], row["C"], row["D"]
            compound_ids.add(sample_id)
            for cell_line, column in zip(CELL_LINES, "EFGHIJ"):
                writer.writerow([
                    sample_id,
                    smiles,
                    cell_line,
                    row[column],
                    "pIC50",
                    core,
                    acid_ester,
                ])
                measurement_count += 1
    return len(compound_ids), measurement_count


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} INPUT.xlsx OUTPUT.csv", file=sys.stderr)
        return 2
    compounds, measurements = normalize(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"normalized_compounds={compounds}")
    print(f"normalized_measurements={measurements}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
