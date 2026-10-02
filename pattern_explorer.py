"""Read model for the interactive Find patterns explorer.

Presentation-only. It reshapes data the analysis page already loads (the latest
saved summary per compound and endpoint, the latest pharmacophore R-group run,
saved property profiles, and saved MMP / activity-cliff records) into one
compact JSON document for ``static/production_patterns.js``.

Nothing here is persisted and no measured value is changed; the browser rounds
for display only. Structural similarity is recalculated on page load with a
standard fingerprint so every compound pair can be placed on the activity
landscape. It is labelled as such, and the saved MCS overlap is shown beside it
whenever an MMP or activity-cliff record exists for the same pair.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable, Mapping, MutableMapping

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

from display import feature_label, is_log_unit, symbol_id
from property_engine import ANALYSIS_VERSION as PROPERTY_VERSION, PropertyAnalysisError, calculate_properties

EXPLORER_VERSION = "pattern-explorer-v1"
SIMILARITY_METHOD = "Morgan fingerprint (radius 2, 2048 bits), Tanimoto"
ALL_PAIRS_LIMIT = 2000        # keep every pair while n(n-1)/2 stays at or below this
NEIGHBOURS_PER_COMPOUND = 12  # otherwise keep each compound's nearest neighbours
MIN_KEPT_SIMILARITY = 0.2
MAX_PAIR_COMPOUNDS = 3000     # fingerprint comparison is skipped beyond this many structures
SHOWN_CLIFF_ROWS = 12         # production_analysis.html lists the first 12 cliffs per endpoint

# Short JSON keys for the browser, mapped to property_engine descriptor names.
PROPERTY_KEYS: tuple[tuple[str, str], ...] = (
    ("mw", "molecular_weight"),
    ("clogp", "logp"),
    ("tpsa", "tpsa"),
    ("hbd", "hbd"),
    ("hba", "hba"),
    ("rb", "rotatable_bonds"),
    ("rings", "ring_count"),
    ("hac", "heavy_atom_count"),
    ("fsp3", "fraction_csp3"),
)


def _endpoint_scale(unit: Any, exact_values: list[float]) -> str:
    """How differences are read: log units subtract; positive linear values compare as fold change."""
    if is_log_unit(unit):
        return "log"
    if exact_values and all(value > 0 for value in exact_values):
        return "fold"
    return "linear"


def _is_potency(unit: Any, direction: str) -> bool:
    """LipE and LE are defined for a p-scaled potency (pIC50, pKi, ...) where higher is better."""
    text = str(unit or "").strip()
    return direction == "higher" and len(text) > 1 and text[0] == "p" and text[1].isupper()


def _finite(value: Any) -> float | None:
    """Float, or None for missing / non-finite values (JSON has no NaN)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cell_payload(cell: Mapping[str, Any]) -> dict[str, Any] | None:
    state = cell.get("state")
    exact = _finite(cell.get("value"))
    bound = _finite(cell.get("sort_value"))
    if state == "exact" and exact is not None:
        return {"v": exact, "s": "e", "d": cell.get("display"), "sid": cell.get("summary_id"), "n": cell.get("n")}
    if state == "censored" and bound is not None:
        return {
            "v": bound,
            "s": "c",
            "q": cell.get("qualifier") or "",
            "d": cell.get("display"),
            "sid": cell.get("summary_id"),
            "n": cell.get("n"),
        }
    return None


