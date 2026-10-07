"""Read-only lookups: status, projects, compounds, measurements, result summaries, export overview."""
from __future__ import annotations

from collections import Counter, defaultdict

from . import SERVER_NAME, __version__
from .client import SarAuthError, SarConnectionError, seg
from .toolkit import (
    COMPOUND_REF, ENDPOINT_KEY, PROJECT_ID, Context, Tool, ToolError, compound_index, latest_summaries, limit_property,
    linked, links_for, resolve_compound, slim_compound,
)


def status(ctx: Context, _args: dict) -> dict:
    s = ctx.settings
    out = {
        "web_link": links_for(ctx).root(),
        "server": {"name": SERVER_NAME, "version": __version__, "mode": s.mode, "base_url": s.base_url},
        "projects_restricted_to": sorted(s.project_ids) or None,
    }
    try:
        out["app"] = ctx.client.get("/api/v1/readyz")
        ctx.client.ensure_session()
        out["sign_in_required"] = bool(ctx.client._auth_required)
        out["signed_in_as"] = ctx.client.user_email or None
    except (SarConnectionError, SarAuthError) as error:
        out["problem"] = str(error)
    return out


def list_projects(ctx: Context, _args: dict) -> dict:
    projects = ctx.client.get("/api/v1/projects").get("projects") or []
    keep = ("id", "name", "description", "status", "created_at", "role")
    rows = [{key: p.get(key) for key in keep if key in p} for p in projects]
    if ctx.settings.project_ids:
        rows = [row for row in rows if row.get("id") in ctx.settings.project_ids]
    links = links_for(ctx)
    rows = [{**row, "web_link": links.page("overview", row["id"])} for row in rows if row.get("id")]
    return {"count": len(rows), "projects": rows}


def search_compounds(ctx: Context, args: dict) -> dict:
    page = ctx.client.get(
        "/api/v1/search/compounds", project_id=args["project_id"], q=args.get("q", ""),
        limit=args.get("limit", 50), offset=args.get("offset", 0),
    )
    rows = [slim_compound(item) for item in page.get("results") or []]
    data = {"total_matches": page.get("count"), "offset": page.get("offset"), "returned": len(rows),
            "has_more": page.get("has_more"), "compounds": rows}
    return linked(data, links_for(ctx).page("overview", args["project_id"]))


def _measurements(ctx: Context, project_id: str) -> list[dict]:
    return ctx.client.get("/api/v1/measurements", project_id=project_id).get("results") or []


def get_compound(ctx: Context, args: dict) -> dict:
    project_id = args["project_id"]
    compound = resolve_compound(ctx, project_id, args["compound"])
    measurements = [m for m in _measurements(ctx, project_id) if m.get("compound_id") == compound["id"]]
    summaries = ctx.client.get("/api/v1/measurement-summaries", project_id=project_id).get("summaries") or []
    mine = latest_summaries([s for s in summaries if s.get("compound_id") == compound["id"]])
    links = links_for(ctx)
    data = {"compound": compound, "measurement_count": len(measurements), "measurements": measurements[:200],
            "latest_result_summaries": mine}
    return linked(data, links.page("evidence", project_id),
                  sar_series_around_compound=links.page("sar", project_id, reference=compound["id"]),
                  pattern_explorer_compared_with_compound=links.page("analysis", project_id, compare=compound["id"], fragment="pattern-explorer"))


def list_endpoints(ctx: Context, args: dict) -> dict:
    summaries = latest_summaries(ctx.client.get("/api/v1/measurement-summaries", project_id=args["project_id"]).get("summaries") or [])
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in summaries:
        groups[str(item.get("compatibility_key"))].append(item)
    endpoints = [
        {"endpoint_key": key, "unit": items[0].get("canonical_unit"), "compounds": len({i.get("compound_id") for i in items}),
         "states": dict(Counter(str(i.get("summary_state")) for i in items))}
        for key, items in sorted(groups.items())
    ]
    note = "" if endpoints else "No result summaries yet. Run sar_run_analysis with analysis=measurement_summaries first."
    return linked({"count": len(endpoints), "endpoints": endpoints, "note": note}, links_for(ctx).page("summaries", args["project_id"]))


def list_measurements(ctx: Context, args: dict) -> dict:
    project_id = args["project_id"]
    rows = _measurements(ctx, project_id)
    wanted = args.get("compound", "").strip().lower()
    if wanted:
        rows = [r for r in rows if wanted in (str(r.get("registration_id", "")).lower(), str(r.get("compound_id", "")).lower())]
    text = args.get("assay", "").strip().lower()
    if text:
        rows = [r for r in rows if text in f"{r.get('assay_name', '')} {r.get('endpoint_code', '')}".lower()]
    limit = args.get("limit", 200)
    return linked({"total_matches": len(rows), "returned": min(len(rows), limit), "measurements": rows[:limit]}, links_for(ctx).page("evidence", project_id))


