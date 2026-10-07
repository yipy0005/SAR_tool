"""Links into the web app's own pages, so an answer can be opened and seen there.

Links carry only project, compound and run ids plus search text. They never carry a token or a
password: opening one needs your normal browser sign-in, so project membership still applies.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

# The workspace pages and what each one shows.
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

    def run(self, project_id: str, run_id: str, *, page: str = "analysis") -> str | None:
        """A page pinned to one stored run, or None when the id is not a usable run id."""
        return self.page(page, project_id, run=run_id) if RUN_ID.match(run_id or "") else None

    def search(self, project_id: str, text: str, text_format: str = "auto") -> str:
        """Find patterns with the Find by structure dialog open and this search already run."""
        return self.page("analysis", project_id, find=text, find_format=None if text_format == "auto" else text_format, fragment="pattern-explorer")


def run_id_of(result: Any) -> str:
    """Analysis endpoints name the run inconsistently: analysis_run_id for most, id for some."""
    if isinstance(result, dict):
        return str(result.get("analysis_run_id") or result.get("id") or "")
    return ""
