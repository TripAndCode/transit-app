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

A route's page is not one report but the responses its panels read: the
agency's route lists and overview its standing comes from, the route's
performance targets and its delay by stop, each from its endpoint's own code.
The Routes table's snapshot likewise stores the two panels the screen shows
beside the table, headway quality and the agency's performance standards.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from api.admin_audit import record_admin_action
from api.deps import get_ch, get_conn, get_locale
from api.middleware.locale import SUPPORTED_LOCALES
from api.middleware.ratelimit import SNAPSHOT_TODAY_LIMIT, limiter, tier_limit
from api.range import RangeCtx, clamp_range_ctx, ctx_payload, resolve_service_dates
from api.routers.map import render_route_shape
from api.routers.overview import render_overview_summary
from api.routers.reports import (
    REPORT_ROWS_MAX,
    ReportResponse,
    render_headway_quality,
    render_performance_standards,
    render_report,
    scope_early,
)
from api.routers.static import fetch_static_routes
from api.security import User, csrf_guard, require_admin
from pipeline.data_sources import credit_line
from pipeline.reports import ROUTE_TABLE_SORTS
from pipeline.reports.capability import CapabilityTier, capability_tiers
from pipeline.reports.rankings import EARLY_ANY

admin_router = APIRouter(prefix="/api/admin/snapshots", tags=["admin"])
router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])

#: The serialized payloads a snapshot may hold. Well above what any screen's
#: responses take at their row limit, in every locale they are stored in, with
#: the agency's route catalogue, so reaching it means a request the screens
#: never make.
MAX_PAYLOAD_BYTES = 1024 * 1024

#: The report each screen's own endpoint serves. A route's page has none of
#: its own: it reads several, so it is absent here.
_REPORT_TYPES = {"council_report": "council_summary", "route_table": "route_table"}

#: The agency's route lists a route's page reads the route's standing from,
#: each at the length the live page asks for (:data:`REPORT_ROWS_MAX`).
_ROUTE_PAGE_LISTS = ("ranking", "on_time", "worst_5min")

#: The options of the report screens, which a route's page has no control for.
_REPORT_SCREEN_OPTIONS = ("preset", "sort", "order", "include_sparse", "include_retired", "include_zero_delay", "limit")

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
    "shape",
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
    limit: int | None = Field(default=None, ge=1, le=REPORT_ROWS_MAX)
    #: The route page's variant (``GET /route-shape``'s ``shape``).
    shape: str | None = Field(default=None, min_length=1, max_length=300)


class SnapshotCreate(BaseModel):
    """A create request: which screen, for which agency and query. It carries
    no figures; any field beyond these is refused."""

    model_config = ConfigDict(extra="forbid")

    screen: Literal["council_report", "route_table", "route_page"]
    agency_id: int
    query: SnapshotQuery = Field(default_factory=SnapshotQuery)
    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "note")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @model_validator(mode="after")
    def _only_the_screens_own_options(self) -> SnapshotCreate:
        """A route's page takes the scope and its variant alone. An option a
        screen has no control for would be stored as though it shaped figures
        it never touched."""
        q = self.query
        if self.screen == "route_page":
            given = [name for name in _REPORT_SCREEN_OPTIONS if getattr(q, name) not in (None, False)]
            if given:
                raise ValueError(f"the route page takes no {', '.join(given)}")
        elif q.shape is not None:
            raise ValueError("shape applies to the route page only")
        return self


