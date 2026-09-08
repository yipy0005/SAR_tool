from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chemistry import StructureValidationError, standardize_structure
from database import read_connection, transaction
from import_formats import SheetSelectionRequired, UploadFormatError, profile_upload, read_tabular_upload
from scientific import MeasurementValidationError, normalize_measurement


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "compound_id": ("compound_id", "compound", "compound_identifier", "registration_id", "id"),
    "structure": ("smiles", "isomeric_smiles", "structure", "canonical_smiles", "molfile", "mol"),
    "assay": ("assay", "assay_name", "endpoint", "endpoint_code", "test"),
    "result": ("result", "value", "measurement", "activity", "response", "ic50", "ec50", "ki", "kd"),
    "unit": ("unit", "units", "measurement_unit", "result_unit"),
    "qualifier": ("qualifier", "relation", "operator", "censor", "comparison"),
    "replicate": ("replicate", "replicate_index", "replicate_number"),
    "date": ("date", "experimental_date", "run_date", "assay_date"),
}

REQUIRED_MAPPING_FIELDS = ("compound_id", "structure", "assay", "result", "unit")
SCIENTIFIC_FORMULA_FIELDS = frozenset((*REQUIRED_MAPPING_FIELDS, "qualifier"))
FORMULA_POLICY_VERSION = "formula-safe-v1"


class ImportValidationError(ValueError):
    pass


class DuplicateImportError(ValueError):
    def __init__(self, message: str, *, import_id: str | None = None, status: str | None = None):
        super().__init__(message)
        self.import_id = import_id
        self.status = status


class ImportConflictError(ValueError):
    pass


@dataclass(frozen=True)
class PreviewRow:
    source_row_id: str
    raw: dict[str, Any]
    normalized: dict[str, Any] | None
    errors: tuple[dict[str, str], ...]
    warnings: tuple[str, ...]
    source_location: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return not self.errors and self.normalized is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _header_key(value: str) -> str:
    return "".join(character.lower() for character in str(value).strip() if character.isalnum())


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


def mapping_candidates(headers: list[str], column_keys: list[str] | None = None) -> dict[str, list[str]]:
    keys = column_keys or _column_keys(headers)
    candidates: dict[str, list[str]] = {}
    for field, aliases in FIELD_ALIASES.items():
        aliases_by_key = {_header_key(alias) for alias in aliases}
        candidates[field] = [
            key for header, key in zip(headers, keys) if _header_key(header) in aliases_by_key
        ]
    return candidates


def infer_mapping(
    headers: list[str],
    column_keys: list[str] | None = None,
    mapping_overrides: dict[str, str] | None = None,
) -> dict[str, str | None]:
    keys = column_keys or _column_keys(headers)
    candidates = mapping_candidates(headers, keys)
    overrides = mapping_overrides or {}
    unknown_fields = sorted(set(overrides) - set(FIELD_ALIASES))
    if unknown_fields:
        raise ImportValidationError(f"Mapping override contains unknown fields: {', '.join(unknown_fields)}")
    mapping: dict[str, str | None] = {}
    for field in FIELD_ALIASES:
        selected = str(overrides.get(field, "")).strip()
        if selected:
            if selected not in candidates[field]:
                raise ImportValidationError(f"Mapping override for {field} does not identify a matching source column")
            mapping[field] = selected
        else:
            mapping[field] = candidates[field][0] if len(candidates[field]) == 1 else None
    return mapping


def _row_value(row: dict[str, Any], mapping: dict[str, str | None], field: str) -> str:
    header = mapping.get(field)
    return "" if not header else str(row.get(header, "") or "").strip()


def preview_csv(content: bytes, filename: str, max_bytes: int = 5_000_000) -> dict[str, Any]:
    """Backward-compatible CSV entry point using the bounded tabular reader."""
    return preview_upload(content, filename, max_bytes=max_bytes)


def _write_quarantine_file(content: bytes, upload_dir: str, sha256: str) -> str:
    target_dir = Path(upload_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sha256}.upload"
    if not target.exists():
        temporary = target_dir / f".{sha256}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return str(target)


