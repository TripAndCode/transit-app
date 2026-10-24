"""Pure-logic helpers behind the admin user drawer (sessions/api-keys/invites):
deriving a display-safe session prefix, resolving the prefix both session
revoke routes accept to one session without ever pattern-matching a raw
secret, and deciding whether a pending invite is still usable.

API key hashing itself is ``api.security.token_hash`` (shared with session id
hashing) and is tested in ``tests/unit/test_token_hash.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.admin_users import (
    SessionPrefixError,
    invite_is_usable,
    resolve_session_prefix,
    session_id_prefix,
)


def test_session_id_prefix_truncates():
    assert session_id_prefix("abcdefghijklmnop", length=6) == "abcdef"


def test_session_id_prefix_shorter_than_length_returns_whole_string():
    assert session_id_prefix("abc", length=6) == "abc"


_PREFIX = "abcdef012345"
_MINE = _PREFIX + "a" * 52
_OTHER = "fedcba987654" + "0" * 52


class _Hashes:
    """The caller's scoped lookup, recording whether it was ever run."""

    def __init__(self, *hashes: str) -> None:
        self.hashes = list(hashes)
        self.calls = 0

    async def __call__(self) -> list[str]:
        self.calls += 1
        return self.hashes


async def _refusal(hashes: _Hashes, prefix: str) -> SessionPrefixError:
    with pytest.raises(SessionPrefixError) as exc:
        await resolve_session_prefix(prefix, hashes)
    return exc.value


async def test_resolve_session_prefix_returns_the_sole_match():
    assert await resolve_session_prefix(_PREFIX, _Hashes(_MINE, _OTHER)) == _MINE


async def test_resolve_session_prefix_accepts_every_hex_digit():
    full = "0123456789abcdef" + "0" * 48
    assert await resolve_session_prefix("0123456789abcdef", _Hashes(full)) == full


async def test_resolve_session_prefix_accepts_the_full_hash():
    assert await resolve_session_prefix(_MINE, _Hashes(_MINE, _OTHER)) == _MINE


@pytest.mark.parametrize(
    ("prefix", "detail"),
    [
        (_PREFIX[:11], "prefix too short"),
        ("", "prefix too short"),
        (_PREFIX[:11] + "g", "invalid prefix"),
        (_PREFIX.upper(), "invalid prefix"),
        ("abcDEF012-_xyz", "invalid prefix"),
        (_PREFIX[:11] + "%", "invalid prefix"),
        (_PREFIX[:11] + "_", "invalid prefix"),
        ("abc def012345", "invalid prefix"),
        ("abc;drop012345", "invalid prefix"),
    ],
)
async def test_resolve_session_prefix_refuses_a_prefix_no_stored_hash_can_have(prefix, detail):
    """A stored ``sid_hash`` is a lowercase SHA-256 hex digest, so anything
    shorter than the display prefix or outside that alphabet is refused with
    a 400 before the lookup runs. Uppercase is rejected, not folded."""
    hashes = _Hashes(_MINE, _PREFIX.upper() + "A" * 52)
    err = await _refusal(hashes, prefix)
    assert (err.status, err.detail) == (400, detail)
    assert hashes.calls == 0


async def test_resolve_session_prefix_404s_when_nothing_matches():
    err = await _refusal(_Hashes(_OTHER), _PREFIX)
    assert (err.status, err.detail) == (404, "session not found")


async def test_resolve_session_prefix_409s_when_several_match():
    err = await _refusal(_Hashes(_MINE, _PREFIX + "b" * 52), _PREFIX)
    assert (err.status, err.detail) == (409, "prefix matches multiple sessions")


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
