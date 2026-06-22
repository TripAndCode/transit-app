"""Tests for module-level startup validators in ``api.main``."""

import pathlib

import pytest

from api.main import _DEV_SIGNING_KEY, _validate_cors_origins, _validate_session_signing_key


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


def test_dockerfile_cmd_trusts_railway_proxy_headers():
    """The production image must run uvicorn with --proxy-headers so the anon
    rate-limiter and audit logs see the real client IP (not Railway's edge),
    and --forwarded-allow-ips so those forwarded headers are trusted."""
    dockerfile = pathlib.Path(__file__).resolve().parents[2] / "Dockerfile"
    cmd = dockerfile.read_text()
    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd
