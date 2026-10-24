"""Test users: `User` values that stand in for the auth dependencies, and
signed-in users seeded into the database for tests that go through the real
session middleware."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import asyncpg

from api.security import User, token_hash

_SESSION_LIFETIME = timedelta(days=30)


def admin_user(user_id: int = 1) -> User:
    """An active, LLM-approved admin, as `require_admin` resolves one.

    `user_id` is a parameter for tests that assert the acting admin's id was
    recorded: an id distinct from every other id in the test (an agency's, a
    target user's) is what lets that assertion tell them apart.
    """
    return User(
        user_id=user_id,
        email="admin@example.com",
        name="Admin",
        avatar_url=None,
        role="admin",
        suspended_at=None,
        llm_approved=True,
    )


class SeededSession(NamedTuple):
    sid: str
    user_id: int
    email: str


async def seed_session(
    conn: asyncpg.Connection,
    *,
    role: str = "user",
    email: str | None = None,
    name: str | None = None,
    suspended: bool = False,
    expires_in: timedelta = _SESSION_LIFETIME,
    user_agent: str | None = None,
) -> SeededSession:
    """Insert a user and one session for them; ``sid`` is the cookie value.

    A suspended user or a negative ``expires_in`` still gets its session row,
    so a test can show that the middleware refuses it.
    """
    if email is None:
        email = f"user-{secrets.token_hex(8)}@x"
    user_id = await conn.fetchval(
        "INSERT INTO users (email, name, role, suspended_at) VALUES ($1, $2, $3, $4) RETURNING user_id",
        email,
        name,
        role,
        datetime.now(timezone.utc) if suspended else None,
    )
    sid = await add_session(conn, user_id, expires_in=expires_in, user_agent=user_agent)
    return SeededSession(sid, user_id, email)


async def add_session(
    conn: asyncpg.Connection,
    user_id: int,
    *,
    expires_in: timedelta = _SESSION_LIFETIME,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    """Insert a session for an existing user and return its sid.

    Only the digest is stored, as sign-in stores it: the legacy raw ``sid``
    column stays NULL.
    """
    sid = secrets.token_urlsafe(32)
    await add_session_digest(conn, user_id, token_hash(sid), expires_in=expires_in, user_agent=user_agent, ip=ip)
    return sid


async def add_session_digest(
    conn: asyncpg.Connection,
    user_id: int,
    sid_hash: str,
    *,
    expires_in: timedelta = _SESSION_LIFETIME,
    user_agent: str | None = None,
    ip: str | None = None,
) -> None:
    """Insert a session stored under a chosen ``sid_hash``.

    For tests that need stored digests to share a prefix, which digests of
    random sids never do. No sid signs in with such a session.
    """
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at, user_agent, ip) VALUES ($1, $2, $3, $4, $5::inet)",
        sid_hash,
        user_id,
        datetime.now(timezone.utc) + expires_in,
        user_agent,
        ip,
    )