def _public_preview(import_id: str, source_id: str, preview: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for row in preview["rows"]:
        rows.append(
            {
                "source_row_id": row.source_row_id,
                "source_location": row.source_location,
                "status": "accepted" if row.accepted else "rejected",
                "errors": list(row.errors),
                "warnings": list(row.warnings),
                "compound_id": row.normalized["compound_id"] if row.normalized else None,
                "assay": row.normalized["assay"] if row.normalized else None,
                "canonical_smiles": row.normalized["structure"]["isomeric_smiles"] if row.normalized else None,
                "canonical_value": row.normalized["measurement"]["canonical_value"] if row.normalized else None,
                "canonical_unit": row.normalized["measurement"]["canonical_unit"] if row.normalized else None,
            }
        )
    return {
        "import_id": import_id,
        "source_document_id": source_id,
        "status": "preview",
        "data_origin": "quarantined_import",
        "filename": preview["filename"],
        "file_format": preview.get("file_format", "csv"),
        "profile": preview.get("profile", {}),
        "sha256": preview["sha256"],
        "byte_size": preview["byte_size"],
        "headers": preview["headers"],
        "mapping": preview["mapping"],
        "mapping_options": preview.get("mapping_options", {}),
        "mapping_confidence": preview.get("mapping_confidence", {}),
        "mapping_issues": preview.get("mapping_issues", []),
        "missing_mapping_fields": preview["missing_mapping_fields"],
        "formula_acknowledged": preview.get("formula_acknowledged", False),
        "formula_policy_version": preview.get("formula_policy_version", FORMULA_POLICY_VERSION),
        "accepted_count": preview["accepted_count"],
        "rejected_count": preview["rejected_count"],
        "warning_count": preview["warning_count"],
        "rows": rows,
    }


def preview_upload(
    content: bytes,
    filename: str,
    max_bytes: int = 5_000_000,
    *,
    sheet_name: str | None = None,
    header_row: int = 1,
    data_start_row: int | None = None,
    mapping_overrides: dict[str, str] | None = None,
    formula_acknowledged: bool = False,
) -> dict[str, Any]:
    try:
        tabular = read_tabular_upload(
            content,
            filename,
            sheet_name=sheet_name,
            header_row=header_row,
            data_start_row=data_start_row,
            max_bytes=max_bytes,
        )
    except SheetSelectionRequired:
        raise
    except UploadFormatError as exc:
        raise ImportValidationError(str(exc)) from exc

    headers = tabular.headers
    column_keys = tabular.column_keys or _column_keys(headers)
    candidates = mapping_candidates(headers, column_keys)
    mapping = infer_mapping(headers, column_keys, mapping_overrides)
    required = list(REQUIRED_MAPPING_FIELDS)
    missing_fields = [field for field in required if not mapping.get(field)]
    normalized_header_keys = [_header_key(header) for header in headers]
    duplicate_headers = sorted({key for key in normalized_header_keys if normalized_header_keys.count(key) > 1 and key})
    mapping_issues: list[dict[str, Any]] = []
    for field, field_candidates in candidates.items():
        if len(field_candidates) > 1:
            explicitly_selected = bool(mapping_overrides and mapping_overrides.get(field) == mapping.get(field))
            severity = "warning" if explicitly_selected or field not in required else "blocking"
            code = "duplicate_required_header" if field in required else "duplicate_header"
            mapping_issues.append({
                "code": code,
                "field": field,
                "severity": severity,
                "message": f"Multiple source columns match {field}: {', '.join(field_candidates)}. "
                + ("Explicit mapping selected; retain the duplicate-header warning for review." if explicitly_selected else "Select an explicit mapping."),
            })
    duplicate_candidate_keys = {
        key
        for field_candidates in candidates.values()
        if len(field_candidates) > 1
        for key in field_candidates
    }
    if duplicate_headers and not duplicate_candidate_keys:
        mapping_issues.append({
            "code": "duplicate_headers",
            "severity": "warning",
            "message": f"Duplicate source header names retained for review: {', '.join(duplicate_headers)}",
        })
    if missing_fields:
        mapping_issues.append({
            "code": "missing_mapping_fields",
            "severity": "blocking",
            "fields": missing_fields,
            "message": f"Required columns are unmapped: {', '.join(missing_fields)}",
        })

    mapping_options = {
        field: [
            {
                "key": key,
                "label": header if key == header else f"{header} ({key})",
            }
            for header, key in zip(headers, column_keys)
            if key in field_candidates
        ]
        for field, field_candidates in candidates.items()
        if field_candidates
    }
    rows: list[PreviewRow] = []
    for number, (row, location) in enumerate(zip(tabular.rows, tabular.locations)):
        errors: list[dict[str, str]] = []
        warnings: list[str] = []
        formula_cells = list(location.get("formula_cells", []))
        if formula_cells:
            warnings.append("Formula cells detected; cached values were not evaluated: " + ", ".join(formula_cells[:20]))
            formula_field_by_address = {
                location.get("cells", {}).get(mapping[field]): field
                for field in mapping
                if mapping.get(field) and location.get("cells", {}).get(mapping[field])
            }
            for address in formula_cells:
                field = formula_field_by_address.get(address)
                if field in SCIENTIFIC_FORMULA_FIELDS:
                    errors.append({
                        "code": "formula_scientific_value",
                        "message": f"Formula in scientific field {field} at {address} is blocked; upload a values-only file.",
                    })
                elif not formula_acknowledged:
                    errors.append({
                        "code": "formula_acknowledgment_required",
                        "message": f"Formula at {address} is not evaluated. Explicitly acknowledge metadata-only formulas to continue.",
                    })
                else:
                    warnings.append(f"Metadata formula acknowledged without evaluation: {address}")
        compound_id = _row_value(row, mapping, "compound_id")
        structure = _row_value(row, mapping, "structure")
        assay = _row_value(row, mapping, "assay")
        result = _row_value(row, mapping, "result")
        unit = _row_value(row, mapping, "unit")
        qualifier = _row_value(row, mapping, "qualifier") or "="
        if not compound_id:
            errors.append({"code": "missing_compound_id", "message": "Compound identifier is required"})
        if not structure:
            errors.append({"code": "missing_structure", "message": "Structure is required"})
        if not assay:
            errors.append({"code": "missing_assay", "message": "Assay or endpoint is required"})
        if not result:
            errors.append({"code": "missing_result", "message": "Measurement result is required"})
        if not unit:
            errors.append({"code": "missing_unit", "message": "Measurement unit is required"})

        standardized = None
        normalized_measurement = None
        if not errors:
            try:
                standardized = standardize_structure(structure)
                warnings.extend(standardized.warnings)
            except StructureValidationError as exc:
                errors.append({"code": exc.code, "message": str(exc)})
            try:
                normalized_measurement = normalize_measurement(
                    result,
                    unit,
                    qualifier,
                    endpoint_code=assay,
                )
            except MeasurementValidationError as exc:
                errors.append({"code": exc.code, "message": str(exc)})

        normalized = None
        if not errors and standardized and normalized_measurement:
            normalized = {
                "compound_id": compound_id,
                "assay": assay,
                "date": _row_value(row, mapping, "date") or None,
                "replicate": _row_value(row, mapping, "replicate") or None,
                "structure": standardized.as_dict(),
                "measurement": normalized_measurement.as_dict(),
            }
        source_row_id = str(location.get("row", number + (data_start_row or 2)))
        rows.append(
            PreviewRow(
                source_row_id=source_row_id,
                raw=dict(row),
                normalized=normalized,
                errors=tuple(errors),
                warnings=tuple(warnings),
                source_location=dict(location),
            )
        )

    profile = dict(tabular.profile)
    profile.setdefault("warnings", [])
    mapping_confidence = {
        field: "explicit" if mapping_overrides and mapping_overrides.get(field) else (
            "inferred" if mapping.get(field) and len(candidates[field]) == 1 else (
                "ambiguous" if len(candidates[field]) > 1 else "unmapped"
            )
        )
        for field in FIELD_ALIASES
    }
    return {
        "filename": filename,
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
        "file_format": tabular.file_format,
        "profile": profile,
        "headers": headers,
        "mapping": mapping,
        "mapping_options": mapping_options,
        "mapping_confidence": mapping_confidence,
        "mapping_issues": mapping_issues,
        "missing_mapping_fields": missing_fields,
        "formula_acknowledged": bool(formula_acknowledged),
        "formula_policy_version": FORMULA_POLICY_VERSION,
        "rows": rows,
        "accepted_count": sum(row.accepted for row in rows),
        "rejected_count": sum(not row.accepted for row in rows),
        "warning_count": sum(len(row.warnings) for row in rows)
        + len(profile.get("warnings", []))
        + sum(issue.get("severity") == "warning" for issue in mapping_issues),
    }


def create_preview(
    database_path: str,
    upload_dir: str,
    content: bytes,
    filename: str,
    content_type: str = "text/csv",
    max_bytes: int = 5_000_000,
    *,
    actor_user_id: str | None = None,
    project_id: str | None = None,
    sheet_name: str | None = None,
    header_row: int = 1,
    data_start_row: int | None = None,
    mapping_overrides: dict[str, str] | None = None,
    formula_acknowledged: bool = False,
) -> dict[str, Any]:
    preview = preview_upload(
        content,
        filename,
        max_bytes=max_bytes,
        sheet_name=sheet_name,
        header_row=header_row,
        data_start_row=data_start_row,
        mapping_overrides=mapping_overrides,
        formula_acknowledged=formula_acknowledged,
    )
    storage_path = _write_quarantine_file(content, upload_dir, preview["sha256"])
    import_id = _id("imp")
    source_id = _id("src")
    now = _now()
    with transaction(database_path) as connection:
        duplicate_rows = connection.execute(
            """
            SELECT ib.id, ib.status, ib.project_id, ib.created_by_user_id, ib.source_document_id,
                   ib.mapping_json, ib.selected_sheet, ib.header_row, ib.data_start_row,
                   ib.formula_acknowledged, ib.formula_policy_version, ib.mapping_issues_json,
                   ib.created_at
            FROM import_batches AS ib
            JOIN source_documents AS sd ON sd.id = ib.source_document_id
            WHERE sd.sha256 = ?
            ORDER BY ib.created_at ASC, ib.id ASC
            """,
            (preview["sha256"],),
        ).fetchall()
        if project_id is None:
            duplicate = duplicate_rows[0] if duplicate_rows else None
        else:
            duplicate = next(
                (row for row in duplicate_rows if row["project_id"] == project_id),
                None,
            )
            if duplicate is None:
                duplicate = next(
                    (row for row in duplicate_rows if row["project_id"] is None),
                    None,
                )
        requested_mapping_json = json.dumps(preview["mapping"], sort_keys=True)
        requested_mapping_issues_json = json.dumps(
            preview.get("mapping_issues", []), ensure_ascii=False, sort_keys=True
        )
        requested_selected_sheet = preview.get("profile", {}).get("sheet_name")
        requested_header_row = int(preview.get("profile", {}).get("header_row", header_row))
        requested_data_start_row = int(preview.get("profile", {}).get("data_start_row", data_start_row or 2))
        same_preview = bool(
            duplicate
            and duplicate["mapping_json"] == requested_mapping_json
            and duplicate["selected_sheet"] == requested_selected_sheet
            and int(duplicate["header_row"] or 1) == requested_header_row
            and int(duplicate["data_start_row"] or 2) == requested_data_start_row
            and bool(duplicate["formula_acknowledged"]) == bool(preview.get("formula_acknowledged"))
            and duplicate["formula_policy_version"] == preview.get("formula_policy_version", FORMULA_POLICY_VERSION)
            and (duplicate["mapping_issues_json"] or "[]") == requested_mapping_issues_json
        )
        if duplicate and (
            duplicate["status"] == "committed"
            or same_preview
            or (
                duplicate["created_by_user_id"] is not None
                and duplicate["created_by_user_id"] != actor_user_id
            )
        ):
            state = "committed" if duplicate["status"] == "committed" else "staged"
            raise DuplicateImportError(
                f"Source has already been {state} as import {duplicate['id']}",
                import_id=duplicate["id"],
                status=duplicate["status"],
            )

        revised = duplicate is not None
        source_id = duplicate_rows[0]["source_document_id"] if duplicate_rows else _id("src")
        if revised:
            import_id = duplicate["id"]
            source_id = duplicate["source_document_id"]
            connection.execute(
                "DELETE FROM import_rows WHERE import_batch_id = ?",
                (import_id,),
            )
            connection.execute(
                """
                UPDATE source_documents
                SET file_format = ?, profile_json = ?, byte_size = ?, storage_path = ?
                WHERE id = ?
                """,
                (
                    preview.get("file_format", "unknown"),
                    json.dumps(preview.get("profile", {}), ensure_ascii=False, sort_keys=True),
                    preview["byte_size"],
                    storage_path,
                    source_id,
                ),
            )
            connection.execute(
                """
                UPDATE import_batches
                SET mapping_json = ?, selected_sheet = ?, header_row = ?, data_start_row = ?,
                    cleaning_policy_version = 'import-clean-v1', mapping_confidence_json = ?,
                    formula_acknowledged = ?, formula_policy_version = ?, mapping_issues_json = ?,
                    created_by_user_id = COALESCE(created_by_user_id, ?),
                    project_id = COALESCE(project_id, ?), status = 'preview',
                    accepted_count = ?, rejected_count = ?, warning_count = ?,
                    committed_at = NULL
                WHERE id = ?
                """,
                (
                    json.dumps(preview["mapping"], sort_keys=True),
                    preview.get("profile", {}).get("sheet_name"),
                    int(preview.get("profile", {}).get("header_row", header_row)),
                    int(preview.get("profile", {}).get("data_start_row", data_start_row or 2)),
                    json.dumps(preview.get("mapping_confidence", {}), sort_keys=True),
                    1 if preview.get("formula_acknowledged") else 0,
                    preview.get("formula_policy_version", FORMULA_POLICY_VERSION),
                    json.dumps(preview.get("mapping_issues", []), ensure_ascii=False, sort_keys=True),
                    actor_user_id,
                    project_id,
                    preview["accepted_count"],
                    preview["rejected_count"],
                    preview["warning_count"],
                    import_id,
                ),
            )
        else:
            if not duplicate_rows:
                connection.execute(
                    """
                    INSERT INTO source_documents
                        (id, filename, sha256, byte_size, content_type, storage_path, file_format, profile_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        filename,
                        preview["sha256"],
                        preview["byte_size"],
                        content_type,
                        storage_path,
                        preview.get("file_format", "unknown"),
                        json.dumps(preview.get("profile", {}), ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO import_batches
                    (id, source_document_id, project_id, mapping_json, selected_sheet, header_row, data_start_row,
                     cleaning_policy_version, mapping_confidence_json, formula_acknowledged, formula_policy_version,
                     mapping_issues_json, created_by_user_id, status, accepted_count, rejected_count, warning_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'import-clean-v1', ?, ?, ?, ?, ?, 'preview', ?, ?, ?, ?)
                """,
                (
                    import_id,
                    source_id,
                    project_id,
                    json.dumps(preview["mapping"], sort_keys=True),
                    preview.get("profile", {}).get("sheet_name"),
                    int(preview.get("profile", {}).get("header_row", header_row)),
                    int(preview.get("profile", {}).get("data_start_row", data_start_row or 2)),
                    json.dumps(preview.get("mapping_confidence", {}), sort_keys=True),
                    1 if preview.get("formula_acknowledged") else 0,
                    preview.get("formula_policy_version", FORMULA_POLICY_VERSION),
                    json.dumps(preview.get("mapping_issues", []), ensure_ascii=False, sort_keys=True),
                    actor_user_id,
                    preview["accepted_count"],
                    preview["rejected_count"],
                    preview["warning_count"],
                    now,
                ),
            )
        for row in preview["rows"]:
            connection.execute(
                """
                INSERT INTO import_rows
                    (id, import_batch_id, source_row_id, raw_json, normalized_json, validation_json, source_location_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("row"),
                    import_id,
                    row.source_row_id,
                    json.dumps(row.raw, ensure_ascii=False),
                    json.dumps(row.normalized, ensure_ascii=False) if row.normalized else None,
                    json.dumps({"errors": list(row.errors), "warnings": list(row.warnings), "mapping_issues": preview.get("mapping_issues", [])}),
                    json.dumps(row.source_location, ensure_ascii=False, sort_keys=True),
                    "accepted" if row.accepted else "rejected",
                ),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'import_batch', ?, 'success', ?, ?)
            """,
            (
                _id("audit"),
                actor_user_id,
                "import_preview_revision" if revised else "import_preview",
                import_id,
                json.dumps({"sha256": preview["sha256"], "revision": revised}),
                now,
            ),
        )
    return _public_preview(import_id, source_id, preview)


def _get_or_create_project(connection: Any, project_id: str) -> None:
    row = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise ImportValidationError(f"Unknown project: {project_id}")


def commit_import(
    database_path: str,
    import_id: str,
    project_id: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    with read_connection(database_path) as connection:
        batch = connection.execute(
            """
            SELECT ib.*, sd.id AS source_id, sd.sha256, sd.storage_path, sd.filename
            FROM import_batches ib
            JOIN source_documents sd ON sd.id = ib.source_document_id
            WHERE ib.id = ?
            """,
            (import_id,),
        ).fetchone()
        if batch is None:
            raise ImportValidationError("Import batch was not found")
        if batch["status"] == "committed":
            return {"import_id": import_id, "status": "already_committed", "data_origin": "imported"}
        rows = connection.execute(
            "SELECT * FROM import_rows WHERE import_batch_id = ? ORDER BY CAST(source_row_id AS INTEGER)",
            (import_id,),
        ).fetchall()
    accepted_rows = [row for row in rows if row["status"] == "accepted" and row["normalized_json"]]
    if not accepted_rows:
        raise ImportValidationError("Import has no valid rows to commit")
    now = _now()
    assay_runs: dict[str, str] = {}
    inserted_compounds = 0
    inserted_measurements = 0
    with transaction(database_path) as connection:
        current = connection.execute(
            "SELECT status FROM import_batches WHERE id = ?",
            (import_id,),
        ).fetchone()
        if current is None:
            raise ImportValidationError("Import batch was not found")
        if current["status"] == "committed":
            return {"import_id": import_id, "status": "already_committed", "data_origin": "imported"}
        _get_or_create_project(connection, project_id)
        for row in accepted_rows:
            normalized = json.loads(row["normalized_json"])
            structure = normalized["structure"]
            measurement = normalized["measurement"]
            existing = connection.execute(
                "SELECT id FROM compounds WHERE project_id = ? AND registration_id = ?",
                (project_id, normalized["compound_id"]),
            ).fetchone()
            if existing:
                compound_id = existing["id"]
                existing_structure = connection.execute(
                    """
                    SELECT canonical_smiles FROM structure_records
                    WHERE compound_id = ? ORDER BY created_at DESC LIMIT 1
                    """,
                    (compound_id,),
                ).fetchone()
                if existing_structure and existing_structure["canonical_smiles"] != structure["canonical_smiles"]:
                    raise ImportConflictError(
                        f"Compound {normalized['compound_id']} has a conflicting structure in this project"
                    )
            else:
                compound_id = _id("cmp")
                connection.execute(
                    """
                    INSERT INTO compounds (id, project_id, registration_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (compound_id, project_id, normalized["compound_id"], now, now),
                )
                inserted_compounds += 1
                structure_id = _id("str")
                connection.execute(
                    """
                    INSERT INTO structure_records
                        (id, compound_id, raw_input, input_format, original_molblock, standardized_molblock,
                         canonical_smiles, isomeric_smiles, inchikey, parent_canonical_smiles, parent_inchikey,
                         component_count, stereochemistry_status, standardization_profile, warnings_json,
                         rendered_svg, created_at)
                    VALUES (?, ?, ?, 'smiles', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        structure_id,
                        compound_id,
                        structure["raw_input"],
                        structure["original_molblock"],
                        structure["standardized_molblock"],
                        structure["canonical_smiles"],
                        structure["isomeric_smiles"],
                        structure["inchikey"],
                        structure["parent_canonical_smiles"],
                        structure["parent_inchikey"],
                        structure["component_count"],
                        structure["stereochemistry_status"],
                        structure["standardization_profile"],
                        json.dumps(structure["warnings"]),
                        structure["rendered_svg"],
                        now,
                    ),
                )
            assay_name = normalized["assay"]
            assay = connection.execute(
                """
                SELECT id FROM assay_definitions
                WHERE project_id = ? AND name = ? AND endpoint_code = ? AND protocol_version = 'import-v1'
                """,
                (project_id, assay_name, assay_name),
            ).fetchone()
            if assay is None:
                assay_id = _id("assay")
                connection.execute(
                    """
                    INSERT INTO assay_definitions
                        (id, project_id, name, endpoint_code, canonical_unit, compatibility_key, protocol_version, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'import-v1', ?)
                    """,
                    (
                        assay_id,
                        project_id,
                        assay_name,
                        assay_name,
                        measurement["canonical_unit"],
                        f"{assay_name}:import-v1",
                        now,
                    ),
                )
            else:
                assay_id = assay["id"]
            if assay_name not in assay_runs:
                run_id = _id("run")
                assay_runs[assay_name] = run_id
                connection.execute(
                    """
                    INSERT INTO assay_runs
                        (id, assay_definition_id, source_document_id, run_date, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, assay_id, batch["source_id"], normalized.get("date"), now),
                )
            connection.execute(
                """
                INSERT INTO measurements
                    (id, compound_id, assay_run_id, replicate_group_id, replicate_type, replicate_index,
                     raw_value_text, value_numeric, unit_ucum, qualifier, lower_bound, upper_bound,
                     canonical_value, canonical_unit, transform_id, missing_reason, source_row_id, created_at)
                VALUES (?, ?, ?, ?, 'unspecified', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("msr"),
                    compound_id,
                    assay_runs[assay_name],
                    f"{import_id}:{row['source_row_id']}",
                    int(normalized["replicate"]) if (normalized.get("replicate") or "").isdigit() else None,
                    measurement["raw_value_text"],
                    measurement["value_numeric"],
                    measurement["unit_ucum"],
                    measurement["qualifier"],
                    measurement["lower_bound"],
                    measurement["upper_bound"],
                    measurement["canonical_value"],
                    measurement["canonical_unit"],
                    measurement["transform_id"],
                    measurement["missing_reason"],
                    row["source_row_id"],
                    now,
                ),
            )
            connection.execute(
                "UPDATE import_rows SET status = 'committed' WHERE id = ?",
                (row["id"],),
            )
            inserted_measurements += 1
        connection.execute(
            "UPDATE import_batches SET project_id = ?, status = 'committed', committed_at = ? WHERE id = ?",
            (project_id, now, import_id),
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'import_commit', 'import_batch', ?, 'success', ?, ?)
            """,
            (
                _id("audit"),
                actor_user_id,
                project_id,
                import_id,
                json.dumps({"inserted_compounds": inserted_compounds, "inserted_measurements": inserted_measurements}),
                now,
            ),
        )
    return {
        "import_id": import_id,
        "status": "committed",
        "data_origin": "imported",
        "inserted_compounds": inserted_compounds,
        "inserted_measurements": inserted_measurements,
        "rejected_rows": len(rows) - len(accepted_rows),
    }
