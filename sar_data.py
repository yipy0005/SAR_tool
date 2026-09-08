from __future__ import annotations


def structure_svg(label: str, variant: str = "lead", accent: str = "#8ed7ff") -> str:
    """Return a small self-contained 2D structure illustration for the demo cards.

    These are intentionally illustrative structures for the UX prototype, not a
    chemistry validation engine. Production rendering should be RDKit-backed.
    """
    substitutions = {
        "lead": ("Cl", "O", "N"),
        "cliff": ("H", "N", "O"),
        "polar": ("F", "O", "N"),
        "rescue": ("Me", "O", "S"),
        "explore": ("Br", "N", "O"),
    }
    r2, r3, hetero = substitutions.get(variant, substitutions["lead"])
    return f"""<svg class=\"molecule molecule--{variant}\" viewBox=\"0 0 224 116\" role=\"img\" aria-label=\"Illustrative 2D structure for {label}\" xmlns=\"http://www.w3.org/2000/svg\">
  <g fill=\"none\" stroke=\"{accent}\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\">
    <path d=\"M39 57 51 36 75 36 87 57 75 78 51 78Z\"/>
    <path d=\"M45 57 54 42M62 36 77 57M54 72 77 58\" opacity=\".62\"/>
    <path d=\"M87 57h30\"/>
    <path d=\"M117 57 130 35 155 35 168 57 155 79 130 79Z\"/>
    <path d=\"M168 57h25\"/>
    <path d=\"M117 57 108 28\"/>
    <path d=\"M108 28h-23\"/>
    <path d=\"M155 79v19h22\"/>
  </g>
  <g fill=\"{accent}\" font-family=\"Fira Code, ui-monospace, monospace\" font-size=\"11\" font-weight=\"600\">
    <text x=\"57\" y=\"28\">R1</text>
    <text x=\"194\" y=\"61\">{r2}</text>
    <text x=\"73\" y=\"24\">{r3}</text>
    <text x=\"178\" y=\"103\">{hetero}</text>
    <text x=\"16\" y=\"105\" fill=\"#8b9aa5\">{label}</text>
  </g>
  <circle cx=\"117\" cy=\"57\" r=\"4\" fill=\"{accent}\" opacity=\".22\"/>
</svg>"""


PROJECT = {
    "name": "Aurora Kinase Program",
    "series": "Series A · ATP-competitive core",
    "status": "Active optimization",
    "last_updated": "12 Sep 2024",
    "source_label": "Illustrative demo data",
    "compound_count": 186,
    "assay_count": 14,
    "measurement_count": 2840,
    "best_compound": "SAR-317",
    "best_compound_reason": "Best balanced biochemical, cellular, and clearance profile",
    "quality_score": 86,
    "unresolved_count": 2,
}


COMPOUNDS = [
    {
        "id": "SAR-317",
        "rank": 1,
        "series": "Series A",
        "scaffold": "Core A",
        "status": "Lead",
        "structure": structure_svg("SAR-317", "lead"),
        "biochemical": "7.8",
        "cellular": "7.1",
        "clearance": "12",
        "logd": "2.1",
        "selectivity": "48×",
        "replicates": "3 / 3",
        "confidence": "Strong",
        "rationale": "Best current balance of target potency, translation, and stability.",
    },
    {
        "id": "SAR-186",
        "rank": 2,
        "series": "Series A",
        "scaffold": "Core A",
        "status": "Lead",
        "structure": structure_svg("SAR-186", "cliff", "#ffd08a"),
        "biochemical": "7.6",
        "cellular": "6.8",
        "clearance": "18",
        "logd": "2.8",
        "selectivity": "36×",
        "replicates": "4 / 4",
        "confidence": "Strong",
        "rationale": "Cl substitution at R2 is reproducibly productive but slightly less translational.",
    },
    {
        "id": "SAR-204",
        "rank": 3,
        "series": "Series B",
        "scaffold": "Core B",
        "status": "Promising",
        "structure": structure_svg("SAR-204", "polar", "#a9e8ce"),
        "biochemical": "7.3",
        "cellular": "7.0",
        "clearance": "9",
        "logd": "1.3",
        "selectivity": "31×",
        "replicates": "2 / 2",
        "confidence": "Moderate",
        "rationale": "Lower logD improves cellular translation and clearance, with a modest potency trade-off.",
    },
    {
        "id": "SAR-142",
        "rank": 4,
        "series": "Series A",
        "scaffold": "Core A",
        "status": "Reference",
        "structure": structure_svg("SAR-142", "rescue", "#c7b7ff"),
        "biochemical": "6.2",
        "cellular": "5.7",
        "clearance": "31",
        "logd": "3.7",
        "selectivity": "18×",
        "replicates": "3 / 3",
        "confidence": "Strong",
        "rationale": "Reference compound for the 27-fold N-methylation activity cliff.",
    },
]


