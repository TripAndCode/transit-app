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


def test_sanitize_drops_backslash_authority():
    """Browsers (and the frontend's identically-named sanitizer) normalize a
    leading backslash to a forward slash for http(s) origins, so "/\\evil.com"
    would resolve to a protocol-relative //evil.com once the browser follows
    the redirect Location header this value ends up in - even though Python's
    own URL resolution treats \\ as a literal, harmless path character."""
    assert sanitize_next("/\\evil.com") == "/"
    assert sanitize_next("/\\/evil.com") == "/"


def test_sanitize_drops_dot_segment_protocol_relative_bypass():
    """A same-origin-looking path can still resolve to a protocol-relative
    "//evil.com" after dot-segment removal (RFC 3986 - the exact browser-side
    algorithm that processes a redirect Location header), even though it
    never contains "//" or "://" as a literal substring."""
    assert sanitize_next("/.//evil.com") == "/"


def test_sanitize_keeps_query_and_fragment():
    assert sanitize_next("/agencies/1/map?tab=live#top") == "/agencies/1/map?tab=live#top"
