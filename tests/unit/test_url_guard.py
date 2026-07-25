"""Tests for the SSRF guard on outbound feed fetches.

All cases use IP-literal hosts (or bad schemes), so ``getaddrinfo`` resolves
without a real DNS query — the suite stays hermetic and network-free.
"""

import pytest

from pipeline.url_guard import FeedURLError, _ip_blocked, _redact_url, validate_feed_url


def test_rejects_file_scheme():
    """urllib would open file:// — a local-file read. Block non-http(s)."""
    with pytest.raises(FeedURLError, match="scheme"):
        validate_feed_url("file:///etc/passwd")


def test_rejects_ftp_scheme():
    with pytest.raises(FeedURLError, match="scheme"):
        validate_feed_url("ftp://example.com/feed")


def test_rejects_missing_host():
    with pytest.raises(FeedURLError, match="host"):
        validate_feed_url("http:///just/a/path")


def test_rejects_loopback_ipv4():
    with pytest.raises(FeedURLError, match="blocked"):
        validate_feed_url("http://127.0.0.1/feed")


def test_rejects_loopback_ipv6():
    with pytest.raises(FeedURLError, match="blocked"):
        validate_feed_url("http://[::1]/feed")


def test_rejects_private_ipv4():
    with pytest.raises(FeedURLError, match="blocked"):
        validate_feed_url("http://10.0.0.5:8080/feed")


def test_rejects_link_local_metadata():
    """169.254.169.254 is the cloud metadata endpoint (link-local range)."""
    with pytest.raises(FeedURLError, match="blocked"):
        validate_feed_url("http://169.254.169.254/latest/meta-data/")


def test_allows_public_ipv4():
    """A public address passes (8.8.8.8 — numeric, so no DNS query)."""
    validate_feed_url("http://8.8.8.8/feed")


def test_allows_public_https():
    validate_feed_url("https://8.8.8.8/gtfs-rt.pb")


def test_blocked_host_error_message_redacts_credentials_and_query_string():
    """GTFS/GTFS-RT feeds (ODPT and similar JP providers) routinely carry an
    API key in the query string or userinfo. That must never land in a log
    line or exception message verbatim - org policy prohibits reproducing
    credentials in output, and a FeedURLError message is exactly the kind
    of string that ends up in application logs."""
    with pytest.raises(FeedURLError) as exc_info:
        validate_feed_url("http://169.254.169.254/latest/meta-data/?acl:consumerKey=SECRET123")
    assert "SECRET123" not in str(exc_info.value)
    assert "consumerKey" not in str(exc_info.value)


def test_redact_url_strips_query_and_userinfo():
    assert _redact_url("http://user:pass@example.com/feed?apikey=SECRET") == "http://example.com/feed"
    assert _redact_url("https://8.8.8.8:8443/gtfs-rt.pb?key=abc") == "https://8.8.8.8:8443/gtfs-rt.pb"


def test_ip_blocked_rejects_ipv4_mapped_loopback():
    """An IPv6 host with an IPv4-mapped address (::ffff:127.0.0.1) must be
    blocked exactly like the plain IPv4 form - a redirect Location could
    steer at this form specifically to slip past a naive is_private check."""
    assert _ip_blocked("::ffff:127.0.0.1") is True


def test_ip_blocked_rejects_ipv4_mapped_metadata_endpoint():
    assert _ip_blocked("::ffff:169.254.169.254") is True


def test_ip_blocked_rejects_6to4_encoded_loopback():
    """2002::/16 (6to4) embeds a full IPv4 address in its low bits, but
    ipaddress's is_private/is_loopback don't unwrap it on this project's
    pinned Python (3.12.2) - confirmed empirically: 2002:7f00:1:: (= 127.0.0.1)
    reports is_private=False, is_loopback=False on this interpreter. A
    redirect Location using this encoding would otherwise slip past every
    check in _ip_blocked."""
    assert _ip_blocked("2002:7f00:1::") is True  # embeds 127.0.0.1


def test_ip_blocked_rejects_6to4_encoded_metadata_endpoint():
    assert _ip_blocked("2002:a9fe:a9fe::") is True  # embeds 169.254.169.254


def test_ip_blocked_rejects_nat64_encoded_private_address():
    """64:ff9b::/96 (NAT64) also embeds an IPv4 address; happens to be
    caught today via is_reserved on this interpreter, but that's
    incidental to is_reserved's own range definition, not a deliberate
    unwrap - pin the behavior explicitly rather than relying on it."""
    assert _ip_blocked("64:ff9b::a00:1") is True  # embeds 10.0.0.1


def test_ip_blocked_rejects_carrier_grade_nat():
    """100.64.0.0/10 (RFC 6598 CGNAT) is routable internal address space -
    common in cloud/container/ISP networks - but none of is_loopback/
    is_private/is_link_local/is_reserved/is_multicast/is_unspecified catch
    it (confirmed empirically on this interpreter), so the enumerated
    denylist let it straight through. A redirect Location pointed at this
    range would reach an internal host unblocked."""
    assert _ip_blocked("100.64.0.1") is True


def test_ip_blocked_allows_6to4_encoded_public_address():
    """A 6to4 address embedding a genuinely public IPv4 must still pass -
    the unwrap must not become a blanket reject of the whole 2002::/16 range."""
    assert _ip_blocked("2002:0808:0808::") is False  # embeds 8.8.8.8
