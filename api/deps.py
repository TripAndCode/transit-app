from fastapi import Depends, HTTPException, Request

from api.security import current_user, require_user


async def get_conn(request: Request):
    async with request.app.state.pool.acquire() as conn:
        yield conn


class _ClickHouseUnavailable:
    """Stand-in for ``app.state.ch_client`` when ClickHouse never came up at
    startup (see api.main's lifespan) or was explicitly not wired (some test
    fixtures set ``app.state.ch_client = None`` on purpose — see below).

    Many routers declare ``ch=Depends(get_ch)`` unconditionally even though
    the underlying compute function only touches ``ch`` on a live-fallback
    path (e.g. a ``time_band`` filter forcing a raw ClickHouse scan); the
    agg-table fast path never calls it at all. Raising eagerly inside
    :func:`get_ch` would 503 those Postgres-only requests too — regressing
    exactly the "purely-Postgres routes ... have nothing to do with
    ClickHouse" guarantee this dependency exists to protect, and making
    map.py's ``today_route_summary`` freshness try/except (a deliberate
    non-fatal degrade) unreachable dead code, since the dependency would
    fail before the route handler body ever runs.

    So this only raises lazily, on actual use (``ch.query(...)``, etc.) —
    harmless to hold onto and never touch (matching the ``ch=None``-is-safe
    convention already documented in pipeline.query.tool_queries /
    pipeline.query.meta_tools for callers with no ClickHouse client), but a
    clean ``HTTPException(503)`` instead of an ``AttributeError`` the moment
    something genuinely CH-dependent tries to use it.
    """

    def __getattr__(self, name: str):
        raise HTTPException(status_code=503, detail="ClickHouse is unavailable")


_CH_UNAVAILABLE = _ClickHouseUnavailable()


async def get_ch(request: Request):
    """ClickHouse client dependency.

    Returns the real client, or a stand-in that raises a clean 503 lazily
    (only if a caller actually tries to use it) when ``app.state.ch_client``
    is ``None`` — see :class:`_ClickHouseUnavailable`.
    """
    ch_client = request.app.state.ch_client
    return _CH_UNAVAILABLE if ch_client is None else ch_client


async def get_agency(agency_id: int, conn=Depends(get_conn)):
    row = await conn.fetchrow("SELECT agency_id FROM agencies WHERE agency_id=$1 AND deleted_at IS NULL", agency_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return agency_id


def get_locale(request: Request) -> str:
    """Read the per-request locale set by :class:`LocaleMiddleware`.

    Defaults to ``"ja"`` when the middleware hasn't run (e.g. ad-hoc test
    fixtures), so callers can always assume a non-empty supported value.
    """
    return getattr(request.state, "locale", "ja")


# Alias so routers can import get_current_user from api.deps and tests
# can override it via app.dependency_overrides without touching api.security.
get_current_user = require_user

# Optional variant — returns None for anonymous callers instead of 401.
get_current_user_optional = current_user
