import asyncpg
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Resolves the request's rate-limit tier from an optional ``X-API-Key``.

    No header means the free tier, which is every caller today. Rows in
    ``api_keys`` are operator-inserted by hand -- the same convention as
    ``ridership_weights`` (migration 0035) and ``route_performance_standards``
    (0041): a paid tier is a commercial decision, not something derivable from
    the data, so there is no ingestion path and no CRUD endpoint. The table's
    ``tier`` column defaults to ``'pro'`` so that inserting a key and an owner
    email is the whole provisioning step.

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
        row = await pool.fetchrow("SELECT tier FROM api_keys WHERE key = $1", key)
        if row is None:
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)
        request.state.tier = row["tier"]
        return await call_next(request)
