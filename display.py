"""Presentation-only helpers for scientist-facing views.

Nothing here changes stored values. These functions round numbers for display,
render units the way chemists write them, and turn internal codes into readable
labels. Exports and APIs keep full precision.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any

from markupsafe import Markup, escape
from rdkit import Chem

_SUPERSCRIPT = str.maketrans("0123456789-+", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺")
_LOG_UNIT = re.compile(r"^(p[A-Z]|log)", re.IGNORECASE)


def is_log_unit(unit: Any) -> bool:
    text = str(unit or "").strip()
    return bool(text) and bool(_LOG_UNIT.match(text)) and text.lower() not in {"ppm", "ppb"}


def format_unit(unit: Any) -> str:
    """Render a stored unit string in conventional typography (uL → µL, 10^-6 → 10⁻⁶)."""
    text = str(unit or "").strip()
    if not text:
        return ""
    text = re.sub(r"\^(-?\+?\d+)", lambda match: match.group(1).translate(_SUPERSCRIPT), text)
    text = re.sub(r"(?<![A-Za-z])u(?=(L|M|g|mol|m)\b)", "µ", text)
    return text


def format_number(value: Any, unit: Any = None, *, digits: int | None = None) -> str:
    """Round a value for reading: 2 decimals on log scales, otherwise 3 significant figures."""
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "—"
    if digits is not None:
        return f"{number:.{digits}f}"
    if is_log_unit(unit):
        return f"{number:.2f}"
    if number == 0:
        return "0"
    magnitude = abs(number)
    if magnitude >= 1000:
        return f"{number:,.0f}"
    if magnitude < 0.001:
        return f"{number:.2e}"
    decimals = max(0, 2 - int(math.floor(math.log10(magnitude))))
    text = f"{number:.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_measure(value: Any, unit: Any = None, qualifier: Any = None) -> str:
    """Value with its qualifier in front and unit behind, e.g. '> 10 µM'."""
    if value is None or value == "":
        return "—"
    qualifier_text = str(qualifier or "").strip()
    prefix = f"{qualifier_text} " if qualifier_text and qualifier_text != "=" else ""
    return f"{prefix}{format_number(value, unit)}{_unit_suffix(unit)}"


def _unit_suffix(unit: Any) -> str:
    """' µM', or ' × 10⁻⁶ cm/s' when the unit carries a power-of-ten scale."""
    unit_text = format_unit(unit)
    if not unit_text:
        return ""
    return f" × {unit_text}" if unit_text.startswith("10") and unit_text[2:3] in "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺" else f" {unit_text}"


def format_delta(value: Any, unit: Any = None) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "—"
    sign = "+" if number > 0 else "−" if number < 0 else "±"
    return f"{sign}{format_number(abs(number), unit)}{_unit_suffix(unit)}"


_FEATURE_LABELS = {
    "Donor": "H-bond donor",
    "Acceptor": "H-bond acceptor",
    "NegIonizable": "Negative ionizable",
    "PosIonizable": "Positive ionizable",
    "ZnBinder": "Zn binder",
    "Aromatic": "Aromatic",
    "Hydrophobe": "Hydrophobe",
    "LumpedHydrophobe": "Hydrophobic region",
    "Halogen": "Halogen substituent",
    "r_group": "R-group",
    "core": "Shared core",
    "interface": "Core / R-group interface",
    "ambiguous": "Ambiguous across sites",
    "unassigned": "Unassigned",
}


def feature_label(value: Any) -> str:
    text = str(value or "").strip()
    return _FEATURE_LABELS.get(text, humanize(text))


_CODE_LABELS = {
    "no_chiral_centers_detected": "No stereocentres",
    "fully_defined": "Stereo defined",
    "undefined_stereocenters": "Undefined stereocentre(s)",
    "partially_defined": "Stereo partly defined",
    "observed": "Exact",
    "observed_with_censored": "Exact + threshold values",
    "observed_with_missing": "Exact + missing values",
    "observed_with_censored_and_missing": "Exact + threshold + missing",
    "censored": "Threshold only",
    "censored_with_missing": "Threshold + missing",
    "missing": "No result",
    "unreviewed": "Not reviewed",
    "pass": "Passed",
    "fail": "Failed",
    "complete_observed": "Complete",
    "complete_non_pareto": "Dominated",
    "pareto": "Pareto front",
    "incomplete": "Incomplete",
    "selective": "Selective",
    "non_selective": "Not selective",
    "translated": "Translates",
    "translation_loss": "Drops in cells",
    "unreconciled": "Needs review",
}


def humanize(value: Any) -> str:
    """Readable label for an internal status code; unknown codes get spaces instead of underscores."""
    text = str(value or "").strip()
    if not text:
        return "—"
    if text in _CODE_LABELS:
        return _CODE_LABELS[text]
    spaced = text.replace("_", " ")
    # Only raise the first letter; str.capitalize() would turn "IC50" into "Ic50".
    return spaced[:1].upper() + spaced[1:]


def endpoint_name(compatibility_key: Any) -> str:
    """'IC50:import-v1' → 'IC50'. The protocol suffix stays available in tooltips."""
    return str(compatibility_key or "").split(":", 1)[0] or "Endpoint"


# Common medicinal-chemistry substituents keyed by SMILES with one attachment point.
_SUBSTITUENTS = {
    "*C": "Me",
    "*CC": "Et",
    "*CCC": "n-Pr",
    "*C(C)C": "i-Pr",
    "*C(C)(C)C": "t-Bu",
    "*C1CC1": "c-Pr",
    "*C1CCC1": "c-Bu",
    "*C1CCCC1": "c-Pent",
    "*C1CCCCC1": "c-Hex",
    "*C=C": "vinyl",
    "*C#C": "ethynyl",
    "*F": "F",
    "*Cl": "Cl",
    "*Br": "Br",
    "*I": "I",
    "*O": "OH",
    "*OC": "OMe",
    "*OCC": "OEt",
    "*OC(C)C": "Oi-Pr",
    "*OC(F)(F)F": "OCF₃",
    "*OC(F)F": "OCHF₂",
    "*C(F)(F)F": "CF₃",
    "*C(F)F": "CHF₂",
    "*CF": "CH₂F",
    "*CO": "CH₂OH",
    "*C#N": "CN",
    "*N": "NH₂",
    "*NC": "NHMe",
    "*N(C)C": "NMe₂",
    "*NC(C)=O": "NHAc",
    "*[N+](=O)[O-]": "NO₂",
    "*C(C)=O": "Ac",
    "*C=O": "CHO",
    "*C(=O)O": "CO₂H",
    "*C(=O)OC": "CO₂Me",
    "*C(=O)OCC": "CO₂Et",
    "*C(N)=O": "CONH₂",
    "*C(=O)NC": "CONHMe",
    "*C(=O)N(C)C": "CONMe₂",
    "*S": "SH",
    "*SC": "SMe",
    "*S(C)(=O)=O": "SO₂Me",
    "*S(N)(=O)=O": "SO₂NH₂",
    "*c1ccccc1": "Ph",
    "*Cc1ccccc1": "Bn",
    "*c1ccccn1": "2-pyridyl",
    "*c1cccnc1": "3-pyridyl",
    "*c1ccncc1": "4-pyridyl",
    "*N1CCOCC1": "morpholino",
    "*N1CCCC1": "pyrrolidin-1-yl",
    "*N1CCCCC1": "piperidin-1-yl",
    "*N1CCNCC1": "piperazin-1-yl",
    "*N1CCN(C)CC1": "4-Me-piperazin-1-yl",
}


@lru_cache(maxsize=1)
def _substituent_table() -> dict[str, str]:
    table: dict[str, str] = {}
    for smiles, name in _SUBSTITUENTS.items():
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is not None:
            table[Chem.MolToSmiles(molecule)] = name
    return table


def substituent_name(attached_smiles: Any) -> str | None:
    """Common name for a substituent written with one dummy attachment atom, or None."""
    text = str(attached_smiles or "").strip()
    if not text:
        return None
    molecule = Chem.MolFromSmiles(text)
    if molecule is None:
        return None
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() == 0:
            atom.SetAtomMapNum(0)
            atom.SetIsotope(0)
    return _substituent_table().get(Chem.MolToSmiles(molecule))


def atom_formula(symbols: Any) -> str:
    """Compact Hill-style formula for a list of heavy-atom symbols, e.g. ['F','F','F','C'] → 'CF₃'."""
    counts: dict[str, int] = {}
    for symbol in symbols or []:
        key = str(symbol).strip()
        if key:
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "H"
    order = (["C"] if "C" in counts else []) + sorted(key for key in counts if key != "C")
    subscript = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    return "".join(f"{key}{str(counts[key]).translate(subscript) if counts[key] > 1 else ''}" for key in order)


_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_VIEWBOX = re.compile(r"viewBox=['\"]([^'\"]+)['\"]")


def symbol_id(value: Any) -> str:
    """DOM-safe id for an SVG <symbol>.

    The server-rendered sprite, the tables, and the browser-side pattern
    explorer all build ids through this one function, so ``<use href>``
    references always resolve to the same symbol.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(value or ""))


