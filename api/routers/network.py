"""Cross-agency network summary endpoint (not scoped to a single agency)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_ch, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, get_range_ctx
from pipeline.reports.definition import DefinitionMeta, resolve_definition_meta
from pipeline.reports.network import compute_network_summary

router = APIRouter(prefix="/api/network", tags=["network"])


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


@router.get("/summary", response_model=NetworkSummary)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def network_summary(
    request: Request,
    conn=Depends(get_conn),
    ch=Depends(get_ch),
    ctx: RangeCtx = Depends(get_range_ctx),
):
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
    )
