"""Links into the web app's own pages."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

PAGES = {
    "overview": "Start: the SAR table of every compound and endpoint",
    "evidence": "Check results: the uploaded source data and how each structure was recognised",
    "summaries": "Build summaries: grouped repeat results",
    "explore": "Compare: two assays side by side",
    "sar": "SAR series: compounds grouped by scaffold",
    "analysis": "Find patterns: calculated views, the pattern explorer and prediction models",
    "designs": "Choose next: recommendations awaiting review and design candidates",
}
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _safe_state(value: str, *, max_length: int = 200) -> str:
    text = str(value or "")
    if not text or len(text) > max_length or any(character in text for character in "\\/?#%\r\n"):
        raise ValueError("link state contains an unsafe value")
    return text


class Links:
    def __init__(self, origin: str) -> None:
        self.origin = origin.rstrip("/")

    def root(self) -> str:
        return self.origin + "/"

    def page(self, page: str, project_id: str, *, fragment: str = "", **params: Any) -> str:
        if page not in PAGES:
            raise ValueError(f"unknown page {page!r}")
        query = {"project_id": project_id, **{key: value for key, value in params.items() if value not in (None, "")}}
        url = f"{self.origin}/workspace/{page}?{urlencode(query, quote_via=quote)}"
        return url + (f"#{quote(fragment, safe='-_')}" if fragment else "")

    def export_csv(self, project_id: str) -> str:
        return f"{self.origin}/api/v1/exports/project/{quote(project_id, safe='')}?format=csv"

    def compare_endpoint_id(self, endpoint_key: str | None, unit: str | None) -> str | None:
        """Build the endpoint id consumed by production_explorer.js, or omit it when ambiguous."""
        if not endpoint_key or not unit:
            return None
        try:
            key = _safe_state(endpoint_key)
            canonical_unit = _safe_state(unit, max_length=100)
        except ValueError:
            return None
        if "|" in key or "|" in canonical_unit:
            return None
        return f"summary|{key}|{canonical_unit}"

    def explore_compare_result(self, project_id: str, result: dict[str, Any], *, stage: str = "compare", coverage: str = "all", q: str = "") -> str:
        """Link a comparison result using browser endpoint ids only when units are unique."""
        endpoint_a = result.get("endpoint_a_metadata") or {}
        endpoint_b = result.get("endpoint_b_metadata") or {}
        endpoint_a_id = self.compare_endpoint_id(
            endpoint_a.get("endpoint_key"),
            endpoint_a.get("canonical_unit") if endpoint_a.get("unit_consistent") else None,
        )
        endpoint_b_key = result.get("endpoint_b")
        endpoint_b_id = self.compare_endpoint_id(
            endpoint_b.get("endpoint_key"),
            endpoint_b.get("canonical_unit") if endpoint_b.get("unit_consistent") else None,
        ) if endpoint_b_key and endpoint_b_key != "none" else "none"
        if endpoint_a_id is None or (endpoint_b_key and endpoint_b_key != "none" and endpoint_b_id is None):
            return self.page(
                "explore", _safe_state(project_id, max_length=128), stage=stage, coverage=coverage, q=str(q or "")[:200],
            )
        return self.explore_compare(
            project_id, endpoint_a_id, endpoint_b_id, stage=stage, coverage=coverage, q=q,
        )

    def staged_import_review(self, project_id: str, import_id: str) -> str:
        return self.page("evidence", _safe_state(project_id, max_length=128), stage="import_review", import_id=_safe_state(import_id, max_length=128))

    def prodrug(self, project_id: str, endpoint_key: str | None = None, relationship_id: str | None = None, comparison_run_id: str | None = None) -> str:
        project_id = _safe_state(project_id, max_length=128)
        for value in (endpoint_key, relationship_id, comparison_run_id):
            if value is not None:
                _safe_state(value)
        return self.page("sar", project_id, sar_mode="prodrug", endpoint_key=endpoint_key, relationship_id=relationship_id, comparison_run_id=comparison_run_id)

    def evidence(self, project_id: str, compound_id: str | None = None, measurement_id: str | None = None, summary_id: str | None = None) -> str:
        project_id = _safe_state(project_id, max_length=128)
        for value in (compound_id, measurement_id, summary_id):
            if value is not None:
                _safe_state(value)
        return self.page("evidence", project_id, compound_id=compound_id, measurement_id=measurement_id, summary_id=summary_id)

    def explore_compare(self, project_id: str, endpoint_a: str, endpoint_b: str | None = None, stage: str = "compare", coverage: str = "all", q: str = "", selected_compound_id: str | None = None) -> str:
        if coverage not in {"all", "endpoint-a", "both", "review"} or stage not in {"import", "configure", "filter", "compare", "evidence"}:
            raise ValueError("unsupported compare state")
        _safe_state(endpoint_a)
        if endpoint_b is not None:
            _safe_state(endpoint_b)
        if selected_compound_id is not None:
            _safe_state(selected_compound_id, max_length=128)
        return self.page("explore", _safe_state(project_id, max_length=128), endpoint_a=endpoint_a, endpoint_b=endpoint_b or "none", stage=stage, coverage=coverage, q=str(q or "")[:200], selected=selected_compound_id)

    def run(self, project_id: str, run_id: str, *, page: str = "analysis") -> str | None:
        return self.page(page, project_id, run=run_id) if RUN_ID.match(run_id or "") else None

    def search(self, project_id: str, text: str, text_format: str = "auto") -> str:
        return self.page("analysis", project_id, find=text, find_format=None if text_format == "auto" else text_format, fragment="pattern-explorer")


def run_id_of(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("analysis_run_id") or result.get("id") or "")
    return ""
