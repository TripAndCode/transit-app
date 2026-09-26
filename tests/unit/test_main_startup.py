"""Tests for module-level startup validators in ``api.main``."""

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import api.main
from api.main import _DEV_SIGNING_KEY, _validate_cors_origins, _validate_llm_providers, _validate_session_signing_key
from pipeline.query.llm_client import ProviderConfig


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


def test_openapi_docs_off_when_unset(monkeypatch):
    """The closed default. `PUBLIC_BASE_URL` is deliberately not consulted: it
    is only set when SSO is configured, so a live HTTPS deployment without SSO
    leaves it at its localhost default and would read as local dev."""
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://transit.example.com")
    assert api.main._openapi_docs_enabled() is False

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    assert api.main._openapi_docs_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " true "])
def test_openapi_docs_on_for_truthy_flag(monkeypatch, value):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", value)
    assert api.main._openapi_docs_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe"])
def test_openapi_docs_off_for_anything_else(monkeypatch, value):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", value)
    assert api.main._openapi_docs_enabled() is False


def test_env_example_enables_docs_for_local_dev():
    """Local dev keeps its docs: the template every developer copies opts in,
    while a deployment setting its variables directly inherits the closed
    default."""
    env_example = (pathlib.Path(__file__).resolve().parents[2] / ".env.example").read_text()
    assert "\nOPENAPI_DOCS_ENABLED=true" in env_example


def test_app_disables_the_builtin_docs_urls():
    """The built-ins are always off -- the app registers its own /docs,
    /redoc and /openapi.json routes instead, each gated per-request by
    `_require_docs_enabled` (see the tests below), since FastAPI's own
    `docs_url`/`redoc_url`/`openapi_url` are fixed for the process's whole
    lifetime once passed to the constructor and can't reflect a `flags`
    override without a restart."""
    assert (api.main.app.docs_url, api.main.app.redoc_url, api.main.app.openapi_url) == (None, None, None)


def test_require_docs_enabled_blocks_when_flag_off(monkeypatch):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(Exception) as exc_info:
        api.main._require_docs_enabled()
    assert exc_info.value.status_code == 404


def test_require_docs_enabled_allows_when_flag_on(monkeypatch):
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    api.main._require_docs_enabled()  # must not raise


def test_dockerfile_cmd_trusts_railway_proxy_headers():
    """The production image must run uvicorn with --proxy-headers so the anon
    rate-limiter and audit logs see the real client IP (not Railway's edge),
    and --forwarded-allow-ips so those forwarded headers are trusted."""
    dockerfile = pathlib.Path(__file__).resolve().parents[2] / "Dockerfile"
    cmd = dockerfile.read_text()
    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd


async def test_lifespan_closes_pool_when_post_pool_setup_fails(monkeypatch):
    """``lifespan``'s cleanup after ``yield`` only runs once ``yield`` is
    reached -- a failure in post-pool startup (here, seed_local_admin) raises
    before that point and must close what startup already opened itself
    instead of leaking it. That includes the ClickHouse client: it is opened
    inside the same guarded block, so closing only the pool leaks its HTTP
    session on every failed boot."""
    for var in api.main._AUTH_ENV:
        monkeypatch.delenv(var, raising=False)

    mock_pool = AsyncMock()
    mock_ch_client = AsyncMock()

    monkeypatch.setattr(api.main.asyncpg, "create_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(api.main, "get_ch_client", AsyncMock(return_value=mock_ch_client))
    monkeypatch.setattr(api.main, "seed_local_admin", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(
        "pipeline.query.llm_client._load_providers",
        lambda: [ProviderConfig(name="gemini", api_key="x", base_url="https://x", model="m")],
    )

    fake_app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="boom"):
        async with api.main.lifespan(fake_app):
            pass

    mock_pool.close.assert_awaited_once()
    mock_ch_client.close.assert_awaited_once()


async def test_lifespan_failure_reports_the_startup_error_not_a_cleanup_error(monkeypatch):
    """A failing close() must not replace the error that caused the shutdown.

    The startup exception is the actionable one; surfacing a secondary
    close() failure in its place would send an operator after the wrong
    thing."""
    for var in api.main._AUTH_ENV:
        monkeypatch.delenv(var, raising=False)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock(side_effect=RuntimeError("close exploded"))
    mock_ch_client = AsyncMock()
    mock_ch_client.close = AsyncMock(side_effect=RuntimeError("ch close exploded"))

    monkeypatch.setattr(api.main.asyncpg, "create_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(api.main, "get_ch_client", AsyncMock(return_value=mock_ch_client))
    monkeypatch.setattr(api.main, "seed_local_admin", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(
        "pipeline.query.llm_client._load_providers",
        lambda: [ProviderConfig(name="gemini", api_key="x", base_url="https://x", model="m")],
    )

    fake_app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="boom"):
        async with api.main.lifespan(fake_app):
            pass
