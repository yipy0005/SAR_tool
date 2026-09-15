from app import create_app


app = create_app(
    {
        "TESTING": True,
        "SAR_ENV": "demo",
        "SAR_DEMO_MODE": True,
        "SAR_AUTH_MODE": "test-only",
        "SECRET_KEY": "demo-test-secret-that-is-long-enough",
        "DATABASE_PATH": ":memory:",
        "UPLOAD_DIR": "instance/workbench/test-uploads",
    }
)


def test_homepage_renders_decision_workspace():
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Make your next decision with evidence." in body
    assert "R-group / SAR matrix" in body
    assert "What should we make next?" in body
    assert "Demo mode: predictive inference is disabled" in body


def test_health_endpoint_is_local_smoke_check():
    client = app.test_client()
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_compound_search_returns_evidence_bound_results():
    client = app.test_client()
    response = client.get("/api/compounds?q=SAR-317")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["results"][0]["id"] == "SAR-317"


def test_matrix_endpoint_rejects_unknown_endpoint():
    client = app.test_client()
    response = client.get("/api/matrix/unknown")

    assert response.status_code == 404
    assert "available" in response.get_json()


def test_hypothesis_endpoint_requires_meaningful_statement():
    client = app.test_client()
    response = client.post("/api/hypotheses", json={"statement": "short"})

    assert response.status_code == 400
    assert "at least eight characters" in response.get_json()["error"]


def test_hypothesis_endpoint_captures_decision():
    client = app.test_client()
    response = client.post(
        "/api/hypotheses",
        json={
            "statement": "R2-Cl retains potency when R3 is less basic.",
            "rationale": "Test the current decision focus.",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()["hypothesis"]
    assert payload["statement"].startswith("R2-Cl")
    assert payload["status"] == "Untested"


def test_local_runtime_has_no_demo_fallback(tmp_path):
    local_app = create_app(
        {
            "TESTING": True,
            "SAR_ENV": "local",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "disabled",
            "SECRET_KEY": "local-test-secret-that-is-long-enough",
            "DATABASE_PATH": str(tmp_path / "local.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
        }
    )
    response = local_app.test_client().get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Production data boundary" in body
    assert "Illustrative demo data" not in body
    assert "Create a project" in body
