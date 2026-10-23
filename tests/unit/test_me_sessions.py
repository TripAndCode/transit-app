"""Unit tests for the session-revocation lookup in api.routers.me.

DB-backed behavior (does the query actually match rows) is covered by
tests/api, and the prefix's own validation by
tests/unit/test_admin_users_helpers.py; this pins the SQL text itself so a
LIKE wildcard can't creep back in without a DB connection.
"""

from api.routers.me import _SID_PREFIX_QUERY


def test_sid_prefix_query_uses_exact_prefix_match_not_like():
    """A LIKE comparison lets an unescaped `_` in the prefix match any
    single character in the stored hash (a wildcard), turning the
    caller-supplied prefix into a session-guessing tool.
    `left(sid_hash, length($2)) = $2` is an exact-prefix comparison with no
    wildcard semantics — kept alongside the hex check so neither is the only
    thing standing between a crafted prefix and someone else's session."""
    assert "LIKE" not in _SID_PREFIX_QUERY
    assert "left(sid_hash, length($2)) = $2" in _SID_PREFIX_QUERY
