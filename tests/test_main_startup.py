"""Tests for module-level startup validators in ``api.main``."""

import pytest

from api.main import _validate_cors_origins


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
