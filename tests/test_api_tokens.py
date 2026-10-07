"""API tokens: per-user bearer authentication for non-interactive clients (the MCP server)."""
from __future__ import annotations

import re
import threading

import pytest
from werkzeug.serving import make_server

import api_tokens
import portal_sso
from app import create_app
from authorization import ensure_local_user
from database import apply_migrations, read_connection, transaction
from sar_mcp.client import SarAuthError, SarClient
from sar_mcp.config import ConfigError, Settings

ALICE = "alice@example.test"
BOB = "bob@example.test"


class FakePortal:
    """Stands in for the portal's /sso/api/check-access."""

    def __init__(self):
        self.allowed = {ALICE, BOB}
        self.calls = 0
        self.unavailable = False

    def __call__(self, email: str) -> bool:
        self.calls += 1
        if self.unavailable:
            raise portal_sso.PortalUnavailableError("down")
        return email in self.allowed


@pytest.fixture()
def portal(monkeypatch):
    fake = FakePortal()
    monkeypatch.setattr(portal_sso, "has_app_access", fake)
    portal_sso.clear_access_cache()
    yield fake
    portal_sso.clear_access_cache()


def _app(tmp_path, auth_mode="portal"):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "production",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": auth_mode,
            "SECRET_KEY": "token-tests-secret-that-is-long-enough",
            "DATABASE_PATH": str(tmp_path / "tokens.db"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1_000_000,
            "SESSION_COOKIE_SECURE": False,
        }
    )


def _browser(app, email):
    """A client holding a signed-in browser session, as after the portal hand-off."""
    apply_migrations(app.config["DATABASE_PATH"])
    ensure_local_user(app.config["DATABASE_PATH"], email)
    client = app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_email"] = email
        session["csrf_token"] = "csrf-" + email
    return client


def _issue(app, client, email, name="Kiro on my laptop"):
    response = client.post("/account/tokens", data={"name": name, "_csrf_token": "csrf-" + email})
    assert response.status_code == 201, response.get_data(as_text=True)
    return re.search(r'value="(sarpat_[^"]+)"', response.get_data(as_text=True)).group(1)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---- storage layer ----------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "unit.db")
    apply_migrations(path)
    ids = {}
    for email in (ALICE, BOB):
        ids[email] = ensure_local_user(path, email)
    return path, ids


def test_token_is_random_prefixed_and_only_its_hash_is_stored(db):
    path, ids = db
    record, plaintext = api_tokens.create_token(path, ids[ALICE], "  Kiro   on laptop ")
    assert plaintext.startswith("sarpat_") and len(plaintext) > 40
    assert record["name"] == "Kiro on laptop"
    with read_connection(path) as connection:
        stored = dict(connection.execute("SELECT * FROM api_tokens").fetchone())
    assert plaintext not in stored.values()
    assert stored["token_hash"] == api_tokens.hash_token(plaintext)
    assert stored["token_prefix"] == plaintext[:12]
    assert api_tokens.create_token(path, ids[ALICE], "second")[1] != plaintext


@pytest.mark.parametrize("name,days", [("", 30), ("   ", 30), ("x" * 61, 30), ("ok", 0), ("ok", 91), ("ok", -5)])
def test_invalid_requests_are_refused(db, name, days):
    path, ids = db
    with pytest.raises(api_tokens.TokenError):
        api_tokens.create_token(path, ids[ALICE], name, days)


def test_active_token_limit_counts_only_live_tokens(db):
    path, ids = db
    made = [api_tokens.create_token(path, ids[ALICE], f"t{i}")[0] for i in range(api_tokens.MAX_ACTIVE_TOKENS_PER_USER)]
    with pytest.raises(api_tokens.TokenError, match="Revoke"):
        api_tokens.create_token(path, ids[ALICE], "one too many")
    assert api_tokens.create_token(path, ids[BOB], "other user is unaffected")
    assert api_tokens.revoke_token(path, ids[ALICE], made[0]["id"])
    api_tokens.create_token(path, ids[ALICE], "slot freed")


