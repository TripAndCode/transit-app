"""Unit tests for the session-revocation lookup in api.routers.me.

DB-backed behavior (does the query actually match rows) is covered by
tests/api, and the prefix's validation and resolution by
tests/unit/test_admin_users_helpers.py; this pins the SQL text itself.
"""

from api.routers.me import _SID_PREFIX_QUERY


def test_sid_prefix_query_uses_exact_prefix_match_not_like():
    """The query only narrows the caller's sessions to the prefix's matches;
    resolve_session_prefix refuses a malformed prefix and decides among
    them. `left(sid_hash, length($2)) = $2` is an exact-prefix comparison
    that keeps the two in agreement, where LIKE would read an `_` in the
    prefix as a single-character wildcard."""
    assert "LIKE" not in _SID_PREFIX_QUERY
    assert "left(sid_hash, length($2)) = $2" in _SID_PREFIX_QUERY
