from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from rdkit import Chem
from rdkit.Chem import Draw


class StructureValidationError(ValueError):
    def __init__(self, message: str, code: str = "invalid_structure") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StandardizedStructure:
    raw_input: str
    input_format: str
    original_molblock: str
    standardized_molblock: str
    canonical_smiles: str
    isomeric_smiles: str
    inchikey: str | None
    parent_canonical_smiles: str
    parent_inchikey: str | None
    component_count: int
    stereochemistry_status: str
    standardization_profile: str
    warnings: tuple[str, ...]
    rendered_svg: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mol_from_input(raw_input: str, input_format: str) -> Chem.Mol:
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise StructureValidationError("Structure input cannot be empty", "missing_structure")
    if input_format.lower() in {"mol", "molfile", "sdf", "molblock"}:
        molecule = Chem.MolFromMolBlock(raw_input, sanitize=False, removeHs=False)
    else:
        molecule = Chem.MolFromSmiles(raw_input, sanitize=False)
    if molecule is None:
        raise StructureValidationError("RDKit could not parse the structure", "parse_failed")
    try:
        Chem.SanitizeMol(molecule)
    except Exception as exc:  # RDKit exposes several typed sanitization exceptions.
        raise StructureValidationError(
            f"Structure failed RDKit sanitization: {exc}", "sanitize_failed"
        ) from exc
    return molecule


def _render_svg(molecule: Chem.Mol) -> str:
    drawer = Draw.MolDraw2DSVG(320, 180)
    drawer.DrawMolecule(molecule)
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()
    start = svg.find("<svg")
    return svg[start:] if start >= 0 else svg


SCAFFOLD_REGION_COLOURS = {
    "r1": (130 / 255, 201 / 255, 1.0),
    "r2": (201 / 255, 182 / 255, 1.0),
    "r3": (141 / 255, 228 / 255, 187 / 255),
    "amide": (255 / 255, 157 / 255, 157 / 255),
}


def _aromatic_attachment_region(molecule: Chem.Mol, dummy_index: int) -> tuple[set[int], set[int]]:
    """Return a dummy attachment, its aromatic ring, and the ring bonds."""
    dummy = molecule.GetAtomWithIdx(dummy_index)
    region_atoms = {dummy_index}
    region_bonds: set[int] = set()
    pending = [neighbor.GetIdx() for neighbor in dummy.GetNeighbors()]
    while pending:
        atom_index = pending.pop()
        if atom_index in region_atoms:
            continue
        atom = molecule.GetAtomWithIdx(atom_index)
        if not atom.GetIsAromatic():
            continue
        region_atoms.add(atom_index)
        for bond in atom.GetBonds():
            other_index = bond.GetOtherAtomIdx(atom_index)
            if other_index == dummy_index:
                region_bonds.add(bond.GetIdx())
            elif molecule.GetAtomWithIdx(other_index).GetIsAromatic():
                region_bonds.add(bond.GetIdx())
                if other_index not in region_atoms:
                    pending.append(other_index)
    return region_atoms, region_bonds


def _bond_between(molecule: Chem.Mol, first_index: int, second_index: int) -> int | None:
    bond = molecule.GetBondBetweenAtoms(first_index, second_index)
    return bond.GetIdx() if bond is not None else None


