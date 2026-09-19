"""Unit tests for the session-revocation prefix lookup in api.routers.me.

DB-backed behavior (does the query actually match rows) is covered by
tests/api; these pin the pure-Python guard and the SQL text itself so a
LIKE wildcard can't creep back in without a DB connection.
"""

from api.routers.me import _SID_PREFIX_QUERY, _is_valid_sid_prefix


def test_valid_prefix_accepts_token_urlsafe_charset():
    assert _is_valid_sid_prefix("abcDEF012-_xyz")


def test_valid_prefix_rejects_percent():
    assert not _is_valid_sid_prefix("abc%")


def test_valid_prefix_rejects_other_specials():
    assert not _is_valid_sid_prefix("abc def")
    assert not _is_valid_sid_prefix("abc;drop")


def test_sid_prefix_query_uses_exact_prefix_match_not_like():
    """A LIKE comparison lets an unescaped `_` in the prefix match any
    single character in `sid` (a wildcard), turning the caller-supplied
    prefix into a session-guessing tool. `left(sid, length($2)) = $2` is
    an exact-prefix comparison with no wildcard semantics."""
    assert "LIKE" not in _SID_PREFIX_QUERY
    assert "left(sid, length($2)) = $2" in _SID_PREFIX_QUERY
