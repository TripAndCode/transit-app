"""SSRF guard for outbound feed fetches.

``ingest_live`` opens an agency's ``feed_url`` with ``urllib``. Unguarded, a
``feed_url`` set to a non-HTTP scheme (urllib will happily open ``file://``,
``ftp://``, …) or pointed at an internal host (loopback, RFC-1918, or the cloud
metadata endpoint ``169.254.169.254``) turns that fetch into a local-file read
or a server-side request forgery. Agency rows are admin-set (create is
``require_admin``), so this is defense-in-depth — it stops a typo'd or malicious
``feed_url`` from ever reaching internal resources.

:func:`safe_urlopen` wraps :func:`validate_feed_url` with the actual fetch:
every outbound ``feed_url``/``static_url`` request in this codebase should go
through it rather than calling ``urllib.request.urlopen`` directly, since a
one-time pre-fetch validation alone leaves two gaps open — a 3xx redirect to
a new host is never re-validated by plain ``urlopen``, and an unbounded
response body can OOM the ingest process (a huge or zip-bomb'd payload).
"""

import ipaddress
import socket
import urllib.error
import urllib.request
from typing import Literal
from urllib.parse import urljoin, urlsplit

_ALLOWED_SCHEMES = ("http", "https")
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 200 * 1024 * 1024  # generous cap for GTFS-RT pb / static zips


class FeedURLError(ValueError):
    """Raised when a ``feed_url`` is unsafe to fetch."""


def _ip_blocked(ip: str) -> bool:
    """True if ``ip`` falls in a range we must never fetch from — loopback,
    private (RFC 1918 / ULA), link-local (incl. metadata ``169.254.169.254``),
    reserved, multicast, or unspecified."""
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_feed_url(url: str) -> None:
    """Raise :class:`FeedURLError` unless ``url`` is an http(s) URL whose host
    resolves only to public addresses. Call before fetching a DB-supplied
    ``feed_url``.

    Resolution happens here, not at fetch time, so a DNS-rebinding host could in
    principle differ when ``urllib`` re-resolves. That residual is acceptable
    for admin-set URLs; pin the resolved IP if ``feed_url`` ever becomes
    user-supplied.
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise FeedURLError(f"feed_url scheme must be http or https, got {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise FeedURLError("feed_url has no host")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port)
    except socket.gaierror as exc:
        raise FeedURLError(f"feed_url host does not resolve: {host}") from exc
    for info in infos:
        ip = str(info[4][0])  # sockaddr[0] is the address; getaddrinfo types it str | int
        if _ip_blocked(ip):
            raise FeedURLError(f"feed_url host {host} resolves to a blocked address: {ip}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Disables urllib's automatic redirect-following.

    Returning ``None`` here makes ``urlopen`` raise ``HTTPError`` on any 3xx
    instead of silently following it — :func:`safe_urlopen` then re-validates
    the redirect target itself before deciding whether to follow it.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class _CappedResponse:
    """Minimal ``urlopen``-response shape (``read``/``headers``/``getcode``)
    wrapping an already-fetched, already-size-checked body."""

    def __init__(self, body: bytes, headers, status: int):
        self._body = body
        self.headers = headers
        self.status = status

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_CappedResponse":
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


def safe_urlopen(
    url_or_request: str | urllib.request.Request,
    *,
    timeout: float = 30,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> _CappedResponse:
    """Validate-then-fetch: the SSRF-safe replacement for ``urllib.request.urlopen``.

    Accepts a URL string or a pre-built ``Request`` (its headers/method are
    preserved across redirect hops, e.g. ``direct_url.py``'s conditional-GET
    ``If-Modified-Since``/``If-None-Match``). Every hop — the original URL and
    every subsequent redirect target, up to :data:`_MAX_REDIRECTS` — is passed
    through :func:`validate_feed_url` before being fetched, and the response
    body is capped at ``max_bytes`` (raises :class:`FeedURLError` if
    exceeded), so a malicious or misconfigured server can't redirect the
    fetch into an internal network after the initial URL passed validation,
    or OOM the ingest process with an oversized/zip-bomb'd body.

    A non-redirect HTTP error (e.g. 304 Not Modified) propagates unchanged as
    ``urllib.error.HTTPError``, matching plain ``urlopen``'s behavior, so
    existing callers that catch it keep working.
    """
    if isinstance(url_or_request, urllib.request.Request):
        base_headers = dict(url_or_request.header_items())
        method = url_or_request.get_method()
        current = url_or_request.full_url
    else:
        base_headers = {}
        method = None
        current = url_or_request

    for _ in range(_MAX_REDIRECTS + 1):
        validate_feed_url(current)
        req = urllib.request.Request(current, headers=base_headers, method=method)
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in _REDIRECT_CODES:
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                raise FeedURLError(f"redirect from {current} ({exc.code}) has no Location header") from exc
            current = urljoin(current, location)
            continue
        with resp:
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FeedURLError(f"response from {current} exceeded the {max_bytes}-byte cap")
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            return _CappedResponse(body, resp.headers, status)
    raise FeedURLError(f"too many redirects fetching {url_or_request}")
