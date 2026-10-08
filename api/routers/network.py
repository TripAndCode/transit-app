"""Cross-agency network summary endpoint (not scoped to a single agency)."""

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_ch, get_conn
from api.middleware.ratelimit import limiter, tier_limit
from api.range import RangeCtx, get_range_ctx
from api.scope_applied import scope_applied
from pipeline.reports.definition import DefinitionMeta, resolve_definition_meta
from pipeline.reports.network import compute_network_summary

router = APIRouter(prefix="/api/network", tags=["network"])

# The board compares whole agencies over a date range only.
_NETWORK_SCOPE = scope_applied("from", "to")


class NetworkAgencyRow(BaseModel):
    agency_id: int
    agency_name: str
    avg_delay_min: float | None
    on_time_pct: float | None
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
    # never a misleading 100%; the UI falls back to planned_trip_count alone.
    static_version_id: str | None
    planned_trip_count: int | None
    planned_vehicle_km: float | None
    vehicle_km_delivered_pct: float | None


class NetworkSummary(BaseModel):
    from_: str = Field(serialization_alias="from")
    to: str
    agencies: list[NetworkAgencyRow]
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
    """Per-agency network health board over [from, to], ranked worst-avg-delay first.

    Honors the date range only; service/time_band/dow/routes are not applied
    (whole-agency comparison). Read-only.
    """
    rows = await compute_network_summary(conn, ch, ctx.from_date, ctx.to_date)
    return NetworkSummary(
        from_=ctx.from_date.isoformat(),
        to=ctx.to_date.isoformat(),
        agencies=[NetworkAgencyRow.model_validate(r) for r in rows],
        definition=resolve_definition_meta("on_time", None, None),
        scope_applied=_NETWORK_SCOPE,
    )
