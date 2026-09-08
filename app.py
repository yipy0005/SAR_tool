from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, redirect, render_template, request, g, session, url_for
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
from chemistry import StructureValidationError, render_scaffold_svg, standardize_structure

SCAFFOLD_VIEW_SMILES = "O=C(Nc1ccc([*:2])cn1)Cc1ccc([*:1])cc1"
from config import Settings
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

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if app.config["SAR_ENV"] == "production":
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'",
            )
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
        if view not in {"overview", "explore", "evidence", "analysis", "designs"}:
            return jsonify(error="workspace_not_found", message="Unknown production workspace page."), 404
        _ensure_database(app)
        project = _get_project(app, request.args.get("project_id"))
        return render_template(
            f"production_{view}.html",
            **_production_page_context(app, project),
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
            return jsonify(status="ready", mode="production", schema_version=version)
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
            SELECT m.id, c.registration_id, ad.name AS assay_name, m.raw_value_text, m.value_numeric,
                   m.unit_ucum, m.qualifier, m.lower_bound, m.upper_bound, m.canonical_value,
                   m.canonical_unit, m.transform_id, m.missing_reason, m.source_row_id, m.created_at
            FROM measurements m
            JOIN compounds c ON c.id = m.compound_id
            JOIN assay_runs ar ON ar.id = m.assay_run_id
            JOIN assay_definitions ad ON ad.id = ar.assay_definition_id
            WHERE c.project_id = ?
            ORDER BY m.created_at DESC
            LIMIT 200
            """,
            (project_id,),
        ).fetchall()
    return [_row_dict(row) | {"data_origin": "imported"} for row in rows]


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


def _production_rgroup_assignments(app: Flask, project_id: str) -> list[dict[str, Any]]:
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = connection.execute(
            """
            SELECT ra.*, c.registration_id, ar.algorithm_version
            FROM rgroup_assignments AS ra
            JOIN analysis_runs AS ar ON ar.id = ra.analysis_run_id
            JOIN compounds AS c ON c.id = ra.compound_id
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
    return result


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


def _production_page_context(app: Flask, project: dict[str, Any] | None) -> dict[str, Any]:
    if project is None:
        return {
            "project": None,
            "counts": {},
            "compounds": [],
            "measurements": [],
            "measurement_summaries": [],
            "rgroup_assignments": [],
            "activity_cliffs": [],
            "mmp_run": {},
            "selectivity_observations": [],
            "translation_observations": [],
            "adme_observations": [],
            "pareto_observations": [],
            "contradiction_observations": [],
            "property_profiles": [],
            "scaffold_svg": "",
            "design_candidates": [],
            "hypotheses": [],
            "recommendations": [],
        }
    project_id = project["id"]
    return {
        "project": project,
        "counts": _production_counts(app, project_id),
        "compounds": _production_compounds(app, project_id),
        "measurements": _production_measurements(app, project_id),
        "measurement_summaries": _production_measurement_summaries(app, project_id),
        "rgroup_assignments": _production_rgroup_assignments(app, project_id),
        "activity_cliffs": _production_activity_cliffs(app, project_id),
        "mmp_run": _production_mmp(app, project_id),
        "selectivity_observations": _production_selectivity(app, project_id),
        "translation_observations": _production_translation_observations(app, project_id),
        "adme_observations": _production_adme_observations(app, project_id),
        "pareto_observations": _production_pareto_observations(app, project_id),
        "contradiction_observations": _production_contradictions(app, project_id),
        "property_profiles": _production_property_profiles(app, project_id),
        "scaffold_svg": render_scaffold_svg(SCAFFOLD_VIEW_SMILES),
        "design_candidates": _production_design_candidates(app, project_id),
        "hypotheses": _production_hypotheses(app, project_id),
        "recommendations": list_generated_recommendations(app.config["DATABASE_PATH"], project_id),
    }


def _production_design_candidates(app: Flask, project_id: str) -> list[dict[str, Any]]:
    return list_candidates(app.config["DATABASE_PATH"], project_id)


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
