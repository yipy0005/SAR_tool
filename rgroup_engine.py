from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Iterable, Mapping

from rdkit import Chem
from rdkit.Chem import rdFMCS

from database import read_connection, transaction


ANALYSIS_VERSION = "rgroup-cliff-rdkit-v1"


class RGroupAnalysisError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _molecule(smiles: str) -> Chem.Mol:
    molecule = Chem.MolFromSmiles(str(smiles))
    if molecule is None:
        raise RGroupAnalysisError("Invalid standardized structure")
    return molecule


def _fragment_smiles(molecule: Chem.Mol, atom_indices: Iterable[int]) -> str:
    atoms = sorted(set(atom_indices))
    if not atoms:
        return ""
    return Chem.MolFragmentToSmiles(molecule, atomsToUse=atoms, canonical=True)


def decompose_rgroups(
    compound_id: str,
    smiles: str,
    scaffold_smarts: str,
) -> dict[str, Any]:
    """Assign substituent fragments at scaffold attachment points.

    The scaffold is supplied by the user or a versioned project definition;
    this function never silently chooses a scaffold. Multiple attachments are
    ordered by matched scaffold atom index for deterministic R labels.
    """
    molecule = _molecule(smiles)
    scaffold = Chem.MolFromSmarts(str(scaffold_smarts))
    if scaffold is None:
        raise RGroupAnalysisError("Invalid scaffold SMARTS")
    match = molecule.GetSubstructMatch(scaffold)
    if not match:
        return {
            "compound_id": compound_id,
            "scaffold_smarts": scaffold_smarts,
            "status": "unmatched",
            "match_atoms": [],
            "assignments": {},
            "failure_reason": "scaffold_not_found",
        }

    scaffold_atoms = set(match)
    seen: set[int] = set()
    attachments: list[tuple[int, str]] = []
    for scaffold_index in sorted(scaffold_atoms):
        atom = molecule.GetAtomWithIdx(scaffold_index)
        for neighbor in atom.GetNeighbors():
            neighbor_index = neighbor.GetIdx()
            if neighbor_index in scaffold_atoms or neighbor_index in seen:
                continue
            stack = [neighbor_index]
            fragment: set[int] = set()
            while stack:
                current = stack.pop()
                if current in fragment or current in scaffold_atoms:
                    continue
                fragment.add(current)
                seen.add(current)
                current_atom = molecule.GetAtomWithIdx(current)
                stack.extend(
                    child.GetIdx()
                    for child in current_atom.GetNeighbors()
                    if child.GetIdx() not in fragment and child.GetIdx() not in scaffold_atoms
                )
            fragment_smiles = _fragment_smiles(molecule, fragment)
            if fragment_smiles:
                attachments.append((scaffold_index, fragment_smiles))

    assignments = {
        f"R{index}": fragment
        for index, (_, fragment) in enumerate(attachments, start=1)
    }
    return {
        "compound_id": compound_id,
        "scaffold_smarts": scaffold_smarts,
        "status": "assigned",
        "match_atoms": list(match),
        "assignments": assignments,
        "failure_reason": None,
    }


def _pair_similarity(smiles_a: str, smiles_b: str) -> tuple[str, float, dict[str, list[str]]] | None:
    molecule_a = Chem.MolFromSmiles(smiles_a)
    molecule_b = Chem.MolFromSmiles(smiles_b)
    if molecule_a is None or molecule_b is None:
        return None
    result = rdFMCS.FindMCS(
        [molecule_a, molecule_b],
        timeout=2,
        ringMatchesRingOnly=True,
        completeRingsOnly=True,
        atomCompare=rdFMCS.AtomCompare.CompareElements,
    )
    if result.canceled or result.numAtoms < 3:
        return None
    max_atoms = max(molecule_a.GetNumHeavyAtoms(), molecule_b.GetNumHeavyAtoms())
    similarity = result.numAtoms / max_atoms
    if similarity < 0.8:
        return None
    query = Chem.MolFromSmarts(result.smartsString)
    if query is None:
        return None
    match_a = set(molecule_a.GetSubstructMatch(query))
    match_b = set(molecule_b.GetSubstructMatch(query))
    changed_a = sorted(atom.GetSymbol() for index, atom in enumerate(molecule_a.GetAtoms()) if index not in match_a)
    changed_b = sorted(atom.GetSymbol() for index, atom in enumerate(molecule_b.GetAtoms()) if index not in match_b)
    return result.smartsString, similarity, {"a": changed_a, "b": changed_b}


