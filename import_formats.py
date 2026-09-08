"""Safe, bounded profiling and extraction for user-supplied tabular files.

This module deliberately supports a narrow, dependency-free subset of Excel:

* CSV/TSV are decoded as UTF-8 and profiled as rectangular text.
* XLSX worksheets are read from the OOXML ZIP package without evaluating
  formulas, loading macros, following external links, or executing anything.
* Legacy .xls, macro-bearing .xlsm, encrypted workbooks, and unknown formats
  fail closed until separately qualified.

Returned values are source observations for a quarantine preview. They are not
scientific interpretations and must not replace the original upload.
"""

from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from hashlib import sha256
from typing import Any
from xml.etree import ElementTree as ET


DEFAULT_MAX_ROWS = 100_000
DEFAULT_MAX_COLUMNS = 256
DEFAULT_MAX_ZIP_MEMBERS = 2_000
DEFAULT_MAX_ZIP_UNCOMPRESSED = 128 * 1024 * 1024
DEFAULT_MAX_MEMBER_BYTES = 64 * 1024 * 1024


class UploadFormatError(ValueError):
    """Raised when a file cannot be safely profiled or extracted."""


class SheetSelectionRequired(UploadFormatError):
    """Raised when an XLSX contains multiple candidate worksheets."""

    def __init__(self, profile: dict[str, Any]):
        super().__init__("Select a worksheet before previewing this Excel file")
        self.profile = profile


@dataclass(frozen=True)
class TabularRows:
    file_format: str
    headers: list[str]
    rows: list[dict[str, Any]]
    locations: list[dict[str, Any]]
    profile: dict[str, Any]
    column_keys: list[str] = field(default_factory=list)


def _format_for_filename(filename: str) -> str:
    suffix = PurePosixPath(str(filename or "")).suffix.lower()
    if suffix in {".csv", ""}:
        return "csv"
    if suffix == ".tsv":
        return "tsv"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".xlsm":
        raise UploadFormatError("Macro-enabled Excel files (.xlsm) are not accepted")
    if suffix == ".xls":
        raise UploadFormatError("Legacy .xls files must be converted to .xlsx or CSV before upload")
    raise UploadFormatError("Unsupported upload format; use CSV, TSV, or XLSX")


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadFormatError("CSV/TSV files must be UTF-8 encoded") from exc


def _delimiter_for(text: str, file_format: str) -> str:
    if file_format == "tsv":
        return "\t"
    sample = text[:64_000]
    candidates = [",", "\t", ";", "|"]
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {candidate: first_line.count(candidate) for candidate in candidates}
    best = max(counts.values(), default=0)
    if best == 0:
        return ","
    winners = [candidate for candidate, count in counts.items() if count == best]
    if len(winners) > 1:
        raise UploadFormatError("CSV delimiter is ambiguous; choose comma, tab, semicolon, or pipe")
    return winners[0]


