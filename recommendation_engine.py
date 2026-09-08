from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from chemistry import StructureValidationError, standardize_structure
from database import read_connection, transaction


CATEGORIES = frozenset({"exploit", "test", "fill", "resolve", "explore", "rescue", "challenge"})
STATUSES = frozenset({"proposed", "selected", "tested", "rejected"})
FEASIBILITY = frozenset({"tractable", "review", "unreviewed", "rejected"})
RANKING_VERSION = "transparent-mpo-information-gain-v1"


class RecommendationValidationError(ValueError):
    pass


@dataclass(frozen=True)
class DesignCandidate:
    category: str
    title: str
    rationale: str
    hypothesis: str
    expected_outcome: str
    uncertainty: str
    evidence_ids: tuple[str, ...]
    novelty_score: float
    feasibility_status: str
    feasibility_reasons: tuple[str, ...]
    information_gain_score: float
    objective_alignment_score: float
    parent_compound_id: str | None = None
    structure_smiles: str | None = None
    status: str = "proposed"
    candidate_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category,
            "title": self.title,
            "rationale": self.rationale,
            "hypothesis": self.hypothesis,
            "expected_outcome": self.expected_outcome,
            "uncertainty": self.uncertainty,
            "evidence_ids": list(self.evidence_ids),
            "novelty_score": self.novelty_score,
            "feasibility_status": self.feasibility_status,
            "feasibility_reasons": list(self.feasibility_reasons),
            "information_gain_score": self.information_gain_score,
            "objective_alignment_score": self.objective_alignment_score,
            "parent_compound_id": self.parent_compound_id,
            "structure_smiles": self.structure_smiles,
            "status": self.status,
            "data_origin": "curated",
        }


def _score(value: Any, field: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise RecommendationValidationError(f"{field} must be numeric") from exc
    if not 0 <= numeric <= 1:
        raise RecommendationValidationError(f"{field} must be between 0 and 1")
    return numeric


def validate_candidate(candidate: Mapping[str, Any]) -> DesignCandidate:
    category = str(candidate.get("category", "")).strip().lower()
    if category not in CATEGORIES:
        raise RecommendationValidationError(f"Unsupported design category: {category!r}")
    status = str(candidate.get("status", "proposed")).strip().lower()
    if status not in STATUSES:
        raise RecommendationValidationError("A recommendation cannot be marked experimentally confirmed")
    evidence_ids = tuple(sorted({str(item).strip() for item in candidate.get("evidence_ids", []) if str(item).strip()}))
    if not evidence_ids:
        raise RecommendationValidationError("Every candidate needs at least one evidence identifier")
    required_text = ("title", "rationale", "hypothesis", "expected_outcome", "uncertainty")
    for field in required_text:
        if len(str(candidate.get(field, "")).strip()) < 8:
            raise RecommendationValidationError(f"{field} must contain at least eight characters")
    feasibility = str(candidate.get("feasibility_status", "unreviewed")).strip().lower()
    if feasibility not in FEASIBILITY:
        raise RecommendationValidationError(f"Unsupported feasibility status: {feasibility!r}")
    structure_smiles = candidate.get("structure_smiles")
    if structure_smiles:
        try:
            standardized = standardize_structure(str(structure_smiles))
            structure_smiles = standardized.isomeric_smiles
        except StructureValidationError as exc:
            raise RecommendationValidationError(f"Candidate structure is invalid: {exc}") from exc
    return DesignCandidate(
        category=category,
        title=str(candidate["title"]).strip(),
        rationale=str(candidate["rationale"]).strip(),
        hypothesis=str(candidate["hypothesis"]).strip(),
        expected_outcome=str(candidate["expected_outcome"]).strip(),
        uncertainty=str(candidate["uncertainty"]).strip(),
        evidence_ids=evidence_ids,
        novelty_score=_score(candidate.get("novelty_score", 0.5), "novelty_score"),
        feasibility_status=feasibility,
        feasibility_reasons=tuple(str(item).strip() for item in candidate.get("feasibility_reasons", []) if str(item).strip()),
        information_gain_score=_score(candidate.get("information_gain_score", 0.5), "information_gain_score"),
        objective_alignment_score=_score(candidate.get("objective_alignment_score", 0.5), "objective_alignment_score"),
        parent_compound_id=str(candidate["parent_compound_id"]) if candidate.get("parent_compound_id") else None,
        structure_smiles=structure_smiles,
        status=status,
        candidate_id=str(candidate["candidate_id"]) if candidate.get("candidate_id") else None,
    )


def rank_candidates(candidates: Iterable[Mapping[str, Any] | DesignCandidate]) -> list[dict[str, Any]]:
    normalized = [candidate if isinstance(candidate, DesignCandidate) else validate_candidate(candidate) for candidate in candidates]
    ranked: list[dict[str, Any]] = []
    feasibility_weight = {"tractable": 1.0, "review": 0.65, "unreviewed": 0.45, "rejected": 0.0}
    for candidate in normalized:
        support = min(len(candidate.evidence_ids) / 5.0, 1.0)
        feasibility = feasibility_weight[candidate.feasibility_status]
        score_components = {
            "objective_alignment": round(candidate.objective_alignment_score * 0.35, 6),
            "information_gain": round(candidate.information_gain_score * 0.25, 6),
            "feasibility": round(feasibility * 0.20, 6),
            "evidence_support": round(support * 0.15, 6),
            "novelty": round(candidate.novelty_score * 0.05, 6),
        }
        item = candidate.as_dict()
        item["score_components"] = score_components
        item["ranking_score"] = round(sum(score_components.values()), 6)
        item["ranking_version"] = RANKING_VERSION
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["ranking_score"], item["category"], item["title"]))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


