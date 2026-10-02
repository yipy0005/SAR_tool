"""Pharmacophore-level R-group decomposition.

This module projects RDKit's graph-level chemical features onto the same
scaffold-relative R-sites used by the SAR Workbench. It deliberately does not
claim a 3-D binding pharmacophore: structures arrive as SMILES without saved
conformers, so every result is a reproducible 2-D atom projection with explicit
core/interface/ambiguous scope labels.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from rdkit import Chem, RDConfig
from rdkit.Chem import ChemicalFeatures, rdMolDescriptors, rdFMCS
from rdkit.Chem.Scaffolds import MurckoScaffold

from database import read_connection, transaction
from sar_discovery import assign_position_labels, consistent_substituents, describe_substituents


ANALYSIS_VERSION = "rgroup-pharmacophore-rdkit-v8"
FEATURE_PROFILE = "rdkit-basefeatures-v1+medchem-normalization-v3"
FEATURE_DEFINITION_PATH = Path(RDConfig.RDDataDir) / "BaseFeatures.fdef"


class PharmacophoreRGroupError(ValueError):
    """Raised when a pharmacophore R-group analysis cannot be run safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _molecule(smiles: Any) -> Chem.Mol | None:
    text = str(smiles or "").strip()
    if not text:
        return None
    return Chem.MolFromSmiles(text)


@lru_cache(maxsize=1)
def _feature_factory() -> tuple[Any, str]:
    if not FEATURE_DEFINITION_PATH.is_file():
        raise PharmacophoreRGroupError(
            f"RDKit feature definitions were not found at {FEATURE_DEFINITION_PATH}."
        )
    try:
        definition_hash = hashlib.sha256(FEATURE_DEFINITION_PATH.read_bytes()).hexdigest()
        factory = ChemicalFeatures.BuildFeatureFactory(str(FEATURE_DEFINITION_PATH))
    except Exception as exc:  # pragma: no cover - environment failure is covered by API mapping
        raise PharmacophoreRGroupError("RDKit pharmacophore feature definitions could not be loaded.") from exc
    return factory, definition_hash


def feature_profile_metadata() -> dict[str, Any]:
    """Return reproducibility metadata for the built-in RDKit feature profile."""
    _factory, definition_hash = _feature_factory()
    return {
        "feature_profile": FEATURE_PROFILE,
        "feature_definition_hash": definition_hash,
        "feature_families": [
            "Donor",
            "Acceptor",
            "NegIonizable",
            "PosIonizable",
            "ZnBinder",
            "Aromatic",
            "Hydrophobe",
            "LumpedHydrophobe",
            "Halogen",
        ],
        "coordinate_policy": "2d_atom_projection_no_3d_coordinates",
    }


def _feature_key(feature: Mapping[str, Any]) -> str:
    return f"{feature.get('family', '')}:{feature.get('type', '')}"


def _feature_records(molecule: Chem.Mol) -> list[dict[str, Any]]:
    factory, _definition_hash = _feature_factory()
    records: list[dict[str, Any]] = []
    for index, feature in enumerate(factory.GetFeaturesForMol(molecule)):
        family = feature.GetFamily()
        feature_type = feature.GetType()
        atom_ids = list(feature.GetAtomIds())
        # RDKit's BaseFeatures profile intentionally treats isolated halogens
        # as acceptors. Medicinal-chemistry R-group reporting should not call
        # aryl F/Cl/Br/I H-bond acceptors, so normalize that structural class
        # to an explicit halogen feature instead of presenting a misleading
        # pharmacophore claim.
        if family == "Acceptor" and atom_ids and all(
            molecule.GetAtomWithIdx(atom_id).GetAtomicNum() in {9, 17, 35, 53}
            for atom_id in atom_ids
        ):
            family = "Halogen"
            feature_type = "HalogenSubstituent"
        records.append(
            {
                "feature_index": index,
                "family": family,
                "type": feature_type,
                "atom_ids": atom_ids,
            }
        )
    return records


