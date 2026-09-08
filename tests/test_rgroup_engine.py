from __future__ import annotations


import pytest
from rgroup_engine import decompose_rgroups, find_activity_cliffs


def test_rgroup_decomposition_requires_explicit_scaffold_and_assigns_substituent():
    result = decompose_rgroups("cmp-1", "Cc1ccccc1", "c1ccccc1")

    assert result["status"] == "assigned"
    assert result["match_atoms"]
    assert result["assignments"] == {"R1": "C"}


def test_rgroup_decomposition_surfaces_unmatched_scaffold():
    result = decompose_rgroups("cmp-1", "CCO", "c1ccccc1")

    assert result["status"] == "unmatched"
    assert result["assignments"] == {}
    assert result["failure_reason"] == "scaffold_not_found"


def _summary(compound_id, smiles, value, *, state="observed", qualifier="=", context="ic50:v1"):
    return {
        "summary_id": f"summary-{compound_id}",
        "compound_id": compound_id,
        "isomeric_smiles": smiles,
        "summary_value": value,
        "summary_state": state,
        "summary_qualifier": qualifier,
        "canonical_unit": "pIC50",
        "compatibility_key": context,
    }


def test_activity_cliffs_require_similarity_effect_and_observed_compatible_summaries():
    records = [
        _summary("cmp-a", "c1ccccc1", 7.0),
        _summary("cmp-b", "Cc1ccccc1", 8.2),
        _summary("cmp-c", "CCO", 9.5, state="censored", qualifier="<"),
        _summary("cmp-d", "Cc1ccccc1", 9.5, context="ic50:other"),
    ]

    cliffs = find_activity_cliffs(records, effect_threshold=1.0, similarity_threshold=0.8)

    assert len(cliffs) == 1
    assert cliffs[0]["compound_a_id"] == "cmp-a"
    assert cliffs[0]["compound_b_id"] == "cmp-b"
    assert cliffs[0]["effect_value"] == pytest.approx(1.2)
    assert cliffs[0]["evidence_status"] == "eligible_observed_summary"


def test_activity_cliffs_do_not_report_small_effects():
    records = [_summary("cmp-a", "c1ccccc1", 7.0), _summary("cmp-b", "Cc1ccccc1", 7.5)]

    assert find_activity_cliffs(records, effect_threshold=1.0) == []
