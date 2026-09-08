from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from statistics import median
from typing import Any

from rdkit import Chem
from rdkit.Chem import rdFMCS

from database import read_connection, transaction


class AnalysisValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _mcs_pair(smiles_a: str, smiles_b: str) -> tuple[str, float, dict[str, list[str]]] | None:
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
    max_heavy_atoms = max(molecule_a.GetNumHeavyAtoms(), molecule_b.GetNumHeavyAtoms())
    min_heavy_atoms = min(molecule_a.GetNumHeavyAtoms(), molecule_b.GetNumHeavyAtoms())
    similarity = result.numAtoms / max_heavy_atoms
    if similarity < 0.65 or min_heavy_atoms - result.numAtoms > 2:
        return None
    query = Chem.MolFromSmarts(result.smartsString)
    if query is None:
        return None
    match_a = set(molecule_a.GetSubstructMatch(query))
    match_b = set(molecule_b.GetSubstructMatch(query))
    changed_a = sorted(atom.GetSymbol() for index, atom in enumerate(molecule_a.GetAtoms()) if index not in match_a)
    changed_b = sorted(atom.GetSymbol() for index, atom in enumerate(molecule_b.GetAtoms()) if index not in match_b)
    return result.smartsString, similarity, {"a": changed_a, "b": changed_b}


def _load_measurements(database_path: str, project_id: str) -> list[dict[str, Any]]:
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT c.id AS compound_id, c.registration_id,
                   s.isomeric_smiles, m.id AS measurement_id,
                   m.canonical_value, m.canonical_unit, m.qualifier,
                   ad.compatibility_key
            FROM compounds c
            JOIN structure_records s ON s.compound_id = c.id
            JOIN measurements m ON m.compound_id = c.id
            JOIN assay_runs ar ON ar.id = m.assay_run_id
            JOIN assay_definitions ad ON ad.id = ar.assay_definition_id
            WHERE c.project_id = ?
            ORDER BY c.registration_id, m.created_at
            """,
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def run_mmp_analysis(
    database_path: str,
    project_id: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    rows = _load_measurements(database_path, project_id)
    if not rows:
        raise AnalysisValidationError("No persisted measurements are available for MMP analysis")

    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    excluded = {"missing_value": 0, "censored_value": 0, "incompatible_context": 0}
    for row in rows:
        compatibility = row["compatibility_key"]
        unit = row["canonical_unit"] or "unknown"
        key = (compatibility, unit)
        if row["canonical_value"] is None:
            excluded["missing_value"] += 1
            continue
        if row["qualifier"] not in {"=", "~"}:
            excluded["censored_value"] += 1
            continue
        compound = grouped[key].setdefault(
            row["compound_id"],
            {
                "compound_id": row["compound_id"],
                "registration_id": row["registration_id"],
                "isomeric_smiles": row["isomeric_smiles"],
                "values": [],
                "measurement_ids": [],
                "unit": unit,
            },
        )
        compound["values"].append(float(row["canonical_value"]))
        compound["measurement_ids"].append(row["measurement_id"])

    pairs: list[dict[str, Any]] = []
    for (compatibility, unit), compounds in grouped.items():
        for compound_a, compound_b in combinations(sorted(compounds.values(), key=lambda item: item["registration_id"]), 2):
            mcs = _mcs_pair(compound_a["isomeric_smiles"], compound_b["isomeric_smiles"])
            if mcs is None:
                continue
            mcs_smarts, similarity, changed = mcs
            effect = median(compound_b["values"]) - median(compound_a["values"])
            pairs.append(
                {
                    "compatibility_key": compatibility,
                    "unit": unit,
                    "compound_a_id": compound_a["compound_id"],
                    "compound_b_id": compound_b["compound_id"],
                    "compound_a": compound_a["registration_id"],
                    "compound_b": compound_b["registration_id"],
                    "measurement_a_ids": compound_a["measurement_ids"],
                    "measurement_b_ids": compound_b["measurement_ids"],
                    "mcs_smarts": mcs_smarts,
                    "similarity": similarity,
                    "transformation": changed,
                    "effect_value": effect,
                    "effect_unit": unit,
                }
            )

    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs
                (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'mmp', ?, 'rdkit-mcs-mmp-v1', 'completed', ?)
            """,
            (
                run_id,
                project_id,
                json.dumps({"project_id": project_id, "excluded": excluded}, sort_keys=True),
                now,
            ),
        )
        for pair in pairs:
            connection.execute(
                """
                INSERT INTO mmp_pairs
                    (id, analysis_run_id, compound_a_id, compound_b_id, measurement_a_ids_json,
                     measurement_b_ids_json, mcs_smarts, similarity, transformation_json,
                     effect_value, effect_unit, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("mmp"),
                    run_id,
                    pair["compound_a_id"],
                    pair["compound_b_id"],
                    json.dumps(pair["measurement_a_ids"]),
                    json.dumps(pair["measurement_b_ids"]),
                    pair["mcs_smarts"],
                    pair["similarity"],
                    json.dumps(pair["transformation"], sort_keys=True),
                    pair["effect_value"],
                    pair["effect_unit"],
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'analysis_run', 'analysis_run', ?, 'success', ?, ?)
            """,
            (_id("audit"), actor_user_id, project_id, run_id, json.dumps({"type": "mmp", "pair_count": len(pairs)}), now),
        )
    return {
        "analysis_run_id": run_id,
        "analysis_type": "mmp",
        "algorithm_version": "rdkit-mcs-mmp-v1",
        "status": "completed",
        "pair_count": len(pairs),
        "excluded": excluded,
        "pairs": pairs,
        "data_origin": "derived",
    }


