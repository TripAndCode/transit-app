"""FastAPI application bootstrap.

Wires routers, middleware, lifespan (asyncpg pool + Groq key validation), CORS,
and an optional SPA static mount. In production the multistage Dockerfile copies
the built React frontend into ``api/static/``; this module then mounts the SPA
at ``/`` with an explicit JSON 404 for unknown ``/api/*`` paths so frontend
fetches keep getting structured errors instead of HTML index pages.
"""

import logging
import os
import os.path
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware as StarletteSessionMiddleware

from api.aggregate_errors import aggregate_not_ready_handler
from api.logging_config import configure as configure_logging
from api.middleware.auth import APIKeyMiddleware
from api.middleware.cancel_on_disconnect import CancelGETOnDisconnectMiddleware
from api.middleware.locale import LocaleMiddleware
from api.middleware.ratelimit import limiter
from api.middleware.request_log import RequestLogMiddleware
from api.middleware.session import SessionMiddleware
from api.routers.admin import router as admin_router
from api.routers.agencies import router as agencies_router
from api.routers.ask import router as ask_router
from api.routers.ask_dashboard import router as ask_dashboard_router
from api.routers.auth import local_admin_enabled, seed_local_admin
from api.routers.auth import router as auth_router
from api.routers.conversations import router as conversations_router
from api.routers.debug import router as debug_router
from api.routers.internal import router as internal_router
from api.routers.map import router as map_router
from api.routers.me import router as me_router
from api.routers.network import router as network_router
from api.routers.overview import router as overview_router
from api.routers.reports import router as reports_router
from api.routers.static import router as static_router
from api.security import cookie_secure

_log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# The placeholder signing key used when SESSION_SIGNING_KEY is unset. Safe for
# local/anonymous boots; refused at startup once SSO is enabled (see
# _validate_session_signing_key) so a forgeable-cookie config can't reach prod.
_DEV_SIGNING_KEY = "dev-only-not-secret"

# Path prefixes the SPA fallback must NOT swallow. An unknown URL under one
# of these prefixes returns a structured JSON 404 instead of the SPA's
# index.html, so frontend fetches surface real errors instead of choking on
# HTML bodies. The SPA owns everything else — including ``/agencies/:id/map``
# (a client-side route, not the agency CRUD which lives under ``/api/agencies``).
_API_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json", "internal/")


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection setup. Pin the session to Asia/Tokyo so ``captured_at::date``
    casts honor the operator's local calendar instead of UTC (Aomori observations
    span midnight JST and would otherwise straddle two UTC dates).

    Also caps any single query at 30s — all read endpoints serve from small
    precomputed agg_* tables (sub-second), so this only ever fires on a
    pathological live-fallback scan, as a safety net against a hung request.
    (analyze/ingest run on their own psycopg2 connections, not this pool.)"""
    await conn.execute("SET TIME ZONE 'Asia/Tokyo'")
    await conn.execute("SET statement_timeout = '30s'")


_AUTH_ENV = (
    "SESSION_SIGNING_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
)


def auth_status() -> tuple[bool, list[str]]:
    """Read env every call so test monkeypatching + runtime config-flip both
    take effect without re-importing the app. Enabled iff all five vars set."""
    missing = [k for k in _AUTH_ENV if not os.environ.get(k)]
    return (not missing, missing)


def _validate_cors_origins(origins: list[str], allow_credentials: bool) -> None:
    """Reject the spec-incompatible CORS combo: ``*`` + ``Allow-Credentials``.

    Browsers silently block credentialed responses whose ``Access-Control-
    Allow-Origin`` is ``*``. Failing at startup surfaces the misconfiguration
    with a clear error instead of silent breakage at request time.
    """
    if allow_credentials and "*" in origins:
        raise RuntimeError(
            "CORS_ORIGINS contains '*' but allow_credentials=True. "
            "The CORS spec forbids the combination — list explicit origins."
        )


def _validate_session_signing_key(enabled: bool, signing_key: str | None) -> None:
    """Refuse to boot an auth-enabled deployment that still uses the dev signing
    key — every session/OAuth cookie would be forgeable. No-op when auth is off
    (anonymous-only mode never mints those cookies)."""
    if enabled and signing_key == _DEV_SIGNING_KEY:
        raise RuntimeError(
            "SESSION_SIGNING_KEY is the dev default in an auth-enabled deployment. "
            "Set a real secret (e.g. `openssl rand -hex 32`)."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate required env, open the asyncpg pool, and tear it down on exit.

    Auth env is optional in aggregate: all five vars present → SSO on; none
    present → SSO off (anonymous-only mode, ``/api/auth/*`` returns 503). A
    partial set is rejected as a misconfiguration since a half-wired OAuth
    flow would leak state cookies without ever completing.
    """
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY env var is required")
    enabled, missing = auth_status()
    if not enabled and len(missing) != len(_AUTH_ENV):
        raise RuntimeError(
            f"Partial auth env: missing {', '.join(missing)}. Set all five or none — half-wired OAuth is unsafe."
        )
    _validate_session_signing_key(enabled, os.environ.get("SESSION_SIGNING_KEY"))
    # max_size=20 (asyncpg default 10): the overview pool-gather path fans
    # out to ~10 concurrent per-task connections while the request's own
    # get_conn dependency still holds a slot — default sizing left the
    # fan-out one slot short and serialized a stage on every cold request.
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, init=_init_connection, min_size=10, max_size=20)

    # Break-glass local-admin account (independent of the OAuth env block
    # above) — no-ops unless DEFAULT_ADMIN_USERNAME/DEFAULT_ADMIN_PASSWORD
    # are both set. See api.routers.auth.seed_local_admin.
    await seed_local_admin(app.state.pool)

    # Phase 2: warm the embedding model so first request doesn't pay the
    # load cost. Non-fatal: if the model can't load, the router will fall
    # through to the LLM path (Phase 1 behavior).
    from pipeline.query.embeddings import get_embedder

    embedder = get_embedder()
    if not embedder.available:
        _log.warning("Embedder unavailable at startup — Phase 2 router degrades to LLM-only")

    yield
    await app.state.pool.close()


_CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

configure_logging()

app = FastAPI(title="Transit Delay API", lifespan=lifespan)
app.state.limiter = limiter
# slowapi's handler is typed against its own exception class, not Starlette's
# broader (Request, Exception) signature — runtime contract is fine.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
# A read endpoint hitting an agg_* table that doesn't exist yet (deployment behind
# on migrations) degrades to a localized 503 instead of an opaque 500.
app.add_exception_handler(asyncpg.exceptions.UndefinedTableError, aggregate_not_ready_handler)  # type: ignore[arg-type]
# Starlette wraps middleware in reverse-add order — the LAST add_middleware
# call runs FIRST on each request. Order today (request-side, outermost first):
#   StarletteSessionMiddleware  (Authlib needs request.session)
#   SessionMiddleware           (loads request.state.user from sid cookie)
#   APIKeyMiddleware            (loads request.state.tier from X-API-Key)
#   LocaleMiddleware            (parses Accept-Language → request.state.locale)
# That means require_user/require_admin see request.state.user before any
# router runs, which is what we want. LocaleMiddleware is innermost (cheap,
# no I/O) and only needs to run before the route handlers read state.locale.
# Innermost (added first → runs closest to the routers): cancels GET handler
# tasks when the client disconnects, so aborted SPA fetches also cancel the
# asyncpg query instead of letting heavy scans run to completion. GET-only by
# design — see api/middleware/cancel_on_disconnect.py.
app.add_middleware(CancelGETOnDisconnectMiddleware)
app.add_middleware(LocaleMiddleware)
app.add_middleware(APIKeyMiddleware)
app.add_middleware(SessionMiddleware)
app.add_middleware(
    StarletteSessionMiddleware,
    secret_key=os.environ.get("SESSION_SIGNING_KEY", _DEV_SIGNING_KEY),
    session_cookie="auth_tmp",
    max_age=600,
    # Secure when the deployment is HTTPS (PUBLIC_BASE_URL). TLS terminates at
    # Railway's edge — the browser↔edge hop is HTTPS, so a Secure cookie is sent;
    # the edge↔app hop being plain HTTP doesn't matter for the Secure flag.
    https_only=cookie_secure(),
    same_site="lax",
)
# Cross-origin SSO from the Vite dev server (:5173 → :8000) needs both
# the sid cookie sent (credentials: 'include' on the client) and the
# Access-Control-Allow-Credentials response header. Wildcard origins
# are incompatible with that combo — _validate_cors_origins enforces.
_validate_cors_origins(_CORS_ORIGINS, allow_credentials=True)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "X-API-Key"],
)
# Outermost on the request side — sees the final status after every
# inner middleware ran. Assigns request_id, times the request, emits
# one INFO log to 'api.access' per request.
app.add_middleware(RequestLogMiddleware)

app.include_router(admin_router)
app.include_router(agencies_router)
app.include_router(ask_router)
app.include_router(ask_dashboard_router)
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(debug_router)
app.include_router(map_router)
app.include_router(me_router)
app.include_router(network_router)
app.include_router(overview_router)
app.include_router(reports_router)
app.include_router(static_router)
app.include_router(internal_router)


@app.get("/health")
async def health():
    """Liveness probe. Returns ``{"status": "ok"}`` once the app is responding."""
    return {"status": "ok"}


@app.get("/api/config")
async def config():
    """Public client config. Lets the SPA hide login UI when SSO is unconfigured,
    and separately show/hide the break-glass local-admin password form."""
    enabled, _ = auth_status()
    return {"auth_enabled": enabled, "local_admin_enabled": local_admin_enabled()}


def _maybe_mount_static(app: FastAPI) -> None:
    """Mount the built SPA at ``/`` if ``api/static/`` exists.

    Idempotent: prior ``assets`` and ``spa_fallback`` routes are stripped before
    re-adding, so this can be called repeatedly (e.g. from pytest fixtures
    monkeypatching ``STATIC_DIR``).

    The catch-all ``spa_fallback`` route serves ``index.html`` for any path that
    no router consumed, EXCEPT paths starting with API/health/doc prefixes —
    those return a JSON 404 so the frontend fetch layer can render a real error
    instead of trying to JSON-parse the SPA HTML.
    """
    if not os.path.isdir(STATIC_DIR):
        return

    app.routes[:] = [r for r in app.routes if getattr(r, "name", None) not in ("spa_fallback", "assets")]

    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_path = os.path.join(STATIC_DIR, "index.html")

    @app.get("/{full_path:path}", include_in_schema=False, name="spa_fallback")
    async def spa_fallback(full_path: str):
        """Serve ``index.html`` for SPA routes; JSON 404 for unknown API paths."""
        if full_path.startswith(_API_PREFIXES):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(index_path)


_maybe_mount_static(app)
