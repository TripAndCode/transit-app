"""Frozen snapshots: what one screen rendered for one scope, stored so a link
reproduces those exact figures and their method stamp after any later
re-analyze.

Admins create, list and delete snapshots under ``/api/admin/snapshots``.
Anyone holding a snapshot's id reads it at ``GET /api/snapshots/{id}``, and
today's figures for its stored query at ``GET /api/snapshots/{id}/today``,
both of which the sign-in gate leaves public (``api.middleware.login_gate``):
council material reaches readers who have no account. The id is the
capability, 128 random bits, and nothing lists ids to a non-admin.

The figures are never the client's. A create names the screen and the query
that screen's own endpoint was asked, and the payload is computed by that
endpoint's code (``api.routers.reports.render_report``), so a snapshot holds
exactly what the live screen served at that moment. The server writes the
council report's prose in the reader's language, so every locale's response is
stored, and the route catalogue the labels are drawn from is stored beside
them. Today's figures come from the same code, asked the stored resolved
scope and options, so a difference between the two is the aggregates' or the
method's, never the query's.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from typing import Any, Literal

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from api.admin_audit import record_admin_action
from api.deps import get_ch, get_conn, get_locale
from api.middleware.locale import SUPPORTED_LOCALES
from api.middleware.ratelimit import SNAPSHOT_TODAY_LIMIT, limiter, tier_limit
from api.range import RangeCtx, clamp_range_ctx, ctx_payload, resolve_service_dates
from api.routers.reports import ReportResponse, render_report, scope_early
from api.routers.static import fetch_static_routes
from api.security import User, csrf_guard, require_admin
from pipeline.data_sources import credit_line
from pipeline.reports import ROUTE_TABLE_SORTS
from pipeline.reports.rankings import EARLY_ANY

admin_router = APIRouter(prefix="/api/admin/snapshots", tags=["admin"])
router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])

#: The serialized payloads a snapshot may hold. Far above any screen's
#: response (a Routes table at its longest, in every locale, with the agency's
#: route catalogue), so reaching it means a request the screens never make.
MAX_PAYLOAD_BYTES = 1024 * 1024

#: The report each screen's own endpoint serves.
_REPORT_TYPES = {"council_report": "council_summary", "route_table": "route_table"}

# Matches the table's CHECK. An id outside it cannot exist, so it is refused
# before the lookup, with the same answer an unknown id gets.
_ID = re.compile(r"[A-Za-z0-9_-]{22,64}")
_NOT_FOUND = "snapshot not found"

_SNAPSHOT_COLUMNS = "screen, agency_id, agency_name, params, method_version, title, note"

# The query's parameters beyond the scope, which the stored params keep beside
# the resolved scope.
_SCREEN_OPTIONS = {
    "late",
    "early",
    "preset",
    "sort",
    "order",
    "include_sparse",
    "include_retired",
    "include_zero_delay",
    "limit",
}


class SnapshotQuery(BaseModel):
    """The query string the screen's endpoint was asked
    (``GET /api/{agency_id}/reports/{report_type}``), by its parameter names and
    within its bounds. Values may arrive as the query string's text."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    dow: str = "all"
    time_band: str = "all"
    service: str = "all"
    routes: str | None = None
    hour: str | None = None
    stop: str | None = None
    dir_: str | None = Field(default=None, alias="dir")
    late: int | None = Field(default=None, ge=0, le=3600)
    early: str | None = Field(default=None, pattern=f"^({EARLY_ANY}|[0-9]{{1,4}})$")
    preset: str | None = None
    sort: str | None = Field(default=None, pattern=f"^({'|'.join(ROUTE_TABLE_SORTS)})$")
    order: Literal["worst", "best"] | None = None
    include_sparse: bool = False
    include_retired: bool = False
    include_zero_delay: bool = False
    limit: int | None = Field(default=None, ge=1, le=500)