def get_mmp_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    with read_connection(database_path) as connection:
        run = connection.execute("SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'mmp'", (run_id,)).fetchone()
        if run is None:
            return None
        pairs = connection.execute(
            """
            SELECT p.*, a.registration_id AS compound_a, b.registration_id AS compound_b
            FROM mmp_pairs p
            JOIN compounds a ON a.id = p.compound_a_id
            JOIN compounds b ON b.id = p.compound_b_id
            WHERE p.analysis_run_id = ? ORDER BY p.created_at
            """,
            (run_id,),
        ).fetchall()
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json"))
    result["pairs"] = []
    for row in pairs:
        pair = dict(row)
        pair["measurement_a_ids"] = json.loads(pair.pop("measurement_a_ids_json"))
        pair["measurement_b_ids"] = json.loads(pair.pop("measurement_b_ids_json"))
        pair["transformation"] = json.loads(pair.pop("transformation_json"))
        result["pairs"].append(pair)
    result["data_origin"] = "derived"
    return result


def create_claims_from_mmp(
    database_path: str,
    run_id: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    run = get_mmp_run(database_path, run_id)
    if run is None:
        raise AnalysisValidationError("MMP analysis run was not found")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in run["pairs"]:
        key = json.dumps(pair["transformation"], sort_keys=True)
        groups[key].append(pair)
    claims = []
    now = _now()
    with transaction(database_path) as connection:
        for key, pairs in groups.items():
            effects = [float(pair["effect_value"]) for pair in pairs]
            unit = pairs[0]["effect_unit"]
            count = len(effects)
            direction_consistent = all(effect >= 0 for effect in effects) or all(effect <= 0 for effect in effects)
            strength = "strong" if count >= 4 and direction_consistent else "moderate" if count >= 2 and direction_consistent else "preliminary"
            transformation = json.loads(key)
            statement = (
                f"The localized transformation {transformation['a']} → {transformation['b']} "
                f"has a median observed effect of {median(effects):+.2f} {unit} across {count} eligible matched pair(s)."
            )
            claim_id = _id("claim")
            uncertainty = {
                "n_pairs": count,
                "effect_min": min(effects),
                "effect_max": max(effects),
                "method": "median_of_eligible_exact_or_approximate_measurements",
                "censored_values": "excluded",
                "direction_consistent": direction_consistent,
            }
            connection.execute(
                """
                INSERT INTO sar_claims
                    (id, project_id, claim_type, statement, scope_definition_json, effect_json,
                     uncertainty_json, evidence_strength, status, analysis_run_id, version, created_by, created_at)
                SELECT ?, project_id, 'observation', ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?
                FROM analysis_runs WHERE id = ?
                """,
                (
                    claim_id,
                    statement,
                    json.dumps({"transformation": transformation, "analysis_run_id": run_id}, sort_keys=True),
                    json.dumps({"median_effect": median(effects), "unit": unit}),
                    json.dumps(uncertainty, sort_keys=True),
                    strength,
                    run_id,
                    actor_user_id,
                    now,
                    run_id,
                ),
            )
            for pair in pairs:
                connection.execute(
                    """
                    INSERT INTO claim_evidence
                        (id, claim_id, evidence_type, compound_id, mmp_pair_id, relationship, note)
                    VALUES (?, ?, 'mmp_pair', ?, ?, 'supports', ?)
                    """,
                    (_id("evidence"), claim_id, pair["compound_a_id"], pair["id"], "compound A"),
                )
                connection.execute(
                    """
                    INSERT INTO claim_evidence
                        (id, claim_id, evidence_type, compound_id, mmp_pair_id, relationship, note)
                    VALUES (?, ?, 'mmp_pair', ?, ?, 'supports', ?)
                    """,
                    (_id("evidence"), claim_id, pair["compound_b_id"], pair["id"], "compound B"),
                )
            claims.append(
                {
                    "id": claim_id,
                    "claim_type": "observation",
                    "statement": statement,
                    "evidence_strength": strength,
                    "n_pairs": count,
                    "data_origin": "derived",
                }
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            SELECT ?, ?, project_id, 'claim_create', 'analysis_run', ?, 'success', ?, ?
            FROM analysis_runs WHERE id = ?
            """,
            (_id("audit"), actor_user_id, run_id, json.dumps({"claim_count": len(claims)}), now, run_id),
        )
    return {"analysis_run_id": run_id, "claims": claims, "data_origin": "derived"}
