from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, redirect, render_template, request, g, send_from_directory, session, url_for
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from auth import (
    CSRFError,
    authenticate,
    csrf_token,
    is_authenticated,
    login as login_session,
    logout as logout_session,
    validate_csrf,
)
from analysis import AnalysisValidationError, create_claims_from_mmp, get_mmp_run, run_mmp_analysis
from authorization import (
    add_project_member,
    accessible_projects,
    current_user_id,
    ensure_local_user,
    has_project_access,
    list_project_members,
)
from chemistry import (
    StructureValidationError,
    reference_site_map,
    render_fragment_svg,
    render_reference_positions_svg,
    render_scaffold_positions_svg,
    render_scaffold_svg,
    standardize_structure,
)
from pattern_explorer import build_pattern_explorer

from config import Settings
from display import endpoint_name, format_measure, format_number, format_unit, humanize, register_filters as register_display_filters, substituent_name
from database import apply_migrations, database_ready, read_connection, transaction
from export_engine import ExportError, MAX_EXPORT_ROWS, build_project_export, export_project_csv
from measurement_engine import (
    MeasurementEngineError,
    create_measurement_summary,
    get_measurement_summary,
    summarize_project_measurements,
)
from information_gain_engine import (
    InformationGainAnalysisError,
    get_information_gain_run,
    run_information_gain_analysis,
)
from property_engine import PropertyAnalysisError, get_property_run, run_property_analysis
from pharmacophore_engine import (
    PharmacophoreRGroupError,
    ANALYSIS_VERSION as PHARMACOPHORE_ANALYSIS_VERSION,
    derive_series_scaffold,
    get_pharmacophore_rgroup_run,
    run_pharmacophore_rgroup_analysis,
)
from prediction_engine import PredictionError, PredictionNotFoundError, get_prediction_model, list_prediction_models, predict_compounds, train_prediction_model
from recommendation_engine import (
    RecommendationValidationError,
    generate_recommendations,
    list_candidates,
    list_generated_recommendations,
    persist_candidates,
    review_generated_recommendation,
)
from observability import configure_logging, initialize_metrics, metrics_snapshot, record_request
from rgroup_engine import RGroupAnalysisError, run_activity_cliff_analysis, run_rgroup_analysis
from selectivity_engine import SelectivityAnalysisError, get_selectivity_run, run_selectivity_analysis
from series_engine import SeriesError, SeriesNotFoundError, create_series, create_series_version, series_context
from prodrug_engine import (
    ProdrugRelationshipError,
    compare_relationships,
    list_relationships,
    preview_relationships,
    save_relationships,
)
from collections import defaultdict

