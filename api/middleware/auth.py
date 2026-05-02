import asyncpg
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class APIKeyMiddleware(BaseHTTPMiddleware):
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
