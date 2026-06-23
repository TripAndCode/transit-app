"""Tests for the SSRF guard on outbound feed fetches.

All cases use IP-literal hosts (or bad schemes), so ``getaddrinfo`` resolves
without a real DNS query — the suite stays hermetic and network-free.
"""

import pytest

from pipeline.url_guard import FeedURLError, validate_feed_url


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
