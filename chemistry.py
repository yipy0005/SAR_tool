from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from rdkit import Chem, rdBase
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


def _strip_svg_prolog(svg: str) -> str:
    start = svg.find("<svg")
    return svg[start:] if start >= 0 else svg


def render_scaffold_positions_svg(
    scaffold_smiles: str,
    positions: list[tuple[int, str]],
    *,
    width: int = 360,
    height: int = 200,
) -> str:
    """Draw a Murcko scaffold with an R-label on each observed attachment atom.

    ``positions`` pairs scaffold atom indices (from ``Chem.MolFromSmiles(scaffold_smiles)``)
    with labels such as ``"R1"``. Labels are presentation-only.
    """
    with rdBase.BlockLogs():
        scaffold = Chem.MolFromSmiles(scaffold_smiles)
        query_core = scaffold is None
        if query_core:
            # A declared or drawn core is SMARTS (for example [#6]1:[#6]...); draw the query itself.
            scaffold = Chem.MolFromSmarts(scaffold_smiles)
    if scaffold is None:
        raise StructureValidationError("Scaffold could not be parsed", "parse_failed")
    if query_core:
        scaffold.UpdatePropertyCache(strict=False)
        Chem.FastFindRings(scaffold)
    editable = Chem.RWMol(scaffold)
    labels: dict[int, str] = {}
    for atom_index, label in positions:
        if atom_index < 0 or atom_index >= scaffold.GetNumAtoms():
            continue
        dummy_index = editable.AddAtom(Chem.Atom(0))
        editable.AddBond(atom_index, dummy_index, Chem.BondType.SINGLE)
        labels[dummy_index] = label
    molecule = editable.GetMol()
    if query_core:
        molecule.UpdatePropertyCache(strict=False)
        Chem.FastFindRings(molecule)
    else:
        try:
            Chem.SanitizeMol(molecule)
        except Exception:  # Fall back to the bare scaffold when a label cannot be attached.
            molecule = scaffold
            labels = {}
    drawer = Draw.MolDraw2DSVG(width, height)
    options = drawer.drawOptions()
    for atom_index, label in labels.items():
        options.atomLabels[atom_index] = label
    drawer.DrawMolecule(molecule)
    drawer.FinishDrawing()
    return _strip_svg_prolog(drawer.GetDrawingText()).replace(
        "<svg", f'<svg role="img" aria-label="Shared scaffold with positions {", ".join(label for _i, label in positions)}"', 1
    )


def render_fragment_svg(attached_smiles: str, label: str = "R", *, width: int = 130, height: int = 80) -> str:
    """Small drawing of a substituent; the dummy atom is labelled with its position."""
    molecule = Chem.MolFromSmiles(attached_smiles)
    if molecule is None:
        return ""
    drawer = Draw.MolDraw2DSVG(width, height)
    options = drawer.drawOptions()
    options.padding = 0.12
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() == 0:
            options.atomLabels[atom.GetIdx()] = label
    drawer.DrawMolecule(molecule)
    drawer.FinishDrawing()
    return _strip_svg_prolog(drawer.GetDrawingText())


def _draw_reference_positions(
    reference_smiles: str,
    scaffold_smarts: str,
    positions: list[tuple[int, str]],
    width: int,
    height: int,
) -> tuple[str, dict[int, tuple[float, float]], dict[int, str]]:
    """Draw the reference with its R-site atoms highlighted.

    Returns the SVG, the drawing coordinates of each labelled atom, and the
    label for each atom. Shared by the static R-site map and the interactive
    explorer so both put a site on exactly the same atom.
    """
    molecule = Chem.MolFromSmiles(str(reference_smiles))
    scaffold = Chem.MolFromSmarts(str(scaffold_smarts))
    if molecule is None or scaffold is None:
        raise StructureValidationError("Reference or scaffold could not be parsed", "parse_failed")
    match = molecule.GetSubstructMatch(scaffold)
    if not match:
        raise StructureValidationError("Reference does not contain the declared scaffold", "scaffold_not_found")
    labels: dict[int, str] = {}
    highlight_atoms: list[int] = []
    for scaffold_index, label in positions:
        if 0 <= scaffold_index < len(match):
            molecule_index = match[scaffold_index]
            labels[molecule_index] = label
            highlight_atoms.append(molecule_index)
    drawer = Draw.MolDraw2DSVG(width, height)
    options = drawer.drawOptions()
    options.addStereoAnnotation = True
    highlight_colors = {index: (0.32, 0.60, 0.78) for index in highlight_atoms}
    drawer.DrawMolecule(
        molecule,
        highlightAtoms=sorted(set(highlight_atoms)),
        highlightAtomColors=highlight_colors,
    )
    points = {}
    for atom_index in labels:
        point = drawer.GetDrawCoords(atom_index)
        points[atom_index] = (float(point.x), float(point.y))
    drawer.FinishDrawing()
    return _strip_svg_prolog(drawer.GetDrawingText()), points, labels


