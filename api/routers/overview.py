"""概況 (Overview) tab endpoint.

Returns the full magazine payload in a single locale-aware round-trip.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.deps import get_agency, get_conn, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, get_range_ctx
from pipeline.reports import compute_overview_summary

router = APIRouter(prefix="/api/{agency_id}", tags=["overview"])


class Headline(BaseModel):
    """Last-7-day avg + prior-7-day baseline + signed delta.

    ``window_from`` / ``window_to`` are the ISO dates of the 7-day window
    the headline covers (always a 7-day slice anchored at ``ctx.to_date``,
    even when the user has widened the filter to a longer range). Surfaces
    them so the frontend eyebrow can show the actual headline window
    instead of the full ctx range, which would be misleading.
    """

    avg_min: float | None
    baseline_avg_min: float | None
    delta_min: float | None
    delta_pct: float | None
    samples: int
    window_from: str  # ISO date
    window_to: str  # ISO date


class Mover(BaseModel):
    """One worsening or improving route entry."""

    route_code: str
    route_short_name: str | None
    delta_min: float
    delta_pct: float
    current_avg_min: float
    previous_avg_min: float
    streak_weeks: int
    sparkline_points: list[float]


class Movers(BaseModel):
    """Top-10 worsening and top-10 improving routes (card shows 3)."""

    worse: list[Mover]
    better: list[Mover]


class ConcentrationTopRoute(BaseModel):
    """One route's share of total agency delay."""

    route_code: str
    route_short_name: str | None
    share_pct: float


class Concentration(BaseModel):
    """Top-20 routes plus aggregate "rest" share and rest route count.

    Card variant on the frontend uses the first 5; modal variant draws
    a Pareto bar list across all 20 plus a Lorenz-curve overlay.
    """

    top_routes: list[ConcentrationTopRoute]
    rest_share_pct: float
    rest_route_count: int = 0


class TopDelayedRoute(BaseModel):
    """One route's absolute avg delay in the current 7-day window."""

    route_code: str
    route_short_name: str | None
    avg_min: float


class TopDelayed(BaseModel):
    """Top-5 routes by absolute avg delay + a count of routes at/above the
    2.0-min "not ok" threshold, both over the same window the headline
    covers."""

    routes: list[TopDelayedRoute]
    delayed_count: int


class PeakHour(BaseModel):
    """24 hourly buckets, peak hour highlighted."""

    by_hour: list[float | None]
    peak_hour: int
    peak_avg_min: float


class ServiceSplitDay(BaseModel):
    """One day's weekday vs weekend avg_min split."""

    date: str  # ISO date
    weekday: float | None
    weekend: float | None


class OverviewSummary(BaseModel):
    """Magazine payload — 5 modules + sparkline + range echo.

    ``peak_hour_weekday`` / ``peak_hour_weekend`` and
    ``service_split_daily`` are additive fields used by the modal
    drill-downs; existing card consumers ignore them.
    """

    headline: Headline
    movers: Movers
    concentration: Concentration
    top_delayed: TopDelayed
    peak_hour: PeakHour | None
    peak_hour_weekday: PeakHour | None = None
    peak_hour_weekend: PeakHour | None = None
    service_split: dict[str, float]
    service_split_daily: list[ServiceSplitDay] = []
    sparkline_points: list[float]


class RouteHourEntry(BaseModel):
    route_code: str
    service_type: str
    avg_min: float
    samples: int


class PeakHourBreakdown(BaseModel):
    hour: int
    dow: int | None
    routes: list[RouteHourEntry]


@router.get("/peak-hour-breakdown", response_model=PeakHourBreakdown)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def peak_hour_breakdown(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    hour: int = Query(ge=0, le=23),
    dow: int | None = Query(default=None, ge=1, le=7),
) -> PeakHourBreakdown:
    """Top routes by average delay for a given hour (and optionally day-of-week).

    Reads from ``agg_route_hour_dow``. When ``dow`` is omitted, pools all DOWs
    for the requested hour. Routes with fewer than 3 samples are excluded to
    suppress noise from infrequent service patterns. Returns at most 20 routes
    ordered worst-first.
    """
    if dow is not None:
        rows = await conn.fetch(
            """
            SELECT route_code, service_type, avg_min, samples
            FROM agg_route_hour_dow
            WHERE agency_id = $1 AND dow = $2 AND hour = $3 AND samples >= 3
            ORDER BY avg_min DESC
            LIMIT 20
            """,
            agency_id, dow, hour,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT route_code, service_type,
                   SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min,
                   SUM(samples) AS samples
            FROM agg_route_hour_dow
            WHERE agency_id = $1 AND hour = $2 AND samples >= 3
            GROUP BY route_code, service_type
            HAVING SUM(samples) >= 3
            ORDER BY avg_min DESC
            LIMIT 20
            """,
            agency_id, hour,
        )
    return PeakHourBreakdown(
        hour=hour,
        dow=dow,
        routes=[
            RouteHourEntry(
                route_code=r["route_code"],
                service_type=r["service_type"],
                avg_min=round(float(r["avg_min"]), 2),
                samples=int(r["samples"]),
            )
            for r in rows
        ],
    )


@router.get("/overview/summary", response_model=OverviewSummary)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def overview_summary(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
    locale: str = Depends(get_locale),
) -> OverviewSummary:
    """Return the 概況 magazine payload for one agency over ``ctx``.

    Locale picks the language of any string fields the backend emits
    (today: none — strings are frontend-side. Reserved for future
    qualitative labels). See spec section "Architecture".
    """
    payload = await compute_overview_summary(agency_id, ctx, conn, locale, pool=request.app.state.pool)
    return OverviewSummary(**payload)
