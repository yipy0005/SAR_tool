from __future__ import annotations

import pytest

from recommendation_engine import RecommendationValidationError, rank_candidates, validate_candidate


def candidate(**overrides):
    value = {
        "category": "exploit",
        "title": "Combine supported potency and stability changes",
        "rationale": "Two independent observations support this design direction.",
        "hypothesis": "A less basic R3 preserves potency while improving clearance.",
        "expected_outcome": "Maintain biochemical potency and reduce clearance.",
        "uncertainty": "The exact combination has not been measured.",
        "evidence_ids": ["claim-1", "mmp-1"],
        "novelty_score": 0.35,
        "feasibility_status": "tractable",
        "feasibility_reasons": ["Uses a precedent route"],
        "information_gain_score": 0.55,
        "objective_alignment_score": 0.9,
    }
    value.update(overrides)
    return value


def test_candidate_requires_evidence_and_cannot_be_confirmed():
    with pytest.raises(RecommendationValidationError, match="evidence identifier"):
        validate_candidate(candidate(evidence_ids=[]))
    with pytest.raises(RecommendationValidationError, match="experimentally confirmed"):
        validate_candidate(candidate(status="experimentally_confirmed"))


def test_invalid_structure_is_rejected_before_ranking():
    with pytest.raises(RecommendationValidationError, match="structure is invalid"):
        validate_candidate(candidate(structure_smiles="not-a-valid-smiles"))


def test_ranking_is_transparent_and_information_gain_changes_order():
    high_confidence = candidate(title="High confidence incremental design", feasibility_status="tractable", objective_alignment_score=1.0, information_gain_score=0.2)
    high_learning = candidate(category="challenge", title="High learning challenge design", feasibility_status="review", objective_alignment_score=0.55, information_gain_score=1.0, novelty_score=0.9)

    ranked = rank_candidates([high_learning, high_confidence])

    assert ranked[0]["rank"] == 1
    assert ranked[0]["ranking_version"] == "transparent-mpo-information-gain-v1"
    assert set(ranked[0]["score_components"]) == {"objective_alignment", "information_gain", "feasibility", "evidence_support", "novelty"}
    assert all("data_origin" in item and item["data_origin"] == "curated" for item in ranked)


def test_feasibility_and_rationale_are_preserved():
    item = validate_candidate(candidate(feasibility_status="review", feasibility_reasons=["Stereochemical burden", "Route not yet reviewed"]))

    assert item.feasibility_status == "review"
    assert item.feasibility_reasons == ("Stereochemical burden", "Route not yet reviewed")
    assert item.status == "proposed"
