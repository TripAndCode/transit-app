"""Pure logic behind the admin user drawer (sessions, API keys, invites) and
the self-service session revoke.

Kept dependency-free of asyncpg so it's unit-testable without a database --
the routers in ``api/routers/admin.py`` and ``api/routers/me.py`` own all the
actual I/O. API key and session id hashing is ``api.security.token_hash``,
shared with the rest of the auth stack rather than duplicated here.
"""

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime

SESSION_PREFIX_MIN_LENGTH = 12
_HEX_DIGITS = frozenset("0123456789abcdef")


def session_id_prefix(sid_hash: str, length: int = SESSION_PREFIX_MIN_LENGTH) -> str:
    """Display-safe prefix of a session's ``sid_hash`` -- long enough to
    disambiguate a user's sessions in the UI. Sessions are keyed on the hash,
    not the raw session id, so this is already a hash prefix, never a usable
    credential."""
    return sid_hash[:length]


class SessionPrefixError(Exception):
    """A session prefix that names no single session. ``status`` and
    ``detail`` are the HTTP answer, shared by the self-service and admin
    revoke routes so the two cannot drift: 400 for a prefix no stored hash
    can have, 404 when none matches, 409 when several do."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def resolve_session_prefix(prefix: str, scoped_hashes: Callable[[], Awaitable[Iterable[str]]]) -> str:
    """The one ``sid_hash`` starting with ``prefix`` among those
    ``scoped_hashes`` returns, or :class:`SessionPrefixError`.

    ``scoped_hashes`` is the caller's lookup and must return only the
    sessions of the user being acted on, so a prefix can never resolve to
    another user's session or be made ambiguous by one. It is called only
    once ``prefix`` is at least :data:`SESSION_PREFIX_MIN_LENGTH` lowercase
    hex characters: a stored hash is a lowercase SHA-256 hex digest, so
    anything else (uppercase included, which is rejected rather than
    folded) can match nothing, and the alphabet keeps both LIKE wildcards
    out of the value. A prefix matching several sessions is refused rather
    than guessed at.
    """
    if len(prefix) < SESSION_PREFIX_MIN_LENGTH:
        raise SessionPrefixError(400, "prefix too short")
    if not _HEX_DIGITS.issuperset(prefix):
        raise SessionPrefixError(400, "invalid prefix")
    matches = [h for h in await scoped_hashes() if h.startswith(prefix)]
    if not matches:
        raise SessionPrefixError(404, "session not found")
    if len(matches) > 1:
        raise SessionPrefixError(409, "prefix matches multiple sessions")
    return matches[0]


def invite_is_usable(consumed_at: datetime | None, expires_at: datetime | None, now: datetime) -> bool:
    """Whether a pending ``user_invites`` row can still be honored: not
    already consumed, and not past its expiry (an invite with no expiry
    never expires)."""
    if consumed_at is not None:
        return False
    if expires_at is not None and expires_at <= now:
        return False
    return True
