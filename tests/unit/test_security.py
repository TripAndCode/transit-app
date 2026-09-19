"""Tests for the shared cookie-security helper."""

import pytest
from fastapi import HTTPException, Request

import api.security as security
from api.security import cookie_secure


def test_cookie_secure_true_when_public_base_url_https(monkeypatch):
    """A deployment served over HTTPS should mark cookies ``Secure``."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://transit.example.com")
    assert cookie_secure() is True


def test_cookie_secure_false_when_public_base_url_http(monkeypatch):
    """Local-dev over plain HTTP must NOT set ``Secure`` or the browser
    drops the cookie and SSO breaks."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    assert cookie_secure() is False


def test_cookie_secure_false_when_unset(monkeypatch):
    """Unset → the http://localhost default → not secure."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert cookie_secure() is False


def _post_from(origin: str | None) -> Request:
    headers = [(b"origin", origin.encode())] if origin else []
    scope = {"type": "http", "method": "POST", "path": "/x", "headers": headers, "query_string": b""}
    return Request(scope)


def _reset_origins_cache(monkeypatch, fake_now):
    """Point the module's clock at a controllable fake and clear any
    allow-list cached by an earlier test before exercising the TTL."""
    monkeypatch.setattr(security, "_origins_cache_value", None)
    monkeypatch.setattr(security, "_origins_cache_expires_at", 0.0)
    monkeypatch.setattr(security._time, "monotonic", lambda: fake_now[0])


def test_csrf_guard_honors_public_base_url_change_only_after_ttl(monkeypatch):
    """`_ALLOWED_ORIGINS` used to be frozen at import, so a live config flip
    (PUBLIC_BASE_URL) was never honored without a process restart. The
    replacement TTL cache must pick it up -- but only once the TTL elapses,
    not on every request (that would defeat the point of caching)."""
    fake_now = [1_000.0]
    _reset_origins_cache(monkeypatch, fake_now)
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.delenv("ALLOW_TEST_ORIGIN", raising=False)

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://old.example.com")
    security.csrf_guard(_post_from("https://old.example.com"))  # populates the cache

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://new.example.com")
    fake_now[0] += 1.0  # still well inside the TTL window
    with pytest.raises(HTTPException):
        security.csrf_guard(_post_from("https://new.example.com"))
    security.csrf_guard(_post_from("https://old.example.com"))  # stale value still honored

    fake_now[0] += security._ORIGINS_CACHE_TTL_SEC  # TTL elapses
    security.csrf_guard(_post_from("https://new.example.com"))  # now honored
    with pytest.raises(HTTPException):
        security.csrf_guard(_post_from("https://old.example.com"))


def test_get_allowed_origins_rebuilds_from_live_cors_origins_after_ttl(monkeypatch):
    fake_now = [2_000.0]
    _reset_origins_cache(monkeypatch, fake_now)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setattr(security, "_ALLOW_TEST_ORIGIN", False)

    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com")
    assert security._get_allowed_origins() == {"https://a.example.com", "http://localhost:8000"}

    monkeypatch.setenv("CORS_ORIGINS", "https://b.example.com")
    fake_now[0] += security._ORIGINS_CACHE_TTL_SEC
    assert security._get_allowed_origins() == {"https://b.example.com", "http://localhost:8000"}
