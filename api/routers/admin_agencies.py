"""Per-agency diagnostics and operator actions for the admin agency page.

Read side: one bundled snapshot (``GET .../diagnostics``) of everything the
agency drawer shows -- freshness, the RT field-coverage registry, the static
version timeline, clamp history, weather station, and the two manually
curated policy tables. Postgres only: the ClickHouse probe the fleet-wide
ops view runs is deliberately not repeated per agency, so opening a drawer
cannot be slowed (or failed) by the raw-updates store.

Write side: the standards/weights editors, an on-demand RT coverage probe,
a single-agency re-run of the cron ingest path, a timetable reload, and the
two chained into one update. Every mutation records an audit entry through
:func:`api.admin_audit.record_admin_action`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api import agency_diagnostics as ad
from api.admin_audit import record_admin_action
from api.admin_runs import (
    AGENCIES_IN_FLIGHT_SQL,
    AGENCY_RUN_IN_FLIGHT_SQL,
    AGENCY_RUN_REQUEST_LOCK_SQL,
    INSERT_MANUAL_RUN_SQL,
    LAST_STATIC_LOAD_SQL,
    LATEST_OPERATOR_RUNS_SQL,
    build_latest_runs,
    in_flight_args,
    shape_run,
)
from api.deps import get_conn
from api.middleware.ratelimit import ADMIN_ACTION_LIMIT, limiter
from api.range import jst_today
from api.security import User, csrf_guard, require_admin
from pipeline.locks import AGENCY_RUN_REQUEST_LOCK_KEY
from pipeline.reports.network import invalidate_network_summary

router = APIRouter(prefix="/api/admin/agencies", tags=["admin"])

_log = logging.getLogger(__name__)

#: Upper bound on the live feed fetch an operator-triggered probe performs.
#: Shorter than the CLI probe's own timeout: this one runs inside a request
#: an operator is watching, and a feed that slow is itself the finding.
_PROBE_FETCH_TIMEOUT_SEC = 15

#: Bounds the editors' payloads so one request cannot turn into an unbounded
#: write loop. Comfortably above any real agency's route count.
_MAX_EDIT_ROWS = 500


# ── response models ──────────────────────────────────────────────────────


class RtFieldCoverage(BaseModel):
    present: bool
    coverage_pct: float | None
    sample_size: int | None
    probed_at: str | None
    expired: bool
    probed: bool


class RtCoverage(BaseModel):
    fields: dict[str, RtFieldCoverage]
    complete: bool
    last_probed_at: str | None


class StaticVersion(BaseModel):
    version: str
    loaded_at: str | None
    trips: int | None
    vehicle_km: float | None
    routes: int | None
    calendar_until: str | None
    is_current: bool


class ClampDay(BaseModel):
    date: str
    clamp_pct: float | None


class WeatherStation(BaseModel):
    station_id: str
    station_name: str
    source: str
    note: str | None


class StandardRow(BaseModel):
    route_code: str
    metric_type: str
    threshold_value: float
    bonus_malus_rate: float


class WeightRow(BaseModel):
    route_code: str | None
    weight: float


class WeightsCoverage(BaseModel):
    routes_with_weights: int
    routes_total: int


class AgencyRtSummary(BaseModel):
    complete: bool
    present_count: int
    field_count: int
    probed: bool
    last_probed_at: str | None


class AgencyStaticVersion(BaseModel):
    version: str
    loaded_at: str | None


class AgencyRunStep(BaseModel):
    #: ``static`` (timetable), ``ingest`` (realtime, promotion included) or
    #: ``analyze``.
    step: str
    #: ``pending`` (the run has not reached it), ``running``, ``ok``,
    #: ``unchanged`` (a timetable check that loaded nothing newer),
    #: ``skipped``, ``error``, or ``not_run`` (the run ended before it).
    status: str
    started_at: str | None
    finished_at: str | None
    #: Routes in the loaded timetable for ``static``, realtime rows ingested
    #: for ``ingest``; no count for ``analyze``.
    rows: int | None
    error: str | None


class AgencyRun(BaseModel):
    """An agency's newest operator-triggered run, step by step."""

    run_id: int
    kind: str
    status: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    steps: list[AgencyRunStep]