def test_authentication_rejects_unknown_expired_revoked_inactive_and_malformed(db):
    path, ids = db
    record, token = api_tokens.create_token(path, ids[ALICE], "t")
    assert api_tokens.authenticate_token(path, token) == ALICE
    assert api_tokens.authenticate_token(path, token + "x") is None
    for junk in ("", "sarpat_", "Bearer " + token, token[:-3], "x" * 50, token + " "):
        assert api_tokens.authenticate_token(path, junk) is None
    with transaction(path) as connection:
        connection.execute("UPDATE api_tokens SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (record["id"],))
    assert api_tokens.authenticate_token(path, token) is None
    expired = api_tokens.list_tokens(path, ids[ALICE])[0]
    assert expired["status"] == "expired"

    _, fresh = api_tokens.create_token(path, ids[ALICE], "fresh")
    assert api_tokens.authenticate_token(path, fresh) == ALICE
    with transaction(path) as connection:
        connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (ids[ALICE],))
    assert api_tokens.authenticate_token(path, fresh) is None


def test_revoke_only_affects_the_owners_own_token(db):
    path, ids = db
    record, token = api_tokens.create_token(path, ids[ALICE], "t")
    assert api_tokens.revoke_token(path, ids[BOB], record["id"]) is False
    assert api_tokens.authenticate_token(path, token) == ALICE
    assert api_tokens.revoke_token(path, ids[ALICE], record["id"]) is True
    assert api_tokens.revoke_token(path, ids[ALICE], record["id"]) is False
    assert api_tokens.authenticate_token(path, token) is None


# ---- web app ----------------------------------------------------------------------------------

def test_token_is_shown_once_and_listed_by_prefix_only(tmp_path, portal):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    token = _issue(app, alice, ALICE)
    listing = alice.get("/account/tokens")
    body = listing.get_data(as_text=True)
    assert listing.status_code == 200
    assert token not in body and token[:12] in body
    assert "no-store" in listing.headers["Cache-Control"]


def test_bearer_acts_as_its_owner_with_only_that_users_projects(tmp_path, portal):
    app = _app(tmp_path)
    alice, bob = _browser(app, ALICE), _browser(app, BOB)
    alice_token, bob_token = _issue(app, alice, ALICE), _issue(app, bob, BOB)

    created = app.test_client().post("/api/v1/projects", json={"name": "Alice only"}, headers=_bearer(alice_token))
    assert created.status_code == 201  # a bearer request needs no CSRF token and no cookie
    project_id = created.get_json()["project"]["id"]

    anonymous = app.test_client()
    me = anonymous.get("/api/v1/me", headers=_bearer(alice_token)).get_json()
    assert me == {"email": ALICE, "auth": "token"}
    assert [p["id"] for p in anonymous.get("/api/v1/projects", headers=_bearer(alice_token)).get_json()["projects"]] == [project_id]
    assert anonymous.get("/api/v1/projects", headers=_bearer(bob_token)).get_json()["projects"] == []
    forbidden = anonymous.get(f"/api/v1/series?project_id={project_id}", headers=_bearer(bob_token))
    assert forbidden.status_code == 403 and forbidden.get_json()["error"] == "project_forbidden"


def test_a_bearer_request_does_not_create_a_session(tmp_path, portal):
    app = _app(tmp_path)
    token = _issue(app, _browser(app, ALICE), ALICE)
    client = app.test_client()
    response = client.get("/api/v1/projects", headers=_bearer(token))
    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers
    assert client.get("/api/v1/projects").status_code == 401  # nothing carried over


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_scheme_is_case_insensitive(tmp_path, portal, scheme):
    app = _app(tmp_path)
    token = _issue(app, _browser(app, ALICE), ALICE)
    assert app.test_client().get("/api/v1/me", headers={"Authorization": f"{scheme} {token}"}).status_code == 200