def _core_key(smiles: Any, match_atoms: Iterable[int]) -> str | None:
    """Position-aware identity of the matched core: element, aromaticity and charge per scaffold atom.

    Two compounds share a key only when every core atom is the same element in
    the same position, so a ring-nitrogen move (for example a 2- to 3-pyridyl
    change) counts as a structural change even though the R-groups match.
    """
    molecule = Chem.MolFromSmiles(str(smiles or ""))
    match = list(match_atoms or [])
    if molecule is None or not match:
        return None
    parts = []
    for atom_index in match:
        if not 0 <= int(atom_index) < molecule.GetNumAtoms():
            return None
        atom = molecule.GetAtomWithIdx(int(atom_index))
        parts.append(f"{atom.GetSymbol()}{'a' if atom.GetIsAromatic() else ''}{atom.GetFormalCharge():+d}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


def _core_note(assignment: Mapping[str, Any]) -> str:
    relocated = (assignment.get("core_feature_delta") or {}).get("relocated") or []
    families = []
    for change in relocated:
        label = feature_label(change.get("family"))
        if label not in families:
            families.append(label)
    return "; ".join(f"{label} relocated within core" for label in families)


def _site_payload(site: Mapping[str, Any], fragment_svgs: MutableMapping[str, str]) -> dict[str, Any]:
    substituted = site.get("fragment_status") == "substituted"
    profile = site.get("structural_profile") or {}
    features = []
    for feature in site.get("features") or []:
        label = feature_label(feature.get("family"))
        if feature.get("family") and label not in features:
            features.append(label)
    fragment_id = None
    svg = str(site.get("fragment_svg") or "")
    if substituted and svg:
        fragment_id = "frag-" + hashlib.sha1(svg.encode("utf-8")).hexdigest()[:12]
        fragment_svgs.setdefault(fragment_id, svg)
    return {
        "name": (site.get("substituent_name") or site.get("fragment_smiles") or "R") if substituted else "H",
        "smiles": str(site.get("attached_smiles") or "") if substituted else "",
        "frag": fragment_id,
        "features": sorted(features),
        "rb": int(profile.get("rotatable_bonds") or 0),
        "rings": int(profile.get("ring_count") or 0),
        "ring_status": str(profile.get("ring_status") or ("acyclic" if substituted else "unsubstituted")),
        "heavy": int(profile.get("heavy_atom_count") or 0),
    }


def _similarity_pairs(smiles_list: list[str]) -> tuple[list[list[float]], str]:
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    fingerprints = []
    for smiles in smiles_list[:MAX_PAIR_COMPOUNDS]:
        molecule = Chem.MolFromSmiles(smiles) if smiles else None
        fingerprints.append(generator.GetFingerprint(molecule) if molecule is not None else None)
    valid = [index for index, fingerprint in enumerate(fingerprints) if fingerprint is not None]
    keep_all = len(valid) * (len(valid) - 1) // 2 <= ALL_PAIRS_LIMIT
    kept: dict[tuple[int, int], float] = {}
    for position, first in enumerate(valid):
        others = valid[position + 1:] if keep_all else [index for index in valid if index != first]
        if not others:
            continue
        similarities = DataStructs.BulkTanimotoSimilarity(fingerprints[first], [fingerprints[index] for index in others])
        if keep_all:
            for second, similarity in zip(others, similarities):
                kept[(first, second)] = similarity
            continue
        nearest = sorted(zip(others, similarities), key=lambda item: -item[1])[:NEIGHBOURS_PER_COMPOUND]
        for second, similarity in nearest:
            if similarity >= MIN_KEPT_SIMILARITY:
                kept[(min(first, second), max(first, second))] = similarity
    policy = "all pairs" if keep_all else f"{NEIGHBOURS_PER_COMPOUND} nearest neighbours per compound (Tanimoto ≥ {MIN_KEPT_SIMILARITY})"
    if len(smiles_list) > MAX_PAIR_COMPOUNDS:
        policy += f"; first {MAX_PAIR_COMPOUNDS} structures only"
    return [[first, second, round(similarity, 3)] for (first, second), similarity in sorted(kept.items())], policy


def _properties(
    rows: list[Mapping[str, Any]],
    property_profiles: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, float] | None], dict[str, Any]]:
    saved = {profile.get("compound_id"): profile.get("descriptors") or {} for profile in property_profiles}
    result: list[dict[str, float] | None] = []
    counts = {"saved": 0, "calculated": 0, "unavailable": 0}
    for row in rows:
        descriptors = saved.get(row.get("compound_id"))
        if descriptors:
            counts["saved"] += 1
        else:
            try:
                descriptors = calculate_properties(str(row.get("isomeric_smiles") or ""))["descriptors"]
                counts["calculated"] += 1
            except PropertyAnalysisError:
                counts["unavailable"] += 1
                result.append(None)
                continue
        values = {}
        for short_key, descriptor in PROPERTY_KEYS:
            value = _finite((descriptors.get(descriptor) or {}).get("value"))
            if value is not None:
                values[short_key] = round(value, 4)
        result.append(values)
    return result, {"algorithm": PROPERTY_VERSION, **counts}


