"""Unit tests for the session-revocation prefix lookup in api.routers.me.

DB-backed behavior (does the query actually match rows) is covered by
tests/api; these pin the pure-Python guard and the SQL text itself so a
LIKE wildcard can't creep back in without a DB connection.
"""

from api.routers.me import _SID_PREFIX_QUERY, _is_valid_sid_prefix


def test_valid_prefix_accepts_lowercase_hex():
    """Sessions are stored as a SHA-256 hex digest, so the prefix alphabet is
    hex — tighter than the session id's own alphabet, and it excludes both
    LIKE wildcards outright."""
    assert _is_valid_sid_prefix("0123456789abcdef")


def test_valid_prefix_rejects_non_hex_even_though_a_session_id_could_contain_it():
    """`-` and `_` are legal in a token_urlsafe session id but can never
    appear in the stored hash, so accepting them would widen the input for
    no reachable match."""
    assert not _is_valid_sid_prefix("abcDEF012-_xyz")
    assert not _is_valid_sid_prefix("ABCDEF")


def test_valid_prefix_rejects_percent():
    assert not _is_valid_sid_prefix("abc%")


def test_valid_prefix_rejects_other_specials():
    assert not _is_valid_sid_prefix("abc def")
    assert not _is_valid_sid_prefix("abc;drop")


def test_sid_prefix_query_uses_exact_prefix_match_not_like():
    """A LIKE comparison lets an unescaped `_` in the prefix match any
    single character in the stored hash (a wildcard), turning the
    caller-supplied prefix into a session-guessing tool.
    `left(sid_hash, length($2)) = $2` is an exact-prefix comparison with no
    wildcard semantics — kept alongside the hex check so neither is the only
    thing standing between a crafted prefix and someone else's session."""
    assert "LIKE" not in _SID_PREFIX_QUERY
    assert "left(sid_hash, length($2)) = $2" in _SID_PREFIX_QUERY
