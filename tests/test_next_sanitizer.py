"""Tests for sanitize_next() — open-redirect defense for the `?next=` query.

We accept only same-origin absolute paths starting with a single slash. Any
absolute URL (`scheme://...`), scheme-relative URL (`//evil.com/x`), or
empty/None value collapses to `/`.
"""

from api.routers.auth import sanitize_next


def test_sanitize_keeps_path():
    assert sanitize_next("/agencies/1/map") == "/agencies/1/map"


def test_sanitize_drops_double_slash():
    assert sanitize_next("//evil.com/x") == "/"


def test_sanitize_drops_scheme():
    assert sanitize_next("https://evil.com/x") == "/"


def test_sanitize_drops_empty():
    assert sanitize_next(None) == "/"
    assert sanitize_next("") == "/"