INSIGHTS = [
    {
        "id": "insight-1",
        "type": "Observation",
        "tone": "positive",
        "title": "R2 electron-withdrawing groups improve biochemical potency",
        "summary": "Cl and F improved potency in matched pairs across Core A; the effect is strongest when R1 is aromatic.",
        "confidence": "Strong evidence",
        "confidence_detail": "4 independent pairs · 3 assay batches · no contradictory pair",
        "metric": "+0.72 pIC50 median",
        "evidence": [
            {"compound": "SAR-186", "change": "R2: H → Cl", "delta": "+0.9 pIC50"},
            {"compound": "SAR-204", "change": "R2: H → F", "delta": "+0.6 pIC50"},
            {"compound": "SAR-221", "change": "R2: Me → Cl", "delta": "+0.7 pIC50"},
        ],
    },
    {
        "id": "insight-2",
        "type": "Observation",
        "tone": "warning",
        "title": "Basic R3 groups reduce microsomal stability",
        "summary": "Basic amines are associated with higher clearance in related compounds; the trend is consistent but not yet separated from logD.",
        "confidence": "Moderate evidence",
        "confidence_detail": "5 compounds · 2 clearance assays · property confounding present",
        "metric": "+18 µL/min/mg median",
        "evidence": [
            {"compound": "SAR-142", "change": "R3: morpholine → NMe2", "delta": "+22 clearance"},
            {"compound": "SAR-173", "change": "R3: OMe → piperidine", "delta": "+16 clearance"},
            {"compound": "SAR-204", "change": "R3: piperidine → OMe", "delta": "−19 clearance"},
        ],
    },
    {
        "id": "insight-3",
        "type": "Interpretation",
        "tone": "info",
        "title": "Cellular translation deteriorates above logD 3.5",
        "summary": "Biochemical potency is retained while cellular potency drops in the high-logD subset. Treat this as an empirical relationship, not a causal mechanism.",
        "confidence": "Moderate evidence",
        "confidence_detail": "9 compounds · 2 cell models · permeability not yet measured",
        "metric": "~40× translation loss",
        "evidence": [
            {"compound": "SAR-142", "change": "logD 3.7", "delta": "−0.5 pIC50 cell"},
            {"compound": "SAR-186", "change": "logD 2.8", "delta": "−0.8 pIC50 cell"},
            {"compound": "SAR-204", "change": "logD 1.3", "delta": "−0.3 pIC50 cell"},
        ],
    },
]


MATRIX = {
    "biochemical": {
        "label": "Biochemical pIC50",
        "unit": "pIC50",
        "direction": "Higher is better",
        "rows": [
            {"r1": "H", "cells": [{"value": "6.1", "n": 4}, {"value": "6.4", "n": 3}, {"value": "6.7", "n": 5}, {"value": "7.0", "n": 4}]},
            {"r1": "F", "cells": [{"value": "6.5", "n": 3}, {"value": "6.9", "n": 4}, {"value": "7.3", "n": 5}, {"value": "7.5", "n": 4}]},
            {"r1": "Me", "cells": [{"value": "5.9", "n": 2}, {"value": "6.3", "n": 3}, {"value": None, "n": 0}, {"value": None, "n": 0}]},
        ],
        "columns": ["Me", "Et", "F", "Cl"],
        "note": "Missing cells are candidate designs, not zero activity.",
    },
    "cellular": {
        "label": "Cellular pIC50",
        "unit": "pIC50",
        "direction": "Higher is better",
        "rows": [
            {"r1": "H", "cells": [{"value": "5.2", "n": 3}, {"value": "5.7", "n": 3}, {"value": "6.0", "n": 4}, {"value": "6.1", "n": 3}]},
            {"r1": "F", "cells": [{"value": "5.8", "n": 2}, {"value": "6.2", "n": 3}, {"value": "6.8", "n": 4}, {"value": "6.8", "n": 4}]},
            {"r1": "Me", "cells": [{"value": "5.0", "n": 2}, {"value": "5.4", "n": 2}, {"value": None, "n": 0}, {"value": None, "n": 0}]},
        ],
        "columns": ["Me", "Et", "F", "Cl"],
        "note": "Cellular values currently lag biochemical values most at high logD.",
    },
    "adme": {
        "label": "Microsomal clearance",
        "unit": "µL/min/mg",
        "direction": "Lower is better",
        "rows": [
            {"r1": "H", "cells": [{"value": "24", "n": 3}, {"value": "28", "n": 2}, {"value": "21", "n": 3}, {"value": "18", "n": 3}]},
            {"r1": "F", "cells": [{"value": "20", "n": 2}, {"value": "17", "n": 3}, {"value": "14", "n": 3}, {"value": "12", "n": 3}]},
            {"r1": "Me", "cells": [{"value": "31", "n": 2}, {"value": "35", "n": 2}, {"value": None, "n": 0}, {"value": None, "n": 0}]},
        ],
        "columns": ["Me", "Et", "F", "Cl"],
        "note": "Clearance is shown as observed values; no imputation is applied to gaps.",
    },
}


