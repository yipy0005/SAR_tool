"""Tools that store something in the project: derived analysis runs (tier analyze) and
project data (tier write). Review gates (approving recommendations, creating claims or series,
membership) are deliberately not exposed: those decisions stay with people."""
from __future__ import annotations

from .client import seg
from .links import run_id_of
from .schema import validate
from .toolkit import (
    COMPOUND_REF, ENDPOINT_KEY, PROJECT_ID, Context, Tool, ToolError, linked, links_for, resolve_compound, resolve_many,
)

NOTE = ("Stored as a derived, versioned run. It is a calculated association for review, not evidence of causation: "
        "check assay context, censored values and contradictions before drawing a conclusion.")
_NUM01 = {"type": "number", "minimum": 0, "maximum": 1}
_POS = {"type": "number", "minimum": 0.000001, "maximum": 1000}
_SMARTS = {"type": "string", "minLength": 2, "maxLength": 500}
_KEYS = {"type": "array", "minItems": 1, "maxItems": 50, "items": ENDPOINT_KEY}

# analysis -> (API path, accepted parameters, required parameters)
ANALYSES: dict[str, tuple[str, dict, tuple]] = {
    "measurement_summaries": ("/api/v1/measurement-summaries/project",
        {"aggregation_method": {"type": "string", "enum": ["mean", "median"]}, "analysis_version": {"type": "string", "maxLength": 60}}, ()),
    "properties": ("/api/v1/analysis/properties", {}, ()),
    "mmp": ("/api/v1/analysis/mmp", {}, ()),
    "rgroup": ("/api/v1/analysis/rgroup", {"scaffold_smarts": _SMARTS}, ("scaffold_smarts",)),
    "pharmacophore_rgroup": ("/api/v1/analysis/pharmacophore-rgroup",
        {"reference_compound": COMPOUND_REF, "scaffold_smarts": _SMARTS}, ("reference_compound",)),
    "activity_cliffs": ("/api/v1/analysis/activity-cliffs", {"effect_threshold": _POS, "similarity_threshold": _NUM01}, ()),
    "selectivity": ("/api/v1/analysis/selectivity",
        {"primary_compatibility_key": ENDPOINT_KEY, "comparator_compatibility_key": ENDPOINT_KEY, "selectivity_threshold": _POS},
        ("primary_compatibility_key", "comparator_compatibility_key")),
    "cellular_translation": ("/api/v1/analysis/cellular-translation",
        {"biochemical_compatibility_key": ENDPOINT_KEY, "cellular_compatibility_key": ENDPOINT_KEY, "translation_loss_threshold": _POS},
        ("biochemical_compatibility_key", "cellular_compatibility_key")),
    "adme": ("/api/v1/analysis/adme", {"compatibility_keys": _KEYS}, ("compatibility_keys",)),
    "pareto": ("/api/v1/analysis/pareto",
        {"objectives": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"type": "object", "additionalProperties": False,
            "required": ["source", "key", "direction"],
            "properties": {"source": {"type": "string", "enum": ["summary", "property"]}, "key": {"type": "string", "maxLength": 200},
                           "direction": {"type": "string", "enum": ["maximize", "minimize"]}, "label": {"type": "string", "maxLength": 120}}}}},
        ("objectives",)),
    "contradictions": ("/api/v1/analysis/contradictions", {"value_tolerance": _POS}, ()),
    "information_gap": ("/api/v1/analysis/information-gain",
        {"contexts": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"type": "object", "additionalProperties": False,
            "required": ["compatibility_key"],
            "properties": {"compatibility_key": ENDPOINT_KEY, "priority": _NUM01,
                           "minimum_replicates": {"type": "integer", "minimum": 1, "maximum": 16}}}}},
        ("contexts",)),
    "recommendations": ("/api/v1/recommendations/generate",
        {"information_gain_run_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("information_gain_run_id",)),
}


def run_analysis(ctx: Context, args: dict) -> dict:
    name = args["analysis"]
    path, accepted, required = ANALYSES[name]
    parameters = args.get("parameters") or {}
    spec = {"type": "object", "properties": accepted, "required": list(required), "additionalProperties": False}
    problems = validate(parameters, spec, "parameters")
    if problems:
        shown = ", ".join(f"{key} ({'required' if key in required else 'optional'})" for key in accepted) or "none"
        raise ToolError(f"{'; '.join(problems)}. The '{name}' analysis accepts: {shown}.")
    body = {"project_id": args["project_id"], **parameters}
    if "reference_compound" in body:
        body["reference_compound_id"] = resolve_compound(ctx, args["project_id"], body.pop("reference_compound"))["id"]
    result = ctx.client.post(path, body, long=True)
    links = links_for(ctx)
    project_id = args["project_id"]
    if name == "measurement_summaries":
        url = links.page("summaries", project_id)
    elif name in {"information_gap", "recommendations"}:
        url = links.page("designs", project_id)  # recommendations are reviewed on Choose next
    else:
        url = links.run(project_id, run_id_of(result)) or links.page("analysis", project_id)
    return linked({"analysis": name, "stored": True, "result": result, "interpretation": NOTE}, url)


def train_model(ctx: Context, args: dict) -> dict:
    project_id = args["project_id"]
    body = {"project_id": project_id, "compatibility_key": args["endpoint_key"]}
    if args.get("holdout_compounds"):
        body["holdout_compound_ids"] = [row["id"] for row in resolve_many(ctx, project_id, args["holdout_compounds"])]
    result = ctx.client.post("/api/v1/predictions/train", body, long=True)
    data = {"stored": True, "result": result,
            "interpretation": "Acceptance thresholds are fixed by the app and cannot be changed here. Read the model's qualification status before using any prediction."}
    return linked(data, links_for(ctx).page("analysis", project_id))


def predict(ctx: Context, args: dict) -> dict:
    project_id = args["project_id"]
    ids = [row["id"] for row in resolve_many(ctx, project_id, args["compounds"])]
    result = ctx.client.post(f"/api/v1/predictions/{seg(args['model_id'])}/predict", {"project_id": project_id, "compound_ids": ids}, long=True)
    data = {"result": result, "interpretation": "Only a qualified model supports a prediction, and only inside its applicability domain. Blocked or out-of-domain rows must be reported as such."}
    return linked(data, links_for(ctx).page("analysis", project_id))


def create_project(ctx: Context, args: dict) -> dict:
    created = ctx.client.post("/api/v1/projects", {"name": args["name"], "description": args.get("description", "")})
    new_id = (created.get("project") or {}).get("id")
    return linked(created, links_for(ctx).page("overview", new_id) if new_id else None)


def import_preview(ctx: Context, args: dict) -> dict:
    body = {"project_id": args["project_id"], "csv_text": args["csv_text"], "filename": args.get("filename", "mcp-upload.csv")}
    preview = ctx.client.post("/api/v1/imports/preview", body, long=True)
    rows = preview.get("rows") or []
    keep = ("import_id", "status", "filename", "accepted_count", "rejected_count", "warning_count", "mapping", "mapping_confidence",
            "mapping_issues", "missing_mapping_fields", "headers", "file_format", "sha256")
    return {**{key: preview.get(key) for key in keep if key in preview}, "rows_total": len(rows), "rows_sample": rows[:25],
            "next": "Show the user the mapping, issues and rejected rows. Only after they agree, call sar_import_commit. Nothing is saved yet."}


def import_commit(ctx: Context, args: dict) -> dict:
    if args.get("user_confirmed") is not True:
        raise ToolError("Not committed. Ask the user to review the preview, then call again with user_confirmed=true.")
    saved = ctx.client.post(f"/api/v1/imports/{seg(args['import_id'])}/commit", {"project_id": args["project_id"]}, long=True)
    return linked(saved, links_for(ctx).page("evidence", args["project_id"]))


def _describe_analyses() -> str:
    parts = []
    for name, (_path, accepted, required) in ANALYSES.items():
        args = ", ".join(f"{key}{'*' if key in required else ''}" for key in accepted)
        parts.append(f"{name}({args})" if args else name)
    return "; ".join(parts)


_P = {"project_id": PROJECT_ID}
TOOLS = [
    Tool("sar_run_analysis", "Run an analysis",
         "Run one analysis on a project. STORES a derived, versioned run in the project, and can change what the Find patterns page shows (it displays the latest run), so say what you will run and why first. "
         "Analyses and their parameters (* = required): " + _describe_analyses() + ". "
         "Use endpoint_key values from sar_list_endpoints and compound registration ids. Run measurement_summaries first after new data arrives; information_gap feeds recommendations, which people must review.",
         run_analysis,
         {**_P, "analysis": {"type": "string", "enum": list(ANALYSES)},
          "parameters": {"type": "object", "description": "Parameters for the chosen analysis; see the list above."}},
         ("project_id", "analysis"), tier="analyze", idempotent=False),
    Tool("sar_train_prediction_model", "Train a prediction model",
         "Train a local nearest-neighbour model for one endpoint. STORES a model with its qualification result; the acceptance thresholds are fixed by the app. An unqualified model must not be used for predictions.",
         train_model,
         {**_P, "endpoint_key": ENDPOINT_KEY,
          "holdout_compounds": {"type": "array", "maxItems": 500, "items": COMPOUND_REF, "description": "Registration ids held out for validation."}},
         ("project_id", "endpoint_key"), tier="analyze", idempotent=False),
    Tool("sar_predict_compounds", "Predict with a model",
         "Apply a stored prediction model (model id from sar_list_records kind=prediction_models) to project compounds. Blocked, unqualified and out-of-domain results come back as such and must be reported, not hidden.",
         predict,
         {**_P, "model_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
          "compounds": {"type": "array", "minItems": 1, "maxItems": 500, "items": COMPOUND_REF}},
         ("project_id", "model_id", "compounds"), tier="analyze", idempotent=False),
    Tool("sar_create_project", "Create a project",
         "Create an empty project (you become its owner). Changes project data.",
         create_project,
         {"name": {"type": "string", "minLength": 2, "maxLength": 160}, "description": {"type": "string", "maxLength": 2000}},
         ("name",), tier="write", idempotent=False),
    Tool("sar_import_preview", "Preview a CSV import",
         "Check CSV text (long format, one measurement per row) without saving results: column mapping, accepted and rejected rows, warnings. Stores the quarantined upload only. Show the user the preview before any commit.",
         import_preview,
         {**_P, "csv_text": {"type": "string", "minLength": 1, "maxLength": 1000000}, "filename": {"type": "string", "maxLength": 120}},
         ("project_id", "csv_text"), tier="write", idempotent=False),
    Tool("sar_import_commit", "Commit a checked import",
         "Save the accepted rows of a previewed import as project measurements. This changes evidence. Only call it after the user has reviewed the preview and agreed, and then pass user_confirmed=true.",
         import_commit,
         {**_P, "import_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}, "user_confirmed": {"type": "boolean"}},
         ("project_id", "import_id", "user_confirmed"), tier="write", idempotent=False),
]
