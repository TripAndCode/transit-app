"""概況 (Overview) tab endpoint.

Returns the full magazine payload in a single round-trip. Nothing in it is
worded on the server, so it is the same in every language.

Every route list here leaves out the codes :mod:`pipeline.route_catalog`
names, retired codes and routes whose feed reports no delay, from what it
lists and from every count or share beside it, unless the routes filter names
the route.
"""

from __future__ import annotations

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_agency, get_ch
from api.middleware.ratelimit import limiter, tier_limit
from api.range import RangeCtx, get_range_ctx
from api.scope_applied import ALL_SIX, scope_applied
from pipeline.day_types import DayType
from pipeline.reports import compute_overview_summary
from pipeline.reports.reference import ReferenceReason

router = APIRouter(prefix="/api/{agency_id}", tags=["overview"])

_SUMMARY_SCOPE = scope_applied(*ALL_SIX)


class HeadlineReference(BaseModel):
    """The headline's figure over the ``days`` days before the period, under
    the same scope: the agency's own usual level.

    ``avg_min`` is None whenever ``reason`` names why the window cannot stand
    as that level. ``days_selected`` counts the window's days the scope's
    day-of-week and service filters keep, ``days_collected`` those the
    agency's feed holds data on, and ``min_days`` / ``min_samples`` the
    floors the figure needs; ``not_aggregated`` reads nothing, so its counts
    are 0. ``delta_min`` / ``delta_pct`` are the headline's change from it, as
    the baseline's are.
    """

    avg_min: float | None
    delta_min: float | None
    delta_pct: float | None
    samples: int
    window_from: str  # ISO date
    window_to: str  # ISO date
    days: int
    days_selected: int
    days_collected: int
    min_days: int
    min_samples: int
    reason: ReferenceReason | None


class Headline(BaseModel):
    """The period's avg + the same-width window before it as the baseline +
    signed delta, and its trailing reference.

    ``window_from`` / ``window_to`` are the ISO dates of the window the
    headline covers: the requested period up to its last day with data, so
    the hero row and the scope sentence describe the same period. The
    baseline is the same number of days immediately before it. Surfaced so the
    frontend labels the headline by the days it actually averages.
    """

    avg_min: float | None
    baseline_avg_min: float | None
    delta_min: float | None
    delta_pct: float | None
    samples: int
    window_from: str  # ISO date
    window_to: str  # ISO date
    reference: HeadlineReference


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
    """One route's share of the delay of every route the card may list."""

    route_code: str
    route_short_name: str | None
    share_pct: float


class Concentration(BaseModel):
    """Top-20 routes plus aggregate "rest" share and rest route count, of the
    routes the card may list.

    Card variant on the frontend uses the first 5; modal variant draws
    a Pareto bar list across all 20 plus a Lorenz-curve overlay.
    """

    top_routes: list[ConcentrationTopRoute]
    rest_share_pct: float
    rest_route_count: int = 0


class TopDelayedRoute(BaseModel):
    """One route's absolute avg delay in the headline's window, and its
    change (minutes) from the same-width window before; ``delta_min`` is None
    when the route was too thinly observed there to compare."""

    route_code: str
    route_short_name: str | None
    avg_min: float
    delta_min: float | None = None


class RouteBandCount(BaseModel):
    """Routes in one routes-to-check band, and the band's lower bound."""

    count: int
    threshold_min: float


class RouteBandCounts(BaseModel):
    """One count per band the routes-to-check list shows, worst first."""

    severe: RouteBandCount
    moderate: RouteBandCount
    mild: RouteBandCount


class TopDelayed(BaseModel):
    """Top-5 routes by absolute avg delay, plus a count of routes averaging at
    least ``delayed_threshold_min`` late and one per routes-to-check band, all
    over the same window the headline covers. The counts cover every route the
    list may show, not only the five listed: the ``route_count`` routes
    observed at least ``min_samples`` times in the window."""

    routes: list[TopDelayedRoute]
    delayed_count: int
    delayed_threshold_min: float
    band_counts: RouteBandCounts
    route_count: int
    min_samples: int


class PeakHour(BaseModel):
    """24 hourly buckets, peak hour highlighted."""

    by_hour: list[float | None]
    peak_hour: int
    peak_avg_min: float


class ServiceSplitEntry(BaseModel):
    """Mean delay over the dates of one day-type bucket (平日 / 土曜 / 日祝 / 土日祝).

    ``avg_min`` is None when the bucket has no data in the range; ``days`` and
    ``samples`` count the same rows the mean is taken over. ``insufficient``
    marks a bucket with too few days or samples to compare.
    """

    day_type: DayType
    avg_min: float | None
    days: int
    samples: int
    insufficient: bool


class ServiceSplitDay(BaseModel):
    """One date's mean delay, the day type that date ran as, and the
    observations the mean is over."""

    date: str  # ISO date
    day_type: DayType
    avg_min: float
    samples: int


class OverviewSummary(BaseModel):
    """Magazine payload — 5 modules + sparkline + range echo.

    ``peak_hour_weekday`` / ``peak_hour_weekend`` are the hour profiles on
    calendar weekdays and weekends; ``service_split_daily`` is each day's mean
    with its day type, which the headline's latest-day standing reads.

    ``movers`` is None, like the headline's baseline, when the window before
    the period could not be read: a time-band request whose ClickHouse grain
    passed the client's row cap or its time budget, or whose window before
    reaches before the agency's retention floor (``compute_overview_summary``).
    """

    headline: Headline
    movers: Movers | None
    concentration: Concentration
    top_delayed: TopDelayed
    peak_hour: PeakHour | None
    peak_hour_weekday: PeakHour | None = None
    peak_hour_weekend: PeakHour | None = None
    # Every bucket the agency is split into, in display order: 平日 / 土曜 /
    # 日祝, or 平日 / 土日祝 for a feed with one weekend timetable.
    service_split: list[ServiceSplitEntry]
    service_split_daily: list[ServiceSplitDay] = []
    sparkline_points: list[float]
    period_avg_min: float | None = None
    period_from: str
    period_to: str
    scope_applied: dict[str, bool]


@router.get("/overview/summary", response_model=OverviewSummary)
@limiter.limit(tier_limit)
async def overview_summary(
    request: Request,
    agency_id: int = Depends(get_agency),
    ch: AsyncClient = Depends(get_ch),
    ctx: RangeCtx = Depends(get_range_ctx),
) -> OverviewSummary:
    """Return the 概況 magazine payload for one agency over ``ctx``.

    Takes no request connection: every stage acquires its own from the pool,
    and one held here across that fan-out is what lets enough concurrent
    requests wait on each other forever.
    """
    return await render_overview_summary(agency_id, ctx, pool=request.app.state.pool, ch=ch)


async def render_overview_summary(
    agency_id: int,
    ctx: RangeCtx,
    *,
    conn: asyncpg.Connection | None = None,
    pool: asyncpg.Pool | None = None,
    ch: AsyncClient | None,
) -> OverviewSummary:
    """``GET /overview/summary``'s whole response for ``ctx``, its service
    filter unresolved (the compute resolves it over the wider span it reads).
    With ``pool`` the stages fan out on connections of their own; a caller
    already holding ``conn`` passes that instead, and the stages run on it in
    turn, since fanning out while one connection is held is what lets
    concurrent requests starve each other."""
    payload = await compute_overview_summary(agency_id, ctx, conn, pool=pool, ch=ch)
    return OverviewSummary(**payload, scope_applied=_SUMMARY_SCOPE)
