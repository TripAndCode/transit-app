"""Per-agency diagnostics and operator actions for the admin agency page.

Read side: one bundled snapshot (``GET .../diagnostics``) of everything the
agency drawer shows -- freshness, the RT field-coverage registry, the static
version timeline, clamp history, weather station, and the two manually
curated policy tables. Postgres only: the ClickHouse probe the fleet-wide
ops view runs is deliberately not repeated per agency, so opening a drawer
cannot be slowed (or failed) by the raw-updates store.

Write side: the standards/weights editors, an on-demand RT coverage probe,
and a single-agency re-run of the cron ingest path. Every mutation records
an audit entry through :func:`api.admin_audit.record_admin_action`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api import agency_diagnostics as ad
from api.admin_audit import record_admin_action
from api.deps import get_conn
from api.range import jst_today
from api.security import User, csrf_guard, require_admin

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


class AgencyDiagnostics(BaseModel):
    agency_id: int
    agency_name: str
    feed_url: str
    static_url: str | None
    ingest_strategy: str | None
    deleted_at: Any  # datetime | None
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


@router.get("/health", response_model=list[AgencyHealthRow])
async def agencies_health(
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[AgencyHealthRow]:
    """Per-agency health for the list view's columns.

    Four fleet-wide queries pivoted in memory rather than one diagnostics
    bundle per row: the table renders every agency at once, so the cost must
    not grow with the roster. Soft-deleted agencies are included (the admin
    list shows them) and carry their ``deleted_at``.
    """
    today = jst_today()
    window_start = today - timedelta(days=ad.CLAMP_HISTORY_DAYS - 1)
    headers = await conn.fetch(ad.AGENCIES_HEALTH_SQL)
    probes = await conn.fetch(ad.ALL_RT_COVERAGE_SQL)
    clamps = await conn.fetch(ad.ALL_CLAMP_HISTORY_SQL, window_start, today)
    versions = await conn.fetch(ad.CURRENT_STATIC_VERSION_SQL)
    rows = ad.build_agency_health(
        headers=[dict(r) for r in headers],
        probes=[dict(r) for r in probes],
        clamps=[dict(r) for r in clamps],
        versions=[dict(r) for r in versions],
        today=today,
        now=datetime.now(timezone.utc),
    )
    return [AgencyHealthRow(**r) for r in rows]


@router.get("/{agency_id}/diagnostics", response_model=AgencyDiagnostics)
async def agency_diagnostics(
    agency_id: int,
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> AgencyDiagnostics:
    """Everything the agency drawer renders, in one round of queries."""
    header = await _load_agency(conn, agency_id)
    today = jst_today()
    window_start = today - timedelta(days=ad.CLAMP_HISTORY_DAYS - 1)

    probe_rows = await conn.fetch(ad.RT_COVERAGE_SQL, agency_id)
    clamp_rows = await conn.fetch(ad.CLAMP_HISTORY_SQL, agency_id, window_start, today)
    version_rows = await conn.fetch(ad.STATIC_VERSIONS_SQL, agency_id)
    station = await conn.fetchrow(ad.WEATHER_STATION_SQL, agency_id)
    standard_rows = await conn.fetch(ad.STANDARDS_SQL, agency_id)
    weight_rows = await conn.fetch(ad.WEIGHTS_SQL, agency_id)
    coverage_row = await conn.fetchrow(ad.WEIGHTS_COVERAGE_SQL, agency_id)

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
        for item in deletes:
            await conn.execute(ad.DELETE_STANDARD_SQL, agency_id, item["route_code"].strip(), item["metric_type"])
        for item in upserts:
            await conn.execute(
                ad.UPSERT_STANDARD_SQL,
                agency_id,
                item["route_code"].strip(),
                item["metric_type"],
                item["threshold_value"],
                item["bonus_malus_rate"],
            )
        after = [dict(r) for r in await conn.fetch(ad.STANDARDS_SQL, agency_id)]
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="agency_standards_updated",
            target_type="agency",
            target_id=agency_id,
            before=before,
            after=after,
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
        for item in deletes:
            code = item["route_code"]
            if code is None:
                await conn.execute(ad.DELETE_DEFAULT_WEIGHT_SQL, agency_id)
            else:
                await conn.execute(ad.DELETE_ROUTE_WEIGHT_SQL, agency_id, code.strip())
        for item in upserts:
            code = item["route_code"]
            if code is None:
                await conn.execute(ad.UPSERT_DEFAULT_WEIGHT_SQL, agency_id, item["weight"])
            else:
                await conn.execute(ad.UPSERT_ROUTE_WEIGHT_SQL, agency_id, code.strip(), item["weight"])
        rows = await conn.fetch(ad.WEIGHTS_SQL, agency_id)
        after = [{"route_code": r["route_code"], "weight": float(r["weight"])} for r in rows]
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="agency_weights_updated",
            target_type="agency",
            target_id=agency_id,
            before=before,
            after=after,
        )
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
async def probe_agency_feed(
    agency_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
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
    header = await _load_agency(conn, agency_id)
    feed_url = header["feed_url"]

    try:
        cov = await asyncio.to_thread(_fetch_and_measure, feed_url)
    except FeedURLError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        _log.warning("admin probe: feed fetch failed for agency %s: %s", agency_id, exc)
        raise HTTPException(status_code=502, detail="The feed could not be fetched") from None

    try:
        verdicts = await record_field_coverage_probe(conn, agency_id, cov, feed_url)
    except ValueError as exc:
        # An empty poll proves nothing; recording it would turn "probed
        # outside service hours" into a durable refutation.
        raise HTTPException(status_code=409, detail=str(exc)) from None

    await record_admin_action(
        conn,
        actor_id=admin.user_id,
        action="agency_feed_probed",
        target_type="agency",
        target_id=agency_id,
        after={"verdicts": verdicts, "sample_size": cov.get("stop_time_updates")},
    )
    return {
        "status": "recorded",
        "sample_size": cov.get("stop_time_updates"),
        "fields": verdicts,
    }


@router.post("/{agency_id}/reanalyze", status_code=202)
async def reanalyze_agency(
    agency_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, str]:
    """Re-run ingest + analyze for this one agency, in the background.

    Runs the cron job's own function scoped to a single agency rather than a
    second implementation of the same work, so the advisory lock, the JST
    session pin, and the post-run freshness check all behave identically to a
    scheduled run. Contention with a run already in flight is handled there
    (the poke is skipped and logged), which is why this returns 202 without
    waiting.
    """
    from api.routers.internal import _run_ingest_and_analyze

    csrf_guard(request)
    await _load_agency(conn, agency_id)
    await record_admin_action(
        conn,
        actor_id=admin.user_id,
        action="agency_reanalyze_requested",
        target_type="agency",
        target_id=agency_id,
    )
    background_tasks.add_task(_run_ingest_and_analyze, agency_id)
    return {"status": "started"}
