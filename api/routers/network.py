"""Cross-agency network summary endpoint (not scoped to a single agency)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_ch, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, get_range_ctx
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
    )
