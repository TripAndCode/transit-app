"""Tests for module-level startup validators in ``api.main``."""

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import api.main as main_mod
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


def test_dockerfile_cmd_trusts_railway_proxy_headers():
    """The production image must run uvicorn with --proxy-headers so the anon
    rate-limiter and audit logs see the real client IP (not Railway's edge),
    and --forwarded-allow-ips so those forwarded headers are trusted."""
    dockerfile = pathlib.Path(__file__).resolve().parents[2] / "Dockerfile"
    cmd = dockerfile.read_text()
    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd


async def test_lifespan_closes_pool_when_post_pool_setup_fails(monkeypatch):
    """``lifespan``'s except-clause after ``yield`` only runs once ``yield``
    is reached -- a failure in post-pool startup (here, seed_local_admin)
    raises before that point and must close the just-opened pool itself
    instead of leaking it."""
    for var in main_mod._AUTH_ENV:
        monkeypatch.delenv(var, raising=False)

    mock_pool = AsyncMock()
    mock_ch_client = AsyncMock()

    monkeypatch.setattr(main_mod.asyncpg, "create_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(main_mod, "get_ch_client", AsyncMock(return_value=mock_ch_client))
    monkeypatch.setattr(main_mod, "seed_local_admin", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(
        "pipeline.query.llm_client._load_providers",
        lambda: [ProviderConfig(name="gemini", api_key="x", base_url="https://x", model="m")],
    )

    fake_app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="boom"):
        async with main_mod.lifespan(fake_app):
            pass

    mock_pool.close.assert_awaited_once()
