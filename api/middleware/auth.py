from datetime import datetime, timezone

import asyncpg
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.security import token_hash


def _key_usable(row, now: datetime) -> bool:
    """True while an ``api_keys`` row is neither revoked nor past its expiry.

    Both columns are NULL-means-unlimited. The check lives in Python rather
    than in the WHERE clause so a revoked key and an unknown key are
    indistinguishable to the caller: the lookup either way returns the same
    401 and tells an attacker nothing about which keys exist.
    """
    if row["revoked_at"] is not None:
        return False
    expires_at = row["expires_at"]
    return expires_at is None or expires_at > now


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Resolves the request's rate-limit tier from an optional ``X-API-Key``.

    No header means the free tier, which is every caller today. A paid tier
    is a commercial decision, not something derivable from the data, so keys
    are provisioned deliberately: an admin issues one against a user through
    ``POST /api/admin/api-keys``, and older rows were inserted by hand with
    only an owner email. The table's ``tier`` column defaults to ``'pro'``.

    A key is only as live as the account behind it. Suspending or
    soft-deleting a user kills their sessions, and both set
    ``users.suspended_at``, so the owner's state is checked here rather than
    by revoking every key at suspension time -- that keeps the decision
    reversible (restoring the account restores its keys) and leaves no way
    to miss a key issued through some later path. A row with no owner is a
    legacy hand-inserted key and stands on its own columns alone.

    The stored column is ``key_hash``, the SHA-256 digest of the key; the
    operator issuing a key keeps the only copy of the raw string. Revocation
    and expiry are columns on the same row, so withdrawing access never
    depends on deleting history.

    The consequence while the table is empty, which is worth stating because
    it reads as a bug otherwise: *any* request carrying ``X-API-Key`` gets a
    401, and ``PRO_LIMIT`` in ``api/middleware/ratelimit.py`` is unreachable.
    Both are correct for a deployment that has sold nothing.
    """

    async def dispatch(self, request: Request, call_next):
        key = request.headers.get("X-API-Key")
        if not key:
            request.state.tier = "free"
            return await call_next(request)
        pool: asyncpg.Pool = request.app.state.pool
        row = await pool.fetchrow(
            """
            SELECT k.tier, k.revoked_at, k.expires_at, u.suspended_at AS owner_suspended_at
            FROM api_keys k
            LEFT JOIN users u ON u.user_id = k.owner_user_id
            WHERE k.key_hash = $1
            """,
            token_hash(key),
        )
        # One rejection for every reason: an unknown key, a revoked or
        # expired one, and a suspended owner must be indistinguishable to a
        # caller holding a stolen key, or the response becomes an oracle for
        # the account's state.
        if row is None or row["owner_suspended_at"] is not None or not _key_usable(row, datetime.now(timezone.utc)):
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)
        request.state.tier = row["tier"]
        return await call_next(request)