RECOMMENDATIONS = [
    {
        "id": "design-1",
        "category": "Exploit",
        "tone": "positive",
        "title": "Combine the supported R2-Cl gain with a polar R3",
        "structure": structure_svg("DES-041", "lead"),
        "design_id": "DES-041",
        "hypothesis": "R2-Cl retains the potency gain while a less basic R3 improves clearance and cellular translation.",
        "evidence": "Cl improved potency 4–8× in four pairs; polar R3 improved clearance 3–5× in three related compounds.",
        "expected": "Maintain pIC50 ≥ 7.0 and reduce clearance below 15 µL/min/mg.",
        "uncertainty": "Exact R2/R3 combination is untested; interaction risk is moderate.",
        "support": "4 matched pairs · 3 related clearance examples",
        "novelty": 34,
        "priority": "P1",
    },
    {
        "id": "design-2",
        "category": "Resolve",
        "tone": "warning",
        "title": "Test whether the cellular gap is permeability-driven",
        "structure": structure_svg("DES-052", "polar", "#a9e8ce"),
        "design_id": "DES-052",
        "hypothesis": "Lowering logD can preserve biochemical potency while improving cellular translation if permeability is the limiting step.",
        "evidence": "Series B compounds with logD 1–2 show a smaller biochemical-to-cellular drop than the high-logD subset.",
        "expected": "Reduce translation loss without adding a new clearance liability.",
        "uncertainty": "No direct permeability measurements are available for this series.",
        "support": "9 compounds · 2 cell models · correlation only",
        "novelty": 58,
        "priority": "P1",
    },
    {
        "id": "design-3",
        "category": "Fill",
        "tone": "info",
        "title": "Fill the missing R1 = Me / R2 = F cell",
        "structure": structure_svg("DES-067", "explore", "#8ed7ff"),
        "design_id": "DES-067",
        "hypothesis": "The R2-F gain may transfer to the methyl-substituted R1 context, or reveal a context interaction.",
        "evidence": "F improves potency in four aromatic-R1 pairs; no matched measurement exists for R1 = Me.",
        "expected": "Discriminate additive versus context-dependent R1/R2 SAR.",
        "uncertainty": "High extrapolation: the relevant cell is empty and nearby context is sparse.",
        "support": "0 direct pairs · 4 adjacent F examples",
        "novelty": 72,
        "priority": "P2",
    },
    {
        "id": "design-4",
        "category": "Challenge",
        "tone": "violet",
        "title": "Challenge the high-logD translation hypothesis",
        "structure": structure_svg("DES-073", "rescue", "#c7b7ff"),
        "design_id": "DES-073",
        "hypothesis": "Cellular loss above logD 3.5 is not universal and will be rescued by a matched permeability-positive analog.",
        "evidence": "The current trend is empirical and confounded by series and assay differences.",
        "expected": "A high-logD analog with preserved cellular potency would weaken the current interpretation.",
        "uncertainty": "Designed to falsify the leading explanation; outcome is intentionally uncertain.",
        "support": "3 high-logD examples · no direct permeability control",
        "novelty": 86,
        "priority": "P3",
    },
]


QUALITY_FLAGS = [
    {"severity": "high", "label": "Replicate disagreement", "detail": "SAR-288 has a 70-fold IC50 spread across two technical replicates.", "action": "Repeat assay"},
    {"severity": "medium", "label": "Assay context gap", "detail": "Permeability data are missing for 11 compounds used in translation analysis.", "action": "Add endpoint"},
    {"severity": "low", "label": "Registration duplicate", "detail": "Two IDs normalize to the same parent structure after salt stripping.", "action": "Review mapping"},
]


TIMELINE = [
    {"date": "12 Sep", "title": "R2-Cl gain strengthened", "detail": "Two new matched pairs support the same direction across a second assay batch.", "tag": "Hypothesis strengthened", "tone": "positive"},
    {"date": "05 Sep", "title": "New Series B cellular data", "detail": "Three compounds narrow the observed translation gap at logD 1–2.", "tag": "New evidence", "tone": "info"},
    {"date": "29 Aug", "title": "SAR-288 flagged", "detail": "Replicate spread exceeded the project’s 5× QC threshold.", "tag": "Data quality", "tone": "warning"},
]


HYPOTHESES = [
    {"id": "H-001", "statement": "R2 electron-withdrawing groups improve potency when R1 is aromatic.", "rationale": "Supported by four independent matched pairs.", "status": "Supported"},
    {"id": "H-002", "statement": "High logD is the primary driver of cellular translation loss.", "rationale": "Plausible but permeability and protein-binding data are missing.", "status": "Uncertain"},
]
