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

    No header means the free tier, which is every caller today. Rows in
    ``api_keys`` are operator-inserted by hand -- the same convention as
    ``ridership_weights`` (migration 0035) and ``route_performance_standards``
    (0041): a paid tier is a commercial decision, not something derivable from
    the data, so there is no ingestion path and no CRUD endpoint. The table's
    ``tier`` column defaults to ``'pro'`` so that inserting a key hash and an
    owner email is the whole provisioning step.

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
            "SELECT tier, revoked_at, expires_at FROM api_keys WHERE key_hash = $1",
            token_hash(key),
        )
        if row is None or not _key_usable(row, datetime.now(timezone.utc)):
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)
        request.state.tier = row["tier"]
        return await call_next(request)
