from __future__ import annotations

import json

import pytest

from database import apply_migrations, read_connection, transaction
from measurement_engine import (
    IncompatibleAssayContextError,
    create_measurement_summary,
    summarize_measurements,
)



def measurement(
    measurement_id: str,
    value: float | None,
    *,
    qualifier: str | None = "=",
    replicate_type: str = "unspecified",
    replicate_group_id: str | None = None,
    missing_reason: str | None = None,
    compatibility_key: str = "ic50:cell-v1",
    unit: str = "pIC50",
    lower_bound: float | None = None,
    upper_bound: float | None = None,
) -> dict[str, object]:
    return {
        "id": measurement_id,
        "compound_id": "compound-1",
        "compatibility_key": compatibility_key,
        "canonical_unit": unit,
        "canonical_value": value,
        "qualifier": qualifier,
        "replicate_type": replicate_type,
        "replicate_group_id": replicate_group_id,
        "missing_reason": missing_reason,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
    }


def test_exact_values_have_deterministic_mean_and_sample_dispersion():
    summary = summarize_measurements(
        [
            measurement("m-03", 14.0),
            measurement("m-01", 10.0),
            measurement("m-02", 12.0),
        ],
        analysis_version="measurement-summary-test-v1",
    )

    assert summary.summary_value == pytest.approx(12.0)
    assert summary.dispersion == pytest.approx(2.0)
    assert summary.dispersion_method == "sample_standard_deviation"
    assert summary.summary_state == "observed"
    assert summary.eligible_measurement_count == 3
    assert summary.source_measurement_ids == ("m-01", "m-02", "m-03")
    assert summary.analysis_version == "measurement-summary-test-v1"


def test_technical_replicates_are_counted_separately_and_aggregated():
    summary = summarize_measurements(
        [
            measurement("tech-1", 10.0, replicate_type="technical", replicate_group_id="bio-1"),
            measurement("tech-2", 12.0, replicate_type="technical", replicate_group_id="bio-1"),
        ]
    )

    assert summary.summary_value == pytest.approx(11.0)
    assert summary.technical_replicate_count == 2
    assert summary.technical_replicate_group_count == 1
    assert summary.biological_replicate_count == 0
    assert summary.unspecified_replicate_count == 0


def test_biological_replicates_are_counted_separately():
    summary = summarize_measurements(
        [
            measurement("bio-1", 8.0, replicate_type="biological", replicate_group_id="bio-1"),
            measurement("bio-2", 12.0, replicate_type="biological", replicate_group_id="bio-2"),
        ]
    )

    assert summary.summary_value == pytest.approx(10.0)
    assert summary.biological_replicate_count == 2
    assert summary.biological_replicate_group_count == 2
    assert summary.technical_replicate_count == 0


def test_incompatible_assay_contexts_are_rejected_before_aggregation():
    with pytest.raises(IncompatibleAssayContextError, match="not assay-compatible"):
        summarize_measurements(
            [
                measurement("m-1", 8.0, compatibility_key="ic50:cell-v1"),
                measurement("m-2", 9.0, compatibility_key="ic50:cell-v2"),
            ]
        )


def test_censored_and_missing_values_are_excluded_but_preserved():
    summary = summarize_measurements(
        [
            measurement("observed", 10.0),
            measurement("censored", 20.0, qualifier="<", upper_bound=20.0),
            measurement("missing", None, qualifier=None, missing_reason="not_run"),
        ]
    )

    assert summary.summary_value == pytest.approx(10.0)
    assert summary.eligible_measurement_count == 1
    assert summary.censored_measurement_count == 1
    assert summary.missing_measurement_count == 1
    assert summary.summary_state == "observed_with_censored_and_missing"
    assert summary.upper_bound == pytest.approx(20.0)
    assert summary.source_measurement_ids == ("censored", "missing", "observed")
    assert summary.missing_reasons == ("not_run",)


def test_persisted_summary_keeps_provenance_and_analysis_version(tmp_path):
    database_path = str(tmp_path / "measurement-summary.db")
    assert apply_migrations(database_path) == 16
    now = "2026-09-07T08:00:00+00:00"
    with transaction(database_path) as connection:
        connection.execute(
            "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("project-1", "Measurement fixture", now, now),
        )
        connection.execute(
            "INSERT INTO compounds (id, project_id, registration_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("compound-1", "project-1", "CMP-001", now, now),
        )
        connection.execute(
            """
            INSERT INTO assay_definitions
                (id, project_id, name, endpoint_code, canonical_unit, compatibility_key,
                 protocol_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("assay-1", "project-1", "Cell IC50", "IC50", "pIC50", "ic50:cell-v1", "v1", now),
        )
        connection.execute(
            "INSERT INTO assay_runs (id, assay_definition_id, created_at) VALUES (?, ?, ?)",
            ("run-1", "assay-1", now),
        )
        for measurement_id, value in (("m-1", 8.0), ("m-2", 10.0)):
            connection.execute(
                """
                INSERT INTO measurements
                    (id, compound_id, assay_run_id, replicate_group_id, replicate_type,
                     raw_value_text, value_numeric, unit_ucum, qualifier, canonical_value,
                     canonical_unit, source_row_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    measurement_id,
                    "compound-1",
                    "run-1",
                    measurement_id,
                    "biological",
                    str(value),
                    value,
                    "pIC50",
                    "=",
                    value,
                    "pIC50",
                    measurement_id,
                    now,
                ),
            )

    result = create_measurement_summary(
        database_path,
        ["m-1", "m-2"],
        analysis_version="measurement-summary-test-v1",
    )

    assert result["summary_value"] == pytest.approx(9.0)
    assert result["biological_replicate_count"] == 2
    assert result["source_measurement_ids"] == ["m-1", "m-2"]
    assert result["analysis_version"] == "measurement-summary-test-v1"
    with read_connection(database_path) as connection:
        row = connection.execute(
            "SELECT source_measurement_ids_json, analysis_version FROM measurement_summaries WHERE id = ?",
            (result["id"],),
        ).fetchone()
    assert json.loads(row["source_measurement_ids_json"]) == ["m-1", "m-2"]
    assert row["analysis_version"] == "measurement-summary-test-v1"
