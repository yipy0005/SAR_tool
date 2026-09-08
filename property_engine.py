from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

from database import read_connection, transaction


ANALYSIS_VERSION = "rdkit-properties-v1"
PROPERTY_DEFINITIONS: tuple[tuple[str, str, Any], ...] = (
    ("molecular_weight", "Da", Descriptors.MolWt),
    ("logp", "dimensionless", Crippen.MolLogP),
    ("tpsa", "A2", rdMolDescriptors.CalcTPSA),
    ("hbd", "count", Lipinski.NumHDonors),
    ("hba", "count", Lipinski.NumHAcceptors),
    ("rotatable_bonds", "count", Lipinski.NumRotatableBonds),
    ("ring_count", "count", rdMolDescriptors.CalcNumRings),
    ("heavy_atom_count", "count", lambda molecule: molecule.GetNumHeavyAtoms()),
    ("formal_charge", "charge", lambda molecule: Chem.GetFormalCharge(molecule)),
    ("fraction_csp3", "fraction", rdMolDescriptors.CalcFractionCSP3),
)


class PropertyAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def calculate_properties(isomeric_smiles: str) -> dict[str, Any]:
    """Calculate deterministic RDKit descriptors from a validated structure identity."""
    smiles = str(isomeric_smiles or "").strip()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise PropertyAnalysisError("Structure cannot be parsed for property calculation")
    try:
        Chem.SanitizeMol(molecule)
    except Exception as exc:
        raise PropertyAnalysisError("Structure cannot be sanitized for property calculation") from exc

    descriptors: dict[str, dict[str, Any]] = {}
    for name, unit, function in PROPERTY_DEFINITIONS:
        try:
            value = float(function(molecule))
        except Exception as exc:
            raise PropertyAnalysisError(f"Unable to calculate descriptor: {name}") from exc
        if not math.isfinite(value):
            raise PropertyAnalysisError(f"Descriptor is non-finite: {name}")
        descriptors[name] = {"value": value, "unit": unit, "status": "computed"}
    return {
        "algorithm_version": ANALYSIS_VERSION,
        "structure_smiles": smiles,
        "descriptors": descriptors,
        "uncertainty": {
            "status": "deterministic_derived",
            "experimental": False,
            "source": "RDKit",
        },
        "status": "computed",
        "reason": None,
        "data_origin": "derived",
    }


