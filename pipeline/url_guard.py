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


def _redact_url(url: str) -> str:
    """Strip userinfo and query string from ``url`` for safe logging.

    GTFS/GTFS-RT feeds (ODPT and similar providers) routinely carry an API
    key in the query string or a ``user:pass@`` userinfo segment. Every
    place this module puts a URL into an exception message or log line goes
    through here first, so a rejected/oversized/over-redirected fetch never
    writes a credential to the application log.
    """
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path}" if parts.scheme else netloc + parts.path


_SIXTOFOUR_NET = ipaddress.ip_network("2002::/16")
_NAT64_NET = ipaddress.ip_network("64:ff9b::/96")


def _unwrap_ipv4(addr: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """Unwrap an IPv6 encoding of an IPv4 address to its plain ``IPv4Address``.

    ``ipaddress``'s own ``is_private``/``is_loopback``/etc. only reliably
    reflect an IPv4-mapped address (``::ffff:a.b.c.d``) on Python versions
    with the CVE-2024-4032 fix (3.12.4+ / 3.11.9+ — this project pins
    ``>=3.11``, so an older patch release is a real possibility). 6to4
    (``2002::/16``) and NAT64 (``64:ff9b::/96``) both embed a full IPv4
    address in their low bits too, and are never unwrapped by those
    properties on *any* version — confirmed empirically: on this project's
    pinned 3.12.2, ``2002:7f00:1::`` (encoding 127.0.0.1) reports
    ``is_private=False``. A redirect ``Location`` using one of these
    encodings would otherwise slip a blocked address past every check
    below, on the exact path (per-hop redirect validation) that receives
    server-supplied, not admin-set, targets.
    """
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        return mapped
    if isinstance(addr, ipaddress.IPv6Address):
        packed = addr.packed
        if addr in _SIXTOFOUR_NET:
            return ipaddress.IPv4Address(packed[2:6])
        if addr in _NAT64_NET:
            return ipaddress.IPv4Address(packed[12:16])
    return addr


def _ip_blocked(ip: str) -> bool:
    """True if ``ip`` is anything but globally-routable public address space.

    Fail-closed allowlist (``not is_global``) rather than an enumerated
    denylist of ``is_loopback``/``is_private``/etc.: the denylist form missed
    Carrier-Grade NAT (``100.64.0.0/10``, RFC 6598) — confirmed empirically,
    none of those six properties catch it — which is common internal
    addressing in cloud/container networks and a live SSRF target on the
    redirect path (server-supplied, not admin-set). ``is_global`` already
    accounts for loopback/private/link-local/reserved/multicast/unspecified
    plus any future special-use range, so this closes that class of gap
    instead of requiring the list to be kept in sync by hand. See
    :func:`_unwrap_ipv4` for why the address is unwrapped before this check —
    ``is_global`` has the same IPv6-embedded-IPv4 blind spot the individual
    properties did.
    """
    addr = _unwrap_ipv4(ipaddress.ip_address(ip))
    return not addr.is_global


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


def _build_opener() -> urllib.request.OpenerDirector:
    """Build the opener used by :func:`safe_urlopen`.

    Passes an explicit empty ``ProxyHandler({})`` rather than relying on
    ``build_opener``'s implicit default, which constructs a ``ProxyHandler``
    that reads ``http_proxy``/``https_proxy`` from the environment. If the
    ingest process ever runs with those set, every fetch would silently
    route through that proxy — which does its own DNS resolution and
    connection — making :func:`validate_feed_url`'s IP check meaningless
    (it would validate an address nothing actually connects to).
    """
    return urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


_opener = _build_opener()


class _CappedResponse:
    """Minimal ``urlopen``-response shape (``read``/``headers``/``getcode``)
    wrapping an already-fetched, already-size-checked body."""

    def __init__(self, body: bytes, headers, status: int):
        self._body = body
        self.headers = headers
        self.status = status

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:amt], self._body[amt:]
        return data

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
        headers = dict(url_or_request.header_items())
        method = url_or_request.get_method()
        data = url_or_request.data
        current = url_or_request.full_url
    else:
        headers = {}
        method = None
        data = None
        current = url_or_request
    original_host = urlsplit(current).hostname
    original_scheme = urlsplit(current).scheme

    for _ in range(_MAX_REDIRECTS + 1):
        validate_feed_url(current)
        req = urllib.request.Request(current, data=data, headers=headers, method=method)
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in _REDIRECT_CODES:
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                raise FeedURLError(f"redirect from {_redact_url(current)} ({exc.code}) has no Location header") from exc
            current = urljoin(current, location)
            new_parts = urlsplit(current)
            if exc.code in (301, 302, 303):
                # Per RFC 9110 §15.4, only 307/308 preserve method+body on
                # redirect; 301/302/303 downgrade a non-GET request to GET
                # and drop the body — otherwise a future non-GET caller
                # redirected via 303 would silently re-send its body/method
                # to a location that never asked for it. Entity headers
                # describing that body must go with it, or the GET declares
                # a body (Content-Length) it no longer sends.
                method = "GET"
                data = None
                headers = {k: v for k, v in headers.items() if k.lower() not in ("content-type", "content-length")}
            if new_parts.hostname != original_host or (original_scheme == "https" and new_parts.scheme == "http"):
                # Don't forward credentials to a host the caller never
                # intended them for, or send them in cleartext over a
                # same-host HTTPS→HTTP downgrade (standard hardened-client
                # behavior). No current caller sets either header —
                # direct_url.py's conditional-GET uses If-Modified-Since/
                # If-None-Match only — so this is defense-in-depth for
                # whichever future caller adds one.
                headers = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "cookie")}
            continue
        with resp:
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FeedURLError(f"response from {_redact_url(current)} exceeded the {max_bytes}-byte cap")
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            return _CappedResponse(body, resp.headers, status)
    raise FeedURLError(f"too many redirects fetching {_redact_url(current)}")
