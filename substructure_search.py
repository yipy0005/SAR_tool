"""Substructure search over a project's standardized structures.

Queries come from the built-in sketcher (an MDL V2000 molfile) or from typed
SMILES / SMARTS. RDKit turns them into a query molecule and every compound in
the project is matched against it. Read-only: nothing is persisted and results
are labelled as derived.

Drawn queries follow common sketcher conventions:
- a drawn Kekulé ring is aromatized when RDKit can, so it matches aromatic rings;
- a drawn atom constrains the element (and the charge when one is drawn), not its H count;
- an explicitly drawn H requires at least one H on that atom;
- A = any heavy atom, Q = any heteroatom, * = any atom or attachment point;
- the "any" bond matches every bond order. Stereochemistry is not compared.
"""
from __future__ import annotations

import re
import time
from typing import Any

from rdkit import Chem, rdBase
from rdkit.Chem import rdDepictor
from rdkit.Chem.Scaffolds import MurckoScaffold

from chemistry import render_match_svg
from database import read_connection

SEARCH_VERSION = "rdkit-substructure-v1"
MAX_MOLBLOCK_CHARS = 60_000
MAX_TEXT_CHARS = 500
MAX_QUERY_ATOMS = 80
DEFAULT_LIMIT = 60
MAX_LIMIT = 200
TIME_BUDGET_SECONDS = 8.0
# Text that can only be SMARTS: logical operators, atom lists, or [#n] atomic numbers.
_SMARTS_ONLY = re.compile(r"[~!&;$]|\[#\d|\[[^\]]*,[^\]]*\]")


class SubstructureQueryError(ValueError):
    """Raised when a drawn or typed query cannot be used."""

    def __init__(self, message: str, code: str = "invalid_query") -> None:
        super().__init__(message)
        self.code = code


def _parse(parser: Any, text: str, **options: Any) -> Chem.Mol | None:
    """RDKit parse that treats exceptions like a failed parse (None)."""
    try:
        return parser(text, **options)
    except Exception:  # RDKit raises on some malformed molfiles instead of returning None.
        return None


def _prepare(molecule: Chem.Mol) -> Chem.Mol:
    """Aromatize where possible, make dummies match anything, and fold drawn H atoms into H-count queries."""
    candidate = Chem.Mol(molecule)
    try:
        Chem.SanitizeMol(candidate)
    except Exception:  # Partial aromatic fragments and query atoms (A, Q) cannot always be kekulized.
        candidate = Chem.Mol(molecule)
        candidate.UpdatePropertyCache(strict=False)
        Chem.SanitizeMol(candidate, Chem.SanitizeFlags.SANITIZE_SYMMRINGS, catchErrors=True)
    params = Chem.AdjustQueryParameters.NoAdjustments()
    params.makeDummiesQueries = True
    params.aromatizeIfPossible = True
    return Chem.MergeQueryHs(Chem.AdjustQueryProperties(candidate, params))