def _load_compounds(
    database_path: str,
    project_id: str,
    compound_ids: list[str] | None,
) -> list[dict[str, Any]]:
    parameters: list[Any] = [project_id]
    filter_sql = ""
    if compound_ids is not None:
        placeholders = ",".join("?" for _ in compound_ids)
        filter_sql = f" AND c.id IN ({placeholders})"
        parameters.extend(compound_ids)
    with read_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT c.id AS compound_id, c.registration_id,
                   s.id AS structure_record_id, s.isomeric_smiles
            FROM compounds AS c
            LEFT JOIN structure_records AS s
              ON s.id = (
                  SELECT latest.id
                  FROM structure_records AS latest
                  WHERE latest.compound_id = c.id
                  ORDER BY latest.created_at DESC, latest.id DESC
                  LIMIT 1
              )
            WHERE c.project_id = ?{filter_sql}
            ORDER BY c.registration_id, c.id
            """,
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def _profile_for_compound(row: dict[str, Any]) -> dict[str, Any]:
    structure_record_id = row.get("structure_record_id")
    smiles = row.get("isomeric_smiles")
    if not structure_record_id or not smiles:
        return {
            "compound_id": row["compound_id"],
            "registration_id": row["registration_id"],
            "structure_record_id": structure_record_id,
            "status": "missing_structure",
            "reason": "structure_record_missing",
            "descriptors": {},
            "uncertainty": {"status": "not_computable", "experimental": False},
            "data_origin": "derived",
        }
    try:
        profile = calculate_properties(str(smiles))
    except PropertyAnalysisError as exc:
        return {
            "compound_id": row["compound_id"],
            "registration_id": row["registration_id"],
            "structure_record_id": structure_record_id,
            "status": "invalid_structure",
            "reason": str(exc),
            "descriptors": {},
            "uncertainty": {"status": "not_computable", "experimental": False},
            "data_origin": "derived",
        }
    profile.update(
        {
            "compound_id": row["compound_id"],
            "registration_id": row["registration_id"],
            "structure_record_id": structure_record_id,
        }
    )
    return profile


def run_property_analysis(
    database_path: str,
    project_id: str,
    compound_ids: Iterable[str] | None = None,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    project_id = str(project_id).strip()
    if not project_id:
        raise PropertyAnalysisError("project_id is required")
    selected_ids = None if compound_ids is None else list(dict.fromkeys(str(item).strip() for item in compound_ids if str(item).strip()))
    if compound_ids is not None and not selected_ids:
        raise PropertyAnalysisError("compound_ids must contain at least one identifier")
    rows = _load_compounds(database_path, project_id, selected_ids)
    if selected_ids is not None:
        found = {str(row["compound_id"]) for row in rows}
        if found != set(selected_ids):
            raise PropertyAnalysisError("Every compound ID must belong to the target project")
    if not rows:
        raise PropertyAnalysisError("No compounds are available for property analysis")

    profiles = [_profile_for_compound(row) for row in rows]
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            raise PropertyAnalysisError("The target project does not exist")
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'properties', ?, ?, 'completed', ?)
            """,
            (
                run_id,
                project_id,
                json.dumps(
                    {
                        "compound_ids": selected_ids,
                        "selection_policy": "latest_structure_record_per_compound",
                    },
                    sort_keys=True,
                ),
                ANALYSIS_VERSION,
                now,
            ),
        )
        for profile in profiles:
            connection.execute(
                """
                INSERT INTO property_profiles
                    (id, analysis_run_id, project_id, compound_id, structure_record_id,
                     status, reason, descriptors_json, uncertainty_json,
                     algorithm_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("property"),
                    run_id,
                    project_id,
                    profile["compound_id"],
                    profile["structure_record_id"],
                    profile["status"],
                    profile["reason"],
                    json.dumps(profile["descriptors"], sort_keys=True),
                    json.dumps(profile["uncertainty"], sort_keys=True),
                    ANALYSIS_VERSION,
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'analysis_run', 'analysis_run', ?, 'success', ?, ?)
            """,
            (
                _id("audit"),
                actor_user_id,
                project_id,
                run_id,
                json.dumps(
                    {"type": "properties", "profile_count": len(profiles), "analysis_version": ANALYSIS_VERSION},
                    sort_keys=True,
                ),
                now,
            ),
        )
    counts = {
        "computed": sum(profile["status"] == "computed" for profile in profiles),
        "missing_structure": sum(profile["status"] == "missing_structure" for profile in profiles),
        "invalid_structure": sum(profile["status"] == "invalid_structure" for profile in profiles),
    }
    return {
        "analysis_run_id": run_id,
        "analysis_type": "properties",
        "algorithm_version": ANALYSIS_VERSION,
        "status": "completed",
        "counts": counts,
        "profiles": profiles,
        "data_origin": "derived",
    }


def get_property_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'properties'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        profiles = connection.execute(
            """
            SELECT pp.*, c.registration_id
            FROM property_profiles AS pp
            JOIN compounds AS c ON c.id = pp.compound_id
            WHERE pp.analysis_run_id = ?
            ORDER BY c.registration_id, c.id
            """,
            (run_id,),
        ).fetchall()
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["profiles"] = []
    for row in profiles:
        profile = dict(row)
        profile["descriptors"] = json.loads(profile.pop("descriptors_json"))
        profile["uncertainty"] = json.loads(profile.pop("uncertainty_json"))
        profile["data_origin"] = "derived"
        result["profiles"].append(profile)
    result["counts"] = {
        status: sum(profile["status"] == status for profile in result["profiles"])
        for status in ("computed", "missing_structure", "invalid_structure")
    }
    result["data_origin"] = "derived"
    return result
