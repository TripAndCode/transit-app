"""Pure logic behind the admin user drawer: sessions, API keys, invites.

Kept dependency-free of asyncpg so it's unit-testable without a database --
the routers in ``api/routers/admin.py`` own all the actual I/O.
"""

import hashlib
from datetime import datetime


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest of an issued API key. The raw key is shown to the
    admin exactly once (the POST response); only this hash is persisted."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def session_id_prefix(sid: str, length: int = 12) -> str:
    """Display-safe prefix of a session id -- long enough to disambiguate a
    user's sessions in the UI, short enough to never be a usable credential."""
    return sid[:length]


def unique_prefix_match(sids: list[str], prefix: str) -> str | None:
    """Return the one session id in ``sids`` starting with ``prefix``, or
    ``None`` if zero or more than one match -- an ambiguous or absent prefix
    must never cause a delete of the wrong (or multiple) session(s)."""
    matches = [s for s in sids if s.startswith(prefix)]
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
