"""sar_open_in_workbench: a link that opens a chosen page of the web app, optionally pinned to a
compound, a stored run, a structure search or a pair of assays. Builds a URL only; nothing is called
except lookups to make sure the link will not be dead."""
from __future__ import annotations

from .links import PAGES, RUN_ID
from .toolkit import (
    COMPOUND_REF, ENDPOINT_KEY, PROJECT_ID, Context, Tool, ToolError, latest_summaries, links_for, resolve_compound,
)

FORMATS = ["auto", "smiles", "smarts"]


def _assay_id(ctx: Context, project_id: str, key: str) -> str:
    """The Compare page names an assay 'summary|<endpoint key>|<unit>'."""
    summaries = latest_summaries(ctx.client.get("/api/v1/measurement-summaries", project_id=project_id).get("summaries") or [])
    units = {item.get("compatibility_key"): item.get("canonical_unit") or "" for item in summaries}
    if key not in units:
        known = ", ".join(sorted(str(name) for name in units)) or "none yet (run measurement_summaries first)"
        raise ToolError(f"No result summaries for '{key}' in this project. Known endpoints: {known}.")
    return f"summary|{key}|{units[key]}"


def open_in_workbench(ctx: Context, args: dict) -> dict:
    project_id, page = args["project_id"], args["page"]
    visible = {item.get("id") for item in ctx.client.get("/api/v1/projects").get("projects") or []}
    if project_id not in visible:
        raise ToolError("No project with that id is visible to you. Use sar_list_projects.")
    params: dict = {}
    fragment = ""
    notes: list[str] = []
    if args.get("compound"):
        if page not in {"sar", "analysis"}:
            raise ToolError("compound works with page=sar (the series around it) or page=analysis (the pattern explorer compared with it).")
        compound = resolve_compound(ctx, project_id, args["compound"])
        if page == "sar":
            params["reference"] = compound["id"]
            notes.append(f"{compound['registration_id']} is set as the SAR reference.")
        else:
            params["compare"] = compound["id"]
            fragment = "pattern-explorer"
            notes.append(f"The pattern explorer compares results with {compound['registration_id']}.")
    if args.get("run_id"):
        if not RUN_ID.match(args["run_id"]):
            raise ToolError("run_id is not a valid run id.")
        params["run"] = args["run_id"]
        notes.append("Shows that stored run in place of the latest of its kind, with a notice saying so. If the run does not exist in the project, the page says so and shows the latest results.")
    if args.get("find"):
        params["find"] = args["find"]
        if args.get("find_format", "auto") != "auto":
            params["find_format"] = args["find_format"]
        notes.append("Opens Find by structure and runs this search for you. It only reads.")
    if args.get("endpoint_a") or args.get("endpoint_b"):
        if page != "explore":
            raise ToolError("endpoint_a and endpoint_b work with page=explore (the Compare page).")
        for name in ("endpoint_a", "endpoint_b"):
            if args.get(name):
                params[name] = _assay_id(ctx, project_id, args[name])
        params["stage"] = "compare"
    url = links_for(ctx).page(page, project_id, fragment=fragment, **params)
    return {"web_link": url, "page": page, "shows": PAGES[page], "notes": notes,
            "sign_in": "Opens in your browser. Sign in there if asked; what you can see follows your project membership."}


TOOLS = [
    Tool("sar_open_in_workbench", "Open in the web app",
         "Get a link that opens a page of the SAR Workbench in the user's browser, so they can look at the result in the app itself. "
         "Pages: " + "; ".join(f"{name} = {text}" for name, text in PAGES.items()) + ". "
         "Optional: compound (page sar or analysis), run_id (a stored run from sar_run_analysis, shown instead of the latest of its kind), "
         "find (a SMILES or SMARTS, opens Find by structure already run), endpoint_a and endpoint_b (page explore). Builds a link only; nothing is changed.",
         open_in_workbench,
         {"project_id": PROJECT_ID, "page": {"type": "string", "enum": list(PAGES)}, "compound": COMPOUND_REF,
          "run_id": {"type": "string", "pattern": RUN_ID.pattern, "description": "Analysis run id, from sar_run_analysis or sar_get_analysis_run."},
          "find": {"type": "string", "minLength": 1, "maxLength": 500, "description": "SMILES or SMARTS to search for."},
          "find_format": {"type": "string", "enum": FORMATS}, "endpoint_a": ENDPOINT_KEY, "endpoint_b": ENDPOINT_KEY},
         ("project_id", "page")),
]