def render_scaffold_svg(scaffold_smiles: str, *, width: int = 760, height: int = 360) -> str:
    """Render a standard RDKit scaffold with explicit R labels and highlights.

    The declared dummy maps identify R1 (map 1) and R3 (map 2 or 3). R2 is the
    displayed linker/spacing region, while the amide remains a named core
    connector. Colours are presentation-only and do not create experimental or
    derived records.
    """
    molecule = Chem.MolFromSmiles(str(scaffold_smiles))
    if molecule is None:
        raise StructureValidationError("RDKit could not parse the scaffold", "scaffold_parse_failed")

    regions: dict[str, tuple[set[int], set[int]]] = {}
    dummy_by_map = {
        atom.GetAtomMapNum(): atom.GetIdx()
        for atom in molecule.GetAtoms()
        if atom.GetAtomicNum() == 0 and atom.GetAtomMapNum() in {1, 2, 3}
    }
    if 1 in dummy_by_map:
        regions["r1"] = _aromatic_attachment_region(molecule, dummy_by_map[1])
    heteroaryl_map = 3 if 3 in dummy_by_map else 2
    if heteroaryl_map in dummy_by_map:
        regions["r3"] = _aromatic_attachment_region(molecule, dummy_by_map[heteroaryl_map])

    carbonyl = next(
        (
            atom
            for atom in molecule.GetAtoms()
            if atom.GetSymbol() == "C"
            and any(
                bond.GetBondType() == Chem.BondType.DOUBLE
                and bond.GetOtherAtom(atom).GetSymbol() == "O"
                for bond in atom.GetBonds()
            )
        ),
        None,
    )
    if carbonyl is not None:
        carbonyl_index = carbonyl.GetIdx()
        linker = next(
            (
                neighbor
                for neighbor in carbonyl.GetNeighbors()
                if neighbor.GetSymbol() == "C" and not neighbor.GetIsAromatic()
            ),
            None,
        )
        amide_nitrogen = next(
            (neighbor for neighbor in carbonyl.GetNeighbors() if neighbor.GetSymbol() == "N"),
            None,
        )
        amide_atoms = {carbonyl_index}
        if amide_nitrogen is not None:
            amide_atoms.add(amide_nitrogen.GetIdx())
        for bond in carbonyl.GetBonds():
            other_index = bond.GetOtherAtomIdx(carbonyl_index)
            if molecule.GetAtomWithIdx(other_index).GetSymbol() == "O":
                amide_atoms.add(other_index)
        amide_bonds = {
            bond.GetIdx()
            for bond in molecule.GetBonds()
            if bond.GetBeginAtomIdx() in amide_atoms and bond.GetEndAtomIdx() in amide_atoms
        }
        regions["amide"] = (amide_atoms, amide_bonds)
        if linker is not None:
            linker_index = linker.GetIdx()
            linker_bonds = {
                bond_index
                for bond_index in (
                    _bond_between(molecule, carbonyl_index, linker_index),
                    *(
                        _bond_between(molecule, linker_index, neighbor.GetIdx())
                        for neighbor in linker.GetNeighbors()
                        if neighbor.GetIsAromatic()
                    ),
                )
                if bond_index is not None
            }
            regions["r2"] = ({linker_index}, linker_bonds)

    atom_colors: dict[int, tuple[float, float, float]] = {}
    bond_colors: dict[int, tuple[float, float, float]] = {}
    highlight_atoms: set[int] = set()
    highlight_bonds: set[int] = set()
    for region_name, (atom_indices, bond_indices) in regions.items():
        colour = SCAFFOLD_REGION_COLOURS[region_name]
        highlight_atoms.update(atom_indices)
        highlight_bonds.update(bond_indices)
        atom_colors.update({index: colour for index in atom_indices})
        bond_colors.update({index: colour for index in bond_indices})

    drawer = Draw.MolDraw2DSVG(width, height)
    options = drawer.drawOptions()
    options.highlightBondWidthMultiplier = 6
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() != 0:
            continue
        label = {1: "R1", 2: "R3", 3: "R3"}.get(atom.GetAtomMapNum())
        if label:
            options.atomLabels[atom.GetIdx()] = label
        atom.SetAtomMapNum(0)
    linker_index: int | None = None
    if "r2" in regions:
        linker_index = next(iter(regions["r2"][0]))
    drawer.DrawMolecule(
        molecule,
        highlightAtoms=sorted(highlight_atoms),
        highlightBonds=sorted(highlight_bonds),
        highlightAtomColors=atom_colors,
        highlightBondColors=bond_colors,
    )
    linker_point = drawer.GetDrawCoords(linker_index) if linker_index is not None else None
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()
    start = svg.find("<svg")
    if start >= 0:
        svg = svg[start:]
    if linker_point is not None:
        inline_r2 = (
            f'<text x="{linker_point.x:.1f}" y="{linker_point.y + 30:.1f}" '
            'text-anchor="middle" aria-label="R2 linker position" '
            'font-family="sans-serif" font-size="24" font-weight="600" fill="#C9B6FF">R2</text>'
        )
        svg = svg.replace("</svg>", f"{inline_r2}</svg>", 1)
    return svg.replace(
        "<svg",
        '<svg data-scaffold-positions="R1,R2,R3" data-scaffold-highlighted="true"',
        1,
    )


def standardize_structure(
    raw_input: str,
    input_format: str = "smiles",
    profile: str = "rdkit-preserve-stereo-v1",
) -> StandardizedStructure:
    molecule = _mol_from_input(raw_input, input_format)
    components = list(Chem.GetMolFrags(molecule, asMols=True, sanitizeFrags=True))
    parent = max(components, key=lambda item: item.GetNumHeavyAtoms())
    warnings: list[str] = []
    if len(components) > 1:
        warnings.append("disconnected_components_preserved; parent identity is reported separately")

    chiral_centers = Chem.FindMolChiralCenters(molecule, includeUnassigned=True)
    if any(code == "?" for _, code in chiral_centers):
        stereo_status = "undefined_stereochemistry"
        warnings.append("one_or_more_chiral_centers_are_unassigned")
    elif chiral_centers:
        stereo_status = "defined_stereochemistry"
    else:
        stereo_status = "no_chiral_centers_detected"

    try:
        inchikey = Chem.MolToInchiKey(molecule)
    except Exception:
        inchikey = None
        warnings.append("inchikey_unavailable")
    try:
        parent_inchikey = Chem.MolToInchiKey(parent)
    except Exception:
        parent_inchikey = None
        warnings.append("parent_inchikey_unavailable")

    return StandardizedStructure(
        raw_input=raw_input,
        input_format=input_format,
        original_molblock=Chem.MolToMolBlock(molecule),
        standardized_molblock=Chem.MolToMolBlock(molecule),
        canonical_smiles=Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=False),
        isomeric_smiles=Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True),
        inchikey=inchikey,
        parent_canonical_smiles=Chem.MolToSmiles(parent, canonical=True, isomericSmiles=True),
        parent_inchikey=parent_inchikey,
        component_count=len(components),
        stereochemistry_status=stereo_status,
        standardization_profile=profile,
        warnings=tuple(warnings),
        rendered_svg=_render_svg(molecule),
    )
