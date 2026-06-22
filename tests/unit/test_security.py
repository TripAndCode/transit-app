"""Tests for the shared cookie-security helper."""

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