def build_query(*, molblock: Any = None, text: Any = None, text_format: Any = "auto") -> dict[str, Any]:
    """Query molecule plus its SMARTS from a sketcher molfile, or from typed SMILES / SMARTS."""
    block = str(molblock or "")
    typed = str(text or "").strip()
    with rdBase.BlockLogs():
        if block.strip():
            if len(block) > MAX_MOLBLOCK_CHARS:
                raise SubstructureQueryError("The drawing is too large to search.", "query_too_large")
            molecule = _parse(Chem.MolFromMolBlock, block, sanitize=False, removeHs=False)
            if molecule is None:
                raise SubstructureQueryError("The drawing could not be read as a structure.", "invalid_drawing")
            source = "sketch"
        elif typed:
            if len(typed) > MAX_TEXT_CHARS:
                raise SubstructureQueryError(f"Typed queries must be {MAX_TEXT_CHARS} characters or fewer.", "query_too_large")
            chosen = str(text_format or "auto").strip().lower()
            if chosen not in {"auto", "smiles", "smarts"}:
                raise SubstructureQueryError("query_format must be auto, smiles or smarts.", "invalid_query_format")
            molecule = None
            source = "smarts"
            if chosen == "smiles" or (chosen == "auto" and not _SMARTS_ONLY.search(typed)):
                molecule = _parse(Chem.MolFromSmiles, typed)
                source = "smiles"
            if molecule is None and chosen != "smiles":
                molecule = _parse(Chem.MolFromSmarts, typed)
                source = "smarts"
            if molecule is None:
                kind = "SMILES" if chosen == "smiles" else "SMARTS" if chosen == "smarts" else "SMILES or SMARTS"
                raise SubstructureQueryError(f"That text is not valid {kind}.", "invalid_text_query")
        else:
            raise SubstructureQueryError("Draw a substructure or type SMILES / SMARTS first.", "empty_query")
        if molecule.GetNumAtoms() == 0:
            raise SubstructureQueryError("Draw a substructure or type SMILES / SMARTS first.", "empty_query")
        if molecule.GetNumAtoms() > MAX_QUERY_ATOMS:
            raise SubstructureQueryError(f"Queries are limited to {MAX_QUERY_ATOMS} atoms; draw the part you want to find.", "query_too_large")
        if source == "smarts":
            query = molecule
            query.UpdatePropertyCache(strict=False)
            Chem.FastFindRings(query)
        else:
            query = _prepare(molecule)
        smarts = Chem.MolToSmarts(query)
        core = core_from_query(query)
    return {
        "mol": query,
        "smarts": smarts,
        "source": source,
        "atom_count": query.GetNumAtoms(),
        "bond_count": query.GetNumBonds(),
        "core_smarts": core["smarts"],
        "core_attachment_points": core["attachment_points"],
        "core_issue": core["issue"],
    }


def _is_attachment_point(atom: Chem.Atom) -> bool:
    """A terminal *, R# or [#0] marks where a substituent goes; A and Q atoms are real query atoms."""
    if atom.GetAtomicNum() != 0 or atom.GetDegree() > 1:
        return False
    if not atom.HasQuery():
        return True
    description = atom.DescribeQuery().strip()
    return description.startswith("AtomNull") or description == "AtomAtomicNum 0 = val"


def core_from_query(query: Chem.Mol) -> dict[str, Any]:
    """SMARTS usable as a declared R-group core: the query with its attachment points removed.

    The R-group engines put a substituent wherever a compound has an atom
    outside the matched core, so attachment points are implied and a drawn *
    or R# would otherwise swallow the first substituent atom.
    """
    removed = [atom.GetIdx() for atom in query.GetAtoms() if _is_attachment_point(atom)]
    editable = Chem.RWMol(query)
    for index in sorted(removed, reverse=True):
        editable.RemoveAtom(index)
    core = editable.GetMol()
    result: dict[str, Any] = {"smarts": None, "attachment_points": len(removed), "issue": None}
    if core.GetNumAtoms() < 2:
        result["issue"] = "A core needs at least two atoms besides attachment points."
    elif len(Chem.GetMolFrags(core)) > 1:
        result["issue"] = "The core must be one connected piece; draw a single scaffold."
    else:
        smarts = Chem.MolToSmarts(core)
        if Chem.MolFromSmarts(smarts) is None:
            result["issue"] = "This drawing cannot be written as a core SMARTS."
        else:
            result["smarts"] = smarts
    return result


