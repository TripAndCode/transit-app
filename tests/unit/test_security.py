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


def test_csrf_guard_honors_a_public_base_url_change_immediately(monkeypatch):
    """The allow-list used to be frozen at import, so a live config flip was
    never honored without a process restart. It is read per call now, so the
    change takes effect at once -- and, more to the point, an origin *removed*
    from the configuration stops being accepted at once, which is the
    direction that matters for a revocation."""
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.delenv("ALLOW_TEST_ORIGIN", raising=False)

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://old.example.com")
    security.csrf_guard(_post_from("https://old.example.com"))

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://new.example.com")
    security.csrf_guard(_post_from("https://new.example.com"))
    with pytest.raises(HTTPException):
        security.csrf_guard(_post_from("https://old.example.com"))


def test_allowed_origins_reads_cors_origins_per_call(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.delenv("ALLOW_TEST_ORIGIN", raising=False)

    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com")
    assert security._build_allowed_origins() == {"https://a.example.com", "http://localhost:8000"}

    monkeypatch.setenv("CORS_ORIGINS", "https://b.example.com")
    assert security._build_allowed_origins() == {"https://b.example.com", "http://localhost:8000"}


def test_allow_test_origin_is_read_per_call_like_the_others(monkeypatch):
    """All three inputs behave the same way; this one used to stay frozen at
    import while the other two were re-read, so a change to it did nothing."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("CORS_ORIGINS", "")

    monkeypatch.delenv("ALLOW_TEST_ORIGIN", raising=False)
    assert "http://test" not in security._build_allowed_origins()

    monkeypatch.setenv("ALLOW_TEST_ORIGIN", "1")
    assert "http://test" in security._build_allowed_origins()