def _project_evidence_ids(connection: Any, project_id: str) -> set[str]:
    queries = (
        """
        SELECT m.id
        FROM measurements AS m
        JOIN compounds AS c ON c.id = m.compound_id
        WHERE c.project_id = ?
        """,
        """
        SELECT ms.id
        FROM measurement_summaries AS ms
        JOIN compounds AS c ON c.id = ms.compound_id
        WHERE c.project_id = ?
        """,
        "SELECT id FROM analysis_runs WHERE project_id = ?",
        """
        SELECT mp.id
        FROM mmp_pairs AS mp
        JOIN analysis_runs AS ar ON ar.id = mp.analysis_run_id
        WHERE ar.project_id = ?
        """,
        """
        SELECT ra.id
        FROM rgroup_assignments AS ra
        JOIN analysis_runs AS ar ON ar.id = ra.analysis_run_id
        WHERE ar.project_id = ?
        """,
        """
        SELECT ac.id
        FROM activity_cliffs AS ac
        JOIN analysis_runs AS ar ON ar.id = ac.analysis_run_id
        WHERE ar.project_id = ?
        """,
        """
        SELECT so.id
        FROM selectivity_observations AS so
        JOIN analysis_runs AS ar ON ar.id = so.analysis_run_id
        WHERE ar.project_id = ?
        """,
        """
        SELECT tobs.id
        FROM translation_observations AS tobs
        JOIN analysis_runs AS ar ON ar.id = tobs.analysis_run_id
        WHERE ar.project_id = ?
        """,
        """
        SELECT ao.id
        FROM adme_observations AS ao
        JOIN analysis_runs AS ar ON ar.id = ao.analysis_run_id
        WHERE ar.project_id = ?
        """,
        """
        SELECT po.id
        FROM pareto_observations AS po
        JOIN analysis_runs AS ar ON ar.id = po.analysis_run_id
        WHERE ar.project_id = ?
        """,
        """
        SELECT co.id
        FROM contradiction_observations AS co
        JOIN analysis_runs AS ar ON ar.id = co.analysis_run_id
        WHERE ar.project_id = ?
        """,
        "SELECT id FROM sar_claims WHERE project_id = ?",
        """
        SELECT ce.id
        FROM claim_evidence AS ce
        JOIN sar_claims AS sc ON sc.id = ce.claim_id
        WHERE sc.project_id = ?
        """,
    )
    result: set[str] = set()
    for query in queries:
        result.update(str(row["id"]) for row in connection.execute(query, (project_id,)))
    return result