def list_result_summaries(ctx: Context, args: dict) -> dict:
    project_id = args["project_id"]
    summaries = ctx.client.get("/api/v1/measurement-summaries", project_id=project_id).get("summaries") or []
    if args.get("latest_only", True):
        summaries = latest_summaries(summaries)
    if args.get("endpoint_key"):
        summaries = [s for s in summaries if s.get("compatibility_key") == args["endpoint_key"]]
    names = {c["id"]: c.get("registration_id") for c in compound_index(ctx, project_id)[0]}
    if args.get("compound"):
        wanted = args["compound"].strip().lower()
        summaries = [s for s in summaries if wanted in (str(names.get(s.get("compound_id"), "")).lower(), str(s.get("compound_id", "")).lower())]
    rows = [{"registration_id": names.get(s.get("compound_id")), **s} for s in summaries]
    limit = args.get("limit", 300)
    data = {"total_matches": len(rows), "returned": min(len(rows), limit), "summaries": rows[:limit],
            "reading_note": "summary_qualifier other than '=' marks a censored value (for example '>' or '<'); it is not an exact potency."}
    return linked(data, links_for(ctx).page("summaries", project_id))


def export_overview(ctx: Context, args: dict) -> dict:
    bundle = ctx.client.get(f"/api/v1/exports/project/{seg(args['project_id'])}", format="json")
    section = args.get("section")
    download = links_for(ctx).export_csv(args["project_id"])
    if not section:
        counts = {k: (len(v) if isinstance(v, list) else None) for k, v in bundle.items() if isinstance(v, list)}
        return {"download_link": download, "export_id": bundle.get("export_id"), "created_at": bundle.get("created_at"),
                "row_count": bundle.get("row_count"), "sections": counts, "provenance_policy": bundle.get("provenance_policy"),
                "download_note": "Opening download_link in a browser signed in to the workbench downloads a CSV and records another export in the audit trail."}
    if section not in bundle or not isinstance(bundle[section], list):
        raise ToolError(f"Unknown section '{section}'. Call without a section to list them.")
    rows = bundle[section]
    limit = args.get("limit", 50)
    return {"section": section, "total": len(rows), "returned": min(len(rows), limit), "rows": rows[:limit]}


_P = {"project_id": PROJECT_ID}
TOOLS = [
    Tool("sar_status", "Workbench status",
         "Check that the SAR Workbench is reachable, who the server is signed in as, and which permission mode is active. Call this first if anything fails.",
         status, {}),
    Tool("sar_list_projects", "List projects",
         "List the projects you can access. Every other tool needs a project_id from here.",
         list_projects, {}),
    Tool("sar_search_compounds", "Search compounds",
         "Find compounds in a project by registration id, name or structure text (a prefix or substring; not a substructure search). Returns ids and SMILES, without drawings.",
         search_compounds,
         {**_P, "q": {"type": "string", "maxLength": 200, "description": "Text to match; empty lists all compounds."},
          "limit": limit_property(50, 200), "offset": {"type": "integer", "minimum": 0, "maximum": 1000000}},
         ("project_id",)),
    Tool("sar_get_compound", "Get a compound",
         "One compound with its raw measurements (value, unit, qualifier such as > or <, assay QC) and latest result summaries. Qualified values are thresholds, not exact potencies.",
         get_compound, {**_P, "compound": COMPOUND_REF}, ("project_id", "compound")),
    Tool("sar_list_endpoints", "List endpoints",
         "The assay endpoints that have result summaries in a project (endpoint_key, unit, how many compounds, exact versus censored). Use the endpoint_key with other tools.",
         list_endpoints, _P, ("project_id",)),
    Tool("sar_list_measurements", "List raw measurements",
         "Raw imported measurements with qualifiers, units, replicate and QC fields. Filter by compound (registration id) and/or assay text.",
         list_measurements,
         {**_P, "compound": COMPOUND_REF, "assay": {"type": "string", "maxLength": 100, "description": "Substring of the assay name or endpoint code."},
          "limit": limit_property(200, 1000)}, ("project_id",)),
    Tool("sar_list_result_summaries", "List result summaries",
         "Per-compound summarised results (one value per compound and endpoint, with state and qualifier). Needs result summaries to exist; see sar_list_endpoints.",
         list_result_summaries,
         {**_P, "endpoint_key": ENDPOINT_KEY, "compound": COMPOUND_REF,
          "latest_only": {"type": "boolean", "description": "Only the newest summary per compound and endpoint (default true)."},
          "limit": limit_property(300, 2000)}, ("project_id",)),
    Tool("sar_export_overview", "Project export overview",
         "Section sizes of the project's evidence export, or the first rows of one section (for example 'claims' or 'analysis_runs'). The export is recorded in the app's audit trail.",
         export_overview,
         {**_P, "section": {"type": "string", "maxLength": 60}, "limit": limit_property(50, 500)}, ("project_id",)),
]
