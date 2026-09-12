"""Tests for scripts/ops_status_server.py: the authenticated HTTP front end serving the
item-123 combined operations-status document as HTML / compact text / JSON, all gated
behind the same shared-secret (`OPS_STATUS_TOKEN`) HTTP Basic check.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from scripts import ops_status_server


def _auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _client() -> TestClient:
    return TestClient(ops_status_server.app)


def _fake_document() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-09-12T12:00:00Z",
        "overall_state": "healthy",
        "components": [
            {
                "schema_version": 1,
                "component": "vps_loop",
                "state": "healthy",
                "observed_at": "2026-09-12T12:00:00Z",
                "last_success_at": "2026-09-12T11:55:00Z",
                "age_seconds": 300,
                "details": {"current_item": 123},
            }
        ],
        "reasons": {},
    }


def test_status_json_503_when_token_not_configured(monkeypatch):
    monkeypatch.delenv(ops_status_server.TOKEN_ENV_VAR, raising=False)
    response = _client().get("/status.json", headers=_auth_header("ops", "whatever"))
    assert response.status_code == 503


def test_status_json_401_when_no_credentials_supplied(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "correct-token")
    response = _client().get("/status.json")
    assert response.status_code == 401


def test_status_json_401_when_wrong_password(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "correct-token")
    response = _client().get("/status.json", headers=_auth_header("ops", "wrong-token"))
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Basic"


def test_status_json_200_with_correct_token_any_username(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "correct-token")
    monkeypatch.setattr(ops_status_server, "_current_document", _fake_document)

    response = _client().get("/status.json", headers=_auth_header("anyone", "correct-token"))

    assert response.status_code == 200
    body = response.json()
    assert body["overall_state"] == "healthy"
    assert body["components"][0]["component"] == "vps_loop"


def test_status_text_200_and_plain_text_content_type(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "correct-token")
    monkeypatch.setattr(ops_status_server, "_current_document", _fake_document)

    response = _client().get("/status.txt", headers=_auth_header("ops", "correct-token"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "overall=healthy" in response.text


def test_status_page_html_200(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "correct-token")
    monkeypatch.setattr(ops_status_server, "_current_document", _fake_document)

    response = _client().get("/", headers=_auth_header("ops", "correct-token"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "vps_loop" in response.text


def test_root_requires_auth(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "correct-token")
    response = _client().get("/")
    assert response.status_code == 401


def test_status_text_requires_auth(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "correct-token")
    response = _client().get("/status.txt")
    assert response.status_code == 401


def test_no_write_routes_exist():
    """This app has exactly three read-only GET routes -- no POST/PUT/PATCH/DELETE
    exists anywhere in it, structurally enforcing item 123's "no write operations"
    requirement rather than merely documenting it."""

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    for route in ops_status_server.app.routes:
        methods = getattr(route, "methods", None) or set()
        assert not (methods & write_methods), f"unexpected write method on {route}"


def test_docs_and_openapi_are_disabled():
    client = _client()
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_response_never_leaks_the_configured_token(monkeypatch):
    monkeypatch.setenv(ops_status_server.TOKEN_ENV_VAR, "super-secret-token-value")
    monkeypatch.setattr(ops_status_server, "_current_document", _fake_document)

    response = _client().get("/status.json", headers=_auth_header("ops", "super-secret-token-value"))

    assert "super-secret-token-value" not in response.text