def _column_keys(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    used: set[str] = set()
    keys: list[str] = []
    for index, header in enumerate(headers, start=1):
        base = str(header).strip() or f"Unnamed: {index}"
        occurrence = seen.get(base, 0) + 1
        seen[base] = occurrence
        key = base if occurrence == 1 else f"{base}__duplicate_{occurrence}"
        while key in used:
            key = f"{base}__column_{index}"
        used.add(key)
        keys.append(key)
    return keys


def _bounded_text_profile(content: bytes, filename: str, max_rows: int, max_columns: int) -> dict[str, Any]:
    file_format = _format_for_filename(filename)
    text = _decode_text(content)
    delimiter = _delimiter_for(text, file_format)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise UploadFormatError("Tabular file must contain a header row") from exc
    if not headers or all(not str(header).strip() for header in headers):
        raise UploadFormatError("Tabular file must contain a non-empty header row")
    if len(headers) > max_columns:
        raise UploadFormatError(f"File contains more than the {max_columns} column limit")
    row_count = 0
    max_seen_columns = len(headers)
    for row_count, row in enumerate(reader, start=1):
        if row_count > max_rows:
            raise UploadFormatError(f"File contains more than the {max_rows} row limit")
        max_seen_columns = max(max_seen_columns, len(row))
        if max_seen_columns > max_columns:
            raise UploadFormatError(f"File contains more than the {max_columns} column limit")
    return {
        "file_format": file_format,
        "encoding": "utf-8-sig",
        "delimiter": delimiter,
        "headers": [str(header) for header in headers],
        "row_count": row_count,
        "column_count": max_seen_columns,
        "sheets": [{"name": "CSV", "state": "visible", "row_count": row_count, "column_count": max_seen_columns}],
        "warnings": [],
    }


def _xml_bytes(value: bytes, member: str) -> ET.Element:
    if b"<!DOCTYPE" in value.upper() or b"<!ENTITY" in value.upper():
        raise UploadFormatError(f"Workbook member {member} contains a prohibited XML declaration")
    try:
        return ET.fromstring(value)
    except ET.ParseError as exc:
        raise UploadFormatError(f"Workbook member {member} is not valid XML") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_zip(content: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise UploadFormatError("XLSX upload is not a valid ZIP package") from exc
    members = archive.infolist()
    if len(members) > DEFAULT_MAX_ZIP_MEMBERS:
        archive.close()
        raise UploadFormatError("XLSX contains too many ZIP members")
    total = 0
    for info in members:
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            archive.close()
            raise UploadFormatError("XLSX contains an unsafe ZIP member path")
        if info.file_size > DEFAULT_MAX_MEMBER_BYTES:
            archive.close()
            raise UploadFormatError("XLSX member exceeds the per-member size limit")
        if info.compress_size and info.file_size / info.compress_size > 250:
            archive.close()
            raise UploadFormatError("XLSX member has an unsafe compression ratio")
        total += info.file_size
    if total > DEFAULT_MAX_ZIP_UNCOMPRESSED:
        archive.close()
        raise UploadFormatError("XLSX expanded content exceeds the safety limit")
    names = {info.filename.replace("\\", "/") for info in members}
    if "xl/vbaProject.bin" in names or any(name.startswith("xl/externalLinks/") for name in names):
        archive.close()
        raise UploadFormatError("XLSX macros and external workbook links are not accepted")
    for name in names:
        if not name.endswith(".rels"):
            continue
        relation_xml = archive.read(name).lower()
        if b"targetmode=\"external\"" in relation_xml or b"target='http" in relation_xml or b"target=\"http" in relation_xml:
            archive.close()
            raise UploadFormatError("XLSX external workbook links are not accepted")
    return archive


def _workbook_sheets(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    try:
        workbook = _xml_bytes(archive.read("xl/workbook.xml"), "xl/workbook.xml")
        relationships = _xml_bytes(archive.read("xl/_rels/workbook.xml.rels"), "xl/_rels/workbook.xml.rels")
    except KeyError as exc:
        raise UploadFormatError("XLSX package is missing workbook metadata") from exc
    relationship_map = {}
    for relationship in relationships.iter():
        if _local_name(relationship.tag) == "Relationship":
            relationship_map[relationship.attrib.get("Id", "")] = relationship.attrib.get("Target", "")
    sheets: list[dict[str, Any]] = []
    for element in workbook.iter():
        if _local_name(element.tag) != "sheet":
            continue
        relation_id = element.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
        target = relationship_map.get(relation_id, "")
        if not target:
            raise UploadFormatError("XLSX worksheet relationship is missing")
        target = target.lstrip("/")
        target = target if target.startswith("xl/") else posixpath.join("xl", target)
        target = posixpath.normpath(target)
        if not target.startswith("xl/") or target.startswith("xl/../"):
            raise UploadFormatError("XLSX worksheet relationship escapes the package")
        sheets.append({
            "name": element.attrib.get("name", ""),
            "state": element.attrib.get("state", "visible"),
            "path": target,
        })
    if not sheets:
        raise UploadFormatError("XLSX contains no worksheets")
    return sheets


def _worksheet_profile(archive: zipfile.ZipFile, sheet: dict[str, Any]) -> dict[str, Any]:
    try:
        root = _xml_bytes(archive.read(sheet["path"]), sheet["path"])
    except KeyError as exc:
        raise UploadFormatError(f"Worksheet {sheet['name']} is missing from the XLSX package") from exc
    row_elements = [element for element in root.iter() if _local_name(element.tag) == "row"]
    formula_count = sum(
        1
        for cell in root.iter()
        if _local_name(cell.tag) == "f"
    )
    merged_ranges = [element.attrib.get("ref", "") for element in root.iter() if _local_name(element.tag) == "mergeCell"]
    hidden_rows = sum(1 for row in row_elements if row.attrib.get("hidden") == "1")
    hidden_columns = 0
    max_column = 0
    for element in root.iter():
        if _local_name(element.tag) == "col" and element.attrib.get("hidden") == "1":
            try:
                first = int(element.attrib.get("min", "1"))
                last = int(element.attrib.get("max", str(first)))
            except ValueError as exc:
                raise UploadFormatError("XLSX worksheet contains an invalid column range") from exc
            hidden_columns += max(0, last - first + 1)
        elif _local_name(element.tag) == "c":
            reference = element.attrib.get("r", "")
            max_column = max(max_column, _column_number(reference))
    warnings: list[str] = []
    if sheet["state"] != "visible":
        warnings.append("worksheet is hidden")
    if formula_count:
        warnings.append("formula cells detected; cached values were not evaluated")
    if merged_ranges:
        warnings.append("merged cells detected; header interpretation requires review")
    if hidden_rows or hidden_columns:
        warnings.append("hidden worksheet rows or columns detected; review source visibility")
    return {
        "name": sheet["name"],
        "state": sheet["state"],
        "row_count": len(row_elements),
        "column_count": max_column,
        "formula_cell_count": formula_count,
        "merged_cell_count": len(merged_ranges),
        "hidden_row_count": hidden_rows,
        "hidden_column_count": hidden_columns,
        "warnings": warnings,
    }


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = _xml_bytes(archive.read("xl/sharedStrings.xml"), "xl/sharedStrings.xml")
    except KeyError:
        return []
    values = []
    for item in root.iter():
        if _local_name(item.tag) != "si":
            continue
        values.append("".join(part.text or "" for part in item.iter() if _local_name(part.tag) == "t"))
    return values


_CELL_REF = re.compile(r"^([A-Z]+)([0-9]+)$")


def _column_number(reference: str) -> int:
    match = _CELL_REF.match(reference.upper())
    if not match:
        raise UploadFormatError(f"Invalid XLSX cell reference: {reference}")
    number = 0
    for character in match.group(1):
        number = number * 26 + ord(character) - 64
    return number


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> tuple[Any, bool]:
    formula = any(_local_name(child.tag) == "f" for child in cell)
    cell_type = cell.attrib.get("t", "")
    value_node = next((child for child in cell if _local_name(child.tag) == "v"), None)
    raw_value = value_node.text if value_node is not None else None
    if cell_type == "inlineStr":
        value = "".join(child.text or "" for child in cell.iter() if _local_name(child.tag) == "t")
    elif cell_type == "s":
        try:
            value = shared_strings[int(raw_value or "0")]
        except (ValueError, IndexError) as exc:
            raise UploadFormatError("XLSX shared-string reference is invalid") from exc
    elif cell_type == "b":
        value = (raw_value or "0") == "1"
    else:
        value = raw_value if raw_value is not None else ""
    return value, formula


def _xlsx_rows(
    archive: zipfile.ZipFile,
    sheet: dict[str, Any],
    shared_strings: list[str],
    header_row: int,
    data_start_row: int,
    max_rows: int,
    max_columns: int,
) -> TabularRows:
    try:
        root = _xml_bytes(archive.read(sheet["path"]), sheet["path"])
    except KeyError as exc:
        raise UploadFormatError(f"Worksheet {sheet['name']} is missing from the XLSX package") from exc
    merged_ranges = [element.attrib.get("ref", "") for element in root.iter() if _local_name(element.tag) == "mergeCell"]
    hidden_rows = 0
    hidden_columns = 0
    for column_group in root.iter():
        if _local_name(column_group.tag) != "col" or column_group.attrib.get("hidden") != "1":
            continue
        try:
            first = int(column_group.attrib.get("min", "1"))
            last = int(column_group.attrib.get("max", str(first)))
        except ValueError as exc:
            raise UploadFormatError("XLSX worksheet contains an invalid column range") from exc
        hidden_columns += max(0, last - first + 1)
    parsed_rows: dict[int, dict[int, Any]] = {}
    formula_locations: dict[int, list[str]] = {}
    for row_element in root.iter():
        if _local_name(row_element.tag) != "row":
            continue
        try:
            row_number = int(row_element.attrib.get("r", "0"))
        except ValueError as exc:
            raise UploadFormatError("XLSX worksheet contains an invalid row number") from exc
        if row_element.attrib.get("hidden") == "1":
            hidden_rows += 1
        values: dict[int, Any] = {}
        formulas: list[str] = []
        for cell in row_element:
            if _local_name(cell.tag) != "c":
                continue
            reference = cell.attrib.get("r", "")
            column = _column_number(reference)
            if column > max_columns:
                raise UploadFormatError(f"Worksheet exceeds the {max_columns} column limit")
            value, formula = _cell_value(cell, shared_strings)
            values[column] = value
            if formula:
                formulas.append(reference)
        parsed_rows[row_number] = values
        if formulas:
            formula_locations[row_number] = formulas
    header_values = parsed_rows.get(header_row)
    if not header_values:
        raise UploadFormatError(f"Header row {header_row} is empty or not present")
    max_column = max(header_values.keys(), default=0)
    headers = [str(header_values.get(column, "")).strip() for column in range(1, max_column + 1)]
    if not any(headers):
        raise UploadFormatError("Selected worksheet header row is empty")
    column_keys = _column_keys(headers)
    rows: list[dict[str, Any]] = []
    locations: list[dict[str, Any]] = []
    for row_number in sorted(number for number in parsed_rows if number >= data_start_row):
        values = parsed_rows[row_number]
        if not values or all(str(value).strip() == "" for value in values.values()):
            continue
        if len(rows) >= max_rows:
            raise UploadFormatError(f"Worksheet contains more than the {max_rows} row limit")
        row: dict[str, Any] = {}
        cells: dict[str, str] = {}
        for column, (header, key) in enumerate(zip(headers, column_keys), start=1):
            row[key] = values.get(column, "")
            cells[key] = f"{_column_letter(column)}{row_number}"
        rows.append(row)
        locations.append({
            "sheet": sheet["name"],
            "row": row_number,
            "cells": cells,
            "formula_cells": formula_locations.get(row_number, []),
        })
    profile = {
        "file_format": "xlsx",
        "sheet_name": sheet["name"],
        "sheet_state": sheet["state"],
        "header_row": header_row,
        "data_start_row": data_start_row,
        "headers": headers,
        "row_count": len(rows),
        "column_count": len(headers),
        "merged_ranges": merged_ranges[:500],
        "hidden_row_count": hidden_rows,
        "hidden_column_count": hidden_columns,
        "formula_cell_count": sum(len(value) for value in formula_locations.values()),
        "warnings": (["selected worksheet is hidden"] if sheet["state"] != "visible" else [])
        + (["formula cells detected; cached values were not evaluated"] if formula_locations else [])
        + (["merged cells detected; header interpretation requires review"] if merged_ranges else []),
    }
    return TabularRows("xlsx", headers, rows, locations, profile, column_keys)


def _column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def profile_upload(
    content: bytes,
    filename: str,
    *,
    max_bytes: int = 5_000_000,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_columns: int = DEFAULT_MAX_COLUMNS,
) -> dict[str, Any]:
    if len(content) > max_bytes:
        raise UploadFormatError(f"Upload exceeds the {max_bytes} byte limit")
    file_format = _format_for_filename(filename)
    if file_format in {"csv", "tsv"}:
        profile = _bounded_text_profile(content, filename, max_rows, max_columns)
    else:
        archive = _safe_zip(content)
        try:
            sheets = _workbook_sheets(archive)
            sheet_profiles = [_worksheet_profile(archive, sheet) for sheet in sheets]
            warnings = []
            for sheet_profile in sheet_profiles:
                warnings.extend(
                    f"{sheet_profile['name']}: {warning}"
                    for warning in sheet_profile["warnings"]
                )
            profile = {
                "file_format": "xlsx",
                "encoding": "OOXML",
                "headers": [],
                "row_count": None,
                "column_count": None,
                "sheets": sheet_profiles,
                "requires_sheet_selection": len([sheet for sheet in sheets if sheet["state"] == "visible"]) != 1,
                "warnings": warnings,
            }
        finally:
            archive.close()
    profile.update({"filename": str(filename), "byte_size": len(content), "sha256": sha256(content).hexdigest()})
    return profile


def read_tabular_upload(
    content: bytes,
    filename: str,
    *,
    sheet_name: str | None = None,
    header_row: int = 1,
    data_start_row: int | None = None,
    max_bytes: int = 5_000_000,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_columns: int = DEFAULT_MAX_COLUMNS,
) -> TabularRows:
    profile = profile_upload(content, filename, max_bytes=max_bytes, max_rows=max_rows, max_columns=max_columns)
    file_format = profile["file_format"]
    if file_format in {"csv", "tsv"}:
        delimiter = profile["delimiter"]
        text = _decode_text(content)
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        headers = [str(value) for value in next(reader)]
        column_keys = _column_keys(headers)
        rows: list[dict[str, Any]] = []
        locations: list[dict[str, Any]] = []
        for row_number, values in enumerate(reader, start=2):
            if len(rows) >= max_rows:
                raise UploadFormatError(f"File contains more than the {max_rows} row limit")
            row = {}
            for index, key in enumerate(column_keys):
                row[key] = values[index] if index < len(values) else ""
            rows.append(row)
            locations.append({"row": row_number, "cells": {key: f"{_column_letter(index + 1)}{row_number}" for index, key in enumerate(column_keys)}})
        profile["data_start_row"] = 2
        return TabularRows(file_format, headers, rows, locations, profile, column_keys)

    archive = _safe_zip(content)
    try:
        sheets = _workbook_sheets(archive)
        visible = [sheet for sheet in sheets if sheet["state"] == "visible"]
        selected = next((sheet for sheet in sheets if sheet["name"] == sheet_name), None) if sheet_name else None
        if selected is None:
            if len(visible) != 1:
                profile["requires_sheet_selection"] = True
                raise SheetSelectionRequired(profile)
            selected = visible[0]
        if header_row < 1 or header_row > 1_000_000:
            raise UploadFormatError("Header row must be a positive integer")
        start_row = data_start_row if data_start_row is not None else header_row + 1
        if start_row <= header_row:
            raise UploadFormatError("Data start row must be below the header row")
        selected_rows = _xlsx_rows(archive, selected, _shared_strings(archive), header_row, start_row, max_rows, max_columns)
        selected_profile = selected_rows.profile
        selected_profile["sheets"] = profile.get("sheets", [])
        selected_profile["workbook_warnings"] = profile.get("warnings", [])
        selected_profile["warnings"] = list(dict.fromkeys(
            [*selected_profile.get("warnings", []), *profile.get("warnings", [])]
        ))
        return TabularRows(
            selected_rows.file_format,
            selected_rows.headers,
            selected_rows.rows,
            selected_rows.locations,
            selected_profile,
            selected_rows.column_keys,
        )
    finally:
        archive.close()
