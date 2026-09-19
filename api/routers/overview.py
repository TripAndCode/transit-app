"""概況 (Overview) tab endpoint.

Returns the full magazine payload in a single locale-aware round-trip.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.deps import get_agency, get_ch, get_conn, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, get_range_ctx
from pipeline.reports import compute_overview_summary

router = APIRouter(prefix="/api/{agency_id}", tags=["overview"])

# Minimum observations behind a route's peak-hour figure before it is shown.
_PEAK_HOUR_MIN_SAMPLES = 3


def _peak_hour_breakdown_sql(*, by_dow: bool) -> str:
    """Top-20 routes by pooled average delay for one hour of ``agg_route_hour_dow``.

    ``avg_min`` is always re-derived from ``sum_delay_sec``/``samples`` over the
    grouped rows, never read from the stored per-row ``avg_min``, so the
    single-DOW and the all-DOW answer are the same statistic computed the same
    way. The sample floor is applied to the group total alone: a route whose
    observations are spread thinly across the rows being pooled is still
    well-evidenced once pooled, and dropping its rows first would both hide it
    and bias the average that remains.

    ``by_dow`` selects the parameter shape: ``$1`` agency, ``$2`` hour, and
    ``$3`` day-of-week only when scoping to one DOW.
    """
    dow_clause = "AND dow = $3 " if by_dow else ""
    return f"""
        SELECT route_code, service_type,
               (SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric
                   / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min,
               SUM(samples) AS samples
        FROM agg_route_hour_dow
        WHERE agency_id = $1 AND hour = $2 {dow_clause}
        GROUP BY route_code, service_type
        HAVING SUM(samples) >= {_PEAK_HOUR_MIN_SAMPLES}
        ORDER BY avg_min DESC NULLS LAST
        LIMIT 20
    """


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
    delta_pct: float | None
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
    for the requested hour; either way the average is pooled from the summed
    delay and sample columns. A route whose pooled observations for the hour
    number fewer than ``_PEAK_HOUR_MIN_SAMPLES`` is excluded to suppress noise
    from infrequent service patterns. Returns at most 20 routes worst-first.
    """
    params: list[object] = [agency_id, hour]
    if dow is not None:
        params.append(dow)
    rows = await conn.fetch(_peak_hour_breakdown_sql(by_dow=dow is not None), *params)
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
            if r["avg_min"] is not None
        ],
    )


@router.get("/overview/summary", response_model=OverviewSummary)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def overview_summary(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
    ctx: RangeCtx = Depends(get_range_ctx),
    locale: str = Depends(get_locale),
) -> OverviewSummary:
    """Return the 概況 magazine payload for one agency over ``ctx``.

    Locale picks the language of any string fields the backend emits
    (today: none — strings are frontend-side. Reserved for future
    qualitative labels). See spec section "Architecture".
    """
    payload = await compute_overview_summary(agency_id, ctx, conn, locale, pool=request.app.state.pool, ch=ch)
    return OverviewSummary(**payload)
