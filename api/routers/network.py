"""Cross-agency network summary endpoint (not scoped to a single agency)."""

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_ch, get_conn
from api.middleware.ratelimit import limiter, tier_limit
from api.range import RangeCtx, get_range_ctx
from api.scope_applied import scope_applied
from pipeline.reports.comparability import (
    MAX_CI_HALF_WIDTH_PP,
    MAX_CLAMP_PCT,
    MIN_COVERAGE_PCT,
    ComparableReason,
)
from pipeline.reports.definition import DefinitionMeta, resolve_definition_meta
from pipeline.reports.network import compute_network_summary
from pipeline.reports.service_delivered import DeliveredMissing

router = APIRouter(prefix="/api/network", tags=["network"])

# The board compares whole agencies over a date range only.
_NETWORK_SCOPE = scope_applied("from", "to")


class NetworkAgencyRow(BaseModel):
    agency_id: int
    agency_name: str
    avg_delay_min: float | None
    on_time_pct: float | None
    # on_time_pct's 95% interval in percent, counting trips rather than stop
    # events; None without a trip count to count. See
    # pipeline.reports.comparability.on_time_interval.
    ci_low: float | None
    ci_high: float | None
    samples: int
    raw_samples: int
    clamp_count: int
    clamp_pct: float | None
    is_stale: bool
    data_from: str | None
    data_to: str | None
    # See pipeline.reports.service_delivered for the executed/planned
    # definition. executed_trips/service_delivered_pct are None together
    # ("not available") whenever the ratio isn't computable for this agency
    # (its feed doesn't populate schedule_relationship_trip, or there's no
    # static schedule loaded) -- never a misleading 100%.
    planned_trips: int
    executed_trips: int | None
    service_delivered_pct: float | None
    # How many of the range's window_days a timetable plans, by a saved
    # count or the loaded timetable; the three fields above count only those
    # days.
    covered_days: int
    window_days: int
    # Why service_delivered_pct is None, or None beside a rate; see
    # pipeline.reports.service_delivered.DeliveredMissing.
    delivered_missing: DeliveredMissing | None
    # Trips the feed reported on the days a timetable plans through
    # coverage_through, the trips planned on those days, and the first as a
    # share of the second (not capped at 100). coverage_through is the
    # newest day any agency on the board has observations for in the range,
    # the same for every row, so a day no agency's aggregates have reached
    # yet counts against none of them. coverage_pct is None where coverage
    # cannot be measured: observed_trips and coverage_planned_trips are both
    # None for a feed whose trip_ids aren't the timetable's, or an unreadable
    # plan; coverage_planned_trips is 0, observed_trips None, with no plan.
    observed_trips: int | None
    coverage_planned_trips: int | None
    coverage_through: str | None
    coverage_pct: float | None
    # Whether the board ranks this agency, and otherwise the first check it
    # fails; see pipeline.reports.comparability.
    comparable: bool
    comparable_reason: ComparableReason | None
    # pipeline.reports.ridership: has_ridership_weights is False (and
    # weighted_on_time_pct None) whenever this agency has no ridership_weights
    # rows at all -- the frontend toggle must key off has_ridership_weights,
    # never treat a None weighted_on_time_pct alone as "not configured" (a
    # configured agency with zero samples in range is also None here).
    has_ridership_weights: bool
    weighted_on_time_pct: float | None
    # See pipeline.reports.supply for the executed-vs-planned vehicle-km
    # definition. static_version_id/planned_trip_count describe the
    # CURRENTLY loaded static-feed version's schedule definition (not a
    # date-range total). planned_vehicle_km and vehicle_km_delivered_pct are
    # None together whenever vehicle-km isn't computable (no shapes.txt) or
    # the executed/planned trip ratio isn't available for this agency —
    # never a misleading 100%; delivered_missing says why the ratio is absent.
    static_version_id: str | None
    planned_trip_count: int | None
    planned_vehicle_km: float | None
    vehicle_km_delivered_pct: float | None


class ComparabilityThresholds(BaseModel):
    """The gate's thresholds, served so the board's reasons quote the same
    figures the server applied. Each is inclusive: a figure at it passes."""

    min_coverage_pct: float
    max_ci_half_width_pp: float
    max_clamp_pct: float


class NetworkSummary(BaseModel):
    from_: str = Field(serialization_alias="from")
    to: str
    # Comparable agencies first, worst avg_delay_min first; then the rest by
    # agency name.
    agencies: list[NetworkAgencyRow]
    comparability: ComparabilityThresholds
    # This board's on_time_pct always reads agg_route_daily_dist's exact
    # on_time_count column -- there is no tolerance query param here (unlike
    # GET /api/{agency_id}/reports/on_time) -- so the definition is always
    # the legacy_60s preset. Surfaced anyway (rather than assumed) so a user
    # comparing this board against a per-agency report's on_time export can
    # see both are using the same definition. See pipeline.reports.definition.
    definition: DefinitionMeta
    scope_applied: dict[str, bool]


@router.get("/summary", response_model=NetworkSummary)
@limiter.limit(tier_limit)
async def network_summary(
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
    ctx: RangeCtx = Depends(get_range_ctx),
) -> NetworkSummary:
    """Per-agency network health board over [from, to]: the agencies the data
    can compare, ranked worst-avg-delay first, then the rest by name.

    Honors the date range only; service/time_band/dow/routes are not applied
    (whole-agency comparison). Read-only.
    """
    rows = await compute_network_summary(conn, ch, ctx.from_date, ctx.to_date)
    return NetworkSummary(
        from_=ctx.from_date.isoformat(),
        to=ctx.to_date.isoformat(),
        agencies=[NetworkAgencyRow.model_validate(r) for r in rows],
        comparability=ComparabilityThresholds(
            min_coverage_pct=MIN_COVERAGE_PCT,
            max_ci_half_width_pp=MAX_CI_HALF_WIDTH_PP,
            max_clamp_pct=MAX_CLAMP_PCT,
        ),
        definition=resolve_definition_meta("on_time", None, None),
        scope_applied=_NETWORK_SCOPE,
    )