class SnapshotCreate(BaseModel):
    """A create request: which screen, for which agency and query. It carries
    no figures; any field beyond these is refused."""

    model_config = ConfigDict(extra="forbid")

    screen: Literal["council_report", "route_table"]
    agency_id: int
    query: SnapshotQuery = Field(default_factory=SnapshotQuery)
    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "note")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


class SnapshotCreated(BaseModel):
    snapshot_id: str
    # The SPA page that renders it, relative to the deployment's origin.
    path: str
    created_at: datetime
    screen: str
    agency_id: int
    method_version: str


class SnapshotSummary(BaseModel):
    """One row of the admin list: everything but the payloads."""

    snapshot_id: str
    created_at: datetime
    created_by: int | None
    created_by_email: str | None
    screen: str
    agency_id: int
    agency_name: str
    params: dict[str, Any]
    method_version: str
    title: str | None
    note: str | None


class SnapshotToday(BaseModel):
    """Today's figures for a snapshot's stored query: the screen endpoint's
    response as it would serve that query now, in the reader's language.
    ``report`` is None beside an ``unavailable`` reason when no such response
    exists: the agency is no longer listed (``agency_removed``), or today's
    code refuses the stored query or has no computation for the screen
    (``not_computable``)."""

    report: ReportResponse | None
    unavailable: Literal["agency_removed", "not_computable"] | None = None


class SnapshotOut(BaseModel):
    """A snapshot as anyone holding its id reads it. Who created it is left
    out: the reader needs the figures and their stamp, not the operator.

    ``payloads`` holds ``report`` (the screen endpoint's response per locale,
    each with its ``definition``), ``routes`` (the agency's route catalogue,
    ``GET /api/{agency_id}/routes``'s rows) and ``credit`` (the open-data
    credit per locale, None where no source covers the feed)."""

    snapshot_id: str
    created_at: datetime
    screen: str
    agency_id: int
    agency_name: str
    params: dict[str, Any]
    payloads: dict[str, Any]
    method_version: str
    title: str | None
    note: str | None


#: The response fields that legitimately differ between the locales'
#: renders: the server's prose and the moment each was served.
_LOCALE_VARYING = frozenset({"text", "rendered_at"})


def _figures(report: dict[str, Any]) -> dict[str, Any]:
    """The whole response except :data:`_LOCALE_VARYING`, which must match
    across locales: anything else that differs means the aggregates moved
    between the two computations."""
    return {k: v for k, v in report.items() if k not in _LOCALE_VARYING}


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _render(
    report_type: str,
    options: SnapshotQuery,
    *,
    agency_id: int,
    ctx: RangeCtx,
    conn: asyncpg.Connection,
    ch: AsyncClient,
    locale: str,
) -> ReportResponse:
    """The screen endpoint's response for ``ctx`` under the screen's
    ``options``: the one computation a snapshot's figures and today's both
    come from, so the two differ only where the data or the method moved."""
    rendered = await render_report(
        report_type,
        agency_id=agency_id,
        ctx=ctx,
        conn=conn,
        ch=ch,
        locale=locale,
        limit=options.limit,
        preset=options.preset,
        late=options.late,
        early=scope_early(options.early),
        include_sparse=options.include_sparse,
        include_retired=options.include_retired,
        include_zero_delay=options.include_zero_delay,
        order=options.order,
        sort=options.sort,
    )
    assert isinstance(rendered, ReportResponse)  # no format=csv, so never the CSV stream
    return rendered