def find_activity_cliffs(
    records: Iterable[Mapping[str, Any]],
    *,
    effect_threshold: float = 1.0,
    similarity_threshold: float = 0.8,
) -> list[dict[str, Any]]:
    """Find high-effect, structurally similar pairs from observed summaries only."""
    eligible: list[dict[str, Any]] = []
    for record in records:
        state = str(record.get("summary_state", "observed"))
        value = record.get("summary_value", record.get("canonical_value"))
        qualifier = record.get("summary_qualifier", record.get("qualifier", "="))
        if state != "observed" or qualifier not in {"=", "~"} or value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        eligible.append({**dict(record), "_value": numeric})

    cliffs: list[dict[str, Any]] = []
    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for record in eligible:
        groups[(str(record.get("compatibility_key", "")), record.get("canonical_unit"))].append(record)
    for (_, _), group in groups.items():
        for first, second in combinations(sorted(group, key=lambda item: str(item.get("compound_id"))), 2):
            if first.get("compound_id") == second.get("compound_id"):
                continue
            similarity = _pair_similarity(str(first["isomeric_smiles"]), str(second["isomeric_smiles"]))
            if similarity is None or similarity[1] < similarity_threshold:
                continue
            mcs_smarts, score, transformation = similarity
            effect = float(second["_value"]) - float(first["_value"])
            if abs(effect) < effect_threshold:
                continue
            cliffs.append(
                {
                    "compound_a_id": first["compound_id"],
                    "compound_b_id": second["compound_id"],
                    "summary_a_id": first.get("summary_id"),
                    "summary_b_id": second.get("summary_id"),
                    "mcs_smarts": mcs_smarts,
                    "similarity": score,
                    "transformation": transformation,
                    "effect_value": effect,
                    "effect_unit": first.get("canonical_unit"),
                    "assay_compatibility_key": first.get("compatibility_key"),
                    "evidence_status": "eligible_observed_summary",
                }
            )
    return cliffs


def run_rgroup_analysis(
    database_path: str,
    project_id: str,
    scaffold_smarts: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    with read_connection(database_path) as connection:
        compounds = connection.execute(
            """
            SELECT c.id AS compound_id, s.isomeric_smiles
            FROM compounds c JOIN structure_records s ON s.compound_id = c.id
            WHERE c.project_id = ? ORDER BY c.registration_id
            """,
            (project_id,),
        ).fetchall()
    run_id = _id("analysis")
    now = _now()
    assignments = [decompose_rgroups(row["compound_id"], row["isomeric_smiles"], scaffold_smarts) for row in compounds]
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'rgroup', ?, ?, 'completed', ?)
            """,
            (run_id, project_id, json.dumps({"scaffold_smarts": scaffold_smarts}), ANALYSIS_VERSION, now),
        )
        for assignment in assignments:
            connection.execute(
                """
                INSERT INTO rgroup_assignments
                    (id, analysis_run_id, compound_id, scaffold_smarts, match_atoms_json,
                     assignments_json, status, failure_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("rgroup"), run_id, assignment["compound_id"], scaffold_smarts,
                    json.dumps(assignment["match_atoms"]), json.dumps(assignment["assignments"], sort_keys=True),
                    assignment["status"], assignment["failure_reason"], now,
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
                json.dumps({"type": "rgroup", "scaffold_smarts": scaffold_smarts, "assignment_count": len(assignments)}, sort_keys=True),
                now,
            ),
        )
    return {"analysis_run_id": run_id, "analysis_type": "rgroup", "algorithm_version": ANALYSIS_VERSION, "assignments": assignments, "data_origin": "derived"}


def run_activity_cliff_analysis(
    database_path: str,
    project_id: str,
    *,
    effect_threshold: float = 1.0,
    similarity_threshold: float = 0.8,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    with read_connection(database_path) as connection:
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ms.id AS summary_id, ms.compound_id, ms.summary_state, ms.summary_value,
                       ms.summary_qualifier, ms.canonical_unit, ms.compatibility_key,
                       s.isomeric_smiles
                FROM measurement_summaries ms
                JOIN compounds c ON c.id = ms.compound_id
                JOIN structure_records s ON s.compound_id = c.id
                WHERE c.project_id = ?
                """,
                (project_id,),
            )
        ]
    cliffs = find_activity_cliffs(records, effect_threshold=effect_threshold, similarity_threshold=similarity_threshold)
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'activity_cliff', ?, ?, 'completed', ?)
            """,
            (run_id, project_id, json.dumps({"effect_threshold": effect_threshold, "similarity_threshold": similarity_threshold}), ANALYSIS_VERSION, now),
        )
        for cliff in cliffs:
            connection.execute(
                """
                INSERT INTO activity_cliffs
                    (id, analysis_run_id, compound_a_id, compound_b_id, summary_a_id, summary_b_id,
                     mcs_smarts, similarity, transformation_json, effect_value, effect_unit,
                     assay_compatibility_key, evidence_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("cliff"), run_id, cliff["compound_a_id"], cliff["compound_b_id"],
                    cliff["summary_a_id"], cliff["summary_b_id"], cliff["mcs_smarts"], cliff["similarity"],
                    json.dumps(cliff["transformation"], sort_keys=True), cliff["effect_value"], cliff["effect_unit"],
                    cliff["assay_compatibility_key"], cliff["evidence_status"], now,
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
                json.dumps({"type": "activity_cliff", "effect_threshold": effect_threshold, "similarity_threshold": similarity_threshold, "cliff_count": len(cliffs)}, sort_keys=True),
                now,
            ),
        )
    return {"analysis_run_id": run_id, "analysis_type": "activity_cliff", "algorithm_version": ANALYSIS_VERSION, "cliff_count": len(cliffs), "cliffs": cliffs, "data_origin": "derived"}