class AgencyHealthRow(BaseModel):
    agency_id: int
    agency_name: str
    feed_url: str
    ingest_strategy: str | None
    deleted_at: str | None
    freshness: str
    latest_data_date: str | None
    last_analyzed_at: str | None
    last_capture_at: str | None
    rt_coverage: AgencyRtSummary
    clamp_history: list[ClampDay]
    static_version: AgencyStaticVersion | None
    #: JST day the current timetable was loaded, when known.
    static_loaded_on: str | None
    #: A job is working on this agency now; a new run would be refused.
    in_flight: bool
    latest_run: AgencyRun | None


class AgencyDiagnostics(BaseModel):
    agency_id: int
    agency_name: str
    feed_url: str
    static_url: str | None
    ingest_strategy: str | None
    deleted_at: str | None
    freshness: str
    last_analyzed_at: str | None
    latest_data_date: str | None
    last_capture_at: str | None
    rt_coverage: RtCoverage
    static_versions: list[StaticVersion]
    clamp_history: list[ClampDay]
    weather_station: WeatherStation | None
    standards: list[StandardRow]
    standards_count: int
    weights: list[WeightRow]
    weights_coverage: WeightsCoverage


# ── request models ───────────────────────────────────────────────────────


class StandardEdit(BaseModel):
    route_code: str
    metric_type: str
    threshold_value: float
    bonus_malus_rate: float = Field(ge=0)


class StandardsPatch(BaseModel):
    """Rows to upsert and rows to remove, applied as one transaction."""

    upsert: list[StandardEdit] = Field(default_factory=list)
    delete: list[StandardEdit] = Field(default_factory=list)


class WeightEdit(BaseModel):
    #: ``None`` addresses the agency default row (migration 0035's convention).
    route_code: str | None = None
    weight: float = Field(gt=0)


class WeightsPatch(BaseModel):
    upsert: list[WeightEdit] = Field(default_factory=list)
    delete: list[WeightEdit] = Field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────────────────


