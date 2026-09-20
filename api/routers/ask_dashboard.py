"""Dashboard endpoints feeding the Ask tab's empty-thread analysis cards."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_agency, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, clamp_range_ctx
from pipeline.dashboard_queries import anomaly_timeline, delay_heatmap, movers

router = APIRouter(prefix="/api/{agency_id}/ask/dashboard", tags=["dashboard"])


def _resolve_ctx(
    from_date: str | None,
    to_date: str | None,
    dow: str,
    time_band: str,
    service: str,
    routes: tuple[str, ...] = (),
) -> RangeCtx:
    """Build a validated, clamped RangeCtx from this router's query params.

    These endpoints declare their filters as loose ``str``/``list[str]``
    params rather than the shared :func:`api.range.get_range_ctx` dependency
    (they carry extra per-endpoint params alongside), so the validation,
    clamping and route de-duplication have to come from the same shared
    helper instead of a hand-copied variant.
    """
    return clamp_range_ctx(
        from_=from_date,
        to=to_date,
        dow=dow,
        time_band=time_band,
        service=service,
        routes=routes,
    )


@router.get("/heatmap", response_model=None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def heatmap_endpoint(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    dow: str = Query(default="all"),
    time_band: str = Query(default="all"),
    service: str = Query(default="all"),
    routes: list[str] = Query(default=[]),
    dimension: str = Query(default="dow", description="'dow' or 'hour_band'"),
    top_routes: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    if dimension not in ("dow", "hour_band"):
        raise HTTPException(status_code=400, detail="dimension must be 'dow' or 'hour_band'")
    ctx = _resolve_ctx(from_date, to_date, dow, time_band, service, tuple(routes))
    result = await delay_heatmap(conn, agency_id=agency_id, ctx=ctx, dimension=dimension, top_routes=top_routes)
    return asdict(result)


@router.get("/anomalies", response_model=None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def anomalies_endpoint(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    dow: str = Query(default="all"),
    time_band: str = Query(default="all"),
    service: str = Query(default="all"),
    routes: list[str] = Query(default=[]),
    days: int = Query(default=30, ge=7, le=90),
    sigma: float = Query(default=2.0, ge=1.0, le=5.0),
) -> dict[str, Any]:
    ctx = _resolve_ctx(from_date, to_date, dow, time_band, service, tuple(routes))
    result = await anomaly_timeline(conn, agency_id=agency_id, ctx=ctx, days=days, sigma=sigma)
    return asdict(result)


@router.get("/movers", response_model=None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def movers_endpoint(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    dow: str = Query(default="all"),
    time_band: str = Query(default="all"),
    service: str = Query(default="all"),
    routes: list[str] = Query(default=[]),
    window_days: int = Query(default=7, ge=1, le=30),
    top: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    ctx = _resolve_ctx(from_date, to_date, dow, time_band, service, tuple(routes))
    result = await movers(conn, agency_id=agency_id, ctx=ctx, window_days=window_days, top=top)
    return asdict(result)
