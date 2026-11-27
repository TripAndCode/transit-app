"""A figure's trailing reference: the same figure over the ``REFERENCE_DAYS``
days ending the day before the period starts, under the period's scope, as the
agency's usual level, and the rules under which that window may stand as one.

Pulse's headline (:func:`pipeline.reports.overview._reference`) and each
route's figures on the Routes table and the route page
(:func:`pipeline.reports.route_table.compute_route_references`) read their
references through these rules, so a reference is withheld for the same
reasons wherever it is shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from math import ceil
from typing import Literal

from api.range import RangeCtx, dow_isodays, selected_service_dates
from pipeline.reports.rankings import RANKING_MIN_SAMPLES

#: How many days a reference covers: the agency's own trailing window, ending
#: the day before the period starts (:func:`reference_ctx`).
REFERENCE_DAYS = 90

#: The share of the days the scope selects in the reference window that the
#: agency's feed must hold data on. Under it the window shows little of the
#: agency's usual level (a new agency, or a long outage), so the figure is
#: withheld as ``insufficient_days`` rather than printed as that level.
REFERENCE_MIN_COVERAGE = Fraction(4, 5)

#: The fewest observations behind a reference figure: the Routes ranking's
#: floor, under which a route's figure is marked as thinly observed.
REFERENCE_MIN_SAMPLES = RANKING_MIN_SAMPLES

#: Why a reference has no figure. ``not_aggregated``: no aggregate answers the
#: scope by date, under a time band the figure's aggregate cannot split, or
#: under an on-time window with a bound in the delay histogram's open end
#: buckets, so the figure would need a live scan of the whole window.
ReferenceReason = Literal["no_data", "insufficient_days", "insufficient_samples", "not_aggregated"]


def reference_ctx(ctx: RangeCtx) -> RangeCtx:
    """The ``REFERENCE_DAYS`` days ending the day before ``ctx`` starts, under
    ``ctx``'s scope, its service filter not yet resolved: a reference resolves
    it over this window alone, so each date is filed under the timetable the
    agency's calendar ran in the window."""
    to_date = ctx.from_date - timedelta(days=1)
    return RangeCtx(
        from_date=to_date - timedelta(days=REFERENCE_DAYS - 1),
        to_date=to_date,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )


def selected_days(ctx: RangeCtx) -> list[date]:
    """The days of ``ctx``'s window its day-of-week and service filters keep."""
    isodays = dow_isodays(ctx.dow)
    service_dates = selected_service_dates(ctx)
    kept = None if service_dates is None else set(service_dates)
    days = (ctx.from_date + timedelta(days=i) for i in range(ctx.days))
    return [d for d in days if (isodays is None or d.isoweekday() in isodays) and (kept is None or d in kept)]


@dataclass(frozen=True)
class Coverage:
    """How many of the window's days the scope selects, how many of those the
    agency's feed holds data on, and the fewest it needs."""

    days_selected: int = 0
    days_collected: int = 0
    min_days: int = 0


async def reference_coverage(agency_id: int, ctx: RangeCtx, conn) -> tuple[list[date], Coverage]:
    """The days ``ctx`` selects and the window's :class:`Coverage`, ``ctx``'s
    service filter resolved over its own window.

    Coverage is judged by the days the agency's feed was collected on, not the
    days the scope's routes ran, so a route that runs on fewer days than the
    agency is still judged by whether the feed was there to see it.
    """
    selected = selected_days(ctx)
    collected = 0
    if selected:
        row = await conn.fetchrow(
            "SELECT COUNT(DISTINCT date) AS n FROM agg_daily_trend WHERE agency_id = $1 AND date = ANY($2::date[])",
            agency_id,
            selected,
        )
        collected = int(row["n"])
    return selected, Coverage(len(selected), collected, ceil(REFERENCE_MIN_COVERAGE * len(selected)))


def reference_reason(samples: int, coverage: Coverage) -> ReferenceReason | None:
    """Why a figure over ``samples`` observations cannot stand as the usual
    level under ``coverage``, or None when it can."""
    if samples == 0:
        return "no_data"
    if coverage.days_collected < coverage.min_days:
        return "insufficient_days"
    if samples < REFERENCE_MIN_SAMPLES:
        return "insufficient_samples"
    return None


def reference_window(ctx: RangeCtx, coverage: Coverage, reason: ReferenceReason | None, samples: int = 0) -> dict:
    """The fields every reference states beside its figure: its window, what
    the scope selects in it, the floors it needs, and why it has no figure."""
    return {
        "samples": samples,
        "window_from": ctx.from_date.isoformat(),
        "window_to": ctx.to_date.isoformat(),
        "days": ctx.days,
        "days_selected": coverage.days_selected,
        "days_collected": coverage.days_collected,
        "min_days": coverage.min_days,
        "min_samples": REFERENCE_MIN_SAMPLES,
        "reason": reason,
    }