def _project_features(
    molecule: Chem.Mol,
    change: Mapping[str, Any],
    positions: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify each RDKit feature by core/site scope and return all/core features."""
    match = list(change.get("match") or [])
    core_atoms = set(match)
    site_atoms: dict[str, set[int]] = {}
    site_anchor_atoms: dict[str, set[int]] = {}
    for fragment in change.get("fragments", []):
        label = str(fragment.get("label") or "")
        if label:
            site_atoms[label] = set(fragment.get("molecule_atoms") or [])
            site_anchor_atoms[label] = {
                match[scaffold_index]
                for scaffold_index in fragment.get("scaffold_atoms") or []
                if 0 <= int(scaffold_index) < len(match)
            }
    # Keep H/unsubstituted positions represented. Their scaffold anchor is
    # still useful for identifying a one-atom core feature that a substituent
    # can mask (for example amide NH → N-Me).
    for position in positions:
        label = str(position["label"])
        site_atoms.setdefault(label, set())
        site_anchor_atoms.setdefault(
            label,
            {
                match[int(scaffold_index)]
                for scaffold_index in position.get("scaffold_atoms") or []
                if 0 <= int(scaffold_index) < len(match)
            },
        )

    all_features: list[dict[str, Any]] = []
    records = _feature_records(molecule)
    inverse_match = {molecule_index: scaffold_index for scaffold_index, molecule_index in enumerate(match)}
    for label, atoms in site_atoms.items():
        if not atoms:
            continue
        atoms_list = sorted(atoms)
        atom_numbers = [molecule.GetAtomWithIdx(atom_id).GetAtomicNum() for atom_id in atoms_list]
        has_carbon = 6 in atom_numbers
        halogen_only = bool(atom_numbers) and all(number in {9, 17, 35, 53} for number in atom_numbers)
        carbon_or_halogen_only = all(number in {6, 9, 17, 35, 53} for number in atom_numbers)
        already_hydrophobic = any(
            record.get("family") in {"Hydrophobe", "LumpedHydrophobe"}
            and set(record.get("atom_ids", [])).intersection(atoms)
            for record in records
        )
        already_halogen = any(
            record.get("family") == "Halogen"
            and set(record.get("atom_ids", [])).intersection(atoms)
            for record in records
        )
        if halogen_only and not already_halogen:
            records.append(
                {
                    "feature_index": len(records),
                    "family": "Halogen",
                    "type": "HalogenSubstituent",
                    "atom_ids": atoms_list,
                    "custom_normalization": "medchem-halogen-v2",
                }
            )
        if has_carbon and carbon_or_halogen_only and not already_hydrophobic:
            records.append(
                {
                    "feature_index": len(records),
                    "family": "Hydrophobe",
                    "type": "RGroupHydrophobe",
                    "atom_ids": atoms_list,
                    "custom_normalization": "medchem-rgroup-hydrophobe-v1",
                }
            )
    for record in records:
        feature_atoms = set(record["atom_ids"])
        record["scaffold_atom_ids"] = sorted(
            inverse_match[atom_id] for atom_id in feature_atoms if atom_id in inverse_match
        )
        fragment_sites = sorted(
            label for label, atoms in site_atoms.items() if atoms and feature_atoms.intersection(atoms)
        )
        anchor_sites = sorted(
            label for label, atoms in site_anchor_atoms.items()
            if atoms and feature_atoms.issubset(atoms)
        )
        touched_sites = sorted(set(fragment_sites) | set(anchor_sites))
        touches_core = bool(feature_atoms.intersection(core_atoms))
        if len(touched_sites) > 1:
            scope = "ambiguous"
        elif fragment_sites and touches_core:
            scope = "interface"
        elif fragment_sites:
            scope = "r_group"
        elif anchor_sites:
            scope = "interface"
        elif touches_core:
            scope = "core"
        else:
            scope = "unassigned"
        all_features.append(
            {
                **record,
                "site_labels": touched_sites,
                "scope": scope,
                "anchor_only": bool(anchor_sites) and not fragment_sites,
                "feature_key": _feature_key(record),
            }
        )

    core_features = [feature for feature in all_features if feature["scope"] == "core"]
    return all_features, core_features


def _feature_counts(features: Iterable[Mapping[str, Any]]) -> Counter[str]:
    return Counter(
        _feature_key(feature)
        for feature in features
        if feature.get("scope") in {"r_group", "interface", "ambiguous"}
    )


def _delta(reference: Counter[str], current: Counter[str]) -> dict[str, list[dict[str, Any]]]:
    def rows(counts: Counter[str]) -> list[dict[str, Any]]:
        result = []
        for key in sorted(counts):
            family, feature_type = key.split(":", 1)
            result.append({"family": family, "type": feature_type, "count": counts[key]})
        return result

    gained = Counter()
    lost = Counter()
    for key in set(reference) | set(current):
        difference = current[key] - reference[key]
        if difference > 0:
            gained[key] = difference
        elif difference < 0:
            lost[key] = -difference
    return {"gained": rows(gained), "lost": rows(lost)}


def _site_payload(
    change: Mapping[str, Any],
    positions: Iterable[Mapping[str, Any]],
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_label = {str(position["label"]): position for position in positions}
    result: list[dict[str, Any]] = []
    for label, position in by_label.items():
        fragment = next(
            (item for item in change.get("fragments", []) if item.get("label") == label),
            None,
        )
        comparison_features = [
            feature
            for feature in features
            if label in feature.get("site_labels", [])
            and feature.get("scope") in {"r_group", "interface", "ambiguous"}
            and not feature.get("anchor_only")
        ]
        site_features = [feature for feature in comparison_features if not feature.get("anchor_only")]
        anchor_features = [feature for feature in comparison_features if feature.get("anchor_only")]
        result.append(
            {
                "label": label,
                "scaffold_atoms": list(position.get("scaffold_atoms") or []),
                "fragment_smiles": fragment.get("smiles", "") if fragment else "",
                "attached_smiles": fragment.get("attached_smiles", "") if fragment else "",
                "substituent_name": fragment.get("name") if fragment else None,
                "fragment_status": "substituted" if fragment else "unsubstituted",
                "structural_profile": _structural_profile(fragment.get("smiles", "") if fragment else ""),
                "features": site_features,
                "anchor_features": anchor_features,
                "comparison_features": comparison_features,
                "feature_families": sorted({feature["family"] for feature in site_features}),
                "anchor_feature_families": sorted({feature["family"] for feature in anchor_features}),
                "feature_count": len(site_features),
            }
        )
    return result


def decompose_pharmacophore_rgroup(
    compound_id: str,
    registration_id: str,
    smiles: str,
    scaffold_smarts: str,
    change: Mapping[str, Any],
    positions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project RDKit chemical features onto one compound's scaffold-relative sites."""
    molecule = _molecule(smiles)
    base = {
        "compound_id": compound_id,
        "registration_id": registration_id,
        "scaffold_smarts": scaffold_smarts,
        "match_atoms": list(change.get("match") or []),
        "sites": [],
        "features": [],
        "core_features": [],
        "status": change.get("status", "unmatched"),
        "failure_reason": None,
    }
    if molecule is None:
        base["status"] = "invalid"
        base["failure_reason"] = "structure_parse_failed"
        return base
    if change.get("status") == "unmatched":
        base["failure_reason"] = "scaffold_not_found"
        return base
    try:
        features, core_features = _project_features(molecule, change, positions)
    except PharmacophoreRGroupError:
        raise
    except Exception as exc:
        base["status"] = "invalid"
        base["failure_reason"] = f"feature_projection_failed:{type(exc).__name__}"
        return base
    base["status"] = "assigned"
    base["features"] = features
    base["core_features"] = core_features
    base["sites"] = _site_payload(change, positions, features)
    return base


def run_pharmacophore_rgroup_analysis(
    database_path: str,
    project_id: str,
    scaffold_smarts: str,
    reference_compound_id: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Persist a reference-anchored, 2-D pharmacophore R-group projection."""
    scaffold_smarts = str(scaffold_smarts or "").strip()
    reference_compound_id = str(reference_compound_id or "").strip()
    if not reference_compound_id:
        raise PharmacophoreRGroupError("reference_compound_id is required.")

    profile = feature_profile_metadata()
    with read_connection(database_path) as connection:
        compounds = [
            dict(row)
            for row in connection.execute(
                """
                SELECT c.id AS compound_id, c.registration_id, s.isomeric_smiles
                FROM compounds c JOIN structure_records s ON s.compound_id = c.id
                WHERE c.project_id = ? ORDER BY c.registration_id
                """,
                (project_id,),
            )
        ]
    if not compounds:
        raise PharmacophoreRGroupError("The project has no standardized compounds to analyze.")
    if reference_compound_id not in {row["compound_id"] for row in compounds}:
        raise PharmacophoreRGroupError("The reference compound is not in this project.")
    scaffold_metadata = derive_series_scaffold(compounds, reference_compound_id)
    if not scaffold_smarts:
        scaffold_smarts = scaffold_metadata["scaffold_smarts"]
    if Chem.MolFromSmarts(scaffold_smarts) is None:
        raise PharmacophoreRGroupError("Invalid scaffold SMARTS.")

    scaffold = Chem.MolFromSmarts(scaffold_smarts)
    molecules = [_molecule(row["isomeric_smiles"]) for row in compounds]
    changes = consistent_substituents(molecules, scaffold)
    reference_index = next(index for index, row in enumerate(compounds) if row["compound_id"] == reference_compound_id)
    changes = _align_changes_to_reference(molecules, scaffold, changes, reference_index)
    positions = assign_position_labels(changes)
    if changes[reference_index].get("status") == "unmatched":
        raise PharmacophoreRGroupError("The reference compound does not contain the declared scaffold.")

    assignments = [
        decompose_pharmacophore_rgroup(
            row["compound_id"],
            row["registration_id"],
            row["isomeric_smiles"],
            scaffold_smarts,
            change,
            positions,
        )
        for row, change in zip(compounds, changes)
    ]
    reference_assignment = assignments[reference_index]
    reference_by_site = {
        site["label"]: _feature_counts(site.get("comparison_features", site.get("features", [])))
        for site in reference_assignment.get("sites", [])
    }
    reference_structural_by_site = {
        site["label"]: site.get("structural_profile", _empty_structural_profile())
        for site in reference_assignment.get("sites", [])
    }
    reference_core_features = reference_assignment.get("core_features", [])
    for assignment in assignments:
        assignment["core_feature_delta"] = (
            {"gained": [], "lost": [], "relocated": []}
            if assignment.get("status") == "unmatched"
            else _core_feature_delta(
                reference_core_features,
                assignment.get("core_features", []),
            )
        )
        for site in assignment.get("sites", []):
            site["feature_delta"] = _delta(
                reference_by_site.get(site["label"], Counter()),
                _feature_counts(site.get("features", [])),
            )
            site["structural_delta"] = _numeric_delta(
                reference_structural_by_site.get(site["label"], _empty_structural_profile()),
                site.get("structural_profile", _empty_structural_profile()),
            )
    structural_ranges = _structural_ranges(assignments, positions)
    effect_ranges = _series_effect_ranges(database_path, project_id, assignments, reference_compound_id)

    input_selection = {
        "scaffold_smarts": scaffold_smarts,
        "reference_compound_id": reference_compound_id,
        "reference_registration_id": compounds[reference_index]["registration_id"],
        "positions": positions,
        "structural_ranges": structural_ranges,
        "effect_ranges": effect_ranges,
        "scaffold_strategy": scaffold_metadata.get("scaffold_strategy"),
        "matched_compound_count": scaffold_metadata.get("matched_compound_count"),
        "series_compound_count": scaffold_metadata.get("series_compound_count"),
        "match_policy": "symmetry_consistent_shared_numbering",
        **profile,
    }
    run_id = _id("analysis")
    now = _now()
    with transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs (id, project_id, analysis_type, input_selection_json, algorithm_version, status, created_at)
            VALUES (?, ?, 'pharmacophore_rgroup', ?, ?, 'completed', ?)
            """,
            (run_id, project_id, json.dumps(input_selection, sort_keys=True), ANALYSIS_VERSION, now),
        )
        for assignment in assignments:
            connection.execute(
                """
                INSERT INTO pharmacophore_rgroup_assignments
                    (id, analysis_run_id, compound_id, reference_compound_id, scaffold_smarts,
                     match_atoms_json, sites_json, features_json, core_features_json,
                     core_feature_delta_json, status, failure_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("pharmargroup"),
                    run_id,
                    assignment["compound_id"],
                    reference_compound_id,
                    scaffold_smarts,
                    json.dumps(assignment.get("match_atoms", [])),
                    json.dumps(assignment.get("sites", []), sort_keys=True),
                    json.dumps(assignment.get("features", []), sort_keys=True),
                    json.dumps(assignment.get("core_features", []), sort_keys=True),
                    json.dumps(assignment.get("core_feature_delta", {}), sort_keys=True),
                    assignment.get("status", "invalid"),
                    assignment.get("failure_reason"),
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
                    {
                        "type": "pharmacophore_rgroup",
                        "reference_compound_id": reference_compound_id,
                        "scaffold_smarts": scaffold_smarts,
                        "assignment_count": len(assignments),
                        "feature_profile": FEATURE_PROFILE,
                    },
                    sort_keys=True,
                ),
                now,
            ),
        )
    return {
        "analysis_run_id": run_id,
        "analysis_type": "pharmacophore_rgroup",
        "algorithm_version": ANALYSIS_VERSION,
        "input_selection": input_selection,
        "reference_compound_id": reference_compound_id,
        "reference_registration_id": compounds[reference_index]["registration_id"],
        "positions": positions,
        "assignments": assignments,
        "data_origin": "derived",
    }


def get_pharmacophore_rgroup_run(database_path: str, run_id: str) -> dict[str, Any] | None:
    """Load one persisted pharmacophore R-group run with decoded JSON fields."""
    with read_connection(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND analysis_type = 'pharmacophore_rgroup'",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        rows = connection.execute(
            """
            SELECT pa.*, c.registration_id
            FROM pharmacophore_rgroup_assignments pa
            JOIN compounds c ON c.id = pa.compound_id
            WHERE pa.analysis_run_id = ?
            ORDER BY c.registration_id
            """,
            (run_id,),
        ).fetchall()
    result = dict(run)
    result["input_selection"] = json.loads(result.pop("input_selection_json") or "{}")
    assignments = []
    for row in rows:
        item = dict(row)
        item["match_atoms"] = json.loads(item.pop("match_atoms_json") or "[]")
        item["sites"] = json.loads(item.pop("sites_json") or "[]")
        item["features"] = json.loads(item.pop("features_json") or "[]")
        item["core_features"] = json.loads(item.pop("core_features_json") or "[]")
        item["core_feature_delta"] = json.loads(item.pop("core_feature_delta_json") or "{}")
        if item.get("status") == "unmatched":
            item["core_feature_delta"] = {"gained": [], "lost": [], "relocated": []}
        assignments.append(item)
    result["assignments"] = assignments
    result["positions"] = result["input_selection"].get("positions", [])
    result["structural_ranges"] = result["input_selection"].get("structural_ranges", {})
    result["effect_ranges"] = result["input_selection"].get("effect_ranges", {})
    result["reference_compound_id"] = result["input_selection"].get("reference_compound_id")
    result["reference_registration_id"] = result["input_selection"].get("reference_registration_id")
    result["data_origin"] = "derived"
    return result


def derive_series_scaffold(
    compounds: Iterable[Mapping[str, Any]],
    reference_compound_id: str,
    *,
    threshold: float = 0.8,
) -> dict[str, Any]:
    """Derive a tolerant shared scaffold from a selected reference series.

    The previous UI defaulted to benzene, which made the entire invariant
    amide/pyridyl arm appear as ``R1``. This policy starts with the reference's
    Murcko scaffold and uses a ring-complete MCS across the series, allowing
    element substitutions such as a heteroaryl position change. Outliers that
    do not contain this shared core stay explicitly unmatched rather than being
    force-fitted into a chemically misleading R-site.
    """
    records = [dict(item) for item in compounds]
    reference = next(
        (item for item in records if str(item.get("compound_id") or item.get("id")) == reference_compound_id),
        None,
    )
    reference_molecule = _molecule(reference.get("isomeric_smiles") if reference else None)
    if reference_molecule is None:
        raise PharmacophoreRGroupError("The reference compound has no valid standardized structure.")
    reference_scaffold = MurckoScaffold.GetScaffoldForMol(reference_molecule)
    if reference_scaffold is None or reference_scaffold.GetNumAtoms() < 1:
        reference_scaffold = reference_molecule
    fallback = Chem.MolToSmarts(reference_scaffold)
    valid = [molecule for molecule in (_molecule(item.get("isomeric_smiles")) for item in records) if molecule is not None]
    if len(valid) < 2:
        return {
            "scaffold_smarts": fallback,
            "scaffold_strategy": "reference_murcko",
            "matched_compound_count": len(valid),
            "series_compound_count": len(records),
        }
    try:
        mcs = rdFMCS.FindMCS(
            valid,
            threshold=float(threshold),
            timeout=10,
            ringMatchesRingOnly=True,
            completeRingsOnly=True,
            atomCompare=rdFMCS.AtomCompare.CompareAny,
            bondCompare=rdFMCS.BondCompare.CompareOrder,
        )
        query = Chem.MolFromSmarts(mcs.smartsString) if not mcs.canceled else None
        reference_fraction = reference_scaffold.GetNumHeavyAtoms() * 0.5
        if query is not None and mcs.numAtoms >= max(6, reference_fraction):
            matched = sum(1 for molecule in valid if molecule.HasSubstructMatch(query))
            if matched >= max(2, int(len(valid) * float(threshold))):
                return {
                    "scaffold_smarts": mcs.smartsString,
                    "scaffold_strategy": "series_mcs_ring_complete",
                    "matched_compound_count": matched,
                    "series_compound_count": len(records),
                    "mcs_atom_count": mcs.numAtoms,
                }
    except Exception:
        # A reference Murcko scaffold is safer than failing the whole page if
        # a heterogeneous project makes MCS calculation unavailable.
        pass
    return {
        "scaffold_smarts": fallback,
        "scaffold_strategy": "reference_murcko",
        "matched_compound_count": sum(1 for molecule in valid if molecule.HasSubstructMatch(Chem.MolFromSmarts(fallback))),
        "series_compound_count": len(records),
    }


def _empty_structural_profile() -> dict[str, Any]:
    return {
        "heavy_atom_count": 0,
        "rotatable_bonds": 0,
        "ring_count": 0,
        "aromatic_ring_count": 0,
        "non_ring_heavy_atoms": 0,
        "ring_status": "unsubstituted",
    }


def _structural_profile(fragment_smiles: str) -> dict[str, Any]:
    """Calculate descriptors on the isolated R-group fragment, not the core.

    Rotatable bonds intentionally exclude the scaffold attachment bond because
    the descriptor is meant to compare the substituent's internal flexibility.
    """
    molecule = _molecule(fragment_smiles)
    if molecule is None:
        return _empty_structural_profile()
    ring_count = int(rdMolDescriptors.CalcNumRings(molecule))
    return {
        "heavy_atom_count": int(molecule.GetNumHeavyAtoms()),
        "rotatable_bonds": int(rdMolDescriptors.CalcNumRotatableBonds(molecule, strict=True)),
        "ring_count": ring_count,
        "aromatic_ring_count": int(rdMolDescriptors.CalcNumAromaticRings(molecule)),
        "non_ring_heavy_atoms": sum(
            1 for atom in molecule.GetAtoms() if atom.GetAtomicNum() > 1 and not atom.IsInRing()
        ),
        "ring_status": "ring-containing" if ring_count else "acyclic",
    }


def _numeric_delta(reference: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("heavy_atom_count", "rotatable_bonds", "ring_count", "aromatic_ring_count", "non_ring_heavy_atoms")
    result = {field: int(current.get(field, 0)) - int(reference.get(field, 0)) for field in fields}
    result["ring_status_changed"] = reference.get("ring_status") != current.get("ring_status")
    return result


def _structural_ranges(assignments: Iterable[Mapping[str, Any]], positions: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for position in positions:
        label = str(position["label"])
        sites = []
        for assignment in assignments:
            sites.extend(site for site in assignment.get("sites", []) if site.get("label") == label)
        profiles = [site.get("structural_profile", _empty_structural_profile()) for site in sites]
        if not profiles:
            continue
        ranges: dict[str, Any] = {"member_count": len(profiles), "ring_statuses": sorted({profile.get("ring_status") for profile in profiles})}
        for field in ("heavy_atom_count", "rotatable_bonds", "ring_count", "aromatic_ring_count", "non_ring_heavy_atoms"):
            values = [int(profile.get(field, 0)) for profile in profiles]
            ranges[field] = {"min": min(values), "max": max(values)}
        result[label] = ranges
    return result


def _effect_direction(endpoint_key: str, unit: str | None) -> str:
    text = f"{endpoint_key} {unit or ''}".lower()
    if any(token in text for token in ("pic50", "papp", "permeability", "solubility")):
        return "higher"
    if any(token in text for token in ("ic50", "ec50", "ac50", "ki", "kd", "clint")):
        return "lower"
    return "unspecified"


def _effect_group_keys(site: Mapping[str, Any]) -> list[tuple[str, str]]:
    groups: list[tuple[str, str]] = []
    for family in site.get("feature_families", []):
        groups.append(("feature", str(family)))
    profile = site.get("structural_profile") or _empty_structural_profile()
    groups.extend(
        [
            ("rotatable_bonds", str(profile.get("rotatable_bonds", 0))),
            ("ring_count", str(profile.get("ring_count", 0))),
            ("ring_status", str(profile.get("ring_status", "unsubstituted"))),
        ]
    )
    return groups


def _series_effect_ranges(
    database_path: str,
    project_id: str,
    assignments: list[Mapping[str, Any]],
    reference_compound_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Summarize compatible exact endpoint changes by feature/structure group and R-site."""
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT ms.compound_id, ms.compatibility_key, ms.canonical_unit,
                   ms.summary_state, ms.summary_value, ms.summary_qualifier,
                   ms.created_at, ms.id
            FROM measurement_summaries ms
            JOIN compounds c ON c.id = ms.compound_id
            WHERE c.project_id = ?
            ORDER BY ms.created_at, ms.id
            """,
            (project_id,),
        ).fetchall()
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        key = (item["compound_id"], item["compatibility_key"], item.get("canonical_unit") or "")
        latest[key] = item
    endpoint_contexts = sorted({(item["compatibility_key"], item.get("canonical_unit") or "") for item in latest.values()})
    reference_values: dict[tuple[str, str], float] = {}
    for endpoint_key, unit in endpoint_contexts:
        item = latest.get((reference_compound_id, endpoint_key, unit))
        if not item or not str(item.get("summary_state", "")).startswith("observed") or item.get("summary_qualifier") not in {"=", "~"} or item.get("summary_value") is None:
            continue
        reference_values[(endpoint_key, unit)] = float(item["summary_value"])

    groups_by_site: dict[str, dict[tuple[str, str], set[str]]] = {}
    for assignment in assignments:
        compound_id = str(assignment.get("compound_id"))
        for site in assignment.get("sites", []):
            site_groups = groups_by_site.setdefault(str(site["label"]), {})
            for group in _effect_group_keys(site):
                site_groups.setdefault(group, set()).add(compound_id)

    result: dict[str, list[dict[str, Any]]] = {}
    for site_label, groups in groups_by_site.items():
        site_ranges: list[dict[str, Any]] = []
        for (group_type, group_value), member_ids in groups.items():
            if len(member_ids) < 2:
                continue
            for endpoint_key, unit in endpoint_contexts:
                reference_value = reference_values.get((endpoint_key, unit))
                if reference_value is None:
                    continue
                deltas: list[float] = []
                for compound_id in member_ids:
                    item = latest.get((compound_id, endpoint_key, unit))
                    if not item or not str(item.get("summary_state", "")).startswith("observed") or item.get("summary_qualifier") not in {"=", "~"} or item.get("summary_value") is None:
                        continue
                    deltas.append(float(item["summary_value"]) - reference_value)
                if len(deltas) < 2:
                    continue
                if group_type == "feature":
                    label = group_value
                elif group_type == "rotatable_bonds":
                    label = f"RB = {group_value}"
                elif group_type == "ring_count":
                    label = f"{group_value} ring" if group_value == "1" else f"{group_value} rings"
                else:
                    label = group_value
                site_ranges.append(
                    {
                        "site_label": site_label,
                        "group_type": group_type,
                        "group_value": group_value,
                        "group_label": label,
                        "member_count": len(member_ids),
                        "endpoint_key": endpoint_key,
                        "endpoint_name": endpoint_key.split(":", 1)[0] or "Endpoint",
                        "unit": unit,
                        "direction": _effect_direction(endpoint_key, unit),
                        "delta_min": min(deltas),
                        "delta_max": max(deltas),
                        "data_origin": "derived",
                    }
                )
        result[site_label] = sorted(
            site_ranges,
            key=lambda item: (item["group_type"], item["group_label"], item["endpoint_key"], item["unit"]),
        )[:80]
    return result


def _core_feature_delta(
    reference_features: Iterable[Mapping[str, Any]],
    current_features: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Detect core pharmacophore gains, losses and relocations by scaffold atom."""
    def family_rows(features: Iterable[Mapping[str, Any]]) -> Counter[str]:
        return Counter(_feature_key(feature) for feature in features)

    def positions(features: Iterable[Mapping[str, Any]]) -> dict[str, set[tuple[int, ...]]]:
        grouped: dict[str, set[tuple[int, ...]]] = {}
        for feature in features:
            atom_ids = tuple(feature.get("scaffold_atom_ids") or [])
            # Single-atom feature relocation is the useful medicinal-chemistry
            # signal; multi-atom aromatic environments are intentionally not
            # called relocated when RDKit chooses a different symmetric match.
            if len(atom_ids) != 1:
                continue
            grouped.setdefault(_feature_key(feature), set()).add(atom_ids)
        return grouped

    reference = list(reference_features)
    current = list(current_features)
    ref_counts = family_rows(reference)
    current_counts = family_rows(current)
    gained: list[dict[str, Any]] = []
    lost: list[dict[str, Any]] = []
    for key in sorted(set(ref_counts) | set(current_counts)):
        difference = current_counts[key] - ref_counts[key]
        family, feature_type = key.split(":", 1)
        if difference > 0:
            gained.append({"family": family, "type": feature_type, "count": difference})
        elif difference < 0:
            lost.append({"family": family, "type": feature_type, "count": -difference})

    relocated: list[dict[str, Any]] = []
    reference_positions = positions(reference)
    current_positions = positions(current)
    for key in sorted(set(reference_positions) & set(current_positions)):
        if reference_positions[key] == current_positions[key]:
            continue
        family, feature_type = key.split(":", 1)
        relocated.append(
            {
                "family": family,
                "type": feature_type,
                "reference_scaffold_atoms": [list(item) for item in sorted(reference_positions[key])],
                "current_scaffold_atoms": [list(item) for item in sorted(current_positions[key])],
            }
        )
    return {"gained": gained, "lost": lost, "relocated": relocated}


def _core_feature_signature(molecule: Chem.Mol, match: Iterable[int]) -> Counter[tuple[str, str, tuple[int, ...]]]:
    """Return position-aware single-atom core pharmacophore features for a match."""
    inverse = {molecule_index: scaffold_index for scaffold_index, molecule_index in enumerate(match)}
    signature: Counter[tuple[str, str, tuple[int, ...]]] = Counter()
    for feature in _feature_records(molecule):
        scaffold_atoms = tuple(sorted(inverse[atom_id] for atom_id in feature["atom_ids"] if atom_id in inverse))
        if len(scaffold_atoms) == 1 and feature["family"] in {
            "Donor",
            "Acceptor",
            "PosIonizable",
            "NegIonizable",
            "ZnBinder",
        }:
            signature[(feature["family"], feature["type"], scaffold_atoms)] += 1
    return signature


def _align_changes_to_reference(
    molecules: list[Chem.Mol | None],
    scaffold: Chem.Mol,
    initial_changes: list[dict[str, Any]],
    reference_index: int,
) -> list[dict[str, Any]]:
    """Choose each compound's symmetry-equivalent match using the reference pharmacophore.

    Fragment-based matching alone can swap two chemically different rings when
    a tolerant MCS contains alternatives such as ``[#6,#7]``. That makes a
    stable pyridyl nitrogen look like it moved in every analogue. We choose the
    match that preserves the reference's position-aware donor/acceptor pattern;
    a true heteroatom relocation, such as POS-007, then remains visible.
    """
    reference_molecule = molecules[reference_index]
    reference_match = initial_changes[reference_index].get("match")
    if reference_molecule is None or not reference_match:
        return initial_changes
    reference_signature = _core_feature_signature(reference_molecule, reference_match)
    aligned: list[dict[str, Any]] = []
    for index, (molecule, initial) in enumerate(zip(molecules, initial_changes)):
        if molecule is None or initial.get("status") == "unmatched":
            aligned.append(initial)
            continue
        matches = molecule.GetSubstructMatches(scaffold, uniquify=False, maxMatches=96)
        if not matches:
            aligned.append(initial)
            continue
        initial_match = tuple(initial.get("match") or ())
        scored: list[tuple[int, int, tuple[int, ...], tuple[int, ...]]] = []
        for match in matches:
            signature = _core_feature_signature(molecule, match)
            score = sum(min(reference_signature[key], signature[key]) for key in set(reference_signature) | set(signature))
            same_as_initial = int(tuple(match) == initial_match)
            scored.append((score, same_as_initial, tuple(match), tuple(match)))
        _score, _same, _sort_match, chosen_match = max(scored)
        aligned.append(describe_substituents(molecule, scaffold, chosen_match))
    return aligned