def render_reference_positions_svg(
    reference_smiles: str,
    scaffold_smarts: str,
    positions: list[tuple[int, str]],
    *,
    width: int = 760,
    height: int = 300,
) -> str:
    """Render the full reference molecule and label scaffold attachment atoms.

    Unlike a bare scaffold drawing, this keeps the actual reference structure
    visible and places R labels on the matched scaffold atoms. It accepts
    SMARTS (including query atoms from an MCS) and never invents a substituent
    structure. Labels are presentation-only.
    """
    svg, points, labels = _draw_reference_positions(reference_smiles, scaffold_smarts, positions, width, height)
    labels_text = ", ".join(label for _index, label in positions)
    callouts = "".join(
        f'<text x="{x:.1f}" y="{y - 18:.1f}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="18" font-weight="700" '
        f'fill="#82c9ff" stroke="#071016" stroke-width="5" paint-order="stroke">{labels[atom_index]}</text>'
        for atom_index, (x, y) in points.items()
    )
    svg = svg.replace("</svg>", f"{callouts}</svg>", 1)
    return svg.replace(
        "<svg",
        f'<svg role="img" aria-label="Reference structure with R positions {labels_text}"',
        1,
    )


MATCH_HIGHLIGHT_COLOUR = (1.0, 0.71, 0.45)


def render_match_svg(
    molecule: Chem.Mol,
    match_atoms: list[int] | tuple[int, ...],
    query: Chem.Mol | None = None,
    *,
    width: int = 260,
    height: int = 150,
) -> str:
    """Draw a compound with the atoms and bonds of one substructure match highlighted.

    Bonds are highlighted only when both ends belong to the match and the
    query has a bond between the corresponding query atoms, so a ring closure
    outside the drawn query is not shown as matched.
    """
    atoms = [int(index) for index in match_atoms]
    bonds: list[int] = []
    if query is not None and len(atoms) == query.GetNumAtoms():
        for bond in query.GetBonds():
            target = molecule.GetBondBetweenAtoms(atoms[bond.GetBeginAtomIdx()], atoms[bond.GetEndAtomIdx()])
            if target is not None:
                bonds.append(target.GetIdx())
    drawer = Draw.MolDraw2DSVG(width, height)
    drawer.DrawMolecule(
        molecule,
        highlightAtoms=atoms,
        highlightBonds=bonds,
        highlightAtomColors={index: MATCH_HIGHLIGHT_COLOUR for index in atoms},
        highlightBondColors={index: MATCH_HIGHLIGHT_COLOUR for index in bonds},
    )
    drawer.FinishDrawing()
    return _strip_svg_prolog(drawer.GetDrawingText())


def reference_site_map(
    reference_smiles: str,
    scaffold_smarts: str,
    positions: list[tuple[int, str]],
    *,
    width: int = 520,
    height: int = 260,
) -> dict[str, Any]:
    """Reference drawing plus the fractional position of each R-site atom.

    The interactive explorer overlays one keyboard-focusable button per site at
    ``(x, y)`` (0–1 of the drawing's width and height), so the SVG itself has
    no text callouts. Coordinates come from RDKit's own layout.
    """
    svg, points, labels = _draw_reference_positions(reference_smiles, scaffold_smarts, positions, width, height)
    labels_text = ", ".join(label for _index, label in positions)
    svg = svg.replace(
        "<svg",
        f'<svg role="img" aria-label="Reference structure with R-sites {labels_text}"',
        1,
    )
    sites = [
        {"label": labels[atom_index], "x": round(x / width, 4), "y": round(y / height, 4)}
        for atom_index, (x, y) in points.items()
    ]
    sites.sort(key=lambda site: (len(site["label"]), site["label"]))
    return {"svg": svg, "width": width, "height": height, "sites": sites}