def _route_codes(routes: str | None) -> list[str]:
    return [code for code in routes.split(",") if code] if routes else []


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
    credit per locale, None where no source covers the feed). A Routes table
    adds ``standards`` (the agency's performance standards per locale) and,
    under the on-time sort, ``headway`` (headway quality, stored once), the
    panels the screen shows beside it; one taken before they were stored has
    neither. A route page
    holds, in place of ``report``, ``route_lists`` (the agency's ``ranking``,
    ``on_time`` and ``worst_5min`` lists, without their prose) and
    ``concentration`` (the agency overview's), each written in no language
    and so stored once, and ``route_page`` (per locale, the route's
    ``standards`` and its delay by stop, ``shape``)."""

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
    #: pipeline.reports.capability, derived when read rather than stored: a
    #: signed-out reader can't reach the agency catalogue, and the page words
    #: a basic agency's missing figures by its tier. None for an agency no
    #: longer on record.
    capability_tier: CapabilityTier | None


#: The response fields that legitimately differ between the locales'
#: renders: the server's prose and the moment each was served.
_LOCALE_VARYING = frozenset({"text", "rendered_at"})


def _figures(report: dict[str, Any]) -> dict[str, Any]:
    """The whole response except :data:`_LOCALE_VARYING`, which must match
    across locales: anything else that differs means the aggregates moved
    between the two computations."""
    return {k: v for k, v in report.items() if k not in _LOCALE_VARYING}


def _unnamed(items: list[dict[str, Any]], *names: str) -> list[dict[str, Any]]:
    return [{k: v for k, v in item.items() if k not in names} for item in items]


def _route_page_figures(page: dict[str, Any]) -> dict[str, Any]:
    """A route page's per-language responses without what each language
    writes its own way: the targets' disclaimer, and the stop names, since a
    stop the timetable leaves unnamed is named by its sequence in the
    reader's language."""
    shape = page["shape"]
    return {
        "standards": _standards_figures(page["standards"]),
        "shape": {
            **shape,
            "stops": _unnamed(shape["stops"], "stop_name"),
            "unobserved_stops": _unnamed(shape["unobserved_stops"], "stop_name"),
            "variants": _unnamed(shape["variants"], "first_stop_name", "last_stop_name"),
        },
    }


def _standards_figures(standards: dict[str, Any]) -> dict[str, Any]:
    """A performance-standards response without its disclaimer, which is
    written in the reader's language."""
    return {k: v for k, v in standards.items() if k != "disclaimer"}


def _disagree(figures: list[dict[str, Any]]) -> bool:
    first, *others = figures
    return any(other != first for other in others)


_LOCALES_DISAGREE = "the aggregates changed while the snapshot was taken; try again"


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
    scope = clamp_range_ctx(
        from_=q.from_,
        to=q.to,
        dow=q.dow,
        time_band=q.time_band,
        service=q.service,
        routes=_route_codes(q.routes),
        hour=q.hour,
        stop=q.stop,
        direction=q.dir_,
    )
    if body.screen == "route_page" and len(scope.routes) != 1:
        raise HTTPException(status_code=422, detail="a route page snapshot names exactly one route")
    ctx = await resolve_service_dates(scope, conn, body.agency_id) if scope.service != "all" else scope
    params = {**ctx_payload(ctx), **q.model_dump(include=_SCREEN_OPTIONS)}

    screen_payloads: dict[str, Any]
    if body.screen == "route_page":
        screen_payloads = await _route_page_payloads(
            q, agency_id=body.agency_id, scope=scope, ctx=ctx, conn=conn, ch=ch
        )
        method_version = screen_payloads["route_lists"]["on_time"]["definition"]["method_version"]
        # The variant the page showed, named even when the server picked it,
        # as the dates are named even when the request left them to default.
        params["shape"] = next(iter(screen_payloads["route_page"].values()))["shape"]["shape_id"]
    else:
        report_type = _REPORT_TYPES[body.screen]
        reports: dict[str, dict[str, Any]] = {}
        for locale in SUPPORTED_LOCALES:
            rendered = await _render(report_type, q, agency_id=body.agency_id, ctx=ctx, conn=conn, ch=ch, locale=locale)
            reports[locale] = rendered.model_dump(mode="json", by_alias=True)
        if _disagree([_figures(report) for report in reports.values()]):
            raise HTTPException(status_code=409, detail=_LOCALES_DISAGREE)
        screen_payloads = {"report": reports}
        if body.screen == "route_table":
            screen_payloads.update(
                await _route_table_panels(
                    q,
                    agency_id=body.agency_id,
                    scope=scope,
                    ctx=ctx,
                    sort=reports[SUPPORTED_LOCALES[0]]["sort"],
                    conn=conn,
                    ch=ch,
                )
            )
        method_version = next(iter(reports.values()))["definition"]["method_version"]
        params = {"report_type": report_type, **params}

    payloads = {
        **screen_payloads,
        "routes": [r.model_dump(mode="json") for r in await fetch_static_routes(conn, body.agency_id)],
        "credit": {locale: credit_line(agency["feed_url"], locale) for locale in SUPPORTED_LOCALES},
    }
    encoded = json.dumps(payloads, ensure_ascii=False)
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"snapshot payload exceeds {MAX_PAYLOAD_BYTES} bytes")

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


async def _route_page_payloads(
    q: SnapshotQuery,
    *,
    agency_id: int,
    scope: RangeCtx,
    ctx: RangeCtx,
    conn: asyncpg.Connection,
    ch: AsyncClient,
) -> dict[str, Any]:
    """What one route's page reads for ``ctx``, each response computed by its
    endpoint's own code and asked what the page asks it:

    - the agency's route lists and overview over the scope without its route
      (``RouteSummaryStrip``). Their figures are the same in every language,
      so each is computed once: the lists are kept without their prose, the
      overview for its lateness concentration alone, asked the unresolved
      ``scope`` as its endpoint is;
    - per language, the route's performance targets
      (``PerformanceStandardPanel``) and its delay by stop on its variant
      (``RouteAnalysisTab``), without the variant's line, which only the map
      draws and a snapshot does not keep.

    409 when any language's figures disagree with another's."""
    route = ctx.routes[0]
    agency_wide = replace(ctx, routes=())
    early = scope_early(q.early)
    lists: dict[str, dict[str, Any]] = {}
    for report_type in _ROUTE_PAGE_LISTS:
        rendered = await render_report(
            report_type,
            agency_id=agency_id,
            ctx=agency_wide,
            conn=conn,
            ch=ch,
            locale=SUPPORTED_LOCALES[0],
            limit=REPORT_ROWS_MAX,
            late=q.late,
            early=early,
        )
        assert isinstance(rendered, ReportResponse)  # no format=csv, so never the CSV stream
        lists[report_type] = rendered.model_dump(mode="json", by_alias=True, exclude={"text"})
    overview = await render_overview_summary(agency_id, replace(scope, routes=()), conn=conn, ch=ch)
    pages: dict[str, dict[str, Any]] = {}
    for locale in SUPPORTED_LOCALES:
        standards = await render_performance_standards(
            agency_id, ctx, conn, ch, locale=locale, route=route, late=q.late, early=early
        )
        shape = await render_route_shape(conn, ch, agency_id, route, ctx, locale=locale, shape=q.shape)
        pages[locale] = {
            "standards": standards.model_dump(mode="json", by_alias=True),
            "shape": {**jsonable_encoder(shape), "geometry": None},
        }
    if _disagree([_route_page_figures(page) for page in pages.values()]):
        raise HTTPException(status_code=409, detail=_LOCALES_DISAGREE)
    return {
        "route_lists": lists,
        "concentration": overview.model_dump(mode="json", by_alias=True)["concentration"],
        "route_page": pages,
    }


async def _route_table_panels(
    q: SnapshotQuery,
    *,
    agency_id: int,
    scope: RangeCtx,
    ctx: RangeCtx,
    sort: str | None,
    conn: asyncpg.Connection,
    ch: AsyncClient,
) -> dict[str, Any]:
    """The panels the Routes screen draws beside its table, each computed by
    its endpoint's own code and asked what the screen asks it (the scope and
    its on-time tolerance, never the table's options):

    - ``headway`` (``HeadwayQualityPanel``), only where the table is sorted
      by on-time, as the screen shows it. It is written in no language, so it
      is stored once, asked the unresolved ``scope`` as its endpoint is;
    - ``standards`` (``PerformanceStandardPanel``) per language, since the
      targets' disclaimer is the server's prose.

    409 when the languages' targets disagree on anything but that prose."""
    early = scope_early(q.early)
    panels: dict[str, Any] = {}
    if sort == "on_time":
        headway = await render_headway_quality(agency_id, scope, conn)
        panels["headway"] = headway.model_dump(mode="json", by_alias=True)
    standards: dict[str, dict[str, Any]] = {}
    for locale in SUPPORTED_LOCALES:
        rendered = await render_performance_standards(agency_id, ctx, conn, ch, locale=locale, late=q.late, early=early)
        standards[locale] = rendered.model_dump(mode="json", by_alias=True)
    if _disagree([_standards_figures(each) for each in standards.values()]):
        raise HTTPException(status_code=409, detail=_LOCALES_DISAGREE)
    panels["standards"] = standards
    return panels


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
    tier = (await capability_tiers(conn, [row["agency_id"]])).get(row["agency_id"])
    return SnapshotOut(
        **{
            **dict(row),
            "params": json.loads(row["params"]),
            "payloads": json.loads(row["payloads"]),
            "capability_tier": tier,
        }
    )


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