def _limit(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_LIMIT
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SubstructureQueryError("limit must be an integer.", "invalid_limit") from exc
    if not 1 <= parsed <= MAX_LIMIT:
        raise SubstructureQueryError(f"limit must be between 1 and {MAX_LIMIT}.", "invalid_limit")
    return parsed


def search_project(
    database_path: str,
    project_id: str,
    query: dict[str, Any],
    *,
    limit: Any = None,
    time_budget: float = TIME_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Match every standardized structure in the project against the query.

    All matches are listed; the first ``limit`` also carry a drawing with the
    matched atoms highlighted. Scanning stops at ``time_budget`` seconds and
    the response then says the search is incomplete instead of guessing.
    """
    result_limit = _limit(limit)
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.registration_id, c.preferred_name, s.isomeric_smiles
            FROM compounds c
            JOIN structure_records s ON s.compound_id = c.id
            WHERE c.project_id = ?
            ORDER BY c.registration_id, c.id
            """,
            (project_id,),
        ).fetchall()
    pattern = query["mol"]
    started = time.monotonic()
    scanned = 0
    unreadable = 0
    complete = True
    results: list[dict[str, Any]] = []
    with rdBase.BlockLogs():
        for row in rows:
            if time.monotonic() - started > time_budget:
                complete = False
                break
            scanned += 1
            molecule = _parse(Chem.MolFromSmiles, str(row["isomeric_smiles"] or ""))
            if molecule is None:
                unreadable += 1
                continue
            match = molecule.GetSubstructMatch(pattern)
            if not match:
                continue
            item = {
                "compound_id": row["id"],
                "registration_id": row["registration_id"],
                "preferred_name": row["preferred_name"] or "",
                "match_count": len(molecule.GetSubstructMatches(pattern, uniquify=True, maxMatches=50)),
            }
            if len(results) < result_limit:
                item["match_atoms"] = list(match)
                item["svg"] = render_match_svg(molecule, match, pattern)
            results.append(item)
    return {
        "query": {key: value for key, value in query.items() if key != "mol"},
        "results": results,
        "matched_ids": [item["compound_id"] for item in results],
        "counts": {"total": len(rows), "scanned": scanned, "matched": len(results), "unreadable": unreadable, "drawn": min(len(results), result_limit)},
        "complete": complete,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "search_version": SEARCH_VERSION,
        "stereo_compared": False,
        "data_origin": "derived",
    }


def compound_structure(database_path: str, project_id: str, compound_id: str) -> dict[str, Any] | None:
    """Registration ID and saved structure of one compound in the project, or None."""
    with read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT c.id, c.registration_id, s.isomeric_smiles
            FROM compounds c JOIN structure_records s ON s.compound_id = c.id
            WHERE c.project_id = ? AND c.id = ?
            """,
            (project_id, compound_id),
        ).fetchone()
    return dict(row) if row else None


def sketch_from_smiles(smiles: Any, part: Any = "molecule") -> dict[str, Any]:
    """2-D Kekulé molfile for loading a structure (or its Murcko ring scaffold) into the sketcher.

    Stereo is dropped because substructure search does not compare it, and the
    sketcher would otherwise show wedges it cannot edit.
    """
    text = str(smiles or "").strip()
    chosen = str(part or "molecule").strip().lower()
    if chosen not in {"molecule", "scaffold"}:
        raise SubstructureQueryError("part must be molecule or scaffold.", "invalid_part")
    if not text:
        raise SubstructureQueryError("Type a SMILES string or choose a compound to draw.", "empty_query")
    if len(text) > MAX_TEXT_CHARS:
        raise SubstructureQueryError(f"SMILES must be {MAX_TEXT_CHARS} characters or fewer.", "query_too_large")
    with rdBase.BlockLogs():
        molecule = _parse(Chem.MolFromSmiles, text)
    if molecule is None:
        raise SubstructureQueryError("That SMILES string could not be read.", "invalid_smiles")
    if chosen == "scaffold":
        molecule = MurckoScaffold.GetScaffoldForMol(molecule)
        if molecule is None or molecule.GetNumAtoms() == 0:
            raise SubstructureQueryError("This structure has no ring scaffold.", "no_scaffold")
    if molecule.GetNumAtoms() > MAX_QUERY_ATOMS:
        raise SubstructureQueryError(f"Structures over {MAX_QUERY_ATOMS} atoms are too large to edit as a query.", "query_too_large")
    drawable = Chem.Mol(molecule)
    Chem.Kekulize(drawable, clearAromaticFlags=True)
    rdDepictor.Compute2DCoords(drawable)
    return {
        "molblock": Chem.MolToMolBlock(drawable, kekulize=False, includeStereo=False),
        "smiles": Chem.MolToSmiles(molecule),
        "part": chosen,
        "data_origin": "derived",
    }
