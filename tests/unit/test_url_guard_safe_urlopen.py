"""Tests for url_guard.safe_urlopen: SSRF-safe fetch wrapper around urllib.

validate_feed_url alone only checks the URL given to it once, before the
first request - it does nothing about urllib's default redirect handling
(which follows a 3xx to a new host with no re-validation) or an unbounded
response body. safe_urlopen closes both gaps. All cases use IP-literal
hosts so no real DNS/network call happens - the opener itself is mocked,
matching this suite's existing hermetic style (see test_url_guard.py).
"""

import http.client
import socket
import threading
import time
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
    return urllib.error.HTTPError(url, code, "redirect" if location else "error", headers, None)  # type: ignore[arg-type]


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
    re-validated too, or a redirect is a way around the guard entirely."""

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


def test_build_opener_installs_no_redirect_handler():
    """_NoRedirect is the linchpin of the whole SSRF fix - it's what makes
    the real opener raise HTTPError on a 3xx instead of silently
    auto-following it (unvalidated) to a new host. Every other redirect
    test here mocks _opener.open directly, which bypasses this entirely
    and would stay green even if _NoRedirect were dropped from
    _build_opener - so it needs its own direct assertion."""
    opener = url_guard._build_opener()
    assert any(isinstance(h, url_guard._NoRedirect) for h in opener.handlers)  # type: ignore[attr-defined]


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
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in fresh.handle_open.get("http", []))  # type: ignore[attr-defined]
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in fresh.handle_open.get("https", []))  # type: ignore[attr-defined]


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


def test_strips_authorization_and_cookie_on_cross_host_redirect(monkeypatch):
    """A future caller might add Authorization/Cookie to a feed Request -
    those must NOT be forwarded to a different host reached via redirect,
    or a malicious feed server could 302 to its own host and harvest them."""
    seen_headers = []

    def fake_open(req, timeout=None):
        seen_headers.append(dict(req.header_items()))
        if req.full_url == "http://8.8.8.8/start":
            raise _http_error(req.full_url, 302, "http://8.8.4.4/next")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    req = urllib.request.Request("http://8.8.8.8/start")
    req.add_header("Authorization", "Bearer secret")
    req.add_header("Cookie", "session=secret")
    with safe_urlopen(req) as resp:
        assert resp.read() == b"ok"
    assert seen_headers[0].get("Authorization") == "Bearer secret"  # first (same-host) hop keeps it
    assert "Authorization" not in seen_headers[1]  # cross-host hop must not see it
    assert "Cookie" not in seen_headers[1]


def test_strips_authorization_on_same_host_https_to_http_downgrade(monkeypatch):
    """A same-host redirect that drops from https to http must also strip
    credential headers - forwarding them would send a bearer token/cookie
    in cleartext even though the host itself didn't change."""
    seen_headers = []

    def fake_open(req, timeout=None):
        seen_headers.append(dict(req.header_items()))
        if req.full_url == "https://8.8.8.8/start":
            raise _http_error(req.full_url, 302, "http://8.8.8.8/next")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    req = urllib.request.Request("https://8.8.8.8/start")
    req.add_header("Authorization", "Bearer secret")
    with safe_urlopen(req) as resp:
        assert resp.read() == b"ok"
    assert "Authorization" not in seen_headers[1]


def test_downgrades_to_get_and_drops_body_on_302_redirect(monkeypatch):
    """Per RFC 9110, a 301/302/303 redirect should downgrade a non-GET
    request to GET and drop its body - only 307/308 preserve method+body.
    A future POST caller redirected via 302 must not silently re-POST its
    body to the new location."""
    seen = []

    def fake_open(req, timeout=None):
        seen.append((req.get_method(), req.data))
        if req.full_url == "http://8.8.8.8/start":
            raise _http_error(req.full_url, 302, "http://8.8.8.8/next")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    req = urllib.request.Request("http://8.8.8.8/start", data=b"payload", method="POST")
    req.add_header("Content-Type", "application/json")
    with safe_urlopen(req) as resp:
        assert resp.read() == b"ok"
    assert seen == [("POST", b"payload"), ("GET", None)]


