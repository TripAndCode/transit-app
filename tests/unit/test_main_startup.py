"""Tests for module-level startup validators in ``api.main``."""

import importlib
import pathlib

import pytest

import api.main
from api.main import _DEV_SIGNING_KEY, _validate_cors_origins, _validate_llm_providers, _validate_session_signing_key
from pipeline.query.llm_client import ProviderConfig


def _reload_main():
    """Reload ``api.main`` so module-level app construction re-reads env vars
    that were changed after the initial import (e.g. by ``monkeypatch``)."""
    importlib.reload(api.main)
    return api.main.app


def test_validate_cors_origins_rejects_wildcard_with_credentials():
    """`Access-Control-Allow-Origin: *` is incompatible with
    `Access-Control-Allow-Credentials: true` per the CORS spec —
    browsers silently block the response. Fail loud at startup instead."""
    with pytest.raises(RuntimeError, match="forbids"):
        _validate_cors_origins(["*"], allow_credentials=True)


def test_validate_cors_origins_allows_wildcard_without_credentials():
    """`*` is fine when credentials are off."""
    _validate_cors_origins(["*"], allow_credentials=False)


def test_validate_cors_origins_allows_explicit_with_credentials():
    """An explicit allowlist + credentials is the supported combo."""
    _validate_cors_origins(["http://localhost:5173"], allow_credentials=True)


def test_validate_cors_origins_allows_explicit_without_credentials():
    """No-op path — explicit allowlist, no credentials."""
    _validate_cors_origins(["http://localhost:5173"], allow_credentials=False)


def test_validate_cors_origins_rejects_mixed_list_with_wildcard_and_credentials():
    """A list containing `*` alongside explicit origins is still
    spec-incompatible when credentials are enabled. Guards against a
    future refactor that naively checks `origins == ["*"]` instead of
    `"*" in origins`."""
    with pytest.raises(RuntimeError, match="forbids"):
        _validate_cors_origins(["http://localhost:5173", "*"], allow_credentials=True)


def test_session_key_guard_rejects_dev_default_when_auth_enabled():
    """Booting an auth-enabled deployment with the dev signing key would make
    every session/OAuth cookie forgeable. Fail loud at startup instead."""
    with pytest.raises(RuntimeError, match="SESSION_SIGNING_KEY"):
        _validate_session_signing_key(enabled=True, signing_key=_DEV_SIGNING_KEY)


def test_session_key_guard_allows_real_key_when_auth_enabled():
    """A real secret is the supported configuration — no error."""
    _validate_session_signing_key(enabled=True, signing_key="a-real-random-secret")


def test_session_key_guard_ignores_dev_default_when_auth_disabled():
    """Anonymous-only mode never mints those cookies, so the dev default is
    harmless there — don't block local/anon boots."""
    _validate_session_signing_key(enabled=False, signing_key=_DEV_SIGNING_KEY)


def test_validate_llm_providers_rejects_empty_ladder():
    """Zero usable providers means the Ask tab has nothing to fall back to
    at all — fail loud at startup instead of a silent 503 on every request."""
    with pytest.raises(RuntimeError, match="No usable LLM provider configured"):
        _validate_llm_providers([])


def test_validate_llm_providers_allows_nonempty_ladder():
    """At least one usable provider is the supported configuration — no error."""
    _validate_llm_providers([ProviderConfig(name="gemini", api_key="x", base_url="https://x", model="m")])


def test_openapi_docs_disabled_by_default_behind_https(monkeypatch):
    """No explicit ``OPENAPI_DOCS_ENABLED`` and an HTTPS ``PUBLIC_BASE_URL``
    (a real deployment, per ``cookie_secure()``) must not expose the docs."""
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://transit.example.com")
    app = _reload_main()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_openapi_docs_enabled_by_default_for_local_http(monkeypatch):
    """No explicit flag and a plain-HTTP ``PUBLIC_BASE_URL`` (local dev) keeps
    today's behavior: docs stay reachable."""
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    app = _reload_main()
    assert app.openapi_url == "/openapi.json"
    assert any(getattr(route, "path", None) == "/docs" for route in app.routes)


def test_openapi_docs_env_flag_can_force_enable_behind_https(monkeypatch):
    """An explicit flag always wins over the HTTPS-based default."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://transit.example.com")
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "1")
    app = _reload_main()
    assert app.openapi_url == "/openapi.json"


def test_openapi_docs_env_flag_can_force_disable_for_local_http(monkeypatch):
    """An explicit flag can also lock docs off for a local boot."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "0")
    app = _reload_main()
    assert app.openapi_url is None


def test_dockerfile_cmd_trusts_railway_proxy_headers():
    """The production image must run uvicorn with --proxy-headers so the anon
    rate-limiter and audit logs see the real client IP (not Railway's edge),
    and --forwarded-allow-ips so those forwarded headers are trusted."""
    dockerfile = pathlib.Path(__file__).resolve().parents[2] / "Dockerfile"
    cmd = dockerfile.read_text()
    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd
