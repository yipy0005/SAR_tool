from endpoint_comparison_engine import compare_rows


def test_endpoint_comparison_keeps_missing_compounds_and_selects_latest_summary():
    compounds = [
        {"id": "cmp-a", "registration_id": "CMP-A"},
        {"id": "cmp-b", "registration_id": "CMP-B"},
        {"id": "cmp-c", "registration_id": "CMP-C"},
    ]
    summaries = [
        {"id": "old", "compound_id": "cmp-a", "compatibility_key": "IC50:import-v1", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 100, "summary_qualifier": "=", "created_at": "2026-01-01"},
        {"id": "new", "compound_id": "cmp-a", "compatibility_key": "IC50:import-v1", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 10, "summary_qualifier": "=", "created_at": "2026-01-02"},
    ]
    result = compare_rows(summaries, compounds, "IC50:import-v1", None)
    assert [row["registration_id"] for row in result["rows"]] == ["CMP-A", "CMP-B", "CMP-C"]
    assert result["rows"][0]["endpoint_a"]["summary_id"] == "new"
    assert result["rows"][1]["status"] == "missing_a"
    assert compare_rows(summaries, compounds, "IC50:import-v1", None, q="cmp-b")["total"] == 1


def test_endpoint_summary_selection_prefers_browser_observed_state_over_newer_review_state():
    compounds = [{"id": "cmp-a", "registration_id": "CMP-A"}]
    summaries = [
        {"id": "observed-old", "compound_id": "cmp-a", "compatibility_key": "A", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 10, "summary_qualifier": "=", "created_at": "2026-01-01"},
        {"id": "review-new", "compound_id": "cmp-a", "compatibility_key": "A", "canonical_unit": "nM", "summary_state": "review", "summary_value": None, "summary_qualifier": "=", "created_at": "2026-01-02"},
    ]
    result = compare_rows(summaries, compounds, "A", None, coverage="all")
    assert result["rows"][0]["endpoint_a"]["summary_id"] == "observed-old"
    assert result["rows"][0]["status"] == "comparable"

    compounds = [{"id": "cmp-a", "registration_id": "CMP-A"}]
    summaries = [
        {"id": "threshold", "compound_id": "cmp-a", "compatibility_key": "A", "canonical_unit": "nM", "summary_state": "observed", "summary_value": 10, "summary_qualifier": "~"},
        {"id": "invalid", "compound_id": "cmp-a", "compatibility_key": "B", "canonical_unit": "nM", "summary_state": "observed", "summary_value": "NaN", "summary_qualifier": "="},
    ]
    review = compare_rows(summaries[:1], compounds, "A", None, coverage="review")
    assert review["total"] == 1 and review["rows"][0]["status"] == "review"
    invalid = compare_rows(summaries, compounds, "A", "B")
    assert invalid["rows"][0]["status"] == "invalid_numeric"
    assert invalid["rows"][0]["endpoint_b"]["value"] is None

    missing_qualifier = [{**summaries[0], "summary_qualifier": None}]
    non_exact = compare_rows(missing_qualifier, compounds, "A", None)
    assert non_exact["rows"][0]["status"] == "review"
    assert non_exact["rows"][0]["endpoint_a"]["qualifier"] is None

    missing_unit = [{**summaries[0], "summary_qualifier": "=", "canonical_unit": ""}]
    unitless = compare_rows(missing_unit, compounds, "A", None)
    assert unitless["rows"][0]["status"] == "missing_unit"
    assert unitless["rows"][0]["comparability"] == "not_comparable"


def test_endpoint_coverage_matches_browser_missing_reason_predicate():
    compounds = [{"id": "cmp-a", "registration_id": "CMP-A"}]
    summaries = [
        {
            "id": "missing-reason",
            "compound_id": "cmp-a",
            "compatibility_key": "A",
            "canonical_unit": "nM",
            "summary_state": "review",
            "summary_value": 10,
            "summary_qualifier": "=",
            "missing_reason": "not reported",
        },
        {
            "id": "observed-b",
            "compound_id": "cmp-a",
            "compatibility_key": "B",
            "canonical_unit": "nM",
            "summary_state": "observed",
            "summary_value": 20,
            "summary_qualifier": "=",
        },
    ]

    assert compare_rows(summaries, compounds, "A", "B", coverage="all")["total"] == 1
    assert compare_rows(summaries, compounds, "A", "B", coverage="endpoint-a")["total"] == 0
    assert compare_rows(summaries, compounds, "A", "B", coverage="both")["total"] == 0
    review = compare_rows(summaries, compounds, "A", "B", coverage="review")
    assert review["total"] == 1
    assert review["rows"][0]["status"] == "missing_a"

    nonfinite_missing = [{**summaries[0], "summary_state": "missing", "summary_value": "NaN", "missing_reasons": ["not reported"]}]
    for coverage in ("all", "review"):
        invalid = compare_rows(nonfinite_missing, compounds, "A", None, coverage=coverage)
        assert invalid["total"] == 1
        assert invalid["rows"][0]["status"] == "invalid_numeric"
        assert invalid["rows"][0]["endpoint_a"]["missing"] is True
        assert invalid["rows"][0]["endpoint_a"]["missing_reasons"] == ["not reported"]
