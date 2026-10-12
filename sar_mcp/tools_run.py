"""Tools that store something in the project: derived analysis runs (tier analyze) and
project data (tier write). Review gates (approving recommendations, creating claims or series,
membership) are deliberately not exposed: those decisions stay with people."""
from __future__ import annotations

from .client import seg
from .links import run_id_of
from .schema import validate
from .toolkit import (
    COMPOUND_REF, ENDPOINT_KEY, PROJECT_ID, Context, Tool, ToolError, linked, links_for, resolve_compound, resolve_many, limit_property,
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
        raise ToolError("Not committed. user_confirmed must be exactly true after reviewing the preview.")
    if args.get("reviewed_all_rows") is not True:
        raise ToolError("Not committed. reviewed_all_rows must be exactly true after reviewing every staged row.")
    body = {"project_id": args["project_id"], "mcp_request": True}
    body.update({"user_confirmed": args["user_confirmed"], "reviewed_all_rows": args["reviewed_all_rows"], "reviewed_row_count": args["reviewed_row_count"]})
    saved = ctx.client.post(f"/api/v1/imports/{seg(args['import_id'])}/commit", body, long=True)
    return linked({**saved, "review_attestation_unverified": True}, links_for(ctx).staged_import_review(args["project_id"], args["import_id"]))


def _validate_upload_arguments(args: dict) -> None:
    if not isinstance(args.get("filename"), str) or not args["filename"].strip():
        raise ToolError("filename is required for import profile and preview requests.")
    content_fields = [key for key in ("csv_text", "content_base64") if key in args]
    if len(content_fields) != 1:
        raise ToolError("Provide exactly one of csv_text or content_base64.")


def _upload_body(args: dict) -> dict:
    _validate_upload_arguments(args)
    body = {"project_id": args["project_id"], "filename": args["filename"]}
    if "csv_text" in args: body["csv_text"] = args["csv_text"]
    if "content_base64" in args: body["content_base64"] = args["content_base64"]
    for key in ("sheet_name", "header_row", "data_start_row", "mapping_overrides", "formula_acknowledged", "offset", "limit"):
        if key in args: body[key] = args[key]
    return body


def import_profile(ctx: Context, args: dict) -> dict:
    _validate_upload_arguments(args)
    result = ctx.client.post("/api/v1/imports/profile", {key: value for key, value in _upload_body(args).items() if key != "project_id"} | {"project_id": args["project_id"]}, long=True)
    return {**result, "data_origin": "quarantined_import"}


def import_preview(ctx: Context, args: dict) -> dict:
    _validate_upload_arguments(args)
    preview = ctx.client.post("/api/v1/imports/preview", _upload_body(args), long=True)
    keep = ("import_id", "status", "filename", "accepted_count", "rejected_count", "warning_count", "mapping", "mapping_confidence", "mapping_issues", "missing_mapping_fields", "headers", "file_format", "sha256", "profile", "source_document_id", "formula_policy_version", "total", "offset", "returned", "has_more", "next_offset", "stable_order")
    result = {**{key: preview.get(key) for key in keep if key in preview}, "rows": preview.get("rows") or [], "data_origin": "quarantined_import", "next": "Review every paginated staged row before calling sar_import_commit with exact confirmation fields."}
    return linked(result, links_for(ctx).staged_import_review(args["project_id"], result["import_id"]))


def get_import_preview(ctx: Context, args: dict) -> dict:
    result = ctx.client.get(f"/api/v1/imports/{seg(args['import_id'])}", project_id=args["project_id"], offset=args.get("offset", 0), limit=args.get("limit", 100))
    return linked(result, links_for(ctx).staged_import_review(args["project_id"], args["import_id"]))


def _explicit_mapping_body(args: dict) -> dict:
    supplied = [key for key in ("pairs", "pair_text") if key in args]
    if "continuation_token" not in args and len(supplied) != 1:
        raise ToolError("Provide exactly one of pairs or pair_text.")
    if "continuation_token" in args and supplied:
        raise ToolError("Use continuation_token without resubmitting the mapping.")
    return {key: args[key] for key in ("project_id", "relationship_type", "pairs", "pair_text", "continuation_token", "offset", "limit") if key in args}


def preview_prodrug(ctx: Context, args: dict) -> dict:
    result = ctx.client.post("/api/v1/compound-relationships/preview", _explicit_mapping_body(args))
    if result.get("has_more") and result.get("continuation_token"):
        result["continuation"] = {
            "tool": "sar_preview_prodrug_relationships",
            "arguments": {
                "project_id": args["project_id"],
                "relationship_type": args["relationship_type"],
                "continuation_token": result["continuation_token"],
                "offset": result.get("next_offset"),
                "limit": args.get("limit", 500),
            },
        }
    return linked(result, links_for(ctx).page("sar", args["project_id"], sar_mode="prodrug", endpoint_key=args.get("endpoint_key")))


def save_prodrug(ctx: Context, args: dict) -> dict:
    if args.get("user_confirmed") is not True:
        raise ToolError("Not saved. user_confirmed must be exactly true after reviewing the complete preview.")
    body = _explicit_mapping_body(args)
    body["user_confirmed"] = True
    result = ctx.client.post("/api/v1/compound-relationships", body, long=True)
    return linked(result, links_for(ctx).page("sar", args["project_id"], sar_mode="prodrug"))


def compare_prodrug(ctx: Context, args: dict) -> dict:
    body = {"project_id": args["project_id"], "endpoint_key": args["endpoint_key"], "persist": True, "limit": args.get("limit", 500), "offset": args.get("offset", 0)}
    if "relationship_ids" in args: body["relationship_ids"] = args["relationship_ids"]
    result = ctx.client.post("/api/v1/analysis/prodrug-comparison", body, long=True)
    return linked(result, links_for(ctx).page("sar", args["project_id"], sar_mode="prodrug", endpoint_key=args["endpoint_key"], comparison_run_id=result.get("analysis_run_id")))


def compare_endpoints(ctx: Context, args: dict) -> dict:
    query = {"project_id": args["project_id"], "endpoint_a": args["endpoint_a"], "endpoint_b": args.get("endpoint_b", "none"), "coverage": args.get("coverage", "all"), "q": args.get("q", ""), "limit": args.get("limit", 200), "offset": args.get("offset", 0)}
    if query["coverage"] == "both" and query["endpoint_b"] == "none": raise ToolError("coverage=both requires endpoint_b")
    if query["endpoint_b"] != "none" and query["endpoint_a"].casefold() == query["endpoint_b"].casefold(): raise ToolError("endpoint_a and endpoint_b must differ")
    result = ctx.client.get("/api/v1/endpoint-comparison", **query)
    return linked(result, links_for(ctx).page("explore", args["project_id"], endpoint_a=args["endpoint_a"], endpoint_b=query["endpoint_b"], stage="compare", coverage=query["coverage"], q=query["q"]))



    parts = []
    for name, (_path, accepted, required) in ANALYSES.items():
        args = ", ".join(f"{key}{'*' if key in required else ''}" for key in accepted)
        parts.append(f"{name}({args})" if args else name)
    return "; ".join(parts)
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
    Tool("sar_import_preview", "Preview an import", "Profile and stage a bounded CSV, TSV, or XLSX upload without committing measurements. Returns paginated rows, mapping, source locations and formula policy; nothing is committed.", import_preview,
         {**_P, "filename": {"type": "string", "minLength": 1, "maxLength": 255}, "csv_text": {"type": "string", "minLength": 1, "maxLength": 1000000}, "content_base64": {"type": "string", "minLength": 1, "maxLength": 6666668}, "sheet_name": {"type": "string", "minLength": 1, "maxLength": 255}, "header_row": {"type": "integer", "minimum": 1, "maximum": 10000}, "data_start_row": {"type": "integer", "minimum": 1, "maximum": 1000000}, "mapping_overrides": {"type": "object", "maxProperties": 50, "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 200}}, "formula_acknowledged": {"type": "boolean"}, "offset": {"type": "integer", "minimum": 0, "maximum": 1000000}, "limit": limit_property(100, 500)}, ("project_id", "filename"), tier="write", idempotent=False, side_effect="quarantine"),
    Tool("sar_import_profile", "Profile an import", "Profile CSV, TSV, or XLSX metadata and visible sheets without storing a batch or exposing file bytes.", import_profile,
         {**_P, "filename": {"type": "string", "minLength": 1, "maxLength": 255}, "csv_text": {"type": "string", "minLength": 1, "maxLength": 1000000}, "content_base64": {"type": "string", "minLength": 1, "maxLength": 6666668}}, ("project_id", "filename"), tier="write", idempotent=False, side_effect="none"),
    Tool("sar_get_import_preview", "Read staged import rows", "Read a project-bound staged import page with stable ordering, source cells and quarantine/imported origin.", get_import_preview,
         {**_P, "import_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}, "offset": {"type": "integer", "minimum": 0, "maximum": 1000000}, "limit": limit_property(100, 500)}, ("project_id", "import_id"), tier="write", idempotent=True, side_effect="quarantine"),
    Tool("sar_import_commit", "Commit a checked import", "Commit every accepted row from a project-bound staged import only after complete paginated review and exact confirmation fields.", import_commit,
         {**_P, "import_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}, "user_confirmed": {"type": "boolean"}, "reviewed_all_rows": {"type": "boolean"}, "reviewed_row_count": {"type": "integer", "minimum": 0, "maximum": 1000000}}, ("project_id", "import_id", "user_confirmed", "reviewed_all_rows", "reviewed_row_count"), tier="write", idempotent=False, side_effect="project_data"),
    Tool("sar_preview_prodrug_relationships", "Preview explicit prodrug mappings", "Validate explicit project-scoped prodrug-to-active mappings without saving them. No structure, name, or endpoint inference is performed; preview rows are quarantined and need review.", preview_prodrug,
         {**_P, "relationship_type": {"type": "string", "enum": ["prodrug_of"]}, "pairs": {"type": "array", "minItems": 1, "maxItems": 5000, "items": {"type": "object", "additionalProperties": False, "required": ["prodrug_id", "active_id"], "properties": {"prodrug_id": COMPOUND_REF, "active_id": COMPOUND_REF, "notes": {"type": "string", "maxLength": 1000}}}}, "pair_text": {"type": "string", "minLength": 1, "maxLength": 1000000}, "continuation_token": {"type": "string", "pattern": r"^[A-Fa-f0-9]{32}$"}, "offset": {"type": "integer", "minimum": 0, "maximum": 1000000}, "limit": limit_property(100, 500)}, ("project_id", "relationship_type"), tier="analyze", idempotent=False, side_effect="none"),
    Tool("sar_save_prodrug_relationships", "Save explicit prodrug mappings", "Save reviewed explicit prodrug-to-active mappings in full mode with needs_review status; mappings are imported data, not inferred activation or causation evidence.", save_prodrug,
         {**_P, "relationship_type": {"type": "string", "enum": ["prodrug_of"]}, "pairs": {"type": "array", "minItems": 1, "maxItems": 5000, "items": {"type": "object", "additionalProperties": False, "required": ["prodrug_id", "active_id"], "properties": {"prodrug_id": COMPOUND_REF, "active_id": COMPOUND_REF, "notes": {"type": "string", "maxLength": 1000}}}}, "pair_text": {"type": "string", "minLength": 1, "maxLength": 1000000}, "user_confirmed": {"type": "boolean"}}, ("project_id", "relationship_type", "user_confirmed"), tier="write", idempotent=False, side_effect="project_data"),
    Tool("sar_compare_prodrug_pairs", "Compare explicit prodrug pairs", "Persist a project-scoped derived comparison for explicit reviewed relationships and return immutable provenance, audit linkage and bounded pages.", compare_prodrug,
         {**_P, "endpoint_key": ENDPOINT_KEY, "relationship_ids": {"type": "array", "maxItems": 5000, "items": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}}, "offset": {"type": "integer", "minimum": 0, "maximum": 1000000}, "limit": limit_property(200, 500)}, ("project_id", "endpoint_key"), tier="analyze", idempotent=False, side_effect="derived"),
]
