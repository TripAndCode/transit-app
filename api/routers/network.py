"""Cross-agency network summary endpoint (not scoped to a single agency)."""

from datetime import date

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_ch, get_conn
from api.middleware.ratelimit import limiter, tier_limit
from api.range import RangeCtx, get_range_ctx
from api.scope_applied import scope_applied
from pipeline.reports.comparability import (
    BOARD_ON_TIME_DEFINITION,
    MAX_CI_HALF_WIDTH_PP,
    MAX_CLAMP_PCT,
    MIN_COVERAGE_PCT,
    ComparableReason,
)
from pipeline.reports.definition import DefinitionMeta, stamp_build
from pipeline.reports.network import compute_network_summary, coverage_through
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
    # pipeline.reports.comparability.assess.
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
    # observed_trips of coverage_planned_trips over the planned days through
    # NetworkSummary.coverage_through, uncapped; see
    # pipeline.reports.comparability.assess and docs/features/network-tab.md.
    observed_trips: int | None
    coverage_planned_trips: int | None
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


COMPARABILITY_THRESHOLDS = ComparabilityThresholds(
    min_coverage_pct=MIN_COVERAGE_PCT,
    max_ci_half_width_pp=MAX_CI_HALF_WIDTH_PP,
    max_clamp_pct=MAX_CLAMP_PCT,
)


class NetworkSummary(BaseModel):
    from_: str = Field(serialization_alias="from")
    to: str
    # Comparable agencies first, worst avg_delay_min first; then the rest in
    # agency_id order, which the board re-sorts by name in its own collation.
    agencies: list[NetworkAgencyRow]
    comparability: ComparabilityThresholds
    # The newest day any agency has observations for in the range, where every
    # row's observation coverage stops; None when no agency has any.
    coverage_through: str | None
    # Always the legacy_60s preset, since this board takes no tolerance, and
    # surfaced anyway so it can be checked against a report's own definition.
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
    can compare, ranked worst-avg-delay first, then the rest in agency_id
    order. The client orders those by name in its own collation, so the
    server sorts nothing it would only re-sort.

    Honors the date range only; service/time_band/dow/routes are not applied
    (whole-agency comparison). Read-only.
    """
    definition = await stamp_build(conn, BOARD_ON_TIME_DEFINITION, None)
    rows = await compute_network_summary(conn, ch, ctx.from_date, ctx.to_date)
    # The same rule compute_network_summary bounded each row's coverage by,
    # over the same per-agency last days.
    through = coverage_through(date.fromisoformat(r["data_to"]) for r in rows if r["data_to"])
    return NetworkSummary(
        from_=ctx.from_date.isoformat(),
        to=ctx.to_date.isoformat(),
        agencies=[NetworkAgencyRow.model_validate(r) for r in rows],
        comparability=COMPARABILITY_THRESHOLDS,
        coverage_through=through.isoformat() if through else None,
        definition=definition,
        scope_applied=_NETWORK_SCOPE,
    )
