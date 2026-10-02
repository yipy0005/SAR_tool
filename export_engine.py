"""Project-scoped evidence export with explicit provenance and audit coverage."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from database import read_connection, transaction


MAX_EXPORT_ROWS = 100_000


class ExportError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_value(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dict_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def build_project_export(
    database_path: str,
    project_id: str,
    *,
    actor_user_id: str | None = None,
    max_rows: int = MAX_EXPORT_ROWS,
) -> dict[str, Any]:
    try:
        max_rows = int(max_rows)
    except (TypeError, ValueError) as exc:
        raise ExportError("max_rows must be an integer") from exc
    if max_rows < 1:
        raise ExportError("max_rows must be positive")

    with read_connection(database_path) as connection:
        project_row = connection.execute(
            "SELECT id, name, description, status, data_origin, created_at, updated_at FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise ExportError("The target project does not exist")
        compounds = _dict_rows(connection.execute(
            """
            SELECT id, registration_id, preferred_name, lifecycle_status, created_at, updated_at
            FROM compounds WHERE project_id = ? ORDER BY registration_id, id
            """,
            (project_id,),
        ))
        structures = _dict_rows(connection.execute(
            """
            SELECT sr.id, sr.compound_id, c.registration_id, sr.raw_input, sr.input_format,
                   sr.canonical_smiles, sr.isomeric_smiles, sr.inchikey,
                   sr.parent_canonical_smiles, sr.parent_inchikey, sr.component_count,
                   sr.stereochemistry_status, sr.standardization_profile, sr.warnings_json,
                   sr.created_at
            FROM structure_records AS sr
            JOIN compounds AS c ON c.id = sr.compound_id
            WHERE c.project_id = ?
            ORDER BY c.registration_id, sr.created_at, sr.id
            """,
            (project_id,),
        ))
        for item in structures:
            item["warnings"] = _json_value(item.pop("warnings_json", None), [])
            item["data_origin"] = "imported"
        measurements = _dict_rows(connection.execute(
            """
            SELECT m.id, m.compound_id, c.registration_id, m.assay_run_id,
                   m.replicate_group_id, m.replicate_type, m.replicate_index,
                   m.raw_value_text, m.value_numeric, m.unit_ucum, m.qualifier,
                   m.lower_bound, m.upper_bound, m.canonical_value, m.canonical_unit,
                   m.transform_id, m.missing_reason, m.source_row_id, m.well_id,
                   m.qc_status, m.created_at,
                   ar.run_date, ar.source_document_id,
                   ad.name AS assay_name, ad.endpoint_code, ad.modality,
                   ad.direction, ad.compatibility_key, ad.protocol_version,
                   ad.canonical_unit AS assay_canonical_unit,
                   sd.filename AS source_filename, sd.sha256 AS source_sha256
            FROM measurements AS m
            JOIN compounds AS c ON c.id = m.compound_id
            JOIN assay_runs AS ar ON ar.id = m.assay_run_id
            JOIN assay_definitions AS ad ON ad.id = ar.assay_definition_id
            LEFT JOIN source_documents AS sd ON sd.id = ar.source_document_id
            WHERE c.project_id = ?
            ORDER BY c.registration_id, m.created_at, m.id
            """,
            (project_id,),
        ))
        summaries = _dict_rows(connection.execute(
            """
            SELECT ms.*, c.registration_id
            FROM measurement_summaries AS ms
            JOIN compounds AS c ON c.id = ms.compound_id
            WHERE c.project_id = ?
            ORDER BY c.registration_id, ms.compatibility_key, ms.created_at, ms.id
            """,
            (project_id,),
        ))
        analyses = _dict_rows(connection.execute(
            "SELECT * FROM analysis_runs WHERE project_id = ? ORDER BY created_at, id",
            (project_id,),
        ))
        pharmacophore_rgroup = _dict_rows(connection.execute(
            """
            SELECT pra.*, c.registration_id
            FROM pharmacophore_rgroup_assignments pra
            JOIN analysis_runs ar ON ar.id = pra.analysis_run_id
            JOIN compounds c ON c.id = pra.compound_id
            WHERE ar.project_id = ?
            ORDER BY ar.created_at, c.registration_id, pra.id
            """,
            (project_id,),
        ))
        claims = _dict_rows(connection.execute(
            "SELECT * FROM sar_claims WHERE project_id = ? ORDER BY created_at, id",
            (project_id,),
        ))
        designs = _dict_rows(connection.execute(
            "SELECT * FROM design_candidates WHERE project_id = ? ORDER BY ranking_score DESC, created_at, id",
            (project_id,),
        ))
        generated = _dict_rows(connection.execute(
            "SELECT * FROM generated_recommendations WHERE project_id = ? ORDER BY information_gain_score DESC, created_at, id",
            (project_id,),
        ))
        series = _dict_rows(connection.execute(
            "SELECT * FROM series WHERE project_id = ? ORDER BY name, id",
            (project_id,),
        ))
        series_versions = _dict_rows(connection.execute(
            """
            SELECT sv.*, s.project_id, s.name AS series_name
            FROM series_versions sv
            JOIN series s ON s.id = sv.series_id
            WHERE s.project_id = ?
            ORDER BY s.name, sv.version
            """,
            (project_id,),
        ))
        series_memberships = _dict_rows(connection.execute(
            """
            SELECT sm.*, sv.series_id, sv.version AS series_version,
                   s.project_id, s.name AS series_name,
                   c.registration_id
            FROM series_memberships sm
            JOIN series_versions sv ON sv.id = sm.series_version_id
            JOIN series s ON s.id = sv.series_id
            JOIN compounds c ON c.id = sm.compound_id
            WHERE s.project_id = ?
            ORDER BY s.name, sv.version, c.registration_id
            """,
            (project_id,),
        ))
        compound_relationships = _dict_rows(connection.execute(
            """
            SELECT cr.*, p.registration_id AS prodrug_registration_id,
                   a.registration_id AS active_registration_id
            FROM compound_relationships AS cr
            JOIN compounds AS p ON p.id = cr.prodrug_compound_id
            JOIN compounds AS a ON a.id = cr.active_compound_id
            WHERE cr.project_id = ?
            ORDER BY p.registration_id, a.registration_id, cr.id
            """,
            (project_id,),
        ))

    row_count = (
        len(compounds) + len(structures) + len(measurements) + len(summaries)
        + len(analyses) + len(pharmacophore_rgroup) + len(claims) + len(designs) + len(generated)
        + len(series) + len(series_versions) + len(series_memberships)
        + len(compound_relationships)
    )
    if row_count > max_rows:
        raise ExportError(f"Export exceeds the {max_rows} row safety limit")

    for item in measurements:
        item["data_origin"] = "raw"
    for item in summaries:
        item["source_measurement_ids"] = _json_value(item.pop("source_measurement_ids_json", None), [])
        item["missing_reasons"] = _json_value(item.pop("missing_reasons_json", None), [])
        item["assay_definition_ids"] = _json_value(item.pop("assay_definition_ids_json", None), [])
        item["assay_run_ids"] = _json_value(item.pop("assay_run_ids_json", None), [])
        item["data_origin"] = "derived"
    for item in analyses:
        item["input_selection"] = _json_value(item.pop("input_selection_json", None), {})
        item["data_origin"] = "derived"
    for item in pharmacophore_rgroup:
        item["match_atoms"] = _json_value(item.pop("match_atoms_json", None), [])
        item["sites"] = _json_value(item.pop("sites_json", None), [])
        item["features"] = _json_value(item.pop("features_json", None), [])
        item["core_features"] = _json_value(item.pop("core_features_json", None), [])
        item["core_feature_delta"] = _json_value(item.pop("core_feature_delta_json", None), {})
        item["data_origin"] = "derived"
    for item in claims:
        item["scope_definition"] = _json_value(item.pop("scope_definition_json", None), {})
        item["effect"] = _json_value(item.pop("effect_json", None), {})
        item["uncertainty"] = _json_value(item.pop("uncertainty_json", None), {})
        item["data_origin"] = "derived"
    for item in designs:
        item["evidence_ids"] = _json_value(item.pop("evidence_ids_json", None), [])
        item["feasibility_reasons"] = _json_value(item.pop("feasibility_reasons_json", None), [])
        item["ranking_components"] = _json_value(item.pop("ranking_components_json", None), {})
        item["data_origin"] = "curated"
    for item in generated:
        item["evidence_ids"] = _json_value(item.pop("evidence_ids_json", None), [])
        item["score_components"] = _json_value(item.pop("score_components_json", None), {})
        item["data_origin"] = "generated"
        item["experimentally_confirmed"] = False
    for item in compound_relationships:
        item["activation_context"] = _json_value(item.pop("activation_context_json", None), {})
        item["evidence_ids"] = _json_value(item.pop("evidence_ids_json", None), [])
        item["data_origin"] = "imported"
    for item in series:
        item["data_origin"] = "curated"
    for item in series_versions:
        item["data_origin"] = "curated"
    for item in series_memberships:
        item["data_origin"] = "curated"

    export_id = _id("export")
    created_at = _now()
    project = dict(project_row)
    project["data_origin"] = "production"
    bundle = {
        "export_id": export_id,
        "created_at": created_at,
        "project": project,
        "compounds": compounds,
        "structure_records": structures,
        "measurements": measurements,
        "measurement_summaries": summaries,
        "analysis_runs": analyses,
        "pharmacophore_rgroup_assignments": pharmacophore_rgroup,
        "claims": claims,
        "curated_designs": designs,
        "generated_recommendations": generated,
        "series": series,
        "series_versions": series_versions,
        "series_memberships": series_memberships,
        "compound_relationships": compound_relationships,
        "row_count": row_count,
        "max_rows": max_rows,
        "data_origin": "production_export",
        "provenance_policy": {
            "raw_measurements": "raw",
            "summaries_and_analyses": "derived",
            "curated_designs": "curated",
            "series_and_memberships": "curated",
            "generated_recommendations": "generated_review_or_approved",
            "experimental_confirmation": "not represented by generated recommendations",
        },
    }
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'project_export', 'project_export', ?, 'success', ?, ?)
            """,
            (
                _id("audit"), actor_user_id, project_id, export_id,
                json.dumps({"row_count": row_count, "format_options": ["json", "csv"]}, sort_keys=True), created_at,
            ),
        )
    return bundle


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def export_project_csv(bundle: dict[str, Any]) -> str:
    columns = (
        "record_type", "id", "project_id", "compound_id", "registration_id",
        "compatibility_key", "value", "unit", "qualifier", "status",
        "data_origin", "evidence_ids_json", "metadata_json",
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()

    def write_record(record_type: str, item: dict[str, Any]) -> None:
        metadata = {key: value for key, value in item.items() if key not in {
            "id", "project_id", "compound_id", "registration_id", "compatibility_key",
            "value", "summary_value", "canonical_value", "canonical_unit", "unit",
            "qualifier", "summary_qualifier", "status", "data_origin", "evidence_ids",
        }}
        row = {
            "record_type": record_type,
            "id": item.get("id"),
            "project_id": bundle["project"]["id"],
            "compound_id": item.get("compound_id"),
            "registration_id": item.get("registration_id"),
            "compatibility_key": item.get("compatibility_key"),
            "value": item.get("value", item.get("summary_value", item.get("canonical_value"))),
            "unit": item.get("unit", item.get("canonical_unit")),
            "qualifier": item.get("qualifier", item.get("summary_qualifier")),
            "status": item.get("status"),
            "data_origin": item.get("data_origin"),
            "evidence_ids_json": json.dumps(item.get("evidence_ids", []), sort_keys=True),
            "metadata_json": json.dumps(metadata, sort_keys=True, default=str),
        }
        writer.writerow({key: _csv_safe(value) for key, value in row.items()})

    for item in bundle["compounds"]:
        write_record("compound", item)
    for item in bundle["structure_records"]:
        write_record("structure_record", item)
    for item in bundle["measurements"]:
        write_record("measurement", item)
    for item in bundle["measurement_summaries"]:
        write_record("measurement_summary", item)
    for item in bundle["analysis_runs"]:
        write_record("analysis_run", item)
    for item in bundle["pharmacophore_rgroup_assignments"]:
        write_record("pharmacophore_rgroup_assignment", item)
    for item in bundle["claims"]:
        write_record("claim", item)
    for item in bundle["curated_designs"]:
        write_record("curated_design", item)
    for item in bundle["series"]:
        write_record("series", item)
    for item in bundle["series_versions"]:
        write_record("series_version", item)
    for item in bundle["series_memberships"]:
        write_record("series_membership", item)
    for item in bundle["generated_recommendations"]:
        write_record("generated_recommendation", item)
    return output.getvalue()