def svg_symbol(svg: Any, symbol_name: str) -> str:
    """Turn an RDKit SVG into a reusable <symbol> so repeated structures are sent once."""
    text = str(svg or "")
    opening = _SVG_OPEN.search(text)
    if not opening:
        return ""
    view_box = _VIEWBOX.search(opening.group(0))
    body = text[opening.end():]
    body = body[: body.rfind("</svg>")] if "</svg>" in body else body
    return Markup(f'<symbol id="{symbol_id(symbol_name)}" viewBox="{view_box.group(1) if view_box else "0 0 320 180"}">{body}</symbol>')


def svg_use(symbol_name: str, label: str = "", view_box: str = "0 0 320 180", css_class: str = "structure-use") -> str:
    aria = f' role="img" aria-label="{escape(label)}"' if label else ' aria-hidden="true"'
    return Markup(f'<svg class="{escape(css_class)}" viewBox="{escape(view_box)}"{aria}><use href="#{symbol_id(symbol_name)}"></use></svg>')


def register_filters(app: Any) -> None:
    app.jinja_env.filters.update(
        {
            "fmt_number": format_number,
            "fmt_measure": format_measure,
            "fmt_delta": format_delta,
            "fmt_unit": format_unit,
            "humanize": humanize,
            "endpoint_name": endpoint_name,
            "atom_formula": atom_formula,
            "svg_symbol": svg_symbol,
            "feature_label": feature_label,
        }
    )
    app.jinja_env.globals.update(
        {
            "svg_use": svg_use,
            "common_substituents": [{"smiles": smiles, "name": name} for smiles, name in _SUBSTITUENTS.items()],
        }
    )