def test_drops_entity_headers_alongside_body_on_302_redirect(monkeypatch):
    """Dropping the body but leaving Content-Type/Content-Length behind would
    produce a malformed GET (declared body length, no body) - entity
    headers must go with the body they describe."""
    seen_headers = []

    def fake_open(req, timeout=None):
        seen_headers.append(dict(req.header_items()))
        if req.full_url == "http://8.8.8.8/start":
            raise _http_error(req.full_url, 302, "http://8.8.8.8/next")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    req = urllib.request.Request("http://8.8.8.8/start", data=b"payload", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Length", "7")
    with safe_urlopen(req) as resp:
        assert resp.read() == b"ok"
    assert "Content-type" not in seen_headers[1]
    assert "Content-length" not in seen_headers[1]


def test_preserves_request_body_across_redirect(monkeypatch):
    """A future POST-with-body Request must not silently lose its body on
    a redirect hop - the body is carried along with the headers/method."""
    seen_bodies = []

    def fake_open(req, timeout=None):
        seen_bodies.append(req.data)
        if req.full_url == "http://8.8.8.8/start":
            raise _http_error(req.full_url, 307, "http://8.8.8.8/next")  # 307 preserves body/method
        return _FakeResponse(b"ok")

    monkeypatch.setattr(url_guard._opener, "open", fake_open)
    req = urllib.request.Request("http://8.8.8.8/start", data=b"payload", method="POST")
    with safe_urlopen(req) as resp:
        assert resp.read() == b"ok"
    assert seen_bodies == [b"payload", b"payload"]


def _dripping_server(*, chunked: bool):
    """A local HTTP server that sends its body one byte every 50 ms, each well
    inside the client's socket timeout."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            if chunked:
                conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3c\r\n")
            else:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 60\r\n\r\n")
            try:
                for _ in range(60):
                    time.sleep(0.05)
                    conn.sendall(b"x")
                if chunked:
                    conn.sendall(b"\r\n0\r\n\r\n")
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return server


@pytest.mark.parametrize("chunked", [False, True], ids=["content-length", "chunked"])
def test_a_deadline_ends_a_real_fetch_whose_body_drips(chunked):
    server = _dripping_server(chunked=chunked)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.getsockname()[1], timeout=5)
        conn.request("GET", "/feed")
        resp = conn.getresponse()
        deadline = time.monotonic() + 0.3
        with pytest.raises(FeedURLError, match="deadline"):
            url_guard._read_capped(resp, 10 * 1024 * 1024, deadline, "http://127.0.0.1/feed")
        assert time.monotonic() - deadline < 0.5
        conn.close()
    finally:
        server.close()


def test_a_deadline_still_returns_a_body_that_arrives_in_time(monkeypatch):
    monkeypatch.setattr(url_guard._opener, "open", lambda req, timeout=None: _FakeResponse(b"hello" * 30000))
    with safe_urlopen("http://8.8.8.8/feed", deadline=time.monotonic() + 5) as resp:
        assert resp.read() == b"hello" * 30000


def test_a_deadline_keeps_the_size_cap(monkeypatch):
    monkeypatch.setattr(url_guard._opener, "open", lambda req, timeout=None: _FakeResponse(b"x" * 1000))
    with pytest.raises(FeedURLError, match="exceeded"):
        safe_urlopen("http://8.8.8.8/feed", max_bytes=500, deadline=time.monotonic() + 5)


def test_a_deadline_reads_a_content_length_body_the_response_closes_itself_on(monkeypatch):
    """http.client closes a Content-Length response's socket on its last byte,
    so no read may touch that socket again."""
    body = b"y" * 5000
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(url_guard, "validate_feed_url", lambda url: None)
    try:
        url = f"http://127.0.0.1:{server.getsockname()[1]}/feed"
        with safe_urlopen(url, deadline=time.monotonic() + 5) as resp:
            assert resp.read() == body
    finally:
        server.close()