async def _snapshot_row(conn: asyncpg.Connection, snapshot_id: str, columns: str) -> asyncpg.Record:
    """One snapshot's ``columns``, or the one 404 an id that is malformed,
    unknown or deleted gets alike, from a single primary-key lookup."""
    if not _ID.fullmatch(snapshot_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    row = await conn.fetchrow(f"SELECT {columns} FROM snapshots WHERE snapshot_id = $1", snapshot_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return row


@admin_router.post("", response_model=SnapshotCreated, status_code=201)
async def create_snapshot(
    body: SnapshotCreate,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> SnapshotCreated:
    """Compute the screen's response for ``body``'s scope and store it.

    A 404 for an unknown or removed agency, the screen endpoint's own 4xx for
    a query it would refuse, 413 when the payloads exceed
    :data:`MAX_PAYLOAD_BYTES`, and 409 when the aggregates changed between the
    locales' computations, which would freeze two different sets of figures."""
    csrf_guard(request)
    agency = await conn.fetchrow(
        "SELECT agency_name, feed_url FROM agencies WHERE agency_id = $1 AND deleted_at IS NULL", body.agency_id
    )
    if agency is None:
        raise HTTPException(status_code=404, detail=f"Agency {body.agency_id} not found")

    q = body.query
    ctx = clamp_range_ctx(
        from_=q.from_,
        to=q.to,
        dow=q.dow,
        time_band=q.time_band,
        service=q.service,
        routes=q.routes.split(",") if q.routes else (),
        hour=q.hour,
        stop=q.stop,
        direction=q.dir_,
    )
    if ctx.service != "all":
        ctx = await resolve_service_dates(ctx, conn, body.agency_id)
    report_type = _REPORT_TYPES[body.screen]

    reports: dict[str, dict[str, Any]] = {}
    for locale in SUPPORTED_LOCALES:
        rendered = await _render(report_type, q, agency_id=body.agency_id, ctx=ctx, conn=conn, ch=ch, locale=locale)
        reports[locale] = rendered.model_dump(mode="json", by_alias=True)
    first, *others = reports.values()
    if any(_figures(other) != _figures(first) for other in others):
        raise HTTPException(status_code=409, detail="the aggregates changed while the snapshot was taken; try again")

    payloads = {
        "report": reports,
        "routes": [r.model_dump(mode="json") for r in await fetch_static_routes(conn, body.agency_id)],
        "credit": {locale: credit_line(agency["feed_url"], locale) for locale in SUPPORTED_LOCALES},
    }
    encoded = json.dumps(payloads, ensure_ascii=False)
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"snapshot payload exceeds {MAX_PAYLOAD_BYTES} bytes")

    params = {"report_type": report_type, **ctx_payload(ctx), **q.model_dump(include=_SCREEN_OPTIONS)}
    method_version = first["definition"]["method_version"]
    snapshot_id = secrets.token_urlsafe(16)
    async with conn.transaction():
        created_at = await conn.fetchval(
            f"INSERT INTO snapshots (snapshot_id, created_by, {_SNAPSHOT_COLUMNS}, payloads) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::jsonb) RETURNING created_at",
            snapshot_id,
            admin.user_id,
            body.screen,
            body.agency_id,
            agency["agency_name"],
            json.dumps(params),
            method_version,
            body.title,
            body.note,
            encoded,
        )
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="snapshot.create",
            target_type="snapshot",
            target_id=snapshot_id,
            after={
                "screen": body.screen,
                "agency_id": body.agency_id,
                "params": params,
                "method_version": method_version,
                "title": body.title,
                "note": body.note,
            },
            ip=_client_ip(request),
        )
    return SnapshotCreated(
        snapshot_id=snapshot_id,
        path=f"/s/{snapshot_id}",
        created_at=created_at,
        screen=body.screen,
        agency_id=body.agency_id,
        method_version=method_version,
    )


@admin_router.get("", response_model=list[SnapshotSummary])
async def list_snapshots(
    limit: int = Query(default=100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[SnapshotSummary]:
    """The newest snapshots first, without their payloads."""
    rows = await conn.fetch(
        "SELECT s.snapshot_id, s.created_at, s.created_by, u.email AS created_by_email, s.screen, s.agency_id, "
        "  s.agency_name, s.params, s.method_version, s.title, s.note "
        "FROM snapshots s LEFT JOIN users u ON u.user_id = s.created_by "
        "ORDER BY s.created_at DESC, s.snapshot_id LIMIT $1",
        limit,
    )
    return [SnapshotSummary(**{**dict(r), "params": json.loads(r["params"])}) for r in rows]


@admin_router.delete("/{snapshot_id}", status_code=204)
async def delete_snapshot(
    snapshot_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> Response:
    """Remove a snapshot for good: its link then answers as an unknown id."""
    csrf_guard(request)
    async with conn.transaction():
        row = await conn.fetchrow(
            f"DELETE FROM snapshots WHERE snapshot_id = $1 RETURNING {_SNAPSHOT_COLUMNS}, created_at, created_by",
            snapshot_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="snapshot.delete",
            target_type="snapshot",
            target_id=snapshot_id,
            before={**dict(row), "params": json.loads(row["params"])},
            ip=_client_ip(request),
        )
    return Response(status_code=204)


@router.get("/{snapshot_id}", response_model=SnapshotOut)
@limiter.limit(tier_limit)
async def read_snapshot(
    request: Request,
    snapshot_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
) -> SnapshotOut:
    """One snapshot by its id."""
    row = await _snapshot_row(conn, snapshot_id, f"snapshot_id, created_at, {_SNAPSHOT_COLUMNS}, payloads")
    return SnapshotOut(**{**dict(row), "params": json.loads(row["params"]), "payloads": json.loads(row["payloads"])})


@router.get("/{snapshot_id}/today", response_model=SnapshotToday)
@limiter.limit(SNAPSHOT_TODAY_LIMIT)
async def read_snapshot_today(
    request: Request,
    snapshot_id: str,
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
    locale: str = Depends(get_locale),
) -> SnapshotToday:
    """Today's figures for a snapshot's stored query: its resolved dates,
    scope and options replayed through the screen endpoint's own computation,
    in the reader's language, for the page to mark what moved since.

    The same 404 as the snapshot read for an id that is malformed, unknown or
    deleted. A stored query is replayed long after it was accepted, so one the
    current code refuses yields ``not_computable`` rather than the refusal."""
    row = await _snapshot_row(conn, snapshot_id, "screen, agency_id, params")
    agency_id = row["agency_id"]
    listed = await conn.fetchval("SELECT 1 FROM agencies WHERE agency_id = $1 AND deleted_at IS NULL", agency_id)
    if listed is None:
        return SnapshotToday(report=None, unavailable="agency_removed")
    report_type = _REPORT_TYPES.get(row["screen"])
    if report_type is None:
        return SnapshotToday(report=None, unavailable="not_computable")
    params = json.loads(row["params"])
    try:
        options = SnapshotQuery.model_validate(
            {key: params[key] for key in _SCREEN_OPTIONS if params.get(key) is not None}
        )
    except ValidationError:
        return SnapshotToday(report=None, unavailable="not_computable")
    try:
        ctx = await resolve_service_dates(_stored_ctx(params), conn, agency_id)
        report = await _render(report_type, options, agency_id=agency_id, ctx=ctx, conn=conn, ch=ch, locale=locale)
    except HTTPException as refused:
        if refused.status_code >= 500:
            raise
        return SnapshotToday(report=None, unavailable="not_computable")
    return SnapshotToday(report=report)


def _stored_ctx(params: dict[str, Any]) -> RangeCtx:
    """The scope a snapshot stored (:func:`api.range.ctx_payload`'s keys),
    its dates explicit, read back through the same validation a request's
    scope gets."""
    return clamp_range_ctx(
        from_=params.get("from"),
        to=params.get("to"),
        dow=params.get("dow", "all"),
        time_band=params.get("time_band", "all"),
        service=params.get("service", "all"),
        routes=params.get("routes") or (),
        hour=params.get("hour"),
        stop=params.get("stop"),
        direction=params.get("dir"),
    )