def build_pattern_explorer(
    *,
    sar_matrix: Mapping[str, Any],
    pharmacophore_rgroup: Mapping[str, Any] | None,
    property_profiles: Iterable[Mapping[str, Any]],
    activity_cliffs: Iterable[Mapping[str, Any]],
    mmp_pairs: Iterable[Mapping[str, Any]],
    fragment_svgs: MutableMapping[str, str],
    site_map: Mapping[str, Any] | None = None,
    links: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compact, JSON-ready explorer document.

    ``fragment_svgs`` is the page's shared symbol sprite; substituent drawings
    from the pharmacophore run are added to it so the browser can reference
    them with ``<use href="#frag-…">`` instead of receiving the SVG again.
    """
    rows = list(sar_matrix.get("rows") or [])
    matrix_endpoints = list(sar_matrix.get("endpoints") or [])
    activity_cliffs = list(activity_cliffs or [])
    mmp_pairs = list(mmp_pairs or [])
    endpoints = []
    for index, endpoint in enumerate(matrix_endpoints):
        cells = [row["cells"][index] for row in rows if index < len(row.get("cells") or [])]
        exact_values = [
            value
            for value in (_finite(cell.get("value")) for cell in cells if cell.get("state") == "exact")
            if value is not None
        ]
        censored = sum(1 for cell in cells if cell.get("state") == "censored")
        direction = endpoint.get("direction") or "none"
        endpoints.append(
            {
                "key": endpoint.get("id") or f"{endpoint.get('compatibility_key')}|{endpoint.get('unit') or ''}",
                "compatibility_key": endpoint.get("compatibility_key"),
                "name": endpoint.get("name"),
                "unit": endpoint.get("unit") or "",
                "unit_label": endpoint.get("unit_label") or "",
                "direction": direction,
                "scale": _endpoint_scale(endpoint.get("unit"), exact_values),
                "potency": _is_potency(endpoint.get("unit"), direction),
                "exact": len(exact_values),
                "censored": censored,
            }
        )
    endpoint_index = {endpoint["key"]: index for index, endpoint in enumerate(endpoints)}

    run = pharmacophore_rgroup or {}
    assignments = {item.get("compound_id"): item for item in run.get("assignments") or []}
    has_run = bool(assignments)
    properties, property_provenance = _properties(rows, property_profiles)

    compounds = []
    index_by_id: dict[str, int] = {}
    for position, row in enumerate(rows):
        compound_id = row.get("compound_id")
        index_by_id[compound_id] = position
        assignment = assignments.get(compound_id)
        if not has_run:
            status = "no_run"
        elif assignment is None:
            status = "not_in_run"
        else:
            status = str(assignment.get("status") or "invalid")
        sites = {}
        core = None
        core_note = ""
        if assignment is not None and status == "assigned":
            sites = {site["label"]: _site_payload(site, fragment_svgs) for site in assignment.get("sites") or [] if site.get("label")}
            core = _core_key(row.get("isomeric_smiles"), assignment.get("match_atoms") or [])
            core_note = _core_note(assignment)
        registration_id = str(row.get("registration_id") or compound_id)
        compounds.append(
            {
                "id": compound_id,
                "reg": registration_id,
                "name": row.get("preferred_name") or "",
                "symbol": symbol_id(f"structure-{registration_id}"),
                "status": status,
                "sites": sites,
                "core": core,
                "core_note": core_note,
                "values": [_cell_payload(cell) for cell in row.get("cells") or []],
                "props": properties[position],
            }
        )

    pairs, pair_policy = _similarity_pairs([str(row.get("isomeric_smiles") or "") for row in rows])

    mcs: dict[tuple[int, int], float] = {}
    for record in [*mmp_pairs, *activity_cliffs]:
        first = index_by_id.get(record.get("compound_a_id"))
        second = index_by_id.get(record.get("compound_b_id"))
        similarity = _finite(record.get("similarity"))
        if first is None or second is None or first == second or similarity is None:
            continue
        mcs.setdefault((min(first, second), max(first, second)), round(similarity, 3))

    saved_cliffs = []
    shown_per_endpoint: dict[str, int] = {}
    for cliff in activity_cliffs:
        key = str(cliff.get("assay_compatibility_key") or "")
        shown_per_endpoint[key] = shown_per_endpoint.get(key, 0) + 1
        first = index_by_id.get(cliff.get("compound_a_id"))
        second = index_by_id.get(cliff.get("compound_b_id"))
        endpoint_position = endpoint_index.get(f"{key}|{cliff.get('effect_unit') or ''}")
        if first is None or second is None or endpoint_position is None:
            continue
        saved_cliffs.append(
            {
                "i": min(first, second),
                "j": max(first, second),
                "e": endpoint_position,
                "id": cliff.get("id"),
                "shown": shown_per_endpoint[key] <= SHOWN_CLIFF_ROWS,
            }
        )

    positions = list(run.get("positions") or [])
    pins = {pin["label"]: pin for pin in (site_map or {}).get("sites") or []}
    site_list = [
        {
            "label": position["label"],
            "varied": int(position.get("member_count") or 0),
            "x": pins.get(position["label"], {}).get("x"),
            "y": pins.get(position["label"], {}).get("y"),
        }
        for position in positions
        if position.get("label")
    ]

    reference_index = index_by_id.get(run.get("reference_compound_id")) if has_run else None
    default_endpoint = next((endpoint["key"] for endpoint in endpoints if endpoint["exact"] >= 3), None)
    if default_endpoint is None and endpoints:
        default_endpoint = max(endpoints, key=lambda endpoint: endpoint["exact"])["key"]
    default_site = max(site_list, key=lambda site: site["varied"])["label"] if site_list else None
    input_selection = run.get("input_selection") or {}
    return {
        "version": EXPLORER_VERSION,
        "endpoints": endpoints,
        "default_endpoint": default_endpoint,
        "compounds": compounds,
        "reference": reference_index,
        "sites": site_list,
        "default_site": default_site,
        "site_map": {"width": site_map.get("width"), "height": site_map.get("height")} if site_map else None,
        "pairs": pairs,
        "mcs": [[first, second, similarity] for (first, second), similarity in sorted(mcs.items())],
        "saved_cliffs": saved_cliffs,
        "links": dict(links or {}),
        "provenance": {
            "explorer": EXPLORER_VERSION,
            "similarity": SIMILARITY_METHOD,
            "pairs": pair_policy,
            "summaries": "Latest saved summary per compound and endpoint",
            "properties": property_provenance,
            "pharmacophore": {
                "run_id": run.get("id"),
                "algorithm": run.get("algorithm_version"),
                "created_at": run.get("created_at"),
                "reference": run.get("reference_registration_id"),
                "scaffold_smarts": input_selection.get("scaffold_smarts"),
            }
            if has_run
            else None,
        },
        "data_origin": "derived",
    }
