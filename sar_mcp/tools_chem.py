"""Structure tools, series discovery, saved records and stored analysis results (all read-only)."""
from __future__ import annotations

from .client import seg
from .links import PAGES, run_id_of
from .toolkit import (
    COMPOUND_REF, ENDPOINT_KEY, PROJECT_ID, Context, Tool, ToolError, limit_property, linked, links_for, resolve_compound,
)

FORMAT = {"type": "string", "enum": ["auto", "smiles", "smarts"], "description": "How to read the text. 'auto' treats it as SMILES unless it only makes sense as SMARTS."}
STANDARDIZE_KEEP = ("canonical_smiles", "isomeric_smiles", "inchikey", "parent_canonical_smiles", "parent_inchikey",
                    "component_count", "stereochemistry_status", "standardization_profile", "warnings")


def standardize(ctx: Context, args: dict) -> dict:
    result = ctx.client.post("/api/v1/structure/standardize", {"structure": args["structure"], "input_format": args.get("input_format", "smiles")})
    data = result.get("result") or {}
    return {key: data.get(key) for key in STANDARDIZE_KEEP if key in data} | {"data_origin": result.get("data_origin")}


def substructure(ctx: Context, args: dict) -> dict:
    body = {"project_id": args["project_id"], "query": args["query"], "query_format": args.get("query_format", "auto"), "limit": 1}
    found = ctx.client.post("/api/v1/search/substructure", body)
    rows = [{"compound_id": r.get("compound_id"), "registration_id": r.get("registration_id"), "match_count": r.get("match_count")}
            for r in found.get("results") or []]
    limit = args.get("limit", 100)
    data = {"query": found.get("query"), "counts": found.get("counts"), "complete": found.get("complete"),
            "stereo_compared": found.get("stereo_compared"), "matches": rows[:limit], "truncated": len(rows) > limit}
    link = links_for(ctx).search(args["project_id"], args["query"], args.get("query_format", "auto"))
    return linked(data, link)


def core_from_structure(ctx: Context, args: dict) -> dict:
    found = ctx.client.post("/api/v1/structure/core", {"query": args["query"], "query_format": args.get("query_format", "auto")})
    return {**(found.get("result") or {}), "use": "Pass core_smarts as scaffold_smarts to sar_run_analysis (rgroup or pharmacophore_rgroup)."}


def propose_substituent(ctx: Context, args: dict) -> dict:
    if not args.get("compound") and not args.get("smiles"):
        raise ToolError("Provide compound (registration id) or smiles to edit.")
    project_id = args["project_id"]
    compound_id = resolve_compound(ctx, project_id, args["compound"])["id"] if args.get("compound") else ""
    body = {"project_id": project_id, "compound_id": compound_id, "smiles": args.get("smiles", ""),
            "site": args["site"], "replacement": args["replacement"]}
    result = ctx.client.post("/api/v1/structure/replace-substituent", body)
    data = result.get("result") or {}
    return {"proposed": {key: data.get(key) for key in STANDARDIZE_KEEP if key in data},
            "note": "A proposal only: nothing was saved, and it is not a property, activity or synthesis claim."}


def discover_series(ctx: Context, args: dict) -> dict:
    mode = args.get("mode", "discover")
    if mode == "selected" and not args.get("compound"):
        raise ToolError("mode=selected needs compound (registration id).")
    chosen = resolve_compound(ctx, args["project_id"], args["compound"])["id"] if args.get("compound") else None
    body = {"project_id": args["project_id"], "endpoint_key": args["endpoint_key"], "mode": mode, "selected_compound_id": chosen}
    result = ctx.client.post("/api/v1/sar/discover", body, long=True)
    return linked(result, links_for(ctx).page("sar", args["project_id"], reference=chosen))


# kind -> (API path, page that shows it)
RECORDS = {
    "series": ("/api/v1/series", "sar"),
    "claims": ("/api/v1/sar/claims", "overview"),
    "recommendations": ("/api/v1/recommendations", "designs"),
    "designs": ("/api/v1/designs", "designs"),
    "prediction_models": ("/api/v1/predictions", "analysis"),
    "relationships": ("/api/v1/compound-relationships", "sar"),
}


def list_records(ctx: Context, args: dict) -> dict:
    path, page = RECORDS[args["kind"]]
    payload = ctx.client.get(path, project_id=args["project_id"])
    for key, value in payload.items():
        if isinstance(value, list):
            limit = args.get("limit", 100)
            payload = {**payload, key: value[:limit], f"{key}_total": len(value)}
            break
    return linked(payload, links_for(ctx).page(page, args["project_id"]))