async def _load_agency(conn: asyncpg.Connection, agency_id: int) -> asyncpg.Record:
    row = await conn.fetchrow(ad.AGENCY_HEADER_SQL, agency_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return row


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ── read ─────────────────────────────────────────────────────────────────


async def _run_state(
    conn: asyncpg.Connection, now: datetime
) -> tuple[list[asyncpg.Record], dict[int, dict[str, Any]], set[int]]:
    """Timetable loads, each agency's latest operator run, and which agencies
    have a job in flight -- or nothing, when ``pipeline_runs`` cannot be
    read: an environment whose schema predates it still gets its list."""
    try:
        loads = await conn.fetch(LAST_STATIC_LOAD_SQL)
        runs = build_latest_runs(dict(r) for r in await conn.fetch(LATEST_OPERATOR_RUNS_SQL))
        busy = {r["agency_id"] for r in await conn.fetch(AGENCIES_IN_FLIGHT_SQL, *in_flight_args(now))}
    except asyncpg.PostgresError:
        _log.warning("agencies health: pipeline_runs could not be read", exc_info=True)
        return [], {}, set()
    return list(loads), runs, busy


@router.get("/health", response_model=list[AgencyHealthRow])
async def agencies_health(
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[AgencyHealthRow]:
    """Per-agency health for the list view's columns.

    Fleet-wide queries pivoted in memory rather than one diagnostics bundle
    per row: the table renders every agency at once, so the cost must not
    grow with the roster. Soft-deleted agencies are included (the admin list
    shows them) and carry their ``deleted_at``.

    Each row also carries the agency's latest operator run, step by step.
    It lives here rather than behind its own endpoint because the table needs
    every row's run state on load anyway (a row with a run in flight shows
    it in place of its buttons), and because the stage dates beside it --
    the timetable's load day, the data's last day -- are what a finished run
    changes: one poll of this snapshot shows the run ending and the dates it
    moved together.
    """
    today = jst_today()
    now = datetime.now(timezone.utc)
    window_start = today - timedelta(days=ad.CLAMP_HISTORY_DAYS - 1)
    headers = await conn.fetch(ad.AGENCIES_HEALTH_SQL)
    probes = await conn.fetch(ad.ALL_RT_COVERAGE_SQL)
    clamps = await conn.fetch(ad.ALL_CLAMP_HISTORY_SQL, window_start, today)
    versions = await conn.fetch(ad.CURRENT_STATIC_VERSION_SQL)
    loads, runs, busy = await _run_state(conn, now)
    rows = ad.build_agency_health(
        headers=[dict(r) for r in headers],
        probes=[dict(r) for r in probes],
        clamps=[dict(r) for r in clamps],
        versions=[dict(r) for r in versions],
        static_loads=[dict(r) for r in loads],
        today=today,
        now=now,
    )
    return [
        AgencyHealthRow(
            **r,
            in_flight=r["agency_id"] in busy,
            latest_run=AgencyRun(**run) if (run := runs.get(r["agency_id"])) else None,
        )
        for r in rows
    ]


@router.get("/{agency_id}/diagnostics", response_model=AgencyDiagnostics)
async def agency_diagnostics(
    agency_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
) -> AgencyDiagnostics:
    """Everything the agency drawer renders, in one round of queries.

    The seven reads below have no dependency on each other, so they run
    concurrently, each on its own connection acquired from the pool: a
    single asyncpg connection cannot multiplex queries, so awaiting them one
    at a time would serialize seven round trips for no reason. The header
    lookup runs first, to 404 before spending pool connections on an agency
    that doesn't exist, and gives its connection back before the others are
    taken: a request holding one while it waits for seven more is how enough
    concurrent requests drain the pool and wait on each other forever.
    """
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        header = await _load_agency(conn, agency_id)
    today = jst_today()
    window_start = today - timedelta(days=ad.CLAMP_HISTORY_DAYS - 1)

    async def _fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
        async with pool.acquire() as c:
            return await c.fetch(sql, *args)

    async def _fetchrow(sql: str, *args: Any) -> asyncpg.Record | None:
        async with pool.acquire() as c:
            return await c.fetchrow(sql, *args)

    # Indexed with explicit casts rather than unpacked straight off
    # asyncio.gather(...): past a handful of arguments its typeshed overload
    # falls back to a single collapsed element type for every result, losing
    # each one's real list/Record-or-None type.
    gathered = await asyncio.gather(
        _fetch(ad.RT_COVERAGE_SQL, agency_id),
        _fetch(ad.CLAMP_HISTORY_SQL, agency_id, window_start, today),
        _fetch(ad.STATIC_VERSIONS_SQL, agency_id),
        _fetchrow(ad.WEATHER_STATION_SQL, agency_id),
        _fetch(ad.STANDARDS_SQL, agency_id),
        _fetch(ad.WEIGHTS_SQL, agency_id),
        _fetchrow(ad.WEIGHTS_COVERAGE_SQL, agency_id),
    )
    probe_rows = cast("list[asyncpg.Record]", gathered[0])
    clamp_rows = cast("list[asyncpg.Record]", gathered[1])
    version_rows = cast("list[asyncpg.Record]", gathered[2])
    station = cast("asyncpg.Record | None", gathered[3])
    standard_rows = cast("list[asyncpg.Record]", gathered[4])
    weight_rows = cast("list[asyncpg.Record]", gathered[5])
    coverage_row = cast("asyncpg.Record | None", gathered[6])

    latest_data_date: date | None = header["latest_data_date"]
    analyzed_at = header["analyzed_at"]
    captured_at = header["max_updates_captured_at"]

    return AgencyDiagnostics(
        agency_id=header["agency_id"],
        agency_name=header["agency_name"],
        feed_url=header["feed_url"],
        static_url=header["static_url"],
        ingest_strategy=header["ingest_strategy"],
        deleted_at=header["deleted_at"].isoformat() if header["deleted_at"] else None,
        freshness=ad.freshness_state(latest_data_date, today),
        last_analyzed_at=analyzed_at.isoformat() if analyzed_at else None,
        latest_data_date=latest_data_date.isoformat() if latest_data_date else None,
        last_capture_at=captured_at.isoformat() if captured_at else None,
        rt_coverage=RtCoverage(**ad.build_rt_coverage([dict(r) for r in probe_rows], now=datetime.now(timezone.utc))),
        static_versions=[StaticVersion(**v) for v in ad.build_static_versions([dict(r) for r in version_rows])],
        clamp_history=[ClampDay(**d) for d in ad.build_clamp_history([dict(r) for r in clamp_rows], today=today)],
        weather_station=WeatherStation(**dict(station)) if station else None,
        standards=[StandardRow(**dict(r)) for r in standard_rows],
        standards_count=len(standard_rows),
        weights=[WeightRow(route_code=r["route_code"], weight=float(r["weight"])) for r in weight_rows],
        weights_coverage=WeightsCoverage(
            **ad.build_weights_coverage(
                coverage_row["routes_with_weights"] if coverage_row else 0,
                coverage_row["routes_total"] if coverage_row else 0,
            )
        ),
    )


# ── editors ──────────────────────────────────────────────────────────────


@router.patch("/{agency_id}/standards", response_model=list[StandardRow])
async def patch_standards(
    agency_id: int,
    body: StandardsPatch,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[StandardRow]:
    """Upsert and/or remove ``route_performance_standards`` rows.

    Applied in one transaction so a rejected row leaves the agency's
    configuration exactly as it was, never half-edited.
    """
    csrf_guard(request)
    if len(body.upsert) + len(body.delete) > _MAX_EDIT_ROWS:
        raise HTTPException(status_code=422, detail="too many rows in one request")
    await _load_agency(conn, agency_id)
    upserts = [e.model_dump() for e in body.upsert]
    deletes = [e.model_dump() for e in body.delete]
    try:
        ad.validate_standard_edits(upserts)
        ad.validate_standard_edits(deletes)
    except ValueError as exc:
        raise _bad_request(exc) from None

    before = [dict(r) for r in await conn.fetch(ad.STANDARDS_SQL, agency_id)]
    async with conn.transaction():
        if deletes:
            await conn.execute(
                ad.BATCH_DELETE_STANDARD_SQL,
                agency_id,
                [item["route_code"].strip() for item in deletes],
                [item["metric_type"] for item in deletes],
            )
        if upserts:
            await conn.execute(
                ad.BATCH_UPSERT_STANDARD_SQL,
                agency_id,
                [item["route_code"].strip() for item in upserts],
                [item["metric_type"] for item in upserts],
                [item["threshold_value"] for item in upserts],
                [item["bonus_malus_rate"] for item in upserts],
            )
        after = [dict(r) for r in await conn.fetch(ad.STANDARDS_SQL, agency_id)]
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="agency.standards_updated",
            target_type="agency",
            target_id=agency_id,
            before=before,
            after=after,
            ip=request.client.host if request.client else None,
        )
    return [StandardRow(**r) for r in after]


@router.patch("/{agency_id}/weights", response_model=list[WeightRow])
async def patch_weights(
    agency_id: int,
    body: WeightsPatch,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[WeightRow]:
    """Upsert and/or remove ``ridership_weights`` rows.

    A ``route_code`` of ``null`` addresses the agency's default weight, which
    lives in its own partially-indexed row (migration 0035) and therefore
    needs its own statement rather than the per-route one.
    """
    csrf_guard(request)
    if len(body.upsert) + len(body.delete) > _MAX_EDIT_ROWS:
        raise HTTPException(status_code=422, detail="too many rows in one request")
    await _load_agency(conn, agency_id)
    upserts = [e.model_dump() for e in body.upsert]
    deletes = [e.model_dump() for e in body.delete]
    try:
        ad.validate_weight_edits(upserts)
        ad.validate_weight_edits(deletes)
    except ValueError as exc:
        raise _bad_request(exc) from None

    before = [
        {"route_code": r["route_code"], "weight": float(r["weight"])}
        for r in await conn.fetch(ad.WEIGHTS_SQL, agency_id)
    ]
    async with conn.transaction():
        if deletes:
            # NULL (the default row) survives unnest and is matched by
            # BATCH_DELETE_WEIGHT_SQL's IS NOT DISTINCT FROM join, so one
            # statement covers both kinds of delete.
            await conn.execute(
                ad.BATCH_DELETE_WEIGHT_SQL,
                agency_id,
                [item["route_code"].strip() if item["route_code"] is not None else None for item in deletes],
            )
        route_upserts = [item for item in upserts if item["route_code"] is not None]
        default_upsert = next((item for item in upserts if item["route_code"] is None), None)
        if route_upserts:
            await conn.execute(
                ad.BATCH_UPSERT_ROUTE_WEIGHT_SQL,
                agency_id,
                [item["route_code"].strip() for item in route_upserts],
                [item["weight"] for item in route_upserts],
            )
        if default_upsert is not None:
            # ridership_weights' default-row arbiter is a different partial
            # unique index (WHERE route_code IS NULL) than the route-code
            # one above, so ON CONFLICT can't target both in one statement;
            # validate_weight_edits already guarantees at most one such row.
            await conn.execute(ad.UPSERT_DEFAULT_WEIGHT_SQL, agency_id, default_upsert["weight"])
        rows = await conn.fetch(ad.WEIGHTS_SQL, agency_id)
        after = [{"route_code": r["route_code"], "weight": float(r["weight"])} for r in rows]
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="agency.weights_updated",
            target_type="agency",
            target_id=agency_id,
            before=before,
            after=after,
            ip=request.client.host if request.client else None,
        )
    invalidate_network_summary()
    return [WeightRow(**r) for r in after]


# ── actions ──────────────────────────────────────────────────────────────


def _fetch_and_measure(feed_url: str) -> dict[str, Any]:
    """Fetch the live feed and decode its per-field coverage.

    Blocking on purpose -- the caller runs it off the event loop. Uses the
    same ``safe_urlopen`` guard every other server-side fetch of an
    operator-supplied URL goes through, so this endpoint cannot be turned
    into an SSRF sink by repointing an agency's ``feed_url``.
    """
    from pipeline.strategies.static_join import field_coverage
    from pipeline.url_guard import safe_urlopen

    with safe_urlopen(feed_url, timeout=_PROBE_FETCH_TIMEOUT_SEC) as resp:
        raw = resp.read()
    return field_coverage(raw)


@router.post("/{agency_id}/probe", status_code=202)
@limiter.limit(ADMIN_ACTION_LIMIT)
async def probe_agency_feed(
    agency_id: int,
    request: Request,
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Run one RT field-coverage probe against the agency's own live feed and
    record the verdict.

    The probe is the same code path ``scripts/probe_rt_field_coverage.py``
    uses (decode via ``field_coverage``, persist via
    ``record_field_coverage_probe``), so the thresholds that decide what gets
    written cannot drift between the CLI and this button. The feed URL is
    read from the agency row rather than accepted from the caller: a verdict
    describes the feed that row points at, and letting a request name its own
    URL would record one feed's coverage against another's agency.
    """
    from pipeline.strategies.static_join import record_field_coverage_probe
    from pipeline.url_guard import FeedURLError

    csrf_guard(request)
    pool = request.app.state.pool
    # Two acquisitions on purpose: the feed fetch can take seconds, and a
    # connection held across it is one no other request can use.
    async with pool.acquire() as conn:
        header = await _load_agency(conn, agency_id)
    feed_url = header["feed_url"]

    try:
        cov = await asyncio.to_thread(_fetch_and_measure, feed_url)
    except FeedURLError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        _log.warning("admin probe: feed fetch failed for agency %s: %s", agency_id, exc)
        raise HTTPException(status_code=502, detail="The feed could not be fetched") from None

    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                verdicts = await record_field_coverage_probe(conn, agency_id, cov, feed_url)
                await record_admin_action(
                    conn,
                    actor_id=admin.user_id,
                    action="agency.probed",
                    target_type="agency",
                    target_id=agency_id,
                    after={"verdicts": verdicts, "sample_size": cov.get("stop_time_updates")},
                    ip=request.client.host if request.client else None,
                )
        except ValueError as exc:
            # An empty poll proves nothing; recording it would turn "probed
            # outside service hours" into a durable refutation.
            raise HTTPException(status_code=409, detail=str(exc)) from None
    return {
        "status": "recorded",
        "sample_size": cov.get("stop_time_updates"),
        "fields": verdicts,
    }


async def guard_agency_run(conn: asyncpg.Connection, agency_id: int) -> None:
    """Refuse (409) a new run for an agency a job is already working on.

    Call inside the transaction that then opens the run row: the check holds
    the agency's request lock until that transaction ends, so two presses
    arriving together cannot both see "nothing running" and both start.
    """
    await conn.execute(AGENCY_RUN_REQUEST_LOCK_SQL, AGENCY_RUN_REQUEST_LOCK_KEY, agency_id)
    if await conn.fetchval(AGENCY_RUN_IN_FLIGHT_SQL, *in_flight_args(datetime.now(timezone.utc)), agency_id):
        raise HTTPException(status_code=409, detail="A run for this agency is already in progress")


async def _open_agency_run(
    request: Request,
    admin: User,
    *,
    agency_id: int,
    kind: str,
    action: str,
    needs_static: bool,
) -> dict[str, Any]:
    """Open the umbrella row an operator's per-agency run is watched by, and
    audit the press -- or refuse it before anything is queued.

    Refused with 409 when the agency is disabled (the runners select on
    ``deleted_at IS NULL``, so the work would find nothing and report
    "started"), when a timetable step has no source to fetch from (the run
    could only end as "nothing to do", or as an error for an agency with no
    timetable),
    and when a job is already working on the agency (:func:`guard_agency_run`).

    Runs on a pool connection of its own, given back before the handler
    queues the job: a ``Depends(get_conn)`` connection is released only once
    the response's background tasks have finished, so it would stay checked
    out for the whole fetch + ingest + analyze.
    """
    async with request.app.state.pool.acquire() as conn:
        header = await _load_agency(conn, agency_id)
        if header["deleted_at"] is not None:
            raise HTTPException(status_code=409, detail="This agency is disabled")
        if needs_static and not (header["static_url"] and header["static_strategy"]):
            raise HTTPException(status_code=409, detail="This agency has no timetable (static GTFS) source configured")
        async with conn.transaction():
            await guard_agency_run(conn, agency_id)
            row = await conn.fetchrow(INSERT_MANUAL_RUN_SQL, kind, agency_id, admin.user_id)
            if row is None:
                raise HTTPException(status_code=503, detail="pipeline runs are not recordable in this environment")
            run = shape_run(row)
            await record_admin_action(
                conn,
                actor_id=admin.user_id,
                action=action,
                target_type="agency",
                target_id=agency_id,
                after={"run_id": run["run_id"]},
                ip=request.client.host if request.client else None,
            )
    return run


@router.post("/{agency_id}/reanalyze", status_code=202)
@limiter.limit(ADMIN_ACTION_LIMIT)
async def reanalyze_agency(
    agency_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
) -> dict[str, str]:
    """Re-run ingest + analyze for this one agency, in the background.

    Runs the cron job's own function scoped to a single agency rather than a
    second implementation of the same work, so the advisory lock, the JST
    session pin, and the post-run freshness check all behave identically to a
    scheduled run. A cron sweep that holds the lock when this starts turns it
    away (the run closes ``skipped``), which is why this returns 202 without
    waiting.

    An umbrella run row is opened here first, the same way `POST
    /api/admin/runs` does it: without one the operator's action draws no bar
    at all, and the rows the sweep writes per agency have no run to belong
    to. The background runner closes it on every path.
    """
    from api.routers.internal import _run_ingest_and_analyze

    csrf_guard(request)
    run = await _open_agency_run(
        request,
        admin,
        agency_id=agency_id,
        kind="ingest",
        action="agency.reanalyze_requested",
        needs_static=False,
    )
    background_tasks.add_task(
        _run_ingest_and_analyze,
        agency_ids=[agency_id],
        requested_by=admin.user_id,
        run_id=run["run_id"],
    )
    return {"status": "started"}


@router.post("/{agency_id}/refresh-static", status_code=202)
@limiter.limit(ADMIN_ACTION_LIMIT)
async def refresh_agency_static(
    agency_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Reload this agency's timetable (static GTFS), in the background.

    The same ``refresh_static`` that ``gtfs_pipeline.py refresh-static``
    runs, so the button and the command cannot drift. The URL is the agency
    row's own, never one the request names, for the same reason the probe
    reads it from the row: the timetable loaded describes the feed that row
    points at.
    """
    from api.routers.internal import _run_agency_update

    csrf_guard(request)
    run = await _open_agency_run(
        request,
        admin,
        agency_id=agency_id,
        kind="static",
        action="agency.static_refresh_requested",
        needs_static=True,
    )
    background_tasks.add_task(
        _run_agency_update,
        agency_id=agency_id,
        requested_by=admin.user_id,
        run_id=run["run_id"],
        then_ingest_and_analyze=False,
    )
    return {"status": "started", "run_id": run["run_id"]}


@router.post("/{agency_id}/update", status_code=202)
@limiter.limit(ADMIN_ACTION_LIMIT)
async def update_agency(
    agency_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Reload the timetable, then ingest and analyze, as one background job.

    Each step is recorded under this run as it goes (``GET .../health``
    reads it back step by step), and a failed step stops the chain.
    """
    from api.routers.internal import _run_agency_update

    csrf_guard(request)
    run = await _open_agency_run(
        request,
        admin,
        agency_id=agency_id,
        kind="update",
        action="agency.update_requested",
        needs_static=True,
    )
    background_tasks.add_task(
        _run_agency_update,
        agency_id=agency_id,
        requested_by=admin.user_id,
        run_id=run["run_id"],
        then_ingest_and_analyze=True,
    )
    return {"status": "started", "run_id": run["run_id"]}
