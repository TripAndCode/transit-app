"""SSRF guard for outbound feed fetches.

``ingest_live`` opens an agency's ``feed_url`` with ``urllib``. Unguarded, a
``feed_url`` set to a non-HTTP scheme (urllib will happily open ``file://``,
``ftp://``, …) or pointed at an internal host (loopback, RFC-1918, or the cloud
metadata endpoint ``169.254.169.254``) turns that fetch into a local-file read
or a server-side request forgery. Agency rows are admin-set (create is
``require_admin``), so this is defense-in-depth — it stops a typo'd or malicious
``feed_url`` from ever reaching internal resources.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = ("http", "https")


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