from sar_discovery import (
    SarDiscoveryError,
    assign_position_labels,
    consistent_substituents,
    describe_substituents,
    discover_series,
    replace_substituent,
)
from search_engine import SearchValidationError, search_compounds
from substructure_search import (
    SubstructureQueryError,
    build_query as build_substructure_query,
    compound_structure as substructure_compound,
    search_project as search_substructure,
    sketch_from_smiles,
)
from import_pipeline import (
    DuplicateImportError,
    ImportConflictError,
    ImportValidationError,
    SheetSelectionRequired,
    UploadFormatError,
    commit_import,
    create_preview,
    profile_upload,
)
from sar_data import (
    COMPOUNDS,
    HYPOTHESES,
    INSIGHTS,
    MATRIX,
    PROJECT,
    QUALITY_FLAGS,
    RECOMMENDATIONS,
    TIMELINE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _create_project_record(app: Flask, name: str, description: str = "", project_id: str | None = None) -> dict[str, str]:
    project_id = project_id or _id("prj")
    now = _now()
    actor_user_id = current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None
    with transaction(app.config["DATABASE_PATH"]) as connection:
        connection.execute(
            """
            INSERT INTO projects (id, name, description, status, data_origin, created_at, updated_at)
            VALUES (?, ?, ?, 'active', 'imported', ?, ?)
            """,
            (project_id, name, description, now, now),
        )
        if actor_user_id:
            connection.execute(
                """
                INSERT INTO project_members (project_id, user_id, role, created_at)
                VALUES (?, ?, 'owner', ?)
                """,
                (project_id, actor_user_id, now),
            )
        connection.execute(
            """
            INSERT INTO audit_events
                (id, actor_user_id, project_id, operation, resource_type, resource_id, request_id, outcome, created_at)
            VALUES (?, ?, ?, 'project_create', 'project', ?, ?, 'success', ?)
            """,
            (_id("audit"), actor_user_id, project_id, project_id, g.get("request_id"), now),
        )
    return {"id": project_id, "name": name, "description": description, "data_origin": "imported"}


def _settings_from_config(config: dict[str, Any]) -> Settings:
    return Settings(
        environment=config["SAR_ENV"],
        demo_mode=config["SAR_DEMO_MODE"],
        database_path=config["DATABASE_PATH"],
        upload_dir=config["UPLOAD_DIR"],
        secret_key=config["SECRET_KEY"],
        auth_mode=config["SAR_AUTH_MODE"],
        max_upload_bytes=config["MAX_CONTENT_LENGTH"],
        host=config["HOST"],
        port=config["PORT"],
        secure_cookies=config["SESSION_COOKIE_SECURE"],
        log_sink=str(config.get("LOG_SINK", "stdout")),
        log_level=str(config.get("LOG_LEVEL", "INFO")),
        log_retention_days=int(config.get("LOG_RETENTION_DAYS", 14)),
    )


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _json_payload() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _mapping_overrides(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("mapping_overrides must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("mapping_overrides must be a JSON object")
    return {str(key): str(column).strip() for key, column in value.items() if str(column).strip()}


def _boolean_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'"
)
# Ketcher runs its chemistry engine (Indigo) as WebAssembly inside a blob: worker. Only the
# editor frame under /ketcher/ gets those two allowances ('wasm-unsafe-eval', worker-src blob:),
# and only pages from this origin may frame it. Every other page keeps the policy above.
KETCHER_CONTENT_SECURITY_POLICY = (
    CONTENT_SECURITY_POLICY.replace("frame-ancestors 'none'", "frame-ancestors 'self'").replace(
        "script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'"
    )
    + "; worker-src 'self' blob:"
)
DEFAULT_KETCHER_DIR = Path(__file__).resolve().parent / "vendor" / "ketcher"


def _ketcher_install(app: Flask) -> dict[str, Any] | None:
    """Version and licence of the installed Ketcher editor, or None when it is not installed."""
    folder = Path(app.config["KETCHER_DIR"])
    if not (folder / "index.html").is_file():
        return None
    try:
        manifest = json.loads((folder / "ketcher-install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    return {
        "version": str(manifest.get("version") or "unknown"),
        "license": str(manifest.get("license") or "Apache-2.0"),
        "source": str(manifest.get("source") or "https://github.com/epam/ketcher"),
    }


def create_app(config_overrides: dict[str, Any] | None = None) -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key or "demo-only-secret-not-for-production",
        SAR_ENV=settings.environment,
        SAR_DEMO_MODE=settings.demo_mode,
        SAR_AUTH_MODE=settings.auth_mode,
        DATABASE_PATH=settings.database_path,
        UPLOAD_DIR=settings.upload_dir,
        MAX_CONTENT_LENGTH=settings.max_upload_bytes,
        MAX_EXPORT_ROWS=MAX_EXPORT_ROWS,
        HOST=settings.host,
        PORT=settings.port,
        SESSION_COOKIE_SECURE=settings.secure_cookies,
        LOG_SINK=settings.log_sink,
        LOG_LEVEL=settings.log_level,
        LOG_RETENTION_DAYS=settings.log_retention_days,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TESTING=False,
        KETCHER_DIR=os.environ.get("SAR_KETCHER_DIR") or str(DEFAULT_KETCHER_DIR),
    )
    if config_overrides:
        app.config.update(config_overrides)
    app.json.sort_keys = False
    configure_logging(app)
    initialize_metrics(app)

    active_settings = _settings_from_config(app.config)
    if app.config["SAR_ENV"] == "production" and not app.config.get("TESTING"):
        active_settings.validate_startup()
    if not app.config["SAR_DEMO_MODE"] or app.config.get("TESTING"):
        active_settings.ensure_runtime_directories()

    register_display_filters(app)

    @app.template_filter("comma")
    def comma(value: Any) -> str:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)

    @app.before_request
    def request_context() -> None:
        g.request_id = uuid.uuid4().hex
        g.request_started_at = time.monotonic()

    @app.before_request
    def enforce_production_access():
        if app.config["SAR_ENV"] != "production":
            return None
        public_paths = {"healthz", "readyz", "login"}
        if request.endpoint in public_paths or request.path.startswith("/static/"):
            return None
        if not is_authenticated():
            if request.path.startswith("/api/"):
                return jsonify(error="authentication_required", message="Sign in before accessing production data."), 401
            next_path = request.full_path if request.full_path.startswith("/") else "/"
            return redirect(url_for("login", next=next_path))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                validate_csrf(request)
            except CSRFError:
                if request.path.startswith("/api/"):
                    return jsonify(error="csrf_failed", message="A valid CSRF token is required."), 400
                return "CSRF validation failed", 400
        return None

    @app.context_processor
    def security_context():
        return {"csrf_token": csrf_token() if app.config["SAR_ENV"] == "production" else ""}

    @app.context_processor
    def ketcher_context():
        install = _ketcher_install(app)
        return {
            "ketcher": {
                "available": install is not None,
                "version": install["version"] if install else None,
                "url": url_for("ketcher_editor") if install else None,
                "license_url": url_for("ketcher_editor", filename="LICENSE") if install else None,
            }
        }

    @app.get("/ketcher/")
    @app.get("/ketcher/<path:filename>")
    def ketcher_editor(filename: str = "index.html"):
        """Ketcher (EPAM Systems, Apache-2.0), framed by Find by structure. Read-only static files."""
        folder = Path(app.config["KETCHER_DIR"])
        if not (folder / "index.html").is_file():
            return Response("Ketcher is not installed on this server. Run: pixi run fetch-ketcher", status=404, mimetype="text/plain")
        plain = filename in {"LICENSE", "NOTICE"}
        response = send_from_directory(folder, filename, mimetype="text/plain" if plain else None)
        response.headers["Content-Security-Policy"] = KETCHER_CONTENT_SECURITY_POLICY
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        # Asset names carry a content hash, so the 30 MB bundle is downloaded once per version.
        immutable = filename.startswith("static/")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        return response

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if app.config["SAR_ENV"] == "production":
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers["X-Request-ID"] = g.get("request_id", "unknown")
        if app.config["SAR_ENV"] == "production" or request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        record_request(app, request, response.status_code)
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        payload = {"error": "upload_too_large", "message": "The upload exceeds the configured size limit."}
        if request.path.startswith("/api/"):
            return jsonify(payload), 413
        return render_template("production_dashboard.html", project=None, counts={}, compounds=[], measurements=[]), 413

    @app.errorhandler(HTTPException)
    def http_error(_error):
        if request.path.startswith("/api/"):
            status_code = int(_error.code or 500)
            error_code = "not_found" if status_code == 404 else "http_error"
            return jsonify(error=error_code, message="Request could not be completed."), status_code
        return _error

    @app.errorhandler(Exception)
    def unhandled_error(_error):
        app.logger.exception("unhandled request error", extra={"request_id": g.get("request_id")})
        if request.path.startswith("/api/"):
            return jsonify(error="internal_error", message="An internal error occurred."), 500
        return "Internal server error", 500

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if app.config["SAR_ENV"] != "production":
            return redirect(url_for("index"))
        next_path = request.args.get("next", "/")
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"
        if request.method == "POST":
            try:
                validate_csrf(request)
            except CSRFError:
                return render_template("login.html", error="The sign-in form expired. Try again.", next_path=next_path), 400
            email = str(request.form.get("email", ""))
            password = str(request.form.get("password", ""))
            if authenticate(email, password):
                _ensure_database(app)
                if ensure_local_user(app.config["DATABASE_PATH"], email):
                    login_session(email)
                    return redirect(next_path)
            return render_template("login.html", error="Email or password is not valid.", next_path=next_path), 401
        return render_template("login.html", error=None, next_path=next_path)

    @app.post("/logout")
    def logout():
        if app.config["SAR_ENV"] == "production":
            try:
                validate_csrf(request)
            except CSRFError:
                return jsonify(error="csrf_failed", message="A valid CSRF token is required."), 400
        logout_session()
        return redirect(url_for("login")) if app.config["SAR_ENV"] == "production" else jsonify(status="logged_out")

    @app.get("/")
    def index():
        if app.config["SAR_DEMO_MODE"]:
            demo_project = dict(PROJECT, data_origin="demo")
            return render_template(
                "index.html",
                project=demo_project,
                compounds=COMPOUNDS,
                insights=INSIGHTS,
                matrix=MATRIX,
                recommendations=RECOMMENDATIONS,
                quality_flags=QUALITY_FLAGS,
                timeline=TIMELINE,
            )
        _ensure_database(app)
        project = _get_project(app, request.args.get("project_id"))
        return render_template(
            "production_overview.html",
            **_production_page_context(app, project),
            page_view="overview",
        )

    @app.route("/workspace/new", methods=["GET", "POST"])
    def production_new_project():
        if app.config["SAR_DEMO_MODE"]:
            return redirect(url_for("index"))
        _ensure_database(app)
        name = str(request.form.get("name", "")).strip()
        description = str(request.form.get("description", "")).strip()
        error = None
        if request.method == "POST":
            if len(name) < 2:
                error = "Give the new project a name with at least two characters."
            else:
                try:
                    project = _create_project_record(app, name, description)
                except Exception as exc:
                    if "UNIQUE constraint failed" in str(exc):
                        error = "That project could not be created. Try again with a different name."
                    else:
                        raise
                else:
                    return redirect(url_for("index", project_id=project["id"]))
        return render_template(
            "production_new_project.html",
            **_production_page_context(app, None),
            page_view="new",
            form_name=name,
            form_description=description,
            project_error=error,
        )

    @app.get("/workspace/legacy")
    def legacy_production_dashboard():
        if app.config["SAR_DEMO_MODE"]:
            return redirect(url_for("index"))
        _ensure_database(app)
        project = _get_project(app, request.args.get("project_id"))
        return render_template(
            "production_dashboard.html",
            **_production_page_context(app, project),
            page_view="legacy",
        )

    @app.get("/workspace/<view>")
    def production_workspace(view: str):
        if app.config["SAR_DEMO_MODE"]:
            return redirect(url_for("index"))
        if view not in {"overview", "evidence", "summaries", "explore", "analysis", "designs", "sar"}:
            return jsonify(error="workspace_not_found", message="Unknown production workspace page."), 404
        _ensure_database(app)
        project = _get_project(app, request.args.get("project_id"))
        context = _production_page_context(app, project)
        if view == "analysis" and project is not None and context.get("counts", {}).get("analysis_runs"):
            # Built only here: the fingerprint pairs are not needed on other pages.
            context.update(_production_pattern_explorer(project, context))
        return render_template(
            f"production_{view}.html",
            **context,
            page_view=view,
        )

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service="sar-workbench", data_origin="demo" if app.config["SAR_DEMO_MODE"] else "production")

    @app.get("/api/v1/metrics")
    def metrics_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Operational production metrics are disabled in demo mode."), 409
        return jsonify(metrics=metrics_snapshot(app), data_origin="operational")

    @app.get("/api/v1/readyz")
    def readyz():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(status="ready", mode="demo", data_origin="demo")
        try:
            version = _ensure_database(app)
            if not database_ready(app.config["DATABASE_PATH"]):
                raise RuntimeError("database schema is not ready")
            return jsonify(status="ready", mode=app.config["SAR_ENV"], schema_version=version)
        except Exception:
            app.logger.exception("readiness check failed", extra={"request_id": g.get("request_id")})
            return jsonify(status="not_ready", error="database_unavailable", message="Database is not ready."), 503

    @app.get("/api/overview")
    def overview():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(
                project=dict(PROJECT, data_origin="demo"),
                compounds=COMPOUNDS,
                insights=INSIGHTS,
                matrix=MATRIX,
                recommendations=RECOMMENDATIONS,
                quality_flags=QUALITY_FLAGS,
                timeline=TIMELINE,
                hypotheses=HYPOTHESES,
                data_origin="demo",
            )
        _ensure_database(app)
        project = _get_project(app, request.args.get("project_id"))
        if project is None:
            return jsonify(project=None, data_origin="production", counts={})
        return jsonify(
            project=project,
            counts=_production_counts(app, project["id"]),
            data_origin="production",
            analytics_status="not_run",
        )

    @app.get("/api/v1/search/compounds")
    def search_compounds_api():
        _ensure_database(app)
        project_id = str(request.args.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        started = time.monotonic()
        try:
            result = search_compounds(
                app.config["DATABASE_PATH"],
                project_id,
                request.args.get("q", ""),
                limit=request.args.get("limit"),
                offset=request.args.get("offset"),
            )
        except SearchValidationError as exc:
            return jsonify(error="invalid_search", message=str(exc)), 422
        result["search_ms"] = round((time.monotonic() - started) * 1000, 3)
        result["data_origin"] = "imported"
        return jsonify(result)

    @app.post("/api/v1/search/substructure")
    def substructure_search_api():
        """Find project compounds containing a drawn or typed substructure; nothing is saved."""
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        if _get_project(app, project_id) is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        try:
            query = build_substructure_query(
                molblock=payload.get("molblock"),
                text=payload.get("query"),
                text_format=payload.get("query_format", "auto"),
            )
            result = search_substructure(app.config["DATABASE_PATH"], project_id, query, limit=payload.get("limit"))
        except SubstructureQueryError as exc:
            return jsonify(error=exc.code, message=str(exc)), 422
        return jsonify(result)

    @app.post("/api/v1/structure/sketch")
    def structure_sketch_api():
        """2-D molfile for loading a compound, its ring scaffold, or typed SMILES into the sketcher."""
        _ensure_database(app)
        payload = _json_payload()
        compound_id = str(payload.get("compound_id") or "").strip()
        smiles = str(payload.get("smiles") or "").strip()
        registration_id = None
        if compound_id:
            project_id = str(payload.get("project_id") or "").strip()
            if _get_project(app, project_id) is None or not _project_access(app, project_id, "viewer"):
                return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
            record = substructure_compound(app.config["DATABASE_PATH"], project_id, compound_id)
            if record is None:
                return jsonify(error="compound_not_found", message="That compound is not in this project."), 404
            smiles = record["isomeric_smiles"]
            registration_id = record["registration_id"]
        try:
            result = sketch_from_smiles(smiles, payload.get("part", "molecule"))
        except SubstructureQueryError as exc:
            return jsonify(error=exc.code, message=str(exc)), 422
        result["registration_id"] = registration_id
        return jsonify(result=result, data_origin="derived")

    @app.post("/api/v1/structure/core")
    def structure_core_api():
        """Core SMARTS for a drawn or typed structure (attachment points removed); nothing is saved."""
        payload = _json_payload()
        try:
            query = build_substructure_query(
                molblock=payload.get("molblock"),
                text=payload.get("query"),
                text_format=payload.get("query_format", "auto"),
            )
        except SubstructureQueryError as exc:
            return jsonify(error=exc.code, message=str(exc)), 422
        if not query["core_smarts"]:
            return jsonify(error="invalid_core", message=query["core_issue"] or "This structure cannot be used as a core."), 422
        result = {
            "core_smarts": query["core_smarts"],
            "attachment_points": query["core_attachment_points"],
            "atom_count": query["atom_count"],
            "source": query["source"],
        }
        return jsonify(result=result, data_origin="derived")

    @app.get("/api/compounds")
    @app.get("/api/v1/compounds")
    def compounds():
        query = request.args.get("q", "").strip().lower()
        status = request.args.get("status", "all").strip().lower()
        endpoint = request.args.get("endpoint", "all").strip().lower()
        if not app.config["SAR_DEMO_MODE"]:
            _ensure_database(app)
            project = _get_project(app, request.args.get("project_id"))
            if project is None:
                return jsonify(error="project_required", message="Provide a valid project_id."), 400
            results = _production_compounds(app, project["id"])
            if query:
                results = [
                    compound
                    for compound in results
                    if query in " ".join(str(value) for value in compound.values()).lower()
                ]
            if status != "all":
                results = [compound for compound in results if compound["lifecycle_status"].lower() == status]
            return jsonify(results=results, count=len(results), data_origin="production")

        results = COMPOUNDS
        if query:
            results = [
                compound
                for compound in results
                if query in " ".join(
                    str(compound.get(key, ""))
                    for key in ("id", "series", "scaffold", "status", "rationale")
                ).lower()
            ]
        if status != "all":
            results = [compound for compound in results if compound["status"].lower() == status]
        if endpoint == "cellular":
            results = [compound for compound in results if compound.get("cellular")]
        elif endpoint == "adme":
            results = [compound for compound in results if compound.get("clearance")]
        return jsonify(results=results, count=len(results), data_origin="demo")

    @app.get("/api/matrix/<endpoint>")
    def matrix(endpoint: str):
        key = endpoint.strip().lower()
        if not app.config["SAR_DEMO_MODE"]:
            return jsonify(
                error="analysis_not_run",
                message="Production SAR matrices are withheld until a versioned analysis run exists.",
                data_origin="production",
            ), 501
        if key not in MATRIX:
            return jsonify(error="Unknown endpoint", available=sorted(MATRIX)), 404
        return jsonify(MATRIX[key], data_origin="demo")

    @app.post("/api/hypotheses")
    def save_hypothesis_legacy():
        if app.config["SAR_DEMO_MODE"]:
            payload = _json_payload()
            statement = str(payload.get("statement", "")).strip()
            rationale = str(payload.get("rationale", "")).strip()
            if len(statement) < 8:
                return jsonify(error="Add a hypothesis with at least eight characters."), 400
            hypothesis = {
                "id": f"H-{len(HYPOTHESES) + 1:03d}",
                "statement": statement,
                "rationale": rationale,
                "status": "Untested",
                "created_at": _now(),
                "data_origin": "demo",
            }
            HYPOTHESES.append(hypothesis)
            return jsonify(hypothesis=hypothesis), 201
        return _save_production_hypothesis(app)

    @app.post("/api/v1/structure/standardize")
    def standardize_api():
        payload = _json_payload()
        try:
            result = standardize_structure(
                payload.get("structure", ""), payload.get("input_format", "smiles"), payload.get("profile", "rdkit-preserve-stereo-v1")
            )
        except StructureValidationError as exc:
            return jsonify(error=exc.code, message=str(exc)), 422
        return jsonify(result=result.as_dict(), data_origin="derived")

    @app.post("/api/v1/structure/replace-substituent")
    def replace_substituent_api():
        """Propose an analogue by swapping one R-site group; nothing is saved."""
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        if _get_project(app, project_id) is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        try:
            smiles = _replace_substituent_for_compound(
                app,
                project_id,
                str(payload.get("compound_id") or "").strip(),
                str(payload.get("site") or "").strip(),
                str(payload.get("replacement") or "").strip(),
                str(payload.get("smiles") or "").strip(),
            )
            result = standardize_structure(smiles, "smiles")
        except SarDiscoveryError as exc:
            return jsonify(error="substituent_replacement_invalid", message=str(exc)), 422
        except StructureValidationError as exc:
            return jsonify(error=exc.code, message=str(exc)), 422
        return jsonify(result=result.as_dict(), data_origin="curated_proposal")

    @app.post("/api/v1/projects")
    def create_project_api():
        _ensure_database(app)
        payload = _json_payload()
        name = str(payload.get("name", "")).strip()
        description = str(payload.get("description", "")).strip()
        if len(name) < 2:
            return jsonify(error="invalid_project_name", message="Project name must contain at least two characters."), 422
        project_id = str(payload.get("id") or _id("prj"))
        try:
            project = _create_project_record(app, name, description, project_id)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return jsonify(error="project_exists", message="Project identifier already exists."), 409
            raise
        return jsonify(project=project), 201

    @app.get("/api/v1/projects")
    def list_projects_api():
        _ensure_database(app)
        if app.config["SAR_ENV"] == "production":
            projects = accessible_projects(app.config["DATABASE_PATH"], current_user_id(app.config["DATABASE_PATH"]))
        else:
            with read_connection(app.config["DATABASE_PATH"]) as connection:
                projects = [_row_dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY created_at DESC")]
        return jsonify(projects=projects, data_origin="production")

    @app.get("/api/v1/series")
    def list_series_api():
        _ensure_database(app)
        project_id = str(request.args.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        context = series_context(app.config["DATABASE_PATH"], project_id)
        return jsonify(series=context["series"], compound_series=context["compound_series"], data_origin="curated")

    @app.post("/api/v1/series")
    def create_series_api():
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            result = create_series(
                app.config["DATABASE_PATH"],
                project_id,
                payload.get("name"),
                description=payload.get("description", ""),
                membership_source=payload.get("membership_source", "curated"),
                rationale=payload.get("rationale", ""),
                compound_ids=payload.get("compound_ids", []),
                membership_status=payload.get("membership_status", "included"),
                created_by=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except SeriesError as exc:
            return jsonify(error="invalid_series", message=str(exc)), 422
        return jsonify(series=result, data_origin="curated"), 201

    @app.get("/api/v1/series/<series_id>")
    def get_series_api(series_id: str):
        _ensure_database(app)
        project_id = str(request.args.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        result = series_context(app.config["DATABASE_PATH"], project_id)
        series = next((item for item in result["series"] if item["id"] == series_id), None)
        if series is None:
            return jsonify(error="series_not_found", message="Series is not available in this project."), 404
        return jsonify(series=series, data_origin="curated")

    @app.post("/api/v1/series/<series_id>/versions")
    def create_series_version_api(series_id: str):
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            result = create_series_version(
                app.config["DATABASE_PATH"],
                project_id,
                series_id,
                membership_source=payload.get("membership_source", "curated"),
                rationale=payload.get("rationale", ""),
                compound_ids=payload.get("compound_ids", []),
                membership_status=payload.get("membership_status", "included"),
                created_by=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except SeriesNotFoundError as exc:
            return jsonify(error="series_not_found", message=str(exc)), 404
        except SeriesError as exc:
            return jsonify(error="invalid_series_version", message=str(exc)), 422
        return jsonify(series=result, data_origin="curated"), 201

    @app.get("/api/v1/projects/<project_id>/members")
    def list_project_members_api(project_id: str):
        _ensure_database(app)
        if _get_project(app, project_id) is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Project access is not available."), 403
        return jsonify(members=list_project_members(app.config["DATABASE_PATH"], project_id), data_origin="production")

    @app.post("/api/v1/projects/<project_id>/members")
    def add_project_member_api(project_id: str):
        _ensure_database(app)
        if _get_project(app, project_id) is None or not _project_access(app, project_id, "owner"):
            return jsonify(error="project_forbidden", message="Owner access is required to manage project members."), 403
        payload = _json_payload()
        try:
            member = add_project_member(
                app.config["DATABASE_PATH"],
                project_id,
                str(payload.get("email", "")),
                display_name=str(payload.get("display_name", "")),
                role=str(payload.get("role", "viewer")),
            )
        except ValueError as exc:
            return jsonify(error="invalid_project_member", message=str(exc)), 422
        with transaction(app.config["DATABASE_PATH"]) as connection:
            connection.execute(
                """
                INSERT INTO audit_events
                    (id, actor_user_id, project_id, operation, resource_type, resource_id, request_id, outcome, created_at)
                VALUES (?, ?, ?, 'project_member_upsert', 'project_member', ?, ?, 'success', ?)
                """,
                (_id("audit"), current_user_id(app.config["DATABASE_PATH"]), project_id, member["user_id"], g.get("request_id"), _now()),
            )
        return jsonify(member=member, data_origin="curated"), 201

    @app.post("/api/v1/imports/profile")
    def profile_import_api():
        _ensure_database(app)
        upload = request.files.get("file")
        payload = _json_payload()
        if upload:
            content = upload.read()
            filename = Path(upload.filename or "upload.csv").name[:255]
        else:
            csv_text = payload.get("csv_text")
            if not isinstance(csv_text, str):
                return jsonify(error="file_required", message="Provide a multipart file or csv_text for profiling."), 400
            content = csv_text.encode("utf-8")
            filename = Path(str(payload.get("filename", "upload.csv"))).name[:255]
        try:
            profile = profile_upload(
                content,
                filename,
                max_bytes=app.config["MAX_CONTENT_LENGTH"],
            )
        except UploadFormatError as exc:
            return jsonify(error="invalid_upload_format", message=str(exc)), 422
        return jsonify(profile=profile, data_origin="quarantined_import"), 200

    @app.post("/api/v1/imports/preview")
    def preview_import_api():
        _ensure_database(app)
        upload = request.files.get("file")
        payload = _json_payload()
        if upload:
            content = upload.read()
            filename = Path(upload.filename or "upload.csv").name[:255]
            content_type = upload.mimetype or "text/csv"
        else:
            csv_text = payload.get("csv_text")
            if not isinstance(csv_text, str):
                return jsonify(error="file_required", message="Provide a multipart file or csv_text for preview."), 400
            content = csv_text.encode("utf-8")
            filename = Path(str(payload.get("filename", "upload.csv"))).name[:255]
            content_type = "text/csv"
        raw_project_id = request.form.get("project_id") if upload else payload.get("project_id")
        project_id = str(raw_project_id).strip() if raw_project_id else None
        if project_id:
            if _get_project(app, project_id) is None:
                return jsonify(error="project_required", message="Provide a valid project_id."), 422
            if not _project_access(app, project_id, "editor"):
                return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        raw_sheet_name = request.form.get("sheet_name") if upload else payload.get("sheet_name")
        sheet_name = str(raw_sheet_name).strip() if raw_sheet_name else None
        raw_header_row = request.form.get("header_row") if upload else payload.get("header_row")
        raw_data_start_row = request.form.get("data_start_row") if upload else payload.get("data_start_row")
        raw_mapping_overrides = request.form.get("mapping_overrides") if upload else payload.get("mapping_overrides")
        raw_formula_acknowledged = request.form.get("formula_acknowledged") if upload else payload.get("formula_acknowledged")
        try:
            header_row = int(raw_header_row) if raw_header_row not in (None, "") else 1
            data_start_row = int(raw_data_start_row) if raw_data_start_row not in (None, "") else None
        except (TypeError, ValueError):
            return jsonify(error="invalid_sheet_selection", message="header_row and data_start_row must be integers."), 422
        try:
            mapping_overrides = _mapping_overrides(raw_mapping_overrides)
        except ValueError as exc:
            return jsonify(error="invalid_mapping_override", message=str(exc)), 422
        formula_acknowledged = _boolean_value(raw_formula_acknowledged)
        try:
            preview = create_preview(
                app.config["DATABASE_PATH"],
                app.config["UPLOAD_DIR"],
                content,
                filename,
                content_type,
                app.config["MAX_CONTENT_LENGTH"],
                sheet_name=sheet_name,
                header_row=header_row,
                data_start_row=data_start_row,
                mapping_overrides=mapping_overrides,
                formula_acknowledged=formula_acknowledged,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
                project_id=project_id,
            )
            if app.config["SAR_ENV"] == "production":
                owner_user_id = current_user_id(app.config["DATABASE_PATH"])
                with transaction(app.config["DATABASE_PATH"]) as connection:
                    connection.execute(
                        "UPDATE import_batches SET created_by_user_id = ? WHERE id = ?",
                        (owner_user_id, preview["import_id"]),
                    )
                preview["created_by_user_id"] = owner_user_id
        except SheetSelectionRequired as exc:
            return jsonify(error="worksheet_selection_required", message=str(exc), profile=exc.profile), 422
        except DuplicateImportError as exc:
            return jsonify(
                error="duplicate_import",
                message=str(exc),
                import_id=exc.import_id,
                import_status=exc.status,
            ), 422
        except ImportValidationError as exc:
            return jsonify(error="invalid_import", message=str(exc)), 422
        return jsonify(preview), 201



    @app.post("/api/v1/sar/discover")
    def discover_sar_series_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Production SAR discovery is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        endpoint_key = str(payload.get("endpoint_key") or "").strip()
        mode = str(payload.get("mode") or "discover").strip().lower()
        selected_compound_id = str(payload.get("selected_compound_id") or "").strip() or None
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for SAR discovery."), 403
        try:
            with read_connection(app.config["DATABASE_PATH"]) as connection:
                compounds = [
                    _row_dict(row)
                    for row in connection.execute(
                        """
                        SELECT c.id AS compound_id, c.registration_id, c.preferred_name,
                               s.isomeric_smiles, s.rendered_svg
                        FROM compounds c
                        JOIN structure_records s ON s.compound_id = c.id
                        WHERE c.project_id = ?
                        ORDER BY c.registration_id
                        """,
                        (project_id,),
                    )
                ]
                summaries = [
                    _row_dict(row)
                    for row in connection.execute(
                        """
                        SELECT ms.id, ms.compound_id, ms.compatibility_key,
                               ms.canonical_unit, ms.summary_state, ms.summary_value,
                               ms.summary_qualifier, ms.source_measurement_ids_json,
                               ms.created_at, c.registration_id
                        FROM measurement_summaries ms
                        JOIN compounds c ON c.id = ms.compound_id
                        WHERE c.project_id = ? AND ms.compatibility_key = ?
                        ORDER BY ms.created_at DESC, ms.id DESC
                        """,
                        (project_id, endpoint_key),
                    )
                ]
            for summary in summaries:
                summary["source_measurement_ids"] = json.loads(summary.pop("source_measurement_ids_json") or "[]")
            result = discover_series(
                compounds,
                summaries,
                endpoint_key,
                mode=mode,
                selected_compound_id=selected_compound_id,
            )
            for item in result["series"]:
                positions = [
                    (position["scaffold_atoms"][0], position["label"])
                    for position in item.get("positions", [])
                    if position.get("scaffold_atoms")
                ]
                try:
                    item["scaffold_svg"] = render_scaffold_positions_svg(item["scaffold_smiles"], positions)
                except StructureValidationError:
                    item["scaffold_svg"] = ""
                for member in item["members"]:
                    for fragment in member["structural_change"].get("fragments", []):
                        # Draw only substituents without a common name; named ones read better as text.
                        if not fragment.get("name") and fragment.get("attached_smiles"):
                            fragment["svg"] = render_fragment_svg(fragment["attached_smiles"], fragment.get("label", "R"))
        except SarDiscoveryError as exc:
            return jsonify(error="sar_discovery_invalid", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/compound-relationships")
    def list_compound_relationships_api():
        _ensure_database(app)
        project_id = str(request.args.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for compound relationships."), 403
        return jsonify(
            relationships=list_relationships(app.config["DATABASE_PATH"], project_id),
            data_origin="imported",
        )

    @app.post("/api/v1/compound-relationships/preview")
    def preview_compound_relationships_api():
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required to review compound relationships."), 403
        try:
            preview = preview_relationships(
                app.config["DATABASE_PATH"],
                project_id,
                pair_text=payload.get("pair_text"),
                pairs=payload.get("pairs"),
                relationship_type=str(payload.get("relationship_type") or "prodrug_of"),
            )
        except ProdrugRelationshipError as exc:
            return jsonify(error="invalid_relationship_mapping", message=str(exc), details=exc.details), 422
        return jsonify(preview), 200

    @app.post("/api/v1/compound-relationships")
    def save_compound_relationships_api():
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required to save compound relationships."), 403
        try:
            result = save_relationships(
                app.config["DATABASE_PATH"],
                project_id,
                pair_text=payload.get("pair_text"),
                pairs=payload.get("pairs"),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
                request_id=g.get("request_id"),
            )
        except ProdrugRelationshipError as exc:
            return jsonify(error="invalid_relationship_mapping", message=str(exc), details=exc.details), 422
        return jsonify(result), 201

    @app.post("/api/v1/analysis/prodrug-comparison")
    def compare_prodrug_relationships_api():
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        endpoint_key = str(payload.get("endpoint_key") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for prodrug comparison."), 403
        try:
            result = compare_relationships(app.config["DATABASE_PATH"], project_id, endpoint_key)
        except ProdrugRelationshipError as exc:
            return jsonify(error="invalid_prodrug_comparison", message=str(exc)), 422
        return jsonify(result), 201

    @app.post("/api/v1/imports/<import_id>/commit")
    def commit_import_api(import_id: str):
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id:
            return jsonify(error="project_required", message="project_id is required to commit an import."), 422
        if _get_project(app, project_id) is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        if not _import_access(app, import_id, project_id=project_id):
            return jsonify(error="import_forbidden", message="This import is not available to the authenticated user."), 403
        try:
            result = commit_import(
                app.config["DATABASE_PATH"],
                import_id,
                project_id,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except ImportConflictError as exc:
            return jsonify(error="import_conflict", message=str(exc)), 409
        except ImportValidationError as exc:
            return jsonify(error="invalid_import", message=str(exc)), 422
        return jsonify(result), 200

    @app.get("/api/v1/imports/<import_id>")
    def get_import_api(import_id: str):
        _ensure_database(app)
        with read_connection(app.config["DATABASE_PATH"]) as connection:
            batch = connection.execute(
                """
                SELECT ib.*, sd.filename AS source_filename, sd.file_format AS source_file_format,
                       sd.profile_json AS source_profile_json, sd.content_type AS source_content_type,
                       sd.byte_size AS source_byte_size, sd.sha256 AS source_sha256
                FROM import_batches ib
                JOIN source_documents sd ON sd.id = ib.source_document_id
                WHERE ib.id = ?
                """,
                (import_id,),
            ).fetchone()
            if batch is None:
                return jsonify(error="import_not_found"), 404
            if not _import_access(app, import_id, project_id=batch["project_id"]):
                return jsonify(error="import_forbidden", message="This import is not available to the authenticated user."), 403
            rows = []
            for row in connection.execute(
                "SELECT source_row_id, validation_json, source_location_json, status FROM import_rows WHERE import_batch_id = ? ORDER BY CAST(source_row_id AS INTEGER)",
                (import_id,),
            ):
                row_result = _row_dict(row)
                row_result["validation"] = json.loads(row_result.pop("validation_json") or "{}")
                row_result["source_location"] = json.loads(row_result.pop("source_location_json") or "{}")
                rows.append(row_result)
        result = _row_dict(batch)
        result["mapping"] = json.loads(result.pop("mapping_json"))
        result["mapping_confidence"] = json.loads(result.pop("mapping_confidence_json", "{}") or "{}")
        result["mapping_issues"] = json.loads(result.pop("mapping_issues_json", "[]") or "[]")
        result["profile"] = json.loads(result.pop("source_profile_json", "{}") or "{}")
        result["source_document"] = {
            "filename": result.pop("source_filename"),
            "file_format": result.pop("source_file_format"),
            "content_type": result.pop("source_content_type"),
            "byte_size": result.pop("source_byte_size"),
            "sha256": result.pop("source_sha256"),
        }
        result["rows"] = rows
        result["data_origin"] = "imported"
        return jsonify(result)

    @app.get("/api/v1/measurements")
    def measurements_api():
        _ensure_database(app)
        project = _get_project(app, request.args.get("project_id"))
        if project is None:
            return jsonify(error="project_required", message="Provide a valid project_id."), 400
        return jsonify(
            results=_production_measurements(app, project["id"]),
            data_origin="imported",
        )

    @app.get("/api/v1/hypotheses")
    def list_hypotheses_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(hypotheses=HYPOTHESES, data_origin="demo")
        _ensure_database(app)
        project = _get_project(app, request.args.get("project_id"))
        if project is None:
            return jsonify(error="project_required"), 400
        with read_connection(app.config["DATABASE_PATH"]) as connection:
            hypotheses = [
                _row_dict(row)
                for row in connection.execute(
                    "SELECT * FROM hypotheses WHERE project_id = ? ORDER BY created_at DESC", (project["id"],)
                )
            ]
        return jsonify(hypotheses=hypotheses, data_origin="imported")

    @app.post("/api/v1/hypotheses")
    def create_hypothesis_api():
        if app.config["SAR_DEMO_MODE"]:
            return save_hypothesis_legacy()
        return _save_production_hypothesis(app)

    @app.post("/api/v1/analysis/mmp")
    def create_mmp_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="MMP analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            result = run_mmp_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except AnalysisValidationError as exc:
            return jsonify(error="analysis_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/mmp/<run_id>")
    def get_mmp_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="MMP analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        result = get_mmp_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/analysis/mmp/<run_id>/claims")
    def create_mmp_claims_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Claims are disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "editor"):
            return jsonify(error="analysis_not_found"), 404
        try:
            result = create_claims_from_mmp(
                app.config["DATABASE_PATH"],
                run_id,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except AnalysisValidationError as exc:
            return jsonify(error="analysis_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/sar/claims")
    def list_claims_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Claims are disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = request.args.get("project_id")
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required"), 400
        with read_connection(app.config["DATABASE_PATH"]) as connection:
            claims = []
            for row in connection.execute(
                "SELECT * FROM sar_claims WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ):
                claim = _row_dict(row)
                claim["scope_definition"] = json.loads(claim.pop("scope_definition_json"))
                claim["effect"] = json.loads(claim.pop("effect_json"))
                claim["uncertainty"] = json.loads(claim.pop("uncertainty_json"))
                claim["data_origin"] = "derived"
                claims.append(claim)
        return jsonify(claims=claims, data_origin="derived")

    @app.get("/api/v1/measurement-summaries/<summary_id>")
    def get_measurement_summary_api(summary_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Measurement summaries are disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _summary_project_id(app, summary_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="summary_not_found"), 404
        result = get_measurement_summary(app.config["DATABASE_PATH"], summary_id)
        if result is None:
            return jsonify(error="summary_not_found"), 404
        return jsonify(result)

    @app.get("/api/v1/measurement-summaries")
    def list_measurement_summaries_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Measurement summaries are disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = request.args.get("project_id")
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required"), 400
        with read_connection(app.config["DATABASE_PATH"]) as connection:
            summary_ids = [
                row["id"]
                for row in connection.execute(
                    """
                    SELECT ms.id
                    FROM measurement_summaries AS ms
                    JOIN compounds AS c ON c.id = ms.compound_id
                    WHERE c.project_id = ?
                    ORDER BY ms.created_at DESC, ms.id
                    """,
                    (project_id,),
                )
            ]
        summaries = [get_measurement_summary(app.config["DATABASE_PATH"], summary_id) for summary_id in summary_ids]
        return jsonify(summaries=[item for item in summaries if item is not None], data_origin="derived")

    @app.post("/api/v1/measurement-summaries")
    def create_measurement_summary_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Measurement summaries are disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        measurement_ids = payload.get("measurement_ids")
        if not isinstance(measurement_ids, list) or not measurement_ids:
            return jsonify(error="measurement_ids_required", message="measurement_ids must be a non-empty list."), 422
        project_ids = _measurement_project_ids(app, [str(item) for item in measurement_ids])
        if len(project_ids) != 1:
            return jsonify(error="measurement_scope_invalid", message="All measurement IDs must exist in one project."), 422
        project_id = next(iter(project_ids))
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="measurement_forbidden", message="Editor access is required for summary creation."), 403
        try:
            result = create_measurement_summary(
                app.config["DATABASE_PATH"],
                measurement_ids,
                aggregation_method=str(payload.get("aggregation_method", "mean")),
                analysis_version=str(payload.get("analysis_version", "measurement-summary-v1")),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except MeasurementEngineError as exc:
            return jsonify(error="summary_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.post("/api/v1/measurement-summaries/project")
    def create_project_measurement_summaries_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Measurement summaries are disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            results = summarize_project_measurements(
                app.config["DATABASE_PATH"],
                project_id,
                aggregation_method=str(payload.get("aggregation_method", "mean")),
                analysis_version=str(payload.get("analysis_version", "measurement-summary-v1")),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except MeasurementEngineError as exc:
            return jsonify(error="summary_unavailable", message=str(exc)), 422
        return jsonify(summaries=results, count=len(results), data_origin="derived"), 201

    @app.post("/api/v1/analysis/rgroup")
    def create_rgroup_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="R-group analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        scaffold_smarts = str(payload.get("scaffold_smarts", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        if len(scaffold_smarts) < 2:
            return jsonify(error="scaffold_required", message="scaffold_smarts is required."), 422
        try:
            result = run_rgroup_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                scaffold_smarts,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except RGroupAnalysisError as exc:
            return jsonify(error="rgroup_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.post("/api/v1/analysis/pharmacophore-rgroup")
    def create_pharmacophore_rgroup_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Pharmacophore R-group analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        scaffold_smarts = str(payload.get("scaffold_smarts", "")).strip()
        reference_compound_id = str(payload.get("reference_compound_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            result = run_pharmacophore_rgroup_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                scaffold_smarts,
                reference_compound_id,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except PharmacophoreRGroupError as exc:
            return jsonify(error="pharmacophore_rgroup_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/pharmacophore-rgroup/<run_id>")
    def get_pharmacophore_rgroup_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Pharmacophore R-group analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        result = get_pharmacophore_rgroup_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/analysis/activity-cliffs")
    def create_activity_cliff_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Activity-cliff analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            result = run_activity_cliff_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                effect_threshold=float(payload.get("effect_threshold", 1.0)),
                similarity_threshold=float(payload.get("similarity_threshold", 0.8)),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except (RGroupAnalysisError, ValueError) as exc:
            return jsonify(error="cliff_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.post("/api/v1/analysis/selectivity")
    def create_selectivity_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Selectivity analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        primary_key = str(payload.get("primary_compatibility_key", "")).strip()
        comparator_key = str(payload.get("comparator_compatibility_key", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            result = run_selectivity_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                primary_key,
                comparator_key,
                selectivity_threshold=float(payload.get("selectivity_threshold", 1.0)),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except (SelectivityAnalysisError, ValueError) as exc:
            return jsonify(error="selectivity_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/selectivity/<run_id>")
    def get_selectivity_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Selectivity analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        result = get_selectivity_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/analysis/properties")
    def create_property_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Property analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        compound_ids = payload.get("compound_ids")
        if compound_ids is not None and not isinstance(compound_ids, list):
            return jsonify(error="compound_ids_invalid", message="compound_ids must be a list when provided."), 422
        try:
            result = run_property_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                compound_ids,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except PropertyAnalysisError as exc:
            return jsonify(error="property_analysis_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/properties/<run_id>")
    def get_property_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Property analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        result = get_property_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.get("/api/v1/predictions")
    def list_prediction_models_api():
        _ensure_database(app)
        project_id = str(request.args.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        return jsonify(models=list_prediction_models(app.config["DATABASE_PATH"], project_id), data_origin="derived")

    @app.post("/api/v1/predictions/train")
    def train_prediction_model_api():
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        holdout = payload.get("holdout_compound_ids")
        if holdout is not None and not isinstance(holdout, list):
            return jsonify(error="invalid_holdout", message="holdout_compound_ids must be a list."), 422
        try:
            model = train_prediction_model(
                app.config["DATABASE_PATH"],
                project_id,
                payload.get("compatibility_key"),
                holdout_compound_ids=holdout,
                neighbors=payload.get("neighbors"),
                min_similarity=payload.get("min_similarity"),
                min_neighbors=payload.get("min_neighbors"),
                min_training_compounds=payload.get("min_training_compounds"),
                min_validation_compounds=payload.get("min_validation_compounds"),
                min_coverage=payload.get("min_coverage"),
                max_mae=payload.get("max_mae"),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except PredictionError as exc:
            return jsonify(error="prediction_training_failed", message=str(exc)), 422
        return jsonify(model=model, data_origin="derived"), 201

    @app.get("/api/v1/predictions/<model_id>")
    def get_prediction_model_api(model_id: str):
        _ensure_database(app)
        project_id = str(request.args.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        model = get_prediction_model(app.config["DATABASE_PATH"], project_id, model_id)
        if model is None:
            return jsonify(error="prediction_model_not_found", message="Prediction model is not available in this project."), 404
        return jsonify(model=model, data_origin="derived")

    @app.post("/api/v1/predictions/<model_id>/predict")
    def predict_compounds_api(model_id: str):
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id") or "").strip()
        project = _get_project(app, project_id)
        if project is None or not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        compound_ids = payload.get("compound_ids")
        if not isinstance(compound_ids, list):
            return jsonify(error="compound_ids_required", message="compound_ids must be a list."), 422
        try:
            result = predict_compounds(app.config["DATABASE_PATH"], project_id, model_id, compound_ids)
        except PredictionNotFoundError as exc:
            return jsonify(error="prediction_model_not_found", message=str(exc)), 404
        except PredictionError as exc:
            return jsonify(error="prediction_blocked", message=str(exc)), 422
        return jsonify(result)

    @app.post("/api/v1/analysis/cellular-translation")
    def create_cellular_translation_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Cellular translation analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        biochemical_key = str(payload.get("biochemical_compatibility_key", "")).strip()
        cellular_key = str(payload.get("cellular_compatibility_key", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            from cellular_translation_engine import TranslationAnalysisError, run_translation_analysis
            result = run_translation_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                biochemical_key,
                cellular_key,
                translation_loss_threshold=float(payload.get("translation_loss_threshold", 1.0)),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except (TranslationAnalysisError, ValueError) as exc:
            return jsonify(error="translation_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/cellular-translation/<run_id>")
    def get_cellular_translation_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Cellular translation analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        from cellular_translation_engine import get_translation_run
        result = get_translation_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/analysis/adme")
    def create_adme_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="ADME analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        compatibility_keys = payload.get("compatibility_keys")
        if not isinstance(compatibility_keys, list):
            return jsonify(error="compatibility_keys_required", message="compatibility_keys must be a non-empty list."), 422
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            from adme_engine import ADMEAnalysisError, run_adme_analysis
            result = run_adme_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                compatibility_keys,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except ADMEAnalysisError as exc:
            return jsonify(error="adme_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/adme/<run_id>")
    def get_adme_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="ADME analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        from adme_engine import get_adme_run
        result = get_adme_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/analysis/pareto")
    def create_pareto_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Pareto analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        objectives = payload.get("objectives")
        if not isinstance(objectives, list):
            return jsonify(error="objectives_required", message="objectives must be a non-empty list."), 422
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            from pareto_engine import ParetoAnalysisError, run_pareto_analysis
            result = run_pareto_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                objectives,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except (ParetoAnalysisError, ValueError) as exc:
            return jsonify(error="pareto_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/pareto/<run_id>")
    def get_pareto_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Pareto analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        from pareto_engine import get_pareto_run
        result = get_pareto_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/analysis/contradictions")
    def create_contradiction_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Contradiction analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            from contradiction_engine import ContradictionAnalysisError, run_contradiction_analysis
            result = run_contradiction_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                value_tolerance=float(payload.get("value_tolerance", 0.5)),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except (ContradictionAnalysisError, ValueError) as exc:
            return jsonify(error="contradiction_analysis_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/contradictions/<run_id>")
    def get_contradiction_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Contradiction analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        from contradiction_engine import get_contradiction_run
        result = get_contradiction_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/analysis/information-gain")
    def create_information_gain_analysis_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Information-gap analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            result = run_information_gain_analysis(
                app.config["DATABASE_PATH"],
                project_id,
                payload.get("contexts"),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except (InformationGainAnalysisError, ValueError) as exc:
            return jsonify(error="information_gain_unavailable", message=str(exc)), 422
        return jsonify(result), 201

    @app.get("/api/v1/analysis/information-gain/<run_id>")
    def get_information_gain_analysis_api(run_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Information-gap analysis is disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = _analysis_project_id(app, run_id)
        if not project_id or not _project_access(app, project_id, "viewer"):
            return jsonify(error="analysis_not_found"), 404
        result = get_information_gain_run(app.config["DATABASE_PATH"], run_id)
        if result is None:
            return jsonify(error="analysis_not_found"), 404
        return jsonify(result)

    @app.post("/api/v1/recommendations/generate")
    def generate_recommendations_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Generated recommendations are disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        run_id = str(payload.get("information_gain_run_id", "")).strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not run_id:
            return jsonify(error="information_gain_run_required", message="information_gain_run_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        try:
            generated = generate_recommendations(
                app.config["DATABASE_PATH"],
                project_id,
                run_id,
                limit=payload.get("limit", 32),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except RecommendationValidationError as exc:
            return jsonify(error="recommendation_generation_unavailable", message=str(exc)), 422
        return jsonify(recommendations=generated, count=len(generated), data_origin="generated", review_required=True), 201

    @app.get("/api/v1/recommendations")
    def list_generated_recommendations_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Generated recommendations are disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = request.args.get("project_id", "").strip()
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 400
        if not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Project access is not available."), 403
        return jsonify(
            recommendations=list_generated_recommendations(app.config["DATABASE_PATH"], project_id),
            data_origin="generated",
            review_required=True,
        )

    @app.post("/api/v1/recommendations/<recommendation_id>/review")
    def review_generated_recommendation_api(recommendation_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Generated recommendations are disabled for illustrative data."), 409
        _ensure_database(app)
        with read_connection(app.config["DATABASE_PATH"]) as connection:
            recommendation = connection.execute(
                "SELECT project_id FROM generated_recommendations WHERE id = ?",
                (recommendation_id,),
            ).fetchone()
        if recommendation is None or not _project_access(app, recommendation["project_id"], "editor"):
            return jsonify(error="recommendation_not_found"), 404
        payload = _json_payload()
        try:
            result = review_generated_recommendation(
                app.config["DATABASE_PATH"],
                recommendation_id,
                payload.get("status", ""),
                review_note=str(payload.get("review_note", "")),
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except RecommendationValidationError as exc:
            return jsonify(error="invalid_recommendation_review", message=str(exc)), 422
        return jsonify(recommendation=result, data_origin="generated", experimentally_confirmed=False), 200

    @app.post("/api/v1/designs")
    def create_design_candidates_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Design candidates are disabled for illustrative data."), 409
        _ensure_database(app)
        payload = _json_payload()
        project_id = str(payload.get("project_id", "")).strip()
        candidates = payload.get("candidates")
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required", message="A valid project_id is required."), 422
        if not _project_access(app, project_id, "editor"):
            return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
        if not isinstance(candidates, list) or not candidates:
            return jsonify(error="candidates_required", message="candidates must be a non-empty list."), 422
        try:
            ranked = persist_candidates(
                app.config["DATABASE_PATH"],
                project_id,
                candidates,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]) if app.config["SAR_ENV"] == "production" else None,
            )
        except RecommendationValidationError as exc:
            return jsonify(error="invalid_design_candidate", message=str(exc)), 422
        return jsonify(candidates=ranked, count=len(ranked), data_origin="curated"), 201

    @app.get("/api/v1/designs")
    def list_design_candidates_api():
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Design candidates are disabled for illustrative data."), 409
        _ensure_database(app)
        project_id = request.args.get("project_id")
        if not project_id or _get_project(app, project_id) is None:
            return jsonify(error="project_required"), 400
        if not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Project access is not available."), 403
        return jsonify(candidates=list_candidates(app.config["DATABASE_PATH"], project_id), data_origin="curated")

    @app.get("/api/v1/exports/project/<project_id>")
    def export_project_api(project_id: str):
        if app.config["SAR_DEMO_MODE"]:
            return jsonify(error="demo_mode", message="Project exports are disabled for illustrative data."), 409
        _ensure_database(app)
        if _get_project(app, project_id) is None:
            return jsonify(error="project_not_found", message="Project access is not available."), 404
        if not _project_access(app, project_id, "viewer"):
            return jsonify(error="project_forbidden", message="Viewer access is required for this project."), 403
        export_format = request.args.get("format", "json").strip().lower()
        if export_format not in {"json", "csv"}:
            return jsonify(error="invalid_export_format", message="format must be json or csv."), 422
        try:
            bundle = build_project_export(
                app.config["DATABASE_PATH"],
                project_id,
                actor_user_id=current_user_id(app.config["DATABASE_PATH"]),
                max_rows=int(app.config.get("MAX_EXPORT_ROWS", MAX_EXPORT_ROWS)),
            )
        except ExportError as exc:
            return jsonify(error="export_unavailable", message=str(exc)), 422
        safe_project_id = "".join(character if character.isalnum() or character in "-_." else "_" for character in project_id)
        extension = "csv" if export_format == "csv" else "json"
        filename = f"sar-{safe_project_id}-{bundle['export_id']}.{extension}"
        if export_format == "csv":
            response = Response(export_project_csv(bundle), mimetype="text/csv; charset=utf-8")
        else:
            response = Response(json.dumps(bundle, sort_keys=False, default=str), mimetype="application/json")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-SAR-Export-Origin"] = "production_export"
        return response

    return app


def _ensure_database(app: Flask) -> int:
    settings = _settings_from_config(app.config)
    settings.ensure_runtime_directories()
    return apply_migrations(app.config["DATABASE_PATH"])


def _get_project(app: Flask, project_id: str | None) -> dict[str, Any] | None:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        if app.config["SAR_ENV"] == "production":
            user_id = current_user_id(app.config["DATABASE_PATH"])
            if not user_id:
                return None
            if project_id:
                row = connection.execute(
                    """
                    SELECT p.*
                    FROM projects AS p
                    JOIN project_members AS pm ON pm.project_id = p.id
                    WHERE p.id = ? AND pm.user_id = ?
                    """,
                    (project_id, user_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT p.*
                    FROM projects AS p
                    JOIN project_members AS pm ON pm.project_id = p.id
                    WHERE pm.user_id = ?
                    ORDER BY p.created_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
        elif project_id:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        else:
            row = connection.execute("SELECT * FROM projects ORDER BY created_at DESC LIMIT 1").fetchone()
        return _row_dict(row) if row else None


def _project_access(app: Flask, project_id: str, minimum_role: str = "viewer") -> bool:
    if app.config["SAR_ENV"] != "production":
        return True
    return has_project_access(
        app.config["DATABASE_PATH"],
        project_id,
        minimum_role=minimum_role,
        user_id=current_user_id(app.config["DATABASE_PATH"]),
    )


def _import_access(app: Flask, import_id: str, *, project_id: str | None = None) -> bool:
    if app.config["SAR_ENV"] != "production":
        return True
    user_id = current_user_id(app.config["DATABASE_PATH"])
    if not user_id:
        return False
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        row = connection.execute(
            "SELECT project_id, created_by_user_id FROM import_batches WHERE id = ?",
            (import_id,),
        ).fetchone()
    if row is None:
        return False
    if project_id and not _project_access(app, project_id, "viewer"):
        return False
    if row["project_id"] and project_id and row["project_id"] != project_id:
        return False
    if row["project_id"]:
        return _project_access(app, row["project_id"], "viewer")
    return row["created_by_user_id"] == user_id


def _analysis_project_id(app: Flask, run_id: str) -> str | None:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        row = connection.execute("SELECT project_id FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
    return row["project_id"] if row else None


def _summary_project_id(app: Flask, summary_id: str) -> str | None:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        row = connection.execute(
            """
            SELECT c.project_id
            FROM measurement_summaries AS ms
            JOIN compounds AS c ON c.id = ms.compound_id
            WHERE ms.id = ?
            """,
            (summary_id,),
        ).fetchone()
    return row["project_id"] if row else None


def _measurement_project_ids(app: Flask, measurement_ids: list[str]) -> set[str]:
    if not measurement_ids:
        return set()
    placeholders = ",".join("?" for _ in measurement_ids)
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT c.project_id
            FROM measurements AS m
            JOIN compounds AS c ON c.id = m.compound_id
            WHERE m.id IN ({placeholders})
            """,
            measurement_ids,
        ).fetchall()
    return {str(row["project_id"]) for row in rows}


def _production_counts(app: Flask, project_id: str) -> dict[str, int]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        counts = {}
        for key, table in (("compounds", "compounds"), ("measurements", "measurements"), ("imports", "import_batches")):
            if table == "compounds":
                row = connection.execute("SELECT COUNT(*) AS n FROM compounds WHERE project_id = ?", (project_id,)).fetchone()
            elif table == "measurements":
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS n FROM measurements m
                    JOIN compounds c ON c.id = m.compound_id WHERE c.project_id = ?
                    """,
                    (project_id,),
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) AS n FROM import_batches WHERE project_id = ?", (project_id,)).fetchone()
            counts[key] = int(row["n"])
        derived_rows = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM measurement_summaries ms JOIN compounds c ON c.id = ms.compound_id WHERE c.project_id = ?) AS summaries,
                (SELECT COUNT(*) FROM analysis_runs WHERE project_id = ?) AS analyses,
                (SELECT COUNT(*) FROM design_candidates WHERE project_id = ?) AS designs
            """,
            (project_id, project_id, project_id),
        ).fetchone()
        counts["measurement_summaries"] = int(derived_rows["summaries"])
        counts["analysis_runs"] = int(derived_rows["analyses"])
        counts["design_candidates"] = int(derived_rows["designs"])
        return counts


def _production_compounds(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.registration_id, c.lifecycle_status,
                   s.isomeric_smiles, s.canonical_smiles, s.stereochemistry_status,
                   s.rendered_svg, s.inchikey, s.warnings_json
            FROM compounds c
            JOIN structure_records s ON s.compound_id = c.id
            WHERE c.project_id = ?
            ORDER BY c.created_at DESC
            """,
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["warnings"] = json.loads(item.pop("warnings_json"))
        item["data_origin"] = "imported"
        result.append(item)
    return result


def _production_measurements(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT m.id, c.id AS compound_id, c.registration_id, ad.name AS assay_name,
                   ad.endpoint_code, ad.modality, ad.protocol_version,
                   ar.id AS assay_run_id, ar.run_date, ar.qc_status AS assay_qc_status,
                   ar.biological_context_json, sd.filename AS source_filename,
                   sd.sha256 AS source_sha256,
                   m.raw_value_text, m.value_numeric,
                   m.unit_ucum, m.qualifier, m.lower_bound, m.upper_bound, m.canonical_value,
                   m.canonical_unit, m.transform_id, m.missing_reason, m.source_row_id,
                   m.well_id, m.qc_status, m.created_at
            FROM measurements m
            JOIN compounds c ON c.id = m.compound_id
            JOIN assay_runs ar ON ar.id = m.assay_run_id
            JOIN assay_definitions ad ON ad.id = ar.assay_definition_id
            LEFT JOIN source_documents sd ON sd.id = ar.source_document_id
            WHERE c.project_id = ?
            ORDER BY m.created_at DESC
            LIMIT 200
            """,
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["biological_context"] = json.loads(item.pop("biological_context_json") or "{}")
        item["source_document"] = {
            "filename": item.pop("source_filename"),
            "sha256": item.pop("source_sha256"),
        }
        item["data_origin"] = "imported"
        result.append(item)
    return result


def _production_measurement_summaries(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT ms.*, c.registration_id
            FROM measurement_summaries AS ms
            JOIN compounds AS c ON c.id = ms.compound_id
            WHERE c.project_id = ?
            ORDER BY ms.created_at DESC, ms.id
            LIMIT 200
            """,
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        for column in ("missing_reasons_json", "source_measurement_ids_json", "assay_definition_ids_json", "assay_run_ids_json"):
            item[column.removesuffix("_json")] = json.loads(item.pop(column))
        item["value"] = item["summary_value"]
        item["unit"] = item["canonical_unit"]
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_pharmacophore_rgroup(app: Flask, project_id: str) -> dict[str, Any]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        run = connection.execute(
            """
            SELECT id FROM analysis_runs
            WHERE project_id = ? AND analysis_type = 'pharmacophore_rgroup'
              AND algorithm_version = ?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (project_id, PHARMACOPHORE_ANALYSIS_VERSION),
        ).fetchone()
    if run is None:
        return {"assignments": [], "positions": [], "data_origin": "derived"}
    result = get_pharmacophore_rgroup_run(app.config["DATABASE_PATH"], run["id"])
    result = result or {"assignments": [], "positions": [], "data_origin": "derived"}
    for assignment in result.get("assignments", []):
        for site in assignment.get("sites", []):
            attached = site.get("attached_smiles") or ""
            site["fragment_svg"] = render_fragment_svg(attached, site.get("label", "R")) if attached else ""
    return result


def _production_rgroup_assignments(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT ra.*, c.registration_id, ar.algorithm_version, s.isomeric_smiles
            FROM rgroup_assignments AS ra
            JOIN analysis_runs AS ar ON ar.id = ra.analysis_run_id
            JOIN compounds AS c ON c.id = ra.compound_id
            LEFT JOIN structure_records AS s ON s.compound_id = c.id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'rgroup'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'rgroup'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY c.registration_id
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["match_atoms"] = json.loads(item.pop("match_atoms_json"))
        item["assignments"] = json.loads(item.pop("assignments_json"))
        item["data_origin"] = "derived"
        result.append(item)
    _add_rgroup_display_groups(result)
    return result


def _add_rgroup_display_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Readable, position-consistent R-groups for one R-group run. Display only.

    Persisted labels number substituents per compound, so "R1" can be a
    different site in different compounds. Here every compound sharing a
    scaffold is renumbered by scaffold site (see ``consistent_substituents``),
    and each group is named (CF₃, OMe) or drawn. Returns the site list used for
    the scaffold drawing; the persisted assignment keeps its original labels.
    """
    from rdkit import Chem

    positions: list[dict[str, Any]] = []
    by_scaffold: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        item["display_groups"] = []
        item["display_positions"] = []
        # Unsubstituted compounds on the core (no assignments) still take part, so they show "H" per site.
        if item.get("scaffold_smarts") and item.get("status") == "assigned":
            by_scaffold[item["scaffold_smarts"]].append(item)
    for scaffold_smarts, group in by_scaffold.items():
        scaffold = Chem.MolFromSmarts(scaffold_smarts)
        try:
            changes = consistent_substituents(
                [Chem.MolFromSmiles(item.get("isomeric_smiles") or "") for item in group], scaffold
            )
        except Exception:
            continue
        sites = assign_position_labels(changes)
        if not positions:
            positions = sites
        for item, change in zip(group, changes):
            item["display_positions"] = sites
            fragments = sorted(change.get("fragments", []), key=lambda fragment: int(fragment["label"][1:]) if fragment.get("label", "")[1:].isdigit() else 0)
            item["display_groups"] = [
                {
                    "label": fragment.get("label", "R"),
                    "smiles": fragment["smiles"],
                    "name": fragment.get("name"),
                    "svg": render_fragment_svg(fragment["attached_smiles"], fragment.get("label", "R")) if fragment.get("attached_smiles") else "",
                }
                for fragment in fragments
            ]
    return positions


def _add_pair_changes(
    rgroup_assignments: list[dict[str, Any]],
    pairs: list[tuple[dict[str, Any], Any, Any]],
) -> None:
    """Describe each pair's change by R-site (e.g. R2: F → Cl) using the position-consistent groups.

    Sets ``pair["site_changes"]`` to a list of ``{"label", "a", "b"}`` where
    ``a``/``b`` are display groups (``None`` means hydrogen). It stays empty
    when either compound is outside the R-group run; the template then falls
    back to the changed-atom formula. Display only.
    """
    groups_for: dict[str, dict[str, dict[str, Any]]] = {}
    scaffold_for: dict[str, str] = {}
    for item in rgroup_assignments:
        if item.get("status") != "assigned":
            continue
        groups_for[item["registration_id"]] = {group["label"]: group for group in item.get("display_groups", [])}
        scaffold_for[item["registration_id"]] = item.get("scaffold_smarts") or ""
    for pair, first, second in pairs:
        pair["site_changes"] = []
        if first not in groups_for or second not in groups_for or scaffold_for[first] != scaffold_for[second]:
            continue
        a_groups, b_groups = groups_for[first], groups_for[second]
        labels = sorted(set(a_groups) | set(b_groups), key=lambda label: int(label[1:]) if label[1:].isdigit() else 0)
        pair["site_changes"] = [
            {"label": label, "a": a_groups.get(label), "b": b_groups.get(label)}
            for label in labels
            if (a_groups.get(label) or {}).get("smiles") != (b_groups.get(label) or {}).get("smiles")
        ]


def _production_activity_cliffs(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT ac.*, ca.registration_id AS compound_a_registration_id,
                   cb.registration_id AS compound_b_registration_id,
                   ar.algorithm_version
            FROM activity_cliffs AS ac
            JOIN analysis_runs AS ar ON ar.id = ac.analysis_run_id
            JOIN compounds AS ca ON ca.id = ac.compound_a_id
            JOIN compounds AS cb ON cb.id = ac.compound_b_id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'activity_cliff'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'activity_cliff'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY ABS(ac.effect_value) DESC, ac.id
            LIMIT 100
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["transformation"] = json.loads(item.pop("transformation_json"))
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_mmp(app: Flask, project_id: str) -> dict[str, Any]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        row = connection.execute(
            """
            SELECT id FROM analysis_runs
            WHERE project_id = ? AND analysis_type = 'mmp'
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        return {}
    result = get_mmp_run(app.config["DATABASE_PATH"], row["id"])
    pairs = (result or {}).get("pairs", [])
    # Pairs are persisted per assay but without the key itself; recover it from the first source measurement.
    first_ids = sorted({pair["measurement_a_ids"][0] for pair in pairs if pair.get("measurement_a_ids")})
    key_for: dict[str, str] = {}
    if first_ids:
        with read_connection(app.config["DATABASE_PATH"]) as connection:
            for start in range(0, len(first_ids), 500):
                chunk = first_ids[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                for key_row in connection.execute(
                    f"""
                    SELECT m.id, ad.compatibility_key FROM measurements m
                    JOIN assay_runs ar ON ar.id = m.assay_run_id
                    JOIN assay_definitions ad ON ad.id = ar.assay_definition_id
                    WHERE m.id IN ({placeholders})
                    """,
                    chunk,
                ):
                    key_for[key_row["id"]] = key_row["compatibility_key"]
    for pair in pairs:
        effect = pair.get("effect_value")
        pair["abs_effect"] = abs(float(effect)) if effect is not None else 0.0
        first = (pair.get("measurement_a_ids") or [None])[0]
        pair.setdefault("compatibility_key", key_for.get(first, "Unknown assay"))
    return result or {}


def _production_selectivity(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT so.*, c.registration_id, ar.algorithm_version,
                   ar.input_selection_json
            FROM selectivity_observations AS so
            JOIN analysis_runs AS ar ON ar.id = so.analysis_run_id
            JOIN compounds AS c ON c.id = so.compound_id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'selectivity'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'selectivity'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY c.registration_id
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["input_selection"] = json.loads(item.pop("input_selection_json"))
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_property_profiles(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT pp.*, c.registration_id, ar.algorithm_version
            FROM property_profiles AS pp
            JOIN analysis_runs AS ar ON ar.id = pp.analysis_run_id
            JOIN compounds AS c ON c.id = pp.compound_id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'properties'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'properties'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY c.registration_id
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["descriptors"] = json.loads(item.pop("descriptors_json"))
        item["uncertainty"] = json.loads(item.pop("uncertainty_json"))
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_translation_observations(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT tobs.*, c.registration_id, ar.algorithm_version, ar.input_selection_json
            FROM translation_observations AS tobs
            JOIN analysis_runs AS ar ON ar.id = tobs.analysis_run_id
            JOIN compounds AS c ON c.id = tobs.compound_id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'cellular_translation'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'cellular_translation'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY c.registration_id
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["input_selection"] = json.loads(item.pop("input_selection_json"))
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_adme_observations(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT ao.*, c.registration_id, ar.algorithm_version, ar.input_selection_json
            FROM adme_observations AS ao
            JOIN analysis_runs AS ar ON ar.id = ao.analysis_run_id
            JOIN compounds AS c ON c.id = ao.compound_id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'adme'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'adme'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY c.registration_id
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        for column in ("required_contexts_json", "context_values_json", "observed_contexts_json", "censored_contexts_json", "missing_contexts_json"):
            item[column.removesuffix("_json")] = json.loads(item.pop(column))
        item["input_selection"] = json.loads(item.pop("input_selection_json"))
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_pareto_observations(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT po.*, c.registration_id, ar.algorithm_version, ar.input_selection_json
            FROM pareto_observations AS po
            JOIN analysis_runs AS ar ON ar.id = po.analysis_run_id
            JOIN compounds AS c ON c.id = po.compound_id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'pareto'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'pareto'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY c.registration_id
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["objective_values"] = json.loads(item.pop("objective_values_json"))
        item["input_selection"] = json.loads(item.pop("input_selection_json"))
        item["is_pareto"] = bool(item["is_pareto"])
        item["status"] = "pareto" if item["is_pareto"] else ("complete_non_pareto" if item["objective_status"] == "complete" else "incomplete")
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_contradictions(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT co.*, c.registration_id, ar.algorithm_version, ar.input_selection_json
            FROM contradiction_observations AS co
            JOIN analysis_runs AS ar ON ar.id = co.analysis_run_id
            JOIN compounds AS c ON c.id = co.compound_id
            WHERE ar.project_id = ?
              AND ar.analysis_type = 'contradictions'
              AND ar.id = (
                  SELECT id FROM analysis_runs
                  WHERE project_id = ? AND analysis_type = 'contradictions'
                  ORDER BY created_at DESC, id DESC LIMIT 1
              )
            ORDER BY c.registration_id, co.compatibility_key
            """,
            (project_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["summary_ids"] = json.loads(item.pop("summary_ids_json"))
        item["values"] = json.loads(item.pop("values_json"))
        item["input_selection"] = json.loads(item.pop("input_selection_json"))
        item["data_origin"] = "derived"
        result.append(item)
    return result


def _production_hypotheses(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            "SELECT * FROM hypotheses WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        item["data_origin"] = "imported"
        result.append(item)
    return result


def _production_sar_endpoints(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT ms.compatibility_key, ms.canonical_unit,
                   COUNT(DISTINCT ms.compound_id) AS compound_count,
                   MAX(ms.created_at) AS latest_created_at
            FROM measurement_summaries AS ms
            JOIN compounds AS c ON c.id = ms.compound_id
            WHERE c.project_id = ?
            GROUP BY ms.compatibility_key, ms.canonical_unit
            ORDER BY ms.compatibility_key, ms.canonical_unit
            """,
            (project_id,),
        ).fetchall()
    endpoints = []
    for row in rows:
        item = _row_dict(row)
        raw_name = str(item["compatibility_key"]).split(":", 1)[0]
        unit = item.get("canonical_unit") or "unit not recorded"
        item["name"] = raw_name
        item["label"] = f"{raw_name} · {format_unit(unit)}"
        item["data_origin"] = "derived"
        endpoints.append(item)
    endpoints.sort(key=lambda item: (_endpoint_rank(item["name"], item.get("canonical_unit")), item.get("canonical_unit") or ""))
    return endpoints


_ANTITARGET_TOKENS = ("off", "herg", "cyp", "tox", "counter")


def _display_direction(name: str, unit: str | None) -> str:
    """Display-only default for which way an endpoint is 'better'.

    Used for colour shading only. Users can change it in the table, and it is
    never persisted or used in any calculation.
    """
    lowered = name.lower()
    log_scale = bool(unit) and str(unit).strip().lower().startswith("p")
    if any(token in lowered for token in _ANTITARGET_TOKENS):
        return "lower" if log_scale else "higher"
    if any(token in lowered for token in ("clint", "clearance", "efflux")):
        return "lower"
    if any(token in lowered for token in ("solub", "papp", "perm", "stability", "half")):
        return "higher"
    if log_scale:
        return "higher"
    if any(token in lowered for token in ("ic50", "ec50", "ki", "kd")):
        return "lower"
    return "none"


def _endpoint_rank(name: str, unit: str | None) -> tuple[int, str]:
    """Order endpoints the way SAR tables are usually read: potency, cellular, antitargets, then ADME."""
    lowered = name.lower()
    log_scale = bool(unit) and str(unit).strip().lower().startswith("p")
    if any(token in lowered for token in _ANTITARGET_TOKENS):
        rank = 2
    elif log_scale and "cell" in lowered:
        rank = 1
    elif log_scale or any(token in lowered for token in ("ic50", "ec50", "ki", "kd")):
        rank = 0
    else:
        rank = 3
    return rank, lowered


def _production_sar_matrix(app: Flask, project_id: str) -> dict[str, Any]:
    """Compound × endpoint read model built from the latest versioned summary per cell."""
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        compound_rows = connection.execute(
            """
            SELECT c.id, c.registration_id, c.preferred_name, s.isomeric_smiles,
                   s.rendered_svg, s.stereochemistry_status
            FROM compounds c
            JOIN structure_records s ON s.compound_id = c.id
            WHERE c.project_id = ?
            ORDER BY c.registration_id
            """,
            (project_id,),
        ).fetchall()
        summary_rows = connection.execute(
            """
            SELECT ms.id, ms.compound_id, ms.compatibility_key, ms.canonical_unit,
                   ms.summary_state, ms.summary_value, ms.summary_qualifier,
                   ms.lower_bound, ms.upper_bound, ms.eligible_measurement_count,
                   ms.censored_measurement_count, ms.missing_measurement_count,
                   ms.missing_reasons_json, ms.dispersion, ms.dispersion_method,
                   ms.aggregation_method, ms.analysis_version, ms.created_at
            FROM measurement_summaries ms
            JOIN compounds c ON c.id = ms.compound_id
            WHERE c.project_id = ?
            ORDER BY ms.created_at, ms.id
            """,
            (project_id,),
        ).fetchall()

    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    endpoint_keys: dict[tuple[str, str], dict[str, Any]] = {}
    for row in summary_rows:
        item = _row_dict(row)
        unit = item.get("canonical_unit") or ""
        endpoint_id = (item["compatibility_key"], unit)
        latest[(item["compound_id"], *endpoint_id)] = item
        if endpoint_id not in endpoint_keys:
            name = endpoint_name(item["compatibility_key"])
            endpoint_keys[endpoint_id] = {
                "id": f"{item['compatibility_key']}|{unit}",
                "compatibility_key": item["compatibility_key"],
                "name": name,
                "unit": unit,
                "unit_label": format_unit(unit),
                "direction": _display_direction(name, unit),
            }
    endpoints = sorted(endpoint_keys.values(), key=lambda item: (_endpoint_rank(item["name"], item["unit"]), item["unit"]))

    rows = []
    for compound in compound_rows:
        record = _row_dict(compound)
        cells = []
        for endpoint in endpoints:
            summary = latest.get((record["id"], endpoint["compatibility_key"], endpoint["unit"]))
            cells.append(_matrix_cell(summary, endpoint))
        rows.append(
            {
                "compound_id": record["id"],
                "registration_id": record["registration_id"],
                "preferred_name": record.get("preferred_name") or "",
                "isomeric_smiles": record["isomeric_smiles"],
                "rendered_svg": record["rendered_svg"],
                "stereo_label": humanize(record["stereochemistry_status"]),
                "cells": cells,
            }
        )
    return {"endpoints": endpoints, "rows": rows, "data_origin": "derived"}


def _matrix_cell(summary: dict[str, Any] | None, endpoint: dict[str, Any]) -> dict[str, Any]:
    if summary is None:
        return {"state": "not_tested", "display": "n.t.", "title": f"{endpoint['name']}: no result in this project"}
    unit = endpoint["unit"]
    state = str(summary["summary_state"])
    qualifier = summary.get("summary_qualifier") or "="
    value = summary.get("summary_value")
    n_total = int(summary["eligible_measurement_count"]) + int(summary["censored_measurement_count"]) + int(summary["missing_measurement_count"])
    bound = summary.get("lower_bound") if summary.get("lower_bound") is not None else summary.get("upper_bound")
    if value is not None and qualifier in {"=", "~"} and state.startswith("observed"):
        kind = "exact"
        display = format_number(value, unit)
    elif value is not None or bound is not None:
        kind = "censored"
        shown = value if value is not None else bound
        display = f"{qualifier} {format_number(shown, unit)}" if qualifier not in {"=", "~"} else format_number(shown, unit)
    else:
        kind = "missing"
        display = "—"
    details = [f"{endpoint['name']}", humanize(state)]
    details.append(f"n = {n_total} ({summary['eligible_measurement_count']} exact, {summary['censored_measurement_count']} threshold, {summary['missing_measurement_count']} missing)")
    if summary.get("dispersion") is not None:
        details.append(f"{humanize(summary['dispersion_method'])} {format_number(summary['dispersion'], unit)}")
    reasons = json.loads(summary.get("missing_reasons_json") or "[]")
    if reasons:
        details.append("Missing: " + ", ".join(str(reason) for reason in reasons))
    details.append(f"{summary['aggregation_method']} · {summary['analysis_version']}")
    return {
        "state": kind,
        "display": display,
        "value": float(value) if kind == "exact" else None,
        "sort_value": float(value) if value is not None else (float(bound) if bound is not None else None),
        "qualifier": qualifier if kind == "censored" else "=",
        "n": n_total,
        "summary_id": summary["id"],
        "title": " · ".join(details),
    }


def _production_page_context(app: Flask, project: dict[str, Any] | None) -> dict[str, Any]:
    if project is None:
        return {
            "project": None,
            "counts": {},
            "compounds": [],
            "measurements": [],
            "measurement_summaries": [],
            "rgroup_assignments": [],
            "pharmacophore_rgroup": {"assignments": [], "positions": [], "data_origin": "derived"},
            "activity_cliffs": [],
            "mmp_run": {},
            "selectivity_observations": [],
            "translation_observations": [],
            "adme_observations": [],
            "pareto_observations": [],
            "contradiction_observations": [],
            "property_profiles": [],
            "scaffold_svg": "",
            "fragment_svgs": {},
            "design_candidates": [],
            "hypotheses": [],
            "recommendations": [],
            "series_context": {"series": [], "compound_series": {}, "data_origin": "curated"},
            "sar_endpoints": [],
            "prediction_models": [],
            "sar_matrix": {"endpoints": [], "rows": [], "data_origin": "derived"},
        }
    project_id = project["id"]
    compounds = _production_compounds(app, project_id)
    reference_compound_id = compounds[0]["id"] if compounds else ""
    pharmacophore_default_scaffold = ""
    try:
        derived_scaffold = derive_series_scaffold(
            [
                {"compound_id": compound["id"], "isomeric_smiles": compound.get("isomeric_smiles")}
                for compound in compounds
            ],
            reference_compound_id,
        ) if reference_compound_id else {}
        pharmacophore_default_scaffold = derived_scaffold.get("scaffold_smarts", "")
    except PharmacophoreRGroupError:
        pharmacophore_default_scaffold = ""
    counts = _production_counts(app, project_id)
    series_data = series_context(app.config["DATABASE_PATH"], project_id)
    if counts["measurements"] == 0:
        guidance = {
            "step": "01",
            "kicker": "START HERE · STEP 1",
            "title": "Start with one lab results file",
            "description": "Choose the file your lab system exported. We will check what it contains before anything is saved to the project.",
            "reason": "Starting from the raw export preserves compound identity, assay context, and measured values before we ask whether a structural change relates to activity.",
            "primary_label": "Choose a file",
            "primary_view": "overview",
            "primary_anchor": "production-actions",
        }
    elif counts["measurement_summaries"] == 0:
        guidance = {
            "step": "02",
            "kicker": "NEXT · CHECK RESULTS",
            "title": "Review your saved results",
            "description": "Check identities, units, qualifiers, and source rows before building summaries for comparison.",
            "reason": "A comparison is only meaningful when the underlying molecule and assay context have been checked first.",
            "primary_label": "Open Check results",
            "primary_view": "evidence",
            "primary_anchor": "",
        }
    elif counts["analysis_runs"] == 0:
        guidance = {
            "step": "03",
            "kicker": "NEXT · COMPARE",
            "title": "Compare compatible results",
            "description": "Choose a test, narrow the compound list, and inspect matched results before looking for broader patterns.",
            "reason": "Side-by-side comparison helps separate a plausible structural signal from a difference caused by assay or cell context.",
            "primary_label": "Open Compare",
            "primary_view": "explore",
            "primary_anchor": "sar-explorer",
        }
    else:
        guidance = {
            "step": "05",
            "kicker": "NEXT · CHOOSE NEXT",
            "title": "Choose the next question",
            "description": "Review the evidence gaps, hypotheses, and proposed experiments that could resolve the next uncertainty.",
            "reason": "A useful next experiment changes a molecule or test condition for a reason tied to evidence and uncertainty.",
            "primary_label": "Open Choose next",
            "primary_view": "designs",
            "primary_anchor": "",
        }
    guidance["goals"] = [
        {
            "label": "Add lab results",
            "description": "Upload a CSV, TSV, or Excel export and review it before saving.",
            "view": "overview",
            "anchor": "production-actions",
            "ready": True,
            "status": "Available now",
        },
        {
            "label": "Check data quality",
            "description": "Inspect structures, units, qualifiers, warnings, and source rows.",
            "view": "evidence",
            "anchor": "",
            "ready": counts["measurements"] > 0,
            "status": "Available now" if counts["measurements"] > 0 else "Available after import",
        },
        {
            "label": "Build result summaries",
            "description": "Group compatible repeated results before comparing compounds.",
            "view": "summaries",
            "anchor": "",
            "ready": counts["measurements"] > 0,
            "status": "Available now" if counts["measurements"] > 0 else "Available after import",
        },
        {
            "label": "Compare compounds",
            "description": "Put compatible tests side by side without hiding incomplete evidence.",
            "view": "explore",
            "anchor": "sar-explorer",
            "ready": counts["measurement_summaries"] > 0,
            "status": "Available now" if counts["measurement_summaries"] > 0 else "Available after summaries",
        },
        {
            "label": "Plan the next experiment",
            "description": "Review evidence gaps, hypotheses, and untested design candidates.",
            "view": "designs",
            "anchor": "",
            "ready": counts["analysis_runs"] > 0,
            "status": "Available now" if counts["analysis_runs"] > 0 else "Available after pattern review",
        },
    ]
    rgroup_assignments = _production_rgroup_assignments(app, project_id)
    pharmacophore_rgroup = _production_pharmacophore_rgroup(app, project_id)
    if pharmacophore_rgroup.get("input_selection", {}).get("scaffold_smarts"):
        pharmacophore_default_scaffold = pharmacophore_rgroup["input_selection"]["scaffold_smarts"]
    if pharmacophore_rgroup.get("reference_compound_id"):
        reference_compound_id = pharmacophore_rgroup["reference_compound_id"]
    if not pharmacophore_rgroup and rgroup_assignments and pharmacophore_default_scaffold:
        if rgroup_assignments[0].get("scaffold_smarts") != pharmacophore_default_scaffold:
            # Do not display an old benzene-only decomposition beside the new
            # reference-derived pharmacophore core; ask the user to refresh it.
            rgroup_assignments = []
    activity_cliffs = _production_activity_cliffs(app, project_id)
    mmp_run = _production_mmp(app, project_id)
    _add_pair_changes(
        rgroup_assignments,
        [(pair, pair.get("compound_a"), pair.get("compound_b")) for pair in mmp_run.get("pairs", [])]
        + [(cliff, cliff.get("compound_a_registration_id"), cliff.get("compound_b_registration_id")) for cliff in activity_cliffs],
    )
    # Each substituent drawing is sent once as an SVG <symbol> and referenced by id,
    # instead of being repeated in every table row and in the page's JSON data.
    fragment_svgs: dict[str, str] = {}
    for item in rgroup_assignments:
        for group in item.get("display_groups", []):
            svg = group.pop("svg", "")
            if svg:
                symbol_id = "frag-" + hashlib.sha1(svg.encode("utf-8")).hexdigest()[:12]
                fragment_svgs[symbol_id] = svg
                group["svg_id"] = symbol_id
    scaffold_svg = ""
    reference_compound = next((compound for compound in compounds if compound.get("id") == reference_compound_id), None)
    pharma_input = pharmacophore_rgroup.get("input_selection", {})
    pharma_positions = pharma_input.get("positions") or pharmacophore_rgroup.get("positions") or []
    if reference_compound and pharma_input.get("scaffold_smarts") and pharma_positions:
        try:
            scaffold_svg = render_reference_positions_svg(
                reference_compound["isomeric_smiles"],
                pharma_input["scaffold_smarts"],
                [
                    (position["scaffold_atoms"][0], position["label"])
                    for position in pharma_positions
                    if position.get("scaffold_atoms")
                ],
                width=520,
                height=230,
            )
        except StructureValidationError:
            scaffold_svg = ""
    if not scaffold_svg and rgroup_assignments and rgroup_assignments[0].get("scaffold_smarts"):
        first = next((item for item in rgroup_assignments if item.get("display_positions")), rgroup_assignments[0])
        sites = [
            (site["scaffold_atoms"][0], site["label"])
            for site in first.get("display_positions", [])
            if site.get("scaffold_atoms")
        ]
        try:
            scaffold_svg = render_scaffold_positions_svg(first["scaffold_smarts"], sites, width=320, height=180) if sites else ""
        except StructureValidationError:
            scaffold_svg = ""
    return {
        "project": project,
        "counts": counts,
        "guidance": guidance,
        "compounds": compounds,
        "pharmacophore_default_scaffold": pharmacophore_default_scaffold,
        "pharmacophore_default_reference": reference_compound_id,
        "measurements": _production_measurements(app, project_id),
        "measurement_summaries": _production_measurement_summaries(app, project_id),
        "rgroup_assignments": rgroup_assignments,
        "pharmacophore_rgroup": pharmacophore_rgroup,
        "activity_cliffs": activity_cliffs,
        "mmp_run": mmp_run,
        "selectivity_observations": _production_selectivity(app, project_id),
        "translation_observations": _production_translation_observations(app, project_id),
        "adme_observations": _production_adme_observations(app, project_id),
        "pareto_observations": _production_pareto_observations(app, project_id),
        "contradiction_observations": _production_contradictions(app, project_id),
        "property_profiles": _production_property_profiles(app, project_id),
        "scaffold_svg": scaffold_svg,
        "fragment_svgs": fragment_svgs,
        "design_candidates": _production_design_candidates(app, project_id),
        "hypotheses": _production_hypotheses(app, project_id),
        "recommendations": list_generated_recommendations(app.config["DATABASE_PATH"], project_id),
        "series_context": series_data,
        "sar_endpoints": _production_sar_endpoints(app, project_id),
        "prediction_models": list_prediction_models(app.config["DATABASE_PATH"], project_id),
        "sar_matrix": _production_sar_matrix(app, project_id),
    }


def _production_pattern_explorer(project: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Read model for the interactive explorer on Find patterns (presentation-only).

    Reuses the data already loaded for the page. Substituent drawings are added
    to the page's shared ``fragment_svgs`` sprite, so the template must render
    the sprite after this runs (it does: the sprite is part of the page body).
    """
    pharmacophore = context.get("pharmacophore_rgroup") or {}
    input_selection = pharmacophore.get("input_selection") or {}
    positions = input_selection.get("positions") or pharmacophore.get("positions") or []
    reference = next(
        (compound for compound in context.get("compounds", []) if compound.get("id") == pharmacophore.get("reference_compound_id")),
        None,
    )
    site_map = None
    if reference and input_selection.get("scaffold_smarts") and positions:
        try:
            site_map = reference_site_map(
                reference["isomeric_smiles"],
                input_selection["scaffold_smarts"],
                [(position["scaffold_atoms"][0], position["label"]) for position in positions if position.get("scaffold_atoms")],
                width=520,
                height=260,
            )
        except StructureValidationError:
            site_map = None
    data = build_pattern_explorer(
        sar_matrix=context.get("sar_matrix") or {},
        pharmacophore_rgroup=pharmacophore,
        property_profiles=context.get("property_profiles") or [],
        activity_cliffs=context.get("activity_cliffs") or [],
        mmp_pairs=(context.get("mmp_run") or {}).get("pairs") or [],
        fragment_svgs=context["fragment_svgs"],
        site_map=site_map,
        links={
            "summaries": url_for("production_workspace", view="summaries", project_id=project["id"]),
            "sar": url_for("production_workspace", view="sar", project_id=project["id"]),
        },
    )
    return {"pattern_explorer": data, "pattern_explorer_map_svg": site_map["svg"] if site_map else ""}


def _production_design_candidates(app: Flask, project_id: str) -> list[dict[str, Any]]:
    return list_candidates(app.config["DATABASE_PATH"], project_id)


def _replace_substituent_for_compound(
    app: Flask,
    project_id: str,
    compound_id: str,
    site: str,
    replacement: str,
    smiles: str = "",
) -> str:
    """New SMILES with the group at R-site ``site`` swapped for ``replacement``.

    Edits ``smiles`` when given (so several swaps can be chained on a proposal),
    otherwise the saved structure of ``compound_id``. Sites use the same
    position-consistent numbering as the Find patterns R-group table (latest
    R-group run in the project); the proposal is placed on the core the same
    way as the measured compounds.
    """
    from rdkit import Chem

    group = [item for item in _production_rgroup_assignments(app, project_id) if item.get("scaffold_smarts")]
    if not group:
        raise SarDiscoveryError("Run the R-group check on Find patterns first, or edit the SMILES directly.")
    target = next((item for item in group if item.get("compound_id") == compound_id), None)
    scaffold_smarts = (target or group[0])["scaffold_smarts"]
    same_core = [item for item in group if item["scaffold_smarts"] == scaffold_smarts]
    scaffold = Chem.MolFromSmarts(scaffold_smarts)
    molecules = [Chem.MolFromSmiles(item.get("isomeric_smiles") or "") for item in same_core]
    measured = consistent_substituents(molecules, scaffold)
    sites = assign_position_labels(measured)
    if smiles:
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            raise SarDiscoveryError("RDKit could not read the proposed SMILES.")
        if not molecule.HasSubstructMatch(scaffold):
            raise SarDiscoveryError("This structure no longer contains the shared core; edit the SMILES directly.")
        # Fit the proposal to the measured compounds' numbering without moving it.
        change = consistent_substituents([molecule], scaffold, placed=measured)[0]
    elif target is not None:
        index = next(i for i, item in enumerate(same_core) if item.get("compound_id") == compound_id)
        molecule, change = molecules[index], measured[index]
    else:
        raise SarDiscoveryError("This compound is not in the latest R-group run; edit the SMILES directly.")
    position = next((item for item in sites if item["label"] == site), None)
    if position is None or len(position["scaffold_atoms"]) != 1:
        raise SarDiscoveryError("Choose one of the listed R-sites.")
    return replace_substituent(molecule, change, position["scaffold_atoms"][0], replacement)


def _save_production_hypothesis(app: Flask):
    _ensure_database(app)
    payload = _json_payload()
    statement = str(payload.get("statement", "")).strip()
    rationale = str(payload.get("rationale", "")).strip()
    project_id = str(payload.get("project_id", "")).strip()
    if len(statement) < 8:
        return jsonify(error="invalid_hypothesis", message="Add a hypothesis with at least eight characters."), 422
    if not project_id or _get_project(app, project_id) is None:
        return jsonify(error="project_required", message="A valid project_id is required."), 422
    if not _project_access(app, project_id, "editor"):
        return jsonify(error="project_forbidden", message="Editor access is required for this project."), 403
    hypothesis_id = _id("hyp")
    now = _now()
    with transaction(app.config["DATABASE_PATH"]) as connection:
        connection.execute(
            """
            INSERT INTO hypotheses (id, project_id, statement, rationale, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'untested', ?, ?)
            """,
            (hypothesis_id, project_id, statement, rationale, now, now),
        )
        connection.execute(
            """
            INSERT INTO audit_events (id, project_id, operation, resource_type, resource_id, request_id, outcome, created_at)
            VALUES (?, ?, 'hypothesis_create', 'hypothesis', ?, ?, 'success', ?)
            """,
            (_id("audit"), project_id, hypothesis_id, g.get("request_id"), now),
        )
    return jsonify(
        hypothesis={
            "id": hypothesis_id,
            "project_id": project_id,
            "statement": statement,
            "rationale": rationale,
            "status": "untested",
            "data_origin": "imported",
            "created_at": now,
        }
    ), 201


app = create_app()


if __name__ == "__main__":
    settings = _settings_from_config(app.config)
    settings.validate_startup()
    app.run(host=settings.host, port=settings.port, debug=False)
