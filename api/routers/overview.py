"""概況 (Overview) tab endpoint.

Returns the full magazine payload in a single locale-aware round-trip.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
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
    streak_weeks: int
    sparkline_points: list[float]


class Movers(BaseModel):
    """Top-3 worsening and top-3 improving routes."""

    worse: list[Mover]
    better: list[Mover]


class ConcentrationTopRoute(BaseModel):
    """One route's share of total agency delay."""

    route_code: str
    route_short_name: str | None
    share_pct: float


class Concentration(BaseModel):
    """Top-3 routes plus aggregate "rest" share."""

    top_routes: list[ConcentrationTopRoute]
    rest_share_pct: float


class PeakHour(BaseModel):
    """24 hourly buckets, peak hour highlighted."""

    by_hour: list[float | None]
    peak_hour: int
    peak_avg_min: float


class OverviewSummary(BaseModel):
    """Magazine payload — 5 modules + sparkline + range echo."""

    headline: Headline
    movers: Movers
    concentration: Concentration
    peak_hour: PeakHour | None
    service_split: dict[str, float]
    sparkline_points: list[float]


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
    payload = await compute_overview_summary(agency_id, ctx, conn, locale)
    return OverviewSummary(**payload)
