"""``GET /api/{agency_id}/scope/summary``: the data the scope controls draw.

See :mod:`pipeline.reports.scope_summary` for what each section covers and
which condition it deliberately ignores.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.deps import get_agency, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, ctx_payload, get_range_ctx
from api.scope_applied import scope_applied
from pipeline.reports.scope_summary import compute_scope_summary

router = APIRouter(prefix="/api/{agency_id}", tags=["scope"])

# The aggregate holds (date, route, service) rows, so the hour of day, the
# stop and the direction are not representable. The tolerance curve answers
# `late` rather than being filtered by it; `early` bounds the curve.
_SCOPE_SUMMARY_SCOPE = scope_applied("from", "to", "dow", "service", "routes", "early")


class ScopeDay(BaseModel):
    date: date
    avg_min: float
    samples: int


class ScopeWeekday(BaseModel):
    dow: str
    avg_min: float
    samples: int


class ScopeRoute(BaseModel):
    route_code: str
    avg_min: float
    samples: int


class ScopeTolerance(BaseModel):
    late_sec: int
    on_time_pct: float


class ScopeSummaryResponse(BaseModel):
    earliest: date | None
    latest: date | None
    days: list[ScopeDay]
    weekdays: list[ScopeWeekday]
    routes: list[ScopeRoute]
    tolerance: list[ScopeTolerance]
    ctx: dict[str, Any]
    scope_applied: dict[str, bool]


@router.get("/scope/summary", response_model=ScopeSummaryResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def scope_summary(
    request: Request,
    agency_id: int = Depends(get_agency),
    ctx: RangeCtx = Depends(get_range_ctx),
    early: int | None = Query(None, ge=0, le=3600, description="Early on-time tolerance in seconds."),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    body = await compute_scope_summary(agency_id, ctx, conn, early_sec=early)
    return {**body, "ctx": ctx_payload(ctx), "scope_applied": _SCOPE_SUMMARY_SCOPE}