def persist_candidates(
    database_path: str,
    project_id: str,
    candidates: Iterable[Mapping[str, Any] | DesignCandidate],
    *,
    actor_user_id: str | None = None,
) -> list[dict[str, Any]]:
    ranked = rank_candidates(candidates)
    candidate_ids = [str(item["candidate_id"]) for item in ranked if item.get("candidate_id")]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise RecommendationValidationError("Candidate identifiers must be unique")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with transaction(database_path) as connection:
        project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            raise RecommendationValidationError("The target project does not exist")
        allowed_evidence_ids = _project_evidence_ids(connection, project_id)
        for item in ranked:
            parent_compound_id = item.get("parent_compound_id")
            if parent_compound_id:
                parent = connection.execute(
                    "SELECT id FROM compounds WHERE id = ? AND project_id = ?",
                    (parent_compound_id, project_id),
                ).fetchone()
                if parent is None:
                    raise RecommendationValidationError("Parent compounds must belong to the target project")
            if set(item["evidence_ids"]) - allowed_evidence_ids:
                raise RecommendationValidationError(
                    "Every evidence identifier must reference persisted evidence in the target project"
                )
        for item in ranked:
            candidate_id = item.get("candidate_id") or f"design_{uuid.uuid4().hex}"
            item["candidate_id"] = candidate_id
            connection.execute(
                """
                INSERT INTO design_candidates
                    (id, project_id, category, title, parent_compound_id, structure_smiles,
                     hypothesis, rationale, evidence_ids_json, expected_outcome, uncertainty,
                     novelty_score, feasibility_status, feasibility_reasons_json,
                     information_gain_score, objective_alignment_score, status, ranking_score,
                     ranking_components_json, ranking_version, data_origin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'curated', ?, ?)
                """,
                (
                    candidate_id, project_id, item["category"], item["title"], item["parent_compound_id"], item["structure_smiles"],
                    item["hypothesis"], item["rationale"], json.dumps(item["evidence_ids"]), item["expected_outcome"], item["uncertainty"],
                    item["novelty_score"], item["feasibility_status"], json.dumps(item["feasibility_reasons"]), item["information_gain_score"],
                    item["objective_alignment_score"], item["status"], item["ranking_score"], json.dumps(item["score_components"], sort_keys=True),
                    item["ranking_version"], now, now,
                ),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'design_create', 'design_candidates', ?, 'success', ?, ?)
            """,
            (
                f"audit_{uuid.uuid4().hex}",
                actor_user_id,
                project_id,
                project_id,
                json.dumps({"candidate_count": len(ranked), "ranking_version": ranked[0]["ranking_version"] if ranked else None}, sort_keys=True),
                now,
            ),
        )
    return ranked


def list_candidates(database_path: str, project_id: str) -> list[dict[str, Any]]:
    with read_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM design_candidates WHERE project_id = ? ORDER BY ranking_score DESC, created_at DESC",
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["evidence_ids"] = json.loads(item.pop("evidence_ids_json"))
        item["feasibility_reasons"] = json.loads(item.pop("feasibility_reasons_json"))
        item["score_components"] = json.loads(item.pop("ranking_components_json"))
        item["data_origin"] = "curated"
        result.append(item)
    return result


GENERATED_RECOMMENDATION_VERSION = "evidence-gap-recommendation-v1"
EVIDENCE_CLASS = "heuristic_evidence_gap"
GENERATED_RECOMMENDATION_STATUSES = frozenset({"generated_review", "approved", "rejected"})


def _generated_item(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["evidence_ids"] = json.loads(item.pop("evidence_ids_json"))
    item["score_components"] = json.loads(item.pop("score_components_json"))
    item["data_origin"] = "generated"
    item["review_required"] = item["status"] == "generated_review"
    item["experimentally_confirmed"] = False
    return item


def _recommendation_text(registration_id: str, target_key: str, gap_type: str, score: float) -> dict[str, str]:
    gap_labels = {
        "missing_context": "missing assay context",
        "censored_context": "censored assay context",
        "contradictory_context": "unreconciled assay contradiction",
        "replicate_uncertainty": "replicate or dispersion uncertainty",
    }
    label = gap_labels.get(gap_type, "evidence gap")
    return {
        "title": f"Resolve {registration_id} {target_key} evidence gap",
        "rationale": (
            f"A persisted heuristic information-gap observation prioritizes {label} for "
            f"{registration_id} in {target_key} with score {score:.3f}."
        ),
        "hypothesis": (
            f"A compatible, provenance-linked {target_key} measurement for {registration_id} "
            "will reduce the recorded evidence uncertainty."
        ),
        "expected_outcome": (
            "Obtain an assay-compatible observation while preserving qualifiers, censoring, "
            "missingness, replicate metadata, and source provenance."
        ),
        "uncertainty": (
            "Generated from a transparent heuristic evidence-gap score; it is not a predictive "
            "efficacy claim and requires qualified review before experimental use."
        ),
    }


def generate_recommendations(
    database_path: str,
    project_id: str,
    information_gain_run_id: str,
    *,
    limit: int = 32,
    actor_user_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise RecommendationValidationError("limit must be an integer") from exc
    if not 1 <= limit <= 128:
        raise RecommendationValidationError("limit must be between 1 and 128")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with transaction(database_path) as connection:
        run = connection.execute(
            """
            SELECT ar.id, ar.project_id
            FROM analysis_runs AS ar
            WHERE ar.id = ? AND ar.analysis_type = 'information_gain' AND ar.project_id = ?
            """,
            (information_gain_run_id, project_id),
        ).fetchone()
        if run is None:
            raise RecommendationValidationError("The information-gain run does not belong to the target project")
        observations = connection.execute(
            """
            SELECT igo.*, c.registration_id
            FROM information_gain_observations AS igo
            JOIN compounds AS c ON c.id = igo.compound_id
            WHERE igo.analysis_run_id = ? AND igo.status = 'open'
            ORDER BY igo.priority_score DESC, c.registration_id, igo.target_key
            LIMIT ?
            """,
            (information_gain_run_id, limit),
        ).fetchall()
        generated_ids: list[str] = []
        for row in observations:
            observation = dict(row)
            existing = connection.execute(
                """
                SELECT id FROM generated_recommendations
                WHERE information_gain_observation_id = ? AND generated_algorithm_version = ?
                """,
                (observation["id"], GENERATED_RECOMMENDATION_VERSION),
            ).fetchone()
            if existing is not None:
                generated_ids.append(str(existing["id"]))
                continue
            score = float(observation["priority_score"])
            evidence_ids = sorted({str(observation["id"]), *json.loads(observation["evidence_ids_json"])})
            text = _recommendation_text(
                str(observation["registration_id"]),
                str(observation["target_key"]),
                str(observation["gap_type"]),
                score,
            )
            recommendation_id = f"generated_{uuid.uuid4().hex}"
            score_components = {
                "information_gap_score": score,
                "source_score_components": json.loads(observation["score_components_json"]),
                "generation_policy": "deterministic_evidence_gap_template",
            }
            connection.execute(
                """
                INSERT INTO generated_recommendations
                    (id, project_id, information_gain_observation_id, compound_id,
                     recommendation_type, title, rationale, hypothesis, expected_outcome,
                     uncertainty, evidence_ids_json, score_components_json,
                     information_gain_score, evidence_class, status,
                     generated_algorithm_version, created_at)
                VALUES (?, ?, ?, ?, 'evidence_gap_experiment', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'generated_review', ?, ?)
                """,
                (
                    recommendation_id, project_id, observation["id"], observation["compound_id"],
                    text["title"], text["rationale"], text["hypothesis"], text["expected_outcome"],
                    text["uncertainty"], json.dumps(evidence_ids), json.dumps(score_components, sort_keys=True),
                    score, EVIDENCE_CLASS, GENERATED_RECOMMENDATION_VERSION, now,
                ),
            )
            generated_ids.append(recommendation_id)
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'recommendation_generate', 'generated_recommendations', ?, 'success', ?, ?)
            """,
            (
                f"audit_{uuid.uuid4().hex}", actor_user_id, project_id, information_gain_run_id,
                json.dumps({"generated_count": len(generated_ids), "algorithm_version": GENERATED_RECOMMENDATION_VERSION}, sort_keys=True), now,
            ),
        )
    return list_generated_recommendations(database_path, project_id, ids=generated_ids)


