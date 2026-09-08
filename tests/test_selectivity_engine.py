import pytest

from selectivity_engine import SelectivityAnalysisError, calculate_selectivity


def summary(summary_id, compound_id, compatibility_key, value, *, unit="pIC50", state="observed", qualifier="=", created_at="2026-09-07T08:00:00+00:00"):
    return {
        "id": summary_id,
        "compound_id": compound_id,
        "compatibility_key": compatibility_key,
        "canonical_unit": unit,
        "summary_value": value,
        "summary_state": state,
        "summary_qualifier": qualifier,
        "created_at": created_at,
    }


def test_selectivity_uses_primary_minus_comparator_margin():
    results = calculate_selectivity(
        [
            summary("p-1", "cmp-1", "primary:v1", 9.0),
            summary("c-1", "cmp-1", "offtarget:v1", 7.0),
        ],
        "primary:v1",
        "offtarget:v1",
        selectivity_threshold=1.5,
    )

    assert results == [
        {
            "compound_id": "cmp-1",
            "registration_id": None,
            "primary_summary_id": "p-1",
            "comparator_summary_id": "c-1",
            "primary_value": 9.0,
            "comparator_value": 7.0,
            "selectivity_delta": 2.0,
            "canonical_unit": "pIC50",
            "status": "selective",
            "evidence_status": "observed_compatible_summaries",
            "reason": None,
        }
    ]


def test_selectivity_preserves_incomplete_censoring_and_missingness():
    results = calculate_selectivity(
        [
            summary("p-1", "cmp-1", "primary:v1", 9.0),
            summary("c-1", "cmp-1", "offtarget:v1", 8.0, state="censored", qualifier="<"),
            summary("p-2", "cmp-2", "primary:v1", None, state="missing", qualifier=""),
        ],
        "primary:v1",
        "offtarget:v1",
    )

    assert results[0]["status"] == "incomplete"
    assert results[0]["reason"] == "summary_not_exact_observed"
    assert results[0]["selectivity_delta"] is None
    assert results[1]["status"] == "incomplete"
    assert results[1]["reason"] == "summary_missing"


def test_selectivity_rejects_unit_mismatch_and_same_context():
    mismatch = calculate_selectivity(
        [
            summary("p-1", "cmp-1", "primary:v1", 9.0, unit="pIC50"),
            summary("c-1", "cmp-1", "offtarget:v1", 10.0, unit="nM"),
        ],
        "primary:v1",
        "offtarget:v1",
    )[0]
    assert mismatch["status"] == "incomplete"
    assert mismatch["reason"] == "canonical_unit_mismatch"

    with pytest.raises(SelectivityAnalysisError, match="must differ"):
        calculate_selectivity([], "same", "same")

    with pytest.raises(SelectivityAnalysisError, match="non-negative"):
        calculate_selectivity([], "primary", "comparator", selectivity_threshold=-1)
