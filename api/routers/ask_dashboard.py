"""Dashboard endpoints feeding the Ask tab's empty-thread analysis cards."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_agency, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import DEFAULT_RANGE_DAYS, MAX_RANGE_DAYS, DowFilter, RangeCtx, ServiceType, TimeBand, parse_iso_date
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
    today = date.today()
    to_d = parse_iso_date(to_date) or today
    from_d = parse_iso_date(from_date) or (to_d - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if from_d > to_d:
        from_d, to_d = to_d, from_d
    if (to_d - from_d).days >= MAX_RANGE_DAYS:
        from_d = to_d - timedelta(days=MAX_RANGE_DAYS - 1)
    dow_ = cast(DowFilter, dow if dow in ("all", "weekday", "weekend") else "all")
    valid_bands = {"all", "morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"}
    tb_ = cast(TimeBand, time_band if time_band in valid_bands else "all")
    svc_ = cast(ServiceType, service if service in ("all", "平日", "土日祝") else "all")
    return RangeCtx(
        from_date=from_d,
        to_date=to_d,
        dow=dow_,
        time_band=tb_,
        service=svc_,
        routes=routes,
    )


@router.get("/heatmap")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def heatmap_endpoint(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    dow: str = Query(default="all"),
    time_band: str = Query(default="all"),
    service: str = Query(default="all"),
    routes: list[str] = Query(default=[]),
    dimension: str = Query(default="dow", description="'dow' or 'hour_band'"),
    top_routes: int = Query(default=20, ge=1, le=50),
):
    if dimension not in ("dow", "hour_band"):
        raise HTTPException(status_code=400, detail="dimension must be 'dow' or 'hour_band'")
    ctx = _resolve_ctx(from_date, to_date, dow, time_band, service, tuple(routes))
    result = await delay_heatmap(conn, agency_id=agency_id, ctx=ctx, dimension=dimension, top_routes=top_routes)
    return asdict(result)


@router.get("/anomalies")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def anomalies_endpoint(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    dow: str = Query(default="all"),
    time_band: str = Query(default="all"),
    service: str = Query(default="all"),
    routes: list[str] = Query(default=[]),
    days: int = Query(default=30, ge=7, le=90),
    sigma: float = Query(default=2.0, ge=1.0, le=5.0),
):
    ctx = _resolve_ctx(from_date, to_date, dow, time_band, service, tuple(routes))
    result = await anomaly_timeline(conn, agency_id=agency_id, ctx=ctx, days=days, sigma=sigma)
    return asdict(result)


@router.get("/movers")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def movers_endpoint(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    dow: str = Query(default="all"),
    time_band: str = Query(default="all"),
    service: str = Query(default="all"),
    routes: list[str] = Query(default=[]),
    window_days: int = Query(default=7, ge=1, le=30),
    top: int = Query(default=10, ge=1, le=50),
):
    ctx = _resolve_ctx(from_date, to_date, dow, time_band, service, tuple(routes))
    result = await movers(conn, agency_id=agency_id, ctx=ctx, window_days=window_days, top=top)
    return asdict(result)
