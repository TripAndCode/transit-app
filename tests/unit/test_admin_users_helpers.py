"""Pure-logic helpers behind the admin user drawer (sessions/api-keys/invites):
deriving a display-safe session prefix, picking a session by an admin-supplied
prefix without ever pattern-matching a raw secret, and deciding whether a
pending invite is still usable.

API key hashing itself is ``api.security.token_hash`` (shared with session id
hashing) and is tested in ``tests/unit/test_token_hash.py``.
"""

from datetime import datetime, timedelta, timezone

from pipeline.admin_users import (
    invite_is_usable,
    session_id_prefix,
    unique_prefix_match,
)


def test_session_id_prefix_truncates():
    assert session_id_prefix("abcdefghijklmnop", length=6) == "abcdef"


def test_session_id_prefix_shorter_than_length_returns_whole_string():
    assert session_id_prefix("abc", length=6) == "abc"


def test_unique_prefix_match_returns_the_sole_match():
    assert unique_prefix_match(["abc123", "def456"], "abc") == "abc123"


def test_unique_prefix_match_returns_none_when_no_match():
    assert unique_prefix_match(["abc123", "def456"], "zzz") is None


def test_unique_prefix_match_returns_none_when_ambiguous():
    assert unique_prefix_match(["abc123", "abc999"], "abc") is None


def test_invite_is_usable_when_pending_and_unexpired():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert invite_is_usable(consumed_at=None, expires_at=now + timedelta(days=1), now=now) is True


def test_invite_is_usable_false_once_consumed():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert invite_is_usable(consumed_at=now, expires_at=now + timedelta(days=1), now=now) is False


def test_invite_is_usable_false_once_expired():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert invite_is_usable(consumed_at=None, expires_at=now - timedelta(seconds=1), now=now) is False


def test_invite_is_usable_true_with_no_expiry():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert invite_is_usable(consumed_at=None, expires_at=None, now=now) is True
