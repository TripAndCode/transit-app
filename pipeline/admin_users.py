"""Pure logic behind the admin user drawer: sessions, API keys, invites.

Kept dependency-free of asyncpg so it's unit-testable without a database --
the routers in ``api/routers/admin.py`` own all the actual I/O. API key and
session id hashing is ``api.security.token_hash``, shared with the rest of
the auth stack rather than duplicated here.
"""

from datetime import datetime


def session_id_prefix(sid_hash: str, length: int = 12) -> str:
    """Display-safe prefix of a session's ``sid_hash`` -- long enough to
    disambiguate a user's sessions in the UI. Sessions are keyed on the hash,
    not the raw session id, so this is already a hash prefix, never a usable
    credential."""
    return sid_hash[:length]


def unique_prefix_match(sid_hashes: list[str], prefix: str) -> str | None:
    """Return the one ``sid_hash`` in ``sid_hashes`` starting with ``prefix``,
    or ``None`` if zero or more than one match -- an ambiguous or absent
    prefix must never cause a delete of the wrong (or multiple) session(s)."""
    matches = [s for s in sid_hashes if s.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def invite_is_usable(consumed_at: datetime | None, expires_at: datetime | None, now: datetime) -> bool:
    """Whether a pending ``user_invites`` row can still be honored: not
    already consumed, and not past its expiry (an invite with no expiry
    never expires)."""
    if consumed_at is not None:
        return False
    if expires_at is not None and expires_at <= now:
        return False
    return True
