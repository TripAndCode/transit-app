"""Tests for url_guard.safe_urlopen: SSRF-safe fetch wrapper around urllib.

validate_feed_url alone only checks the URL given to it once, before the
first request - it does nothing about urllib's default redirect handling
(which follows a 3xx to a new host with no re-validation) or an unbounded
response body. safe_urlopen closes both gaps. All cases use IP-literal
hosts so no real DNS/network call happens - the opener itself is mocked,
matching this suite's existing hermetic style (see test_url_guard.py).
"""

import urllib.error
import urllib.request

import pytest

from pipeline import url_guard
from pipeline.url_guard import FeedURLError, safe_urlopen


class _FakeResponse:
    def __init__(self, body: bytes, headers=None, status=200):
        self._body = body
        self.headers = headers or {}
        self.status = status

    def read(self, amt=None):
        if amt is None:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:amt], self._body[amt:]
        return data

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code, location=None):
    headers = {"Location": location} if location else {}
    return urllib.error.HTTPError(url, code, "redirect" if location else "error", headers, None)


def test_returns_body_for_simple_public_url(monkeypatch):
    monkeypatch.setattr(url_guard._opener, "open", lambda req, timeout=None: _FakeResponse(b"hello"))
    with safe_urlopen("http://8.8.8.8/feed") as resp:
        assert resp.read() == b"hello"


def test_revalidates_and_follows_redirect_to_a_public_host(monkeypatch):
    calls = []

    def fake_open(req, timeout=None):
        calls.append(req.full_url)
        if req.full_url == "http://8.8.8.8/start":
            raise _http_error(req.full_url, 302, "http://8.8.4.4/next")
        return _FakeResponse(b"final-body")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    with safe_urlopen("http://8.8.8.8/start") as resp:
        assert resp.read() == b"final-body"
    assert calls == ["http://8.8.8.8/start", "http://8.8.4.4/next"]


def test_blocks_redirect_into_an_internal_host(monkeypatch):
    """The original URL passes validation; the redirect target must be
    re-validated too, or this is exactly the bypass the fix exists for."""

    def fake_open(req, timeout=None):
        if req.full_url == "http://8.8.8.8/start":
            raise _http_error(req.full_url, 302, "http://169.254.169.254/latest/meta-data/")
        raise AssertionError("must never follow a redirect into a blocked host")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    with pytest.raises(FeedURLError, match="blocked"):
        safe_urlopen("http://8.8.8.8/start")


def test_propagates_non_redirect_http_errors_unchanged(monkeypatch):
    """e.g. a 304 Not Modified must still surface as HTTPError so
    conditional-GET callers (direct_url.py) can keep catching it."""

    def fake_open(req, timeout=None):
        raise _http_error(req.full_url, 304)

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        safe_urlopen("http://8.8.8.8/feed")
    assert exc_info.value.code == 304


def test_caps_response_body_size(monkeypatch):
    monkeypatch.setattr(url_guard._opener, "open", lambda req, timeout=None: _FakeResponse(b"x" * 1000))
    with pytest.raises(FeedURLError, match="exceeded"):
        safe_urlopen("http://8.8.8.8/feed", max_bytes=500)


def test_too_many_redirects_raises(monkeypatch):
    def fake_open(req, timeout=None):
        raise _http_error(req.full_url, 302, "http://8.8.8.8/loop")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    with pytest.raises(FeedURLError, match="redirect"):
        safe_urlopen("http://8.8.8.8/loop")


def test_opener_ignores_environment_proxy_vars(monkeypatch):
    """A defense-in-depth SSRF guard is pointless if the process's
    http_proxy/https_proxy env vars silently reroute every fetch through a
    proxy that does its own DNS resolution/connection - the IP validation
    would then check nothing about the address actually dialed. Calls the
    actual production _build_opener() (not a hand-rolled equivalent) under
    monkeypatched env vars, so this fails if the real construction ever
    reverts to the implicit default (which DOES pick up env vars)."""
    monkeypatch.setenv("http_proxy", "http://evil-proxy:1234")
    monkeypatch.setenv("https_proxy", "http://evil-proxy:1234")
    fresh = url_guard._build_opener()
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in fresh.handle_open.get("http", []))
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in fresh.handle_open.get("https", []))


def test_preserves_request_headers_across_redirect(monkeypatch):
    """direct_url.py's conditional GET sends If-Modified-Since/If-None-Match;
    those must survive a redirect hop, not just the first request."""
    seen_headers = []

    def fake_open(req, timeout=None):
        seen_headers.append(dict(req.header_items()))
        if req.full_url == "http://8.8.8.8/start":
            raise _http_error(req.full_url, 302, "http://8.8.4.4/next")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    req = urllib.request.Request("http://8.8.8.8/start")
    req.add_header("If-None-Match", "abc123")
    with safe_urlopen(req) as resp:
        assert resp.read() == b"ok"
    assert all(h.get("If-none-match") == "abc123" for h in seen_headers)