def list_generated_recommendations(
    database_path: str,
    project_id: str,
    *,
    ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    with read_connection(database_path) as connection:
        parameters: list[Any] = [project_id]
        query = "SELECT * FROM generated_recommendations WHERE project_id = ?"
        if ids is not None:
            selected_ids = sorted({str(item) for item in ids if str(item).strip()})
            if not selected_ids:
                return []
            placeholders = ",".join("?" for _ in selected_ids)
            query += f" AND id IN ({placeholders})"
            parameters.extend(selected_ids)
        query += " ORDER BY information_gain_score DESC, created_at DESC, id"
        rows = connection.execute(query, parameters).fetchall()
    return [_generated_item(row) for row in rows]


def review_generated_recommendation(
    database_path: str,
    recommendation_id: str,
    status: str,
    *,
    review_note: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    status = str(status).strip().lower()
    if status not in {"approved", "rejected"}:
        raise RecommendationValidationError("Review status must be approved or rejected")
    review_note = str(review_note).strip()
    if len(review_note) < 8:
        raise RecommendationValidationError("A review note must contain at least eight characters")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with transaction(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM generated_recommendations WHERE id = ?",
            (recommendation_id,),
        ).fetchone()
        if row is None:
            raise RecommendationValidationError("Generated recommendation does not exist")
        if row["status"] != "generated_review":
            raise RecommendationValidationError("Only generated recommendations awaiting review can be reviewed")
        connection.execute(
            """
            UPDATE generated_recommendations
            SET status = ?, review_note = ?, reviewed_by = ?, reviewed_at = ?
            WHERE id = ? AND status = 'generated_review'
            """,
            (status, review_note, actor_user_id, now, recommendation_id),
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, 'recommendation_review', 'generated_recommendation', ?, 'success', ?, ?)
            """,
            (
                f"audit_{uuid.uuid4().hex}", actor_user_id, row["project_id"], recommendation_id,
                json.dumps({"status": status, "review_note": review_note}, sort_keys=True), now,
            ),
        )
    reviewed = list_generated_recommendations(database_path, str(row["project_id"]), ids=[recommendation_id])
    return reviewed[0] if reviewed else {}
