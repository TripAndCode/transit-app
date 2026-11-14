"""Admin Ask-ops endpoints: query log, route funnel, and the latest local
weekly eval result.

``ask_query_log`` has no identity column (deliberately — see
``db/migrations/0013_ask_query_log.up.sql``) and no ``latency_ms`` or
``provider`` column (``pipeline/query/query_log.py``'s INSERT list is the
full column set). This router therefore never returns a "user" field, and
the funnel's ``providers`` field is always null rather than fabricated —
see ``pipeline.query.ask_ops`` for the route/status derivation this relies
on instead of matching columns that don't exist.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from api.deps import get_conn
from api.range import DEFAULT_RANGE_DAYS, jst_today
from api.security import User, require_admin
from pipeline.query.ask_ops import (
    build_funnel,
    derive_route,
    derive_status,
    find_latest_eval_result,
    route_to_stage,
    status_to_success,
)

router = APIRouter(prefix="/api/admin/ask", tags=["admin"])

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


class AskQueryLogRow(BaseModel):
    """One ask_query_log row for the admin table.

    ``route`` and ``status`` are derived (see module docstring); ``tool``,
    ``cache_outcome`` and ``numeric_guard_triggered`` are nullable exactly
    as they are in the DB.
    """

    id: int
    agency_id: int
    agency_name: str
    question: str
    route: str
    tool: str | None
    status: str
    cache_outcome: str | None
    numeric_guard_triggered: bool | None
    created_at: datetime


class AskQueryLogPage(BaseModel):
    rows: list[AskQueryLogRow]
    next_cursor: str | None


def _date_filter_sql(args: list[Any], column: str, from_date: date | None, to_date: date | None) -> list[str]:
    # Both bounds are cast to dates server-side, so each becomes midnight in
    # the session's Asia/Tokyo. A date bound straight to a timestamptz
    # parameter would be midnight in the API process's own zone instead.
    where: list[str] = []
    if from_date is not None:
        args.append(from_date)
        where.append(f"{column} >= ${len(args)}::date")
    if to_date is not None:
        args.append(to_date)
        where.append(f"{column} < ${len(args)}::date + INTERVAL '1 day'")
    return where


@router.get("/queries", response_model=AskQueryLogPage)
async def list_ask_queries(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    route: str | None = Query(default=None, description="rules | nn | rag | no_history"),
    status: str | None = Query(default=None, description="ok | error"),
    agency_id: int | None = Query(default=None),
    cursor: str | None = Query(default=None, description="opaque; pass back the previous page's next_cursor"),
    limit: int = Query(default=_DEFAULT_LIMIT),
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> AskQueryLogPage:
    """List ask_query_log rows newest-first, with route/status/agency/date
    filters and id-keyset pagination (stable under concurrent inserts,
    unlike offset paging).

    `question` is the caller's raw, unredacted Ask input. It is
    Internal-classified and retained 90 days by `prune-query-log`."""
    limit = max(1, min(_MAX_LIMIT, limit))

    where: list[str] = []
    args: list[Any] = []

    if route is not None:
        stage = route_to_stage(route)
        if stage is None:
            raise HTTPException(400, f"unknown route {route!r}")
        args.append(stage)
        where.append(f"l.router_stage = ${len(args)}")
    if status is not None:
        success = status_to_success(status)
        if success is None:
            raise HTTPException(400, f"unknown status {status!r}")
        args.append(success)
        where.append(f"l.success = ${len(args)}")
    if agency_id is not None:
        args.append(agency_id)
        where.append(f"l.agency_id = ${len(args)}")
    where += _date_filter_sql(args, "l.created_at", from_date, to_date)
    if cursor is not None:
        try:
            cursor_id = int(cursor)
        except ValueError:
            raise HTTPException(400, "invalid cursor") from None
        args.append(cursor_id)
        where.append(f"l.id < ${len(args)}")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    args.append(limit + 1)
    rows = await conn.fetch(
        f"""
        SELECT l.id, l.agency_id, a.agency_name, l.question, l.router_stage, l.tool,
               l.success, l.cache_outcome, l.numeric_guard_triggered,
               l.created_at
        FROM ask_query_log l
        JOIN agencies a ON a.agency_id = l.agency_id
        {where_sql}
        ORDER BY l.id DESC
        LIMIT ${len(args)}
        """,
        *args,
    )

    page = rows[:limit]
    next_cursor = str(page[-1]["id"]) if len(rows) > limit else None
    out_rows = [
        AskQueryLogRow(
            id=r["id"],
            agency_id=r["agency_id"],
            agency_name=r["agency_name"],
            question=r["question"],
            route=derive_route(r["router_stage"]),
            tool=r["tool"],
            status=derive_status(r["success"]),
            cache_outcome=r["cache_outcome"],
            numeric_guard_triggered=r["numeric_guard_triggered"],
            created_at=r["created_at"],
        )
        for r in page
    ]
    return AskQueryLogPage(rows=out_rows, next_cursor=next_cursor)


class AskFunnelRoute(BaseModel):
    route: str
    count: int
    success_count: int


class AskFunnelOut(BaseModel):
    by_route: list[AskFunnelRoute]
    total: int
    # Always null: no per-query LLM provider is persisted to ask_query_log
    # today (see pipeline.query.ask_ops.AskFunnel). Surfaced explicitly so
    # the UI shows "not tracked" instead of a fabricated or silently-missing
    # breakdown.
    providers: None = None


@router.get("/funnel", response_model=AskFunnelOut)
async def ask_funnel(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    agency_id: int | None = Query(default=None),
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> AskFunnelOut:
    """Route funnel (rules -> nn -> rag, plus the no_history early exit)
    with per-route success counts for the given window.

    Defaults to the trailing `DEFAULT_RANGE_DAYS` JST days when both `from`
    and `to` are omitted, the same default every other admin/report window
    uses (see api.range.clamp_range_ctx) -- an unbounded funnel only gets
    more expensive as ask_query_log grows, and its 90-day retention
    (gtfs_pipeline.py prune_query_log) never claims a query fast enough on
    its own to keep it cheap. Passing either boundary opts out of the
    default entirely; the query then runs open-ended on the other side, same
    as before.
    """
    if from_date is None and to_date is None:
        to_date = jst_today()
        from_date = to_date - timedelta(days=DEFAULT_RANGE_DAYS - 1)

    where: list[str] = []
    args: list[Any] = []
    if agency_id is not None:
        args.append(agency_id)
        where.append(f"agency_id = ${len(args)}")
    where += _date_filter_sql(args, "created_at", from_date, to_date)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = await conn.fetch(
        f"SELECT router_stage, success, count(*) AS n FROM ask_query_log {where_sql} GROUP BY router_stage, success",
        *args,
    )
    funnel = build_funnel([(r["router_stage"], r["success"], r["n"]) for r in rows])
    return AskFunnelOut(
        by_route=[AskFunnelRoute(route=r.route, count=r.count, success_count=r.success_count) for r in funnel.by_route],
        total=funnel.total,
    )


class AskEvalResult(BaseModel):
    """Latest local ask-eval artifact contents.

    ``extra="allow"``: no producer writes this file yet (see
    ``find_latest_eval_result``'s docstring), so its eventual shape isn't
    fixed. ``score``/``generated_at`` are named because they're the obvious
    fields a golden-set eval run would report; anything else round-trips
    through unvalidated.
    """

    model_config = ConfigDict(extra="allow")

    generated_at: str | None = None
    score: float | None = None


# ── Weekly eval result ────────────────────────────────────────────────────
#
# .cache/ is already gitignored (see .gitignore's ops-status-collector
# entry) for exactly this kind of local-only, non-repo runtime artifact.

_EVAL_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"


@router.get("/eval", response_model=AskEvalResult | None)
async def ask_eval_latest(_admin: User = Depends(require_admin)) -> AskEvalResult | None:
    """Latest local ask-eval artifact under .cache/ask-eval-*.json, or null.

    Nothing currently writes this file (see
    pipeline.query.ask_ops.find_latest_eval_result's docstring) — this
    always returns null today, and the admin page renders that as "not run
    yet" rather than a fabricated result.
    """
    data = find_latest_eval_result(_EVAL_CACHE_DIR)
    return None if data is None else AskEvalResult.model_validate(data)
