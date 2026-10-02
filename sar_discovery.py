from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from typing import Any, Iterable, Mapping

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator
from rdkit.Chem.Scaffolds import MurckoScaffold

from display import substituent_name


class SarDiscoveryError(ValueError):
    """Raised when a SAR discovery request cannot be interpreted safely."""


_MORGAN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _molecule(smiles: Any) -> Chem.Mol | None:
    text = _text(smiles)
    if not text:
        return None
    molecule = Chem.MolFromSmiles(text)
    return molecule if molecule is not None else None


def _scaffold(molecule: Chem.Mol) -> str:
    scaffold = MurckoScaffold.GetScaffoldForMol(molecule)
    return Chem.MolToSmiles(scaffold, canonical=True) if scaffold is not None else ""


def _direction(endpoint_key: str, unit: str | None) -> str:
    key = _text(endpoint_key).lower()
    normalized_unit = _text(unit).lower()
    if "pic50" in key or normalized_unit == "pic50":
        return "higher"
    if any(token in key for token in ("ic50", "ec50", "ac50", "ki", "kd", "mic")):
        return "lower"
    return "unspecified"


def _latest_summaries(summaries: Iterable[Mapping[str, Any]], endpoint_key: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for raw in summaries:
        summary = dict(raw)
        if _text(summary.get("compatibility_key")) != endpoint_key:
            continue
        compound_id = _text(summary.get("compound_id"))
        if not compound_id:
            continue
        current = latest.get(compound_id)
        current_stamp = (
            _text(current.get("created_at")) if current else "",
            _text(current.get("id")) if current else "",
        )
        candidate_stamp = (_text(summary.get("created_at")), _text(summary.get("id")))
        if current is None or candidate_stamp > current_stamp:
            latest[compound_id] = summary
    return latest


def _attached_fragment_smiles(molecule: Chem.Mol, fragment: set[int], attachments: list[int]) -> str:
    """SMILES of a substituent with each scaffold attachment atom replaced by a dummy atom."""
    editable = Chem.RWMol(molecule)
    for index in attachments:
        atom = editable.GetAtomWithIdx(index)
        atom.SetAtomicNum(0)
        atom.SetIsAromatic(False)
        atom.SetFormalCharge(0)
        atom.SetNumExplicitHs(0)
        atom.SetNoImplicit(True)
        atom.SetIsotope(0)
        for bond in atom.GetBonds():
            if bond.GetOtherAtomIdx(index) in fragment and bond.GetIsAromatic():
                bond.SetIsAromatic(False)
                bond.SetBondType(Chem.BondType.SINGLE)
    try:
        return Chem.MolFragmentToSmiles(editable, atomsToUse=sorted(fragment | set(attachments)), canonical=True)
    except Exception:  # Keep the plain fragment SMILES if the dummy form cannot be written.
        return ""


def _substituent_changes(smiles: Any, scaffold_smiles: str) -> dict[str, Any]:
    """Describe the fragments outside the shared Murcko scaffold.

    Each fragment records the scaffold atom it is attached to, so the series can
    number positions consistently (R1, R2, …) across all members. This is a
    scaffold-relative display aid; it does not claim that a fragment caused an
    endpoint difference and does not replace a reviewed R-group analysis.
    """
    return describe_substituents(_molecule(smiles), _molecule(scaffold_smiles))


def describe_substituents(
    molecule: Chem.Mol | None,
    scaffold: Chem.Mol | None,
    match: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Fragments outside ``scaffold`` in attachment order, with names where recognised.

    ``match`` selects one of the scaffold's symmetry-equivalent substructure
    matches; by default RDKit's first match is used. Use
    ``consistent_substituents`` when comparing several compounds so the same
    scaffold atom is chosen for the same kind of substituent in each one.
    """
    if molecule is None or scaffold is None:
        return {
            "status": "unmatched",
            "label": "Structure could not be matched to the shared scaffold",
            "fragments": [],
        }
    if match is None:
        match = molecule.GetSubstructMatch(scaffold)
    if not match:
        return {
            "status": "unmatched",
            "label": "Structure differs from the shared scaffold",
            "fragments": [],
        }

    scaffold_index_for = {molecule_index: scaffold_index for scaffold_index, molecule_index in enumerate(match)}
    scaffold_atoms = set(match)
    seen: set[int] = set()
    fragments: list[dict[str, Any]] = []
    for molecule_scaffold_index in sorted(scaffold_atoms):
        atom = molecule.GetAtomWithIdx(molecule_scaffold_index)
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
            fragment_smiles = Chem.MolFragmentToSmiles(
                molecule,
                atomsToUse=sorted(fragment),
                canonical=True,
            )
            if not fragment_smiles:
                continue
            attachments = sorted(
                {
                    other.GetIdx()
                    for index in fragment
                    for other in molecule.GetAtomWithIdx(index).GetNeighbors()
                    if other.GetIdx() in scaffold_atoms
                }
            )
            attached = _attached_fragment_smiles(molecule, fragment, attachments)
            fragments.append(
                {
                    "label": f"R-group {len(fragments) + 1}",
                    "smiles": fragment_smiles,
                    "attached_smiles": attached,
                    "name": substituent_name(attached) if len(attachments) == 1 else None,
                    "scaffold_atoms": [scaffold_index_for[index] for index in attachments],
                    "molecule_atoms": sorted(fragment),
                }
            )

    if not fragments:
        return {
            "status": "parent",
            "label": "Parent scaffold · no outside substituent",
            "fragments": [],
            "match": list(match),
        }
    return {
        "status": "assigned",
        "label": " · ".join(f'{fragment["label"]} = {fragment["smiles"]}' for fragment in fragments),
        "fragments": fragments,
        "match": list(match),
    }


def consistent_substituents(
    molecules: list[Chem.Mol | None],
    scaffold: Chem.Mol | None,
    *,
    max_matches: int = 96,
    placed: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """``describe_substituents`` for a set of compounds with one shared numbering.

    Symmetric scaffolds (a benzene ring, a para-disubstituted core) match each
    compound in several equivalent ways, and RDKit's first match can put the
    same substituent on a different scaffold atom in different compounds. That
    makes "R1" mean one group here and another group there. For every compound
    we instead pick the equivalent match that puts its substituents where the
    same substituents already sit in the other compounds, so a position label
    refers to one scaffold site throughout. Compounds with more substituents
    are placed first because they pin down more of the numbering. Display aid
    only: persisted R-group assignments are not changed.

    ``placed`` takes results from an earlier call; new molecules (for example
    a proposed analogue) are then fitted to that numbering without moving it.
    """
    results: list[dict[str, Any] | None] = [None] * len(molecules)
    seen_at: dict[tuple[int, ...], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for change in placed:
        for fragment in change.get("fragments", []):
            seen_at[tuple(fragment["scaffold_atoms"])][fragment["smiles"]] += 1
    candidates_for: dict[int, list[dict[str, Any]]] = {}
    for index, molecule in enumerate(molecules):
        if molecule is None or scaffold is None:
            candidates_for[index] = [describe_substituents(molecule, scaffold)]
            continue
        matches = molecule.GetSubstructMatches(scaffold, uniquify=False, maxMatches=max_matches)
        described = [describe_substituents(molecule, scaffold, match) for match in matches]
        candidates_for[index] = described or [describe_substituents(molecule, scaffold)]
    order = sorted(
        range(len(molecules)),
        key=lambda index: (-len(candidates_for[index][0].get("fragments", [])), index),
    )
    for index in order:
        def score(candidate: dict[str, Any]) -> tuple[Any, ...]:
            fragments = candidate.get("fragments", [])
            same = sum(seen_at[tuple(item["scaffold_atoms"])][item["smiles"]] for item in fragments if tuple(item["scaffold_atoms"]) in seen_at)
            occupied = sum(1 for item in fragments if tuple(item["scaffold_atoms"]) in seen_at)
            # Negated sorted positions: lower scaffold atom indices win ties, so numbering is deterministic.
            placement = tuple(sorted((tuple(item["scaffold_atoms"]), item["smiles"]) for item in fragments))
            return (same, occupied, tuple((tuple(-atom for atom in atoms), smiles) for atoms, smiles in placement))

        chosen = max(candidates_for[index], key=score)
        for fragment in chosen.get("fragments", []):
            seen_at[tuple(fragment["scaffold_atoms"])][fragment["smiles"]] += 1
        results[index] = chosen
    return [item or describe_substituents(None, None) for item in results]


def assign_position_labels(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Number scaffold attachment sites R1, R2, … in scaffold atom order and label each fragment."""
    counts: dict[tuple[int, ...], int] = defaultdict(int)
    for change in changes:
        for fragment in change.get("fragments", []):
            counts[tuple(fragment.get("scaffold_atoms") or ())] += 1
    ordered = sorted((key for key in counts if key), key=lambda key: (key[0], key))
    position_for = {key: f"R{index + 1}" for index, key in enumerate(ordered)}
    for change in changes:
        for fragment in change.get("fragments", []):
            label = position_for.get(tuple(fragment.get("scaffold_atoms") or ()))
            if label:
                fragment["label"] = label
    return [
        {"label": position_for[key], "scaffold_atoms": list(key), "member_count": counts[key]}
        for key in ordered
    ]


def _assign_positions(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Number attachment positions consistently across a series and relabel member fragments."""
    positions = assign_position_labels([member["structural_change"] for member in members])
    for member in members:
        change = member["structural_change"]
        by_position: dict[str, dict[str, Any]] = {}
        for fragment in change.get("fragments", []):
            label = fragment.get("label", "")
            if not label.startswith("R") or not label[1:].isdigit():
                continue
            fragment["display"] = fragment.get("name") or fragment["smiles"]
            by_position[label] = fragment
        change["by_position"] = by_position
        if change.get("status") in {"assigned", "parent"}:
            parts = [
                f'{position["label"]} = {by_position[position["label"]]["smiles"] if position["label"] in by_position else "H"}'
                for position in positions
            ]
            change["label"] = " · ".join(parts) if parts else change["label"]
            change["display_label"] = " · ".join(
                f'{position["label"]} = {by_position[position["label"]]["display"] if position["label"] in by_position else "H"}'
                for position in positions
            ) or "Unsubstituted scaffold"
    return positions


def _member(
    compound: Mapping[str, Any],
    summary: Mapping[str, Any] | None,
    scaffold_smiles: str,
    structural_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "compound_id": _text(compound.get("compound_id") or compound.get("id")),
        "registration_id": _text(compound.get("registration_id")),
        "preferred_name": _text(compound.get("preferred_name")),
        "isomeric_smiles": _text(compound.get("isomeric_smiles")),
        "rendered_svg": _text(compound.get("rendered_svg")),
        "structural_change": structural_change
        if structural_change is not None
        else _substituent_changes(compound.get("isomeric_smiles"), scaffold_smiles),
        "summary_state": _text(summary.get("summary_state")) if summary else "missing",
        "summary_value": summary.get("summary_value") if summary else None,
        "canonical_unit": _text(summary.get("canonical_unit")) if summary else "",
        "summary_qualifier": _text(summary.get("summary_qualifier")) if summary else "",
        "source_measurement_ids": summary.get("source_measurement_ids", []) if summary else [],
    }


def _is_exact_observed(member: Mapping[str, Any]) -> bool:
    return (
        member.get("summary_value") is not None
        and _text(member.get("summary_state")).startswith("observed")
        and _text(member.get("summary_qualifier") or "=") in {"=", "~"}
    )


def _add_endpoint_effects(
    members: list[dict[str, Any]],
    direction: str,
    preferred_reference_id: str | None = None,
) -> dict[str, Any] | None:
    reference = next(
        (
            member
            for member in members
            if preferred_reference_id
            and member["compound_id"] == preferred_reference_id
            and _is_exact_observed(member)
        ),
        None,
    ) or next((member for member in members if _is_exact_observed(member)), None)
    for member in members:
        member["is_reference"] = bool(
            reference and member["compound_id"] == reference["compound_id"]
        )
        member["endpoint_delta"] = None
        member["endpoint_effect"] = "unavailable"
        if reference is None or not _is_exact_observed(member):
            continue
        delta = float(member["summary_value"]) - float(reference["summary_value"])
        member["endpoint_delta"] = delta
        if member["is_reference"]:
            member["endpoint_effect"] = "reference"
        elif abs(delta) < 1e-12:
            member["endpoint_effect"] = "no_change"
        elif direction == "lower":
            member["endpoint_effect"] = "better" if delta < 0 else "worse"
        elif direction == "higher":
            member["endpoint_effect"] = "better" if delta > 0 else "worse"
        else:
            member["endpoint_effect"] = "observed_change"
    return reference


def _series_payload(
    scaffold_smiles: str,
    members: list[dict[str, Any]],
    endpoint_key: str,
    *,
    selected_compound_id: str | None = None,
    mode: str = "discover",
) -> dict[str, Any]:
    observed = [
        member
        for member in members
        if member["summary_value"] is not None and member["summary_state"].startswith("observed")
    ]
    unit = next((member["canonical_unit"] for member in observed if member["canonical_unit"]), "")
    direction = _direction(endpoint_key, unit)
    reference = _add_endpoint_effects(
        members,
        direction,
        preferred_reference_id=selected_compound_id if mode == "selected" else None,
    )
    reference_selection = (
        "selected_compound"
        if mode == "selected" and reference and reference["compound_id"] == selected_compound_id
        else "first_exact_observed"
    )
    values = [float(member["summary_value"]) for member in observed]
    ranked = sorted(observed, key=lambda item: float(item["summary_value"]), reverse=direction == "higher")
    positions = _assign_positions(members)
    series_id = f"scaffold-{sha256(scaffold_smiles.encode('utf-8')).hexdigest()[:12]}"
    return {
        "series_id": series_id,
        "name": "Selected compound series" if mode == "selected" else "Scaffold series",
        "scaffold_smiles": scaffold_smiles,
        "positions": positions,
        "endpoint_key": endpoint_key,
        "direction": direction,
        "unit": unit,
        "member_count": len(members),
        "observed_count": len(observed),
        "missing_count": len(members) - len(observed),
        "min_value": min(values) if values else None,
        "max_value": max(values) if values else None,
        "best_member": ranked[0] if ranked else None,
        "reference_compound_id": reference["compound_id"] if reference else None,
        "reference_registration_id": reference["registration_id"] if reference else None,
        "reference_selection": reference_selection,
        "selected_compound_id": selected_compound_id,
        "members": members,
        "membership_source": "analysis_derived",
        "evidence_boundary": "Observed compatible summaries only. Substituent labels are scaffold-relative RDKit fragments; endpoint differences are associations, not causal proof.",
    }


def _similar_group(
    compounds: list[dict[str, Any]],
    selected: dict[str, Any],
    minimum_similarity: float = 0.45,
) -> list[dict[str, Any]]:
    selected_mol = _molecule(selected.get("isomeric_smiles"))
    if selected_mol is None:
        return [selected]
    selected_fp = _MORGAN.GetFingerprint(selected_mol)
    scored: list[tuple[float, dict[str, Any]]] = []
    for compound in compounds:
        molecule = _molecule(compound.get("isomeric_smiles"))
        if molecule is None:
            continue
        score = DataStructs.TanimotoSimilarity(selected_fp, _MORGAN.GetFingerprint(molecule))
        if score >= minimum_similarity:
            scored.append((float(score), compound))
    scored.sort(key=lambda item: (-item[0], _text(item[1].get("registration_id"))))
    return [compound for _score, compound in scored[:12]] or [selected]


def discover_series(
    compounds: Iterable[Mapping[str, Any]],
    summaries: Iterable[Mapping[str, Any]],
    endpoint_key: str,
    *,
    mode: str = "discover",
    selected_compound_id: str | None = None,
    minimum_members: int = 2,
) -> dict[str, Any]:
    endpoint = _text(endpoint_key)
    if not endpoint:
        raise SarDiscoveryError("Choose an endpoint before running SAR analysis.")
    if mode not in {"discover", "selected"}:
        raise SarDiscoveryError("SAR mode must be discover or selected.")
    if mode == "selected" and not selected_compound_id:
        raise SarDiscoveryError("Choose a compound for the selected-compound series view.")
    compound_list = [dict(compound) for compound in compounds]
    by_id = {_text(item.get("compound_id") or item.get("id")): item for item in compound_list}
    if selected_compound_id and selected_compound_id not in by_id:
        raise SarDiscoveryError("The selected compound is not available in this project.")
    latest = _latest_summaries(summaries, endpoint)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for compound in compound_list:
        molecule = _molecule(compound.get("isomeric_smiles"))
        if molecule is None:
            continue
        scaffold_smiles = _scaffold(molecule)
        if scaffold_smiles:
            groups[scaffold_smiles].append(compound)

    if mode == "selected":
        selected = by_id[selected_compound_id or ""]
        selected_scaffold = ""
        selected_molecule = _molecule(selected.get("isomeric_smiles"))
        if selected_molecule is not None:
            selected_scaffold = _scaffold(selected_molecule)
        members = groups.get(selected_scaffold, []) if selected_scaffold else []
        if len(members) < minimum_members:
            members = _similar_group(compound_list, selected)
        groups = {selected_scaffold or "similarity-neighborhood": members}
    else:
        groups = {scaffold: members for scaffold, members in groups.items() if len(members) >= minimum_members}

    series: list[dict[str, Any]] = []
    for scaffold_smiles, group in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        changes = consistent_substituents(
            [_molecule(compound.get("isomeric_smiles")) for compound in group],
            _molecule(scaffold_smiles),
        )
        members = [
            _member(
                compound,
                latest.get(_text(compound.get("compound_id") or compound.get("id"))),
                scaffold_smiles,
                change,
            )
            for compound, change in zip(group, changes)
        ]
        series.append(
            _series_payload(
                scaffold_smiles,
                sorted(
                    members,
                    key=lambda item: (
                        0 if mode == "selected" and item["compound_id"] == selected_compound_id else 1,
                        item["registration_id"],
                    ),
                ),
                endpoint,
                selected_compound_id=selected_compound_id,
                mode=mode,
            )
        )

    in_series = {member["compound_id"] for item in series for member in item["members"]}
    unassigned = sorted(
        _text(compound.get("registration_id"))
        for compound in compound_list
        if _text(compound.get("compound_id") or compound.get("id")) not in in_series
    )
    return {
        "endpoint_key": endpoint,
        "mode": mode,
        "unassigned_compounds": unassigned if mode == "discover" else [],
        "selected_compound_id": selected_compound_id,
        "series": series,
        "series_count": len(series),
        "data_origin": "derived",
        "algorithm_version": "murcko-scaffold-morgan-neighborhood-v1",
    }


def replace_substituent(
    molecule: Chem.Mol,
    change: Mapping[str, Any],
    scaffold_atom: int,
    replacement: str,
) -> str:
    """Swap the substituent at one scaffold site and return the new canonical SMILES.

    ``change`` is this molecule's entry from ``consistent_substituents`` (it
    carries the chosen scaffold match and each fragment's atoms), so the site
    means the same thing as the R-label shown in the tables. ``replacement`` is
    a SMILES with one ``*`` attachment atom (``*F``, ``*OC``), or ``"H"``/``""``
    to remove the substituent. This proposes a structure for review; it is not
    a synthesis or property claim.
    """
    match = list(change.get("match") or [])
    if not match or scaffold_atom < 0 or scaffold_atom >= len(match):
        raise SarDiscoveryError("This compound could not be mapped onto the shared core.")
    anchor = match[scaffold_atom]
    replacement = _text(replacement)
    new_group = None
    if replacement and replacement.upper() != "H":
        new_group = Chem.MolFromSmiles(replacement)
        dummies = [atom.GetIdx() for atom in new_group.GetAtoms() if atom.GetAtomicNum() == 0] if new_group else []
        if new_group is None or len(dummies) != 1:
            raise SarDiscoveryError("The replacement must be a SMILES with exactly one * attachment point, e.g. *F or *OC.")

    editable = Chem.RWMol(molecule)
    removed = [
        fragment
        for fragment in change.get("fragments", [])
        if scaffold_atom in (fragment.get("scaffold_atoms") or [])
    ]
    if any(len(fragment.get("scaffold_atoms") or []) > 1 for fragment in removed):
        raise SarDiscoveryError("This site is part of a ring or bridge that joins several core atoms; edit the SMILES directly.")
    for index in sorted({atom for fragment in removed for atom in fragment.get("molecule_atoms", [])}, reverse=True):
        # RDKit renumbers atoms after a removal, so the anchor index moves down when an earlier atom goes.
        if index < anchor:
            anchor -= 1
        editable.RemoveAtom(index)
    anchor_atom = editable.GetAtomWithIdx(anchor)
    anchor_atom.SetNoImplicit(False)
    anchor_atom.SetNumExplicitHs(0)

    if new_group is not None:
        offset = editable.GetNumAtoms()
        dummy = next(atom for atom in new_group.GetAtoms() if atom.GetAtomicNum() == 0)
        neighbor = dummy.GetNeighbors()[0].GetIdx()
        bond_type = new_group.GetBondBetweenAtoms(dummy.GetIdx(), neighbor).GetBondType()
        combined = Chem.RWMol(Chem.CombineMols(editable.GetMol(), new_group))
        combined.AddBond(anchor, offset + neighbor, bond_type)
        combined.RemoveAtom(offset + dummy.GetIdx())
        editable = combined
    result = editable.GetMol()
    try:
        Chem.SanitizeMol(result)
    except Exception as exc:
        raise SarDiscoveryError(f"That substituent cannot be attached at this site ({exc}).") from exc
    return Chem.MolToSmiles(result)
