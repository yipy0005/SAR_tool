"""Tool definition, permission tiers and helpers shared by the tool modules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .client import SarClient
from .config import Settings
from .links import Links
from .schema import ID_PATTERN

# Which SAR_MCP_MODE values may use each tier.
#   read     computes or looks things up; nothing is stored.
#   analyze  stores a derived, versioned analysis run (or model) in the project.
#   write    changes project data (new project, imported measurements).
TIERS = {"read": ("read-only", "analyze", "full"), "analyze": ("analyze", "full"), "write": ("full",)}


class ToolError(Exception):
    """A problem the model can act on (bad reference, missing prerequisite). Shown as a tool error."""


@dataclass(frozen=True)
class Context:
    settings: Settings
    client: SarClient


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    handler: Callable[[Context, dict], Any]
    properties: dict
    required: tuple = ()
    tier: str = "read"
    idempotent: bool = True

    def available_in(self, mode: str) -> bool:
        return mode in TIERS[self.tier]

    def input_schema(self) -> dict:
        schema: dict = {"type": "object", "properties": self.properties, "additionalProperties": False}
        if self.required:
            schema["required"] = list(self.required)
        return schema

    def descriptor(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema(),
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.tier == "read",
                "destructiveHint": False,  # nothing here deletes or overwrites; writes only add records
                "idempotentHint": self.idempotent and self.tier == "read",
                "openWorldHint": False,
            },
        }


def links_for(ctx: Context) -> Links:
    return Links(ctx.settings.link_origin)


def linked(data: dict, url: str | None, **related: str | None) -> dict:
    """Put the main link first so it is the first thing the model sees, then any related links."""
    extra = {name: link for name, link in related.items() if link}
    head: dict = {}
    if url:
        head["web_link"] = url
    if extra:
        head["related_links"] = extra
    return {**head, **data}


PROJECT_ID = {"type": "string", "pattern": ID_PATTERN, "description": "Project id, from sar_list_projects."}
COMPOUND_REF = {"type": "string", "minLength": 1, "maxLength": 100, "description": "Registration id (for example POS-001) or compound id."}
ENDPOINT_KEY = {"type": "string", "minLength": 1, "maxLength": 200, "description": "Endpoint key (compatibility_key) from sar_list_endpoints, for example IC50:import-v1."}
SMARTS_TEXT = {"type": "string", "minLength": 1, "maxLength": 500}


def limit_property(default: int, maximum: int) -> dict:
    return {"type": "integer", "minimum": 1, "maximum": maximum, "description": f"Maximum rows to return (default {default}, at most {maximum})."}


_SLIM_FIELDS = ("id", "registration_id", "preferred_name", "lifecycle_status", "canonical_smiles", "isomeric_smiles", "inchikey", "stereochemistry_status")


def slim_compound(record: dict) -> dict:
    return {key: record.get(key) for key in _SLIM_FIELDS if key in record}


def compound_index(ctx: Context, project_id: str, cap: int = 5000) -> tuple[list[dict], bool]:
    """Every compound in the project (drawings dropped), paging the search API. Second value: cut at ``cap``."""
    rows: list[dict] = []
    offset = 0
    while len(rows) < cap:
        page = ctx.client.get("/api/v1/search/compounds", project_id=project_id, q="", limit=200, offset=offset)
        got = page.get("results") or []
        rows.extend(slim_compound(item) for item in got)
        if not got or not page.get("has_more"):
            return rows, False
        offset += len(got)
    return rows[:cap], True


def resolve_compound(ctx: Context, project_id: str, reference: str) -> dict:
    """One compound by registration id or compound id; raises ToolError with near matches when absent."""
    page = ctx.client.get("/api/v1/search/compounds", project_id=project_id, q=reference, limit=50, offset=0)
    found = [slim_compound(item) for item in page.get("results") or []]
    wanted = reference.strip().lower()
    for item in found:
        if wanted in (str(item.get("id", "")).lower(), str(item.get("registration_id", "")).lower()):
            return item
    near = ", ".join(str(item.get("registration_id")) for item in found[:8])
    raise ToolError(f"No compound '{reference}' in this project." + (f" Similar: {near}." if near else " Use sar_search_compounds to browse."))


def resolve_many(ctx: Context, project_id: str, references: list[str]) -> list[dict]:
    rows, _cut = compound_index(ctx, project_id)
    lookup: dict[str, dict] = {}
    for row in rows:
        lookup[str(row.get("id", "")).lower()] = row
        lookup[str(row.get("registration_id", "")).lower()] = row
    missing = [item for item in references if item.strip().lower() not in lookup]
    if missing:
        raise ToolError(f"Not found in this project: {', '.join(missing[:10])}" + (" and more" if len(missing) > 10 else ""))
    return [lookup[item.strip().lower()] for item in references]


def latest_summaries(summaries: list[dict]) -> list[dict]:
    """The API lists newest first; keep the newest summary per compound and endpoint."""
    seen: set[tuple] = set()
    latest = []
    for item in summaries:
        key = (item.get("compound_id"), item.get("compatibility_key"))
        if key not in seen:
            seen.add(key)
            latest.append(item)
    return latest