def test_bad_expired_and_revoked_tokens_get_401_even_with_a_valid_cookie(tmp_path, portal):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    token = _issue(app, alice, ALICE)
    for header in ({"Authorization": "Bearer sarpat_" + "A" * 43}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Bearer "}):
        response = alice.get("/api/v1/projects", headers=header)  # the signed-in cookie must not rescue it
        assert response.status_code == 401
        assert response.get_json()["error"] == "invalid_token"
        assert response.headers["WWW-Authenticate"].startswith("Bearer")
    page = alice.get("/account/tokens").get_data(as_text=True)
    token_id = re.search(r"/account/tokens/(tok_[0-9a-f]+)/revoke", page).group(1)
    assert alice.post(f"/account/tokens/{token_id}/revoke", data={"_csrf_token": "csrf-" + ALICE}).status_code == 302
    assert app.test_client().get("/api/v1/me", headers=_bearer(token)).status_code == 401


def test_removal_from_the_portal_list_stops_tokens_and_is_not_cached(tmp_path, portal, monkeypatch):
    monkeypatch.setenv("SAR_TOKEN_PORTAL_RECHECK_SECONDS", "300")
    app = _app(tmp_path)
    token = _issue(app, _browser(app, ALICE), ALICE)
    client = app.test_client()
    assert client.get("/api/v1/me", headers=_bearer(token)).status_code == 200
    assert client.get("/api/v1/me", headers=_bearer(token)).status_code == 200
    assert portal.calls == 1  # an allowed answer is reused for the re-check window
    portal_sso.clear_access_cache()
    portal.allowed.discard(ALICE)
    denied = client.get("/api/v1/me", headers=_bearer(token))
    assert denied.status_code == 403 and denied.get_json()["error"] == "access_revoked"
    assert client.get("/api/v1/me", headers=_bearer(token)).status_code == 403
    assert portal.calls == 3  # denials are never cached
    portal.allowed.add(ALICE)
    assert client.get("/api/v1/me", headers=_bearer(token)).status_code == 200


def test_portal_outage_fails_closed(tmp_path, portal):
    app = _app(tmp_path)
    token = _issue(app, _browser(app, ALICE), ALICE)
    portal.unavailable = True
    portal_sso.clear_access_cache()
    response = app.test_client().get("/api/v1/me", headers=_bearer(token))
    assert response.status_code == 503 and response.get_json()["error"] == "portal_unavailable"


def test_a_token_cannot_manage_tokens_or_open_pages(tmp_path, portal):
    app = _app(tmp_path)
    token = _issue(app, _browser(app, ALICE), ALICE)
    client = app.test_client()
    # Bearer auth applies to /api/ only, so token management and pages still need a browser sign-in.
    for method, path in (("get", "/account/tokens"), ("post", "/account/tokens"), ("get", "/"), ("get", "/workspace/overview")):
        response = getattr(client, method)(path, headers=_bearer(token), data={"name": "minted"} if method == "post" else None)
        assert response.status_code == 302
        assert "/sso/generate/sar" in response.location
    assert len(api_tokens.list_tokens(app.config["DATABASE_PATH"], ensure_local_user(app.config["DATABASE_PATH"], ALICE))) == 1


def test_creating_and_revoking_need_csrf_and_a_session(tmp_path, portal):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    assert alice.post("/account/tokens", data={"name": "no csrf"}).status_code == 400
    assert app.test_client().post("/account/tokens", data={"name": "x", "_csrf_token": "csrf"}).status_code == 302


def test_form_validation_messages_and_limit(tmp_path, portal):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    csrf = {"_csrf_token": "csrf-" + ALICE}
    assert alice.post("/account/tokens", data={"name": "", **csrf}).status_code == 422
    assert alice.post("/account/tokens", data={"name": "n", "lifetime_days": "soon", **csrf}).status_code == 422
    assert alice.post("/account/tokens", data={"name": "n", "lifetime_days": "400", **csrf}).status_code == 422
    for index in range(api_tokens.MAX_ACTIVE_TOKENS_PER_USER):
        assert alice.post("/account/tokens", data={"name": f"t{index}", **csrf}).status_code == 201
    over = alice.post("/account/tokens", data={"name": "extra", **csrf})
    assert over.status_code == 422 and "Revoke" in over.get_data(as_text=True)


def test_one_user_cannot_revoke_anothers_token(tmp_path, portal):
    app = _app(tmp_path)
    alice, bob = _browser(app, ALICE), _browser(app, BOB)
    token = _issue(app, alice, ALICE)
    token_id = re.search(r"/account/tokens/(tok_[0-9a-f]+)/revoke", alice.get("/account/tokens").get_data(as_text=True)).group(1)
    bob.post(f"/account/tokens/{token_id}/revoke", data={"_csrf_token": "csrf-" + BOB})
    assert app.test_client().get("/api/v1/me", headers=_bearer(token)).status_code == 200


def test_token_events_are_audited_without_the_secret(tmp_path, portal):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    token = _issue(app, alice, ALICE)
    token_id = re.search(r"/account/tokens/(tok_[0-9a-f]+)/revoke", alice.get("/account/tokens").get_data(as_text=True)).group(1)
    alice.post(f"/account/tokens/{token_id}/revoke", data={"_csrf_token": "csrf-" + ALICE})
    with read_connection(app.config["DATABASE_PATH"]) as connection:
        rows = [dict(r) for r in connection.execute("SELECT * FROM audit_events WHERE resource_type = 'api_token' ORDER BY created_at, operation")]
    assert sorted(r["operation"] for r in rows) == ["api_token_create", "api_token_revoke"]
    assert all(r["resource_id"] == token_id and r["actor_user_id"] for r in rows)
    assert token not in repr(rows)


def test_tokens_are_off_outside_portal_mode(tmp_path, portal):
    app = _app(tmp_path, auth_mode="local")
    alice = _browser(app, ALICE)
    assert alice.get("/account/tokens").status_code == 404
    assert alice.post("/account/tokens", data={"name": "x", "_csrf_token": "csrf-" + ALICE}).status_code == 404
    other = app.test_client().get("/api/v1/projects", headers=_bearer("sarpat_" + "A" * 43))
    assert other.status_code == 401 and other.get_json()["error"] == "authentication_required"


def test_workspace_header_links_to_the_token_page_only_in_portal_mode(tmp_path, portal):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    assert 'href="/account/tokens"' in alice.get("/workspace/new").get_data(as_text=True)
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local = _browser(_app(local_dir, "local"), ALICE)
    assert 'href="/account/tokens"' not in local.get("/workspace/new").get_data(as_text=True)


CERT = "-----BEGIN CERTIFICATE-----\nMIIBfakecertificatebodyfortestsonly\n-----END CERTIFICATE-----\n"


def test_certificate_download_is_session_only_and_serves_only_a_plain_certificate(tmp_path, portal, monkeypatch):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    monkeypatch.delenv("SAR_SERVER_CERT_FILE", raising=False)
    assert alice.get("/account/tokens/certificate").status_code == 404
    assert "Download the server certificate" not in alice.get("/account/tokens").get_data(as_text=True)

    cert = tmp_path / "server.crt"
    cert.write_text(CERT)
    monkeypatch.setenv("SAR_SERVER_CERT_FILE", str(cert))
    served = alice.get("/account/tokens/certificate")
    assert served.status_code == 200 and served.get_data(as_text=True) == CERT
    assert "attachment" in served.headers["Content-Disposition"]
    assert "Download the server certificate" in alice.get("/account/tokens").get_data(as_text=True)

    token = _issue(app, alice, ALICE)
    for client in (app.test_client(),):  # nobody without a browser session, and a bearer token is not a session
        assert client.get("/account/tokens/certificate").status_code == 302
        assert client.get("/account/tokens/certificate", headers=_bearer(token)).status_code == 302


@pytest.mark.parametrize("body", [
    "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
    CERT + "-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----\n",
    "just some text",
    CERT * 2000,
])
def test_certificate_download_refuses_keys_and_junk(tmp_path, portal, monkeypatch, body):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    path = tmp_path / "wrong.pem"
    path.write_text(body)
    monkeypatch.setenv("SAR_SERVER_CERT_FILE", str(path))
    response = alice.get("/account/tokens/certificate")
    assert response.status_code == 404
    assert "secret" not in response.get_data(as_text=True)
    monkeypatch.setenv("SAR_SERVER_CERT_FILE", str(tmp_path / "missing.crt"))
    assert alice.get("/account/tokens/certificate").status_code == 404


def test_token_page_shows_commands_with_the_published_server_address(tmp_path, portal, monkeypatch):
    app = _app(tmp_path)
    alice = _browser(app, ALICE)
    monkeypatch.setenv("SAR_PUBLIC_URL", "https://sar.example.test:8029/")
    page = alice.get("/account/tokens").get_data(as_text=True)
    assert "--base-url https://sar.example.test:8029<" in page.replace("</code>", "<")
    assert "git clone --branch chembiocatalyst" in page and "onfocus" not in page


# ---- MCP client against a real server ---------------------------------------------------------

@pytest.fixture()
def live(tmp_path, portal):
    app = _app(tmp_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


def test_mcp_client_uses_a_token_end_to_end(live):
    app, base = live
    token = _issue(app, _browser(app, ALICE), ALICE)
    client = SarClient(Settings(base_url=base, token=token))
    assert client.get("/api/v1/projects") == {"projects": [], "data_origin": "production"}
    assert client.user_email == ALICE
    created = client.post("/api/v1/projects", {"name": "From MCP"})
    assert created["project"]["name"] == "From MCP"
    assert [p["name"] for p in client.get("/api/v1/projects")["projects"]] == ["From MCP"]


def test_mcp_client_explains_a_rejected_token(live):
    app, base = live
    with pytest.raises(SarAuthError, match="/account/tokens"):
        SarClient(Settings(base_url=base, token="sarpat_" + "B" * 43)).get("/api/v1/projects")


def test_mcp_client_reports_revoked_portal_access(live, portal):
    app, base = live
    token = _issue(app, _browser(app, ALICE), ALICE)
    client = SarClient(Settings(base_url=base, token=token))
    client.get("/api/v1/projects")
    portal.allowed.discard(ALICE)
    portal_sso.clear_access_cache()
    with pytest.raises(SarAuthError, match="no longer has access"):
        client.get("/api/v1/projects")


def test_mcp_settings_read_the_token_and_certificate(tmp_path):
    token = "sarpat_" + "C" * 43
    cert = tmp_path / "platform.crt"
    cert.write_text("not a real certificate")
    settings = Settings.from_env({"SAR_MCP_TOKEN": token, "SAR_MCP_CA_BUNDLE": str(cert), "SAR_MCP_CREDENTIALS_FILE": str(tmp_path / "none")})
    assert settings.has_token and settings.token == token and settings.ca_bundle == str(cert)

    stored = tmp_path / "mcp.env"
    stored.write_text(f'SAR_MCP_TOKEN="{token}"\n')
    stored.chmod(0o600)
    assert Settings.from_env({"SAR_MCP_CREDENTIALS_FILE": str(stored)}).token == token

    with pytest.raises(ConfigError, match="API token"):
        Settings.from_env({"SAR_MCP_TOKEN": "hunter2", "SAR_MCP_CREDENTIALS_FILE": str(tmp_path / "none")})
    with pytest.raises(ConfigError, match="not a file"):
        Settings.from_env({"SAR_MCP_CA_BUNDLE": str(tmp_path / "missing.crt"), "SAR_MCP_CREDENTIALS_FILE": str(tmp_path / "none")})


def test_the_token_never_travels_over_plain_http_off_host():
    settings = Settings(base_url="http://10.0.0.5:8029", token="sarpat_" + "D" * 43)
    assert "plain HTTP" in (settings.transport_problem() or "")


def test_save_token_stores_privately_and_only_checks_when_an_address_is_given(tmp_path, monkeypatch, capsys):
    import stat
    from unittest import mock

    from sar_mcp import cli

    target = tmp_path / "cfg" / "mcp.env"
    token = "sarpat_" + "E" * 43
    monkeypatch.setenv("SAR_MCP_CREDENTIALS_FILE", str(target))
    monkeypatch.delenv("SAR_MCP_BASE_URL", raising=False)
    with mock.patch("getpass.getpass", return_value=token), mock.patch.object(cli, "check", return_value=1) as check:
        assert cli.save_token() == 0  # no address yet: nothing to check, and no scary message
        check.assert_not_called()
        monkeypatch.setenv("SAR_MCP_BASE_URL", "https://server.example.test:8029")
        assert cli.save_token() == 1  # an address is known, so the connection is checked
        check.assert_called_once()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600 and stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert target.read_text() == f'SAR_MCP_TOKEN="{token}"\n'
    assert token not in capsys.readouterr().out
    with mock.patch("getpass.getpass", return_value="hunter2"):
        assert cli.save_token() == 2