ANALYSIS_PATHS = {
    "mmp": "mmp", "pharmacophore_rgroup": "pharmacophore-rgroup", "selectivity": "selectivity", "properties": "properties",
    "cellular_translation": "cellular-translation", "adme": "adme", "pareto": "pareto", "contradictions": "contradictions",
    "information_gain": "information-gain",
}


def get_analysis_run(ctx: Context, args: dict) -> dict:
    run = ctx.client.get(f"/api/v1/analysis/{ANALYSIS_PATHS[args['analysis']]}/{seg(args['run_id'])}")
    owner = run.get("project_id") if isinstance(run, dict) else None
    # Fail closed: with an allowlist, a run whose project cannot be confirmed is not shown.
    if ctx.settings.project_ids and owner not in ctx.settings.project_ids:
        raise ToolError("That run belongs to a project this server is not allowed to use (SAR_MCP_PROJECT_IDS).")
    links = links_for(ctx)
    # Information-gap runs feed recommendations, which live on the Choose next page; every other kind has a view on Find patterns.
    url = None
    if owner:
        url = links.run(owner, run_id_of(run)) if args["analysis"] != "information_gain" else links.page("designs", owner)
    return linked(run, url)


_P = {"project_id": PROJECT_ID}
TOOLS = [
    Tool("sar_standardize_structure", "Standardize a structure",
         "Parse and standardize a structure with RDKit (the app's own rules): canonical and isomeric SMILES, InChIKey, parent, stereochemistry status and warnings. Stateless; needs no project.",
         standardize,
         {"structure": {"type": "string", "minLength": 1, "maxLength": 20000},
          "input_format": {"type": "string", "enum": ["smiles", "molblock"]}}, ("structure",)),
    Tool("sar_substructure_search", "Substructure search",
         "Which compounds in a project contain a SMILES or SMARTS substructure. Stereochemistry is not compared. Drawn Kekule rings match aromatic rings.",
         substructure,
         {**_P, "query": {"type": "string", "minLength": 1, "maxLength": 500, "description": "SMILES or SMARTS, for example c1ccncc1 or [F,Cl]c1ccccc1."},
          "query_format": FORMAT, "limit": limit_property(100, 500)}, ("project_id", "query")),
    Tool("sar_core_from_structure", "Core SMARTS from a structure",
         "Turn a SMILES or SMARTS into a declared-core SMARTS for R-group analysis. A terminal * or [*:n] marks an attachment point and is removed from the core.",
         core_from_structure,
         {"query": {"type": "string", "minLength": 1, "maxLength": 500}, "query_format": FORMAT}, ("query",)),
    Tool("sar_propose_substituent", "Propose a substituent swap",
         "Propose an analogue by replacing the group at one R-site (R1, R2, ...) of a compound. Needs an R-group analysis to exist (sar_run_analysis, analysis=rgroup). Replacement is SMILES with one * attachment atom (for example *F or *OC), or H to remove. Nothing is saved.",
         propose_substituent,
         {**_P, "compound": {"type": "string", "maxLength": 100, "description": "Registration id to edit (or give smiles)."},
          "smiles": {"type": "string", "maxLength": 2000, "description": "An earlier proposal to edit further, instead of a saved compound."},
          "site": {"type": "string", "pattern": "^R[0-9]{1,2}$", "description": "R-site label such as R1."},
          "replacement": {"type": "string", "maxLength": 200}}, ("project_id", "site", "replacement")),
    Tool("sar_discover_series", "Discover SAR series",
         "Group compounds by shared scaffold for one endpoint and describe how substituents differ (R-sites, member results). mode=selected shows the series around one compound. Computed on request; nothing is stored.",
         discover_series,
         {**_P, "endpoint_key": ENDPOINT_KEY, "mode": {"type": "string", "enum": ["discover", "selected"]}, "compound": COMPOUND_REF},
         ("project_id", "endpoint_key")),
    Tool("sar_list_records", "List saved records",
         "Saved project records: series, SAR claims, generated recommendations (awaiting human review), curated designs, prediction models (with qualification status) or prodrug relationships.",
         list_records,
         {**_P, "kind": {"type": "string", "enum": sorted(RECORDS)}, "limit": limit_property(100, 500)}, ("project_id", "kind")),
    Tool("sar_get_analysis_run", "Get a stored analysis run",
         "Read a stored analysis run by id (ids come from sar_run_analysis or the project export). Runs of these types can be read back: " + ", ".join(sorted(ANALYSIS_PATHS)) + ".",
         get_analysis_run,
         {"analysis": {"type": "string", "enum": sorted(ANALYSIS_PATHS)}, "run_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}},
         ("analysis", "run_id")),
]
