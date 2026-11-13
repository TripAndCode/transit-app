"""Bus Town replay: one agency's buses over a bounded window of one past
service day, for a frontend to animate. Read-only.

``GET /api/agencies/{agency_id}/replay`` answers "what was running, where,
and how late" for a span no wider than
:data:`MAX_REPLAY_WINDOW_HOURS` of one service day, packed columnar so a
multi-hour, multi-route reply stays a small payload.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date as CalendarDate
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.clickhouse import newest_filed_date_before, table_holding
from api.deps import get_agency, get_ch, get_conn
from api.middleware.ratelimit import limiter, tier_limit
from pipeline.db import departure_ch, stop_event_filter_ch
from pipeline.live_filing import filing
from pipeline.route_catalog import route_code_sql
from pipeline.service_day import (
    clock_to_sec,
    filed_between_ch,
    scheduled_seconds_ch,
    service_day_offset,
    service_day_offset_ch,
)
from pipeline.static_archive import Stamp, observation_rows

router = APIRouter(prefix="/api/agencies", tags=["replay"])

_JST = ZoneInfo("Asia/Tokyo")

#: The widest a requested window may be. The frontend replays at most a
#: few hours at a time, and a wider window would ship proportionally more
#: stop-event rows for an agency-wide, every-route read (unlike the
#: route-scoped endpoints, which bound cost by picking one route).
MAX_REPLAY_WINDOW_HOURS = 3

#: Sanity bound on from_hour/to_hour. A GTFS post-midnight continuation
#: keeps counting past 24 (a trip scheduled at "27:56" is service-day second
#: 27*3600+56*60), so the bound is two service days' worth, not one.
MAX_REPLAY_HOUR = 47

#: How many of the agency's most recent service days with observations to
#: offer the frontend as alternatives.
MAX_AVAILABLE_DATES = 14


class ReplayStops(BaseModel):
    """Every stop any returned trip visits, columnar: ``lat[i]``/``lon[i]`` go
    with ``id[i]``. A stop with no current static match keeps null
    coordinates rather than being dropped, so a trip's ``stop_idx`` always
    resolves."""

    id: list[str]
    lat: list[float | None]
    lon: list[float | None]


class ReplayRoute(BaseModel):
    code: str
    name: str = Field(description="route_short_name, else route_long_name, else the code itself.")
    color: str | None = Field(description="Always null: no route carries a color in this schema yet.")


class ReplayTrip(BaseModel):
    """One trip's observed stops over the window, in schedule order.

    ``stop_idx``, ``t`` and ``delay`` are parallel arrays, one entry per
    stop the trip has an observation for within the window — a stop the
    feed never reported for that trip, or whose scheduled time falls
    outside the window, is simply absent rather than carrying a null entry.
    """

    trip_id: str
    route_code: str | None = Field(description="Null when refitting leaves the stop event with no route.")
    stop_idx: list[int]
    t: list[int] = Field(description="Seconds since the service day's 00:00: scheduled second plus observed delay.")
    delay: list[int]


class ReplayResponse(BaseModel):
    date: str | None = Field(
        description="The service day replayed, or null when the agency has no observations at all."
    )
    from_hour: int | None
    to_hour: int | None
    available_dates: list[str] = Field(
        description=f"Up to the agency's last {MAX_AVAILABLE_DATES} service days with observations, newest first."
    )
    stops: ReplayStops
    routes: list[ReplayRoute]
    trips: list[ReplayTrip]


def _empty_response(date: CalendarDate | None, available_dates: list[CalendarDate]) -> ReplayResponse:
    return ReplayResponse(
        date=None if date is None else date.isoformat(),
        from_hour=None,
        to_hour=None,
        available_dates=[d.isoformat() for d in available_dates],
        stops=ReplayStops(id=[], lat=[], lon=[]),
        routes=[],
        trips=[],
    )


def validate_replay_window(from_hour: int | None, to_hour: int | None) -> None:
    """Raise a 422 for a malformed or over-wide window. Both bounds must be
    given together -- one alone has no stated meaning in the contract -- and
    the width is capped at :data:`MAX_REPLAY_WINDOW_HOURS`, the one bound
    this agency-wide, every-route read enforces on its own cost."""
    if (from_hour is None) != (to_hour is None):
        raise HTTPException(status_code=422, detail="from_hour and to_hour must be given together")
    if from_hour is None or to_hour is None:
        return
    if to_hour <= from_hour:
        raise HTTPException(status_code=422, detail="to_hour must be greater than from_hour")
    if to_hour - from_hour > MAX_REPLAY_WINDOW_HOURS:
        raise HTTPException(status_code=422, detail=f"the window must be at most {MAX_REPLAY_WINDOW_HOURS} hours wide")


async def _available_dates(conn: asyncpg.Connection, ch: AsyncClient, agency_id: int) -> list[CalendarDate]:
    """The agency's most recent service days with observations, newest
    first, capped at :data:`MAX_AVAILABLE_DATES`.

    Read from `agg_route_daily`'s filed-date column (`pipeline.db.
    build_dedup_ch_sql`'s docstring) -- the same source and the same
    analyze-lag caveat `pipeline.health.aggregate_freshness` and
    `pipeline.reports.map.route_exists` already accept, rather than a new
    live ClickHouse scan of several weeks of agency-wide rows. An agency
    never analyzed falls back to one ClickHouse probe for its single newest
    filed day, same fallback `aggregate_freshness` uses for that case.
    """
    rows = await conn.fetch(
        "SELECT DISTINCT date FROM agg_route_daily WHERE agency_id = $1 ORDER BY date DESC LIMIT $2",
        agency_id,
        MAX_AVAILABLE_DATES,
    )
    if rows:
        return [r["date"] for r in rows]
    today_jst_midnight_utc = (
        datetime.now(_JST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    )
    fallback = await newest_filed_date_before(ch, agency_id, today_jst_midnight_utc)
    return [] if fallback is None else [fallback]


def _replay_rows(day_param: str, stamp: Stamp, table: str, filed_where: str = "") -> str:
    """The FROM clause of one agency's admitted stop events on the service
    day bound as *day_param*, each poll filed under *stamp*
    (`pipeline.static_archive.observation_rows`) and tested by
    *filed_where* on the filed poll.

    Unlike a route-scoped read (`api.routers.map._route_rows`), no column
    here is both a REFIT-rewritten field and a row-admission test, so there
    is nothing a REFIT read would need an OR-NULL branch to keep.
    """
    on_day = filed_between_ch(f"{{{day_param}:Date}}", f"{{{day_param}:Date}}")
    return observation_rows(stop_event_filter_ch(on_day), stamp, table, filed_where, live_form=True)


def _service_sec_ch() -> str:
    return service_day_offset_ch(scheduled_seconds_ch("u."))


def build_default_window_probe_sql(stamp: Stamp, table: str) -> str:
    """The earliest service-day second any surviving (non-withdrawn) stop
    event of the day is scheduled at -- the start of "the first
    :data:`MAX_REPLAY_WINDOW_HOURS` hours of that day that have data".

    Grouped the same way the final read dedupes a stop event
    (`pipeline.db.build_dedup_ch_sql`'s docstring), so the picked window is
    guaranteed to contain at least the earliest stop event the final read
    would itself return -- a stop event this day resolves to NULL would not
    give a boundary the actual data agrees with.
    """
    return f"""
        WITH grouped AS (
            SELECT argMax(tuple({departure_ch()}, {_service_sec_ch()}), (u.captured_at, u.file_name)) AS winner
            {_replay_rows("target_date", stamp, table)}
            GROUP BY u.trip_id, u.stop_sequence
            HAVING tupleElement(winner, 1) IS NOT NULL
        )
        SELECT min(tupleElement(winner, 2)) FROM grouped
    """


def build_replay_sql(stamp: Stamp, table: str) -> str:
    """Rendered per-stop dedup query for one agency's service day, windowed
    to ``[from_sec, to_sec)``, plus the trip's route and stop. Mirrors
    `api.routers.map.build_route_trips_sql`'s single tuple-argMax per
    ``(trip_id, stop_sequence)`` (one winning row decides every column
    together, never a per-column argMax that could mix rows on a tie), just
    agency-wide instead of one route, so ``route_code`` rides along in the
    tuple instead of being fixed by a WHERE. The window is tested on the
    filed schedule (`pipeline.static_archive.observation_rows`'s own
    docstring), since REFIT can rewrite ``scheduled_sec``.
    """
    filed_where = f"{_service_sec_ch()} >= {{from_sec:UInt32}} AND {_service_sec_ch()} < {{to_sec:UInt32}}"
    return f"""
        WITH grouped AS (
            SELECT u.trip_id AS trip_id, u.stop_sequence AS stop_sequence,
                argMax(tuple({departure_ch()}, u.stop_id, u.route_code, u.scheduled_sec, u.scheduled_time),
                    (u.captured_at, u.file_name)) AS winner
            {_replay_rows("target_date", stamp, table, filed_where)}
            GROUP BY u.trip_id, u.stop_sequence
            HAVING tupleElement(winner, 1) IS NOT NULL
        )
        SELECT trip_id, stop_sequence, winner.1 AS dep_delay, winner.2 AS stop_id,
            winner.3 AS route_code, winner.4 AS scheduled_sec, winner.5 AS scheduled_time
        FROM grouped
        ORDER BY trip_id, stop_sequence
    """


def build_replay_trips(
    rows: Iterable[Sequence[Any]],
) -> tuple[list[ReplayTrip], list[str], list[str]]:
    """Group `build_replay_sql`'s rows into trips, sorted by each trip's
    first observed second, plus the stop_ids used (first-appearance order,
    for `stops.id`'s columnar index) and the distinct route_codes used.

    Each row is ``(trip_id, stop_sequence, dep_delay, stop_id, route_code,
    scheduled_sec, scheduled_time)``, already in ``stop_sequence`` order
    within a trip from the query's own ``ORDER BY``. ``t`` is
    ``scheduled + delay`` exactly as `api.routers.map.build_route_trips`
    computes ``observed_sec`` -- the one existing definition of "when a bus
    was at a stop" this reuses rather than inventing a second one.
    """
    stop_index: dict[str, int] = {}
    per_trip: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"route_code": None, "stop_idx": [], "t": [], "delay": []}
    )
    route_codes: dict[str, None] = {}
    for trip_id, _stop_sequence, dep_delay, stop_id, route_code, scheduled_sec, scheduled_time in rows:
        sched = service_day_offset(int(scheduled_sec)) if scheduled_sec is not None else clock_to_sec(scheduled_time)
        if sched is None or stop_id is None:
            continue
        idx = stop_index.setdefault(stop_id, len(stop_index))
        t = per_trip[trip_id]
        t["route_code"] = route_code
        t["stop_idx"].append(idx)
        t["t"].append(sched + int(dep_delay))
        t["delay"].append(int(dep_delay))
        if route_code is not None:
            route_codes[route_code] = None

    trips = [
        ReplayTrip(trip_id=trip_id, route_code=t["route_code"], stop_idx=t["stop_idx"], t=t["t"], delay=t["delay"])
        for trip_id, t in per_trip.items()
    ]
    # "First t": the earliest second a trip is observed at, not the array's
    # first entry -- a window clip can leave a trip's kept stops in an order
    # whose first entry is not its earliest (e.g. a trip crossing midnight).
    trips.sort(key=lambda trip: (min(trip.t) if trip.t else 0, trip.trip_id))
    ordered_stops = sorted(stop_index, key=lambda s: stop_index[s])
    return trips, ordered_stops, list(route_codes)


async def _stop_coordinates(
    conn: asyncpg.Connection, agency_id: int, stop_ids: list[str]
) -> dict[str, tuple[float | None, float | None]]:
    if not stop_ids:
        return {}
    rows = await conn.fetch(
        "SELECT stop_id, stop_lat, stop_lon FROM static_stops WHERE agency_id = $1 AND stop_id = ANY($2)",
        agency_id,
        stop_ids,
    )
    return {r["stop_id"]: (r["stop_lat"], r["stop_lon"]) for r in rows}


async def _route_labels(conn: asyncpg.Connection, agency_id: int, route_codes: list[str]) -> dict[str, str]:
    """``route_code -> display name`` for exactly the codes used, the same
    ``COALESCE(short_name, long_name, code)`` precedence
    ``api.routers.reports.forecast_overview`` derives route names with."""
    if not route_codes:
        return {}
    rows = await conn.fetch(
        "SELECT route_code, route_short_name, route_long_name FROM ("
        f"  SELECT {route_code_sql('route_id')} AS route_code, route_short_name, route_long_name "
        "  FROM static_routes WHERE agency_id = $1"
        ") s WHERE route_code = ANY($2)",
        agency_id,
        route_codes,
    )
    labels = {r["route_code"]: (r["route_short_name"] or r["route_long_name"]) for r in rows}
    return {code: labels.get(code) or code for code in route_codes}


@router.get("/{agency_id}/replay", response_model=ReplayResponse)
@limiter.limit(tier_limit)
async def replay(
    request: Request,
    date: CalendarDate | None = Query(
        default=None, description="Service day to replay. Defaults to the latest with observations."
    ),
    from_hour: int | None = Query(default=None, ge=0, le=MAX_REPLAY_HOUR),
    to_hour: int | None = Query(default=None, ge=0, le=MAX_REPLAY_HOUR),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> ReplayResponse:
    """Replay one agency's buses over a bounded window of one past service
    day: every trip's observed stops, each stop's coordinates, and each
    route's name, packed columnar for a frontend to animate. Read-only.
    """
    validate_replay_window(from_hour, to_hour)
    available = await _available_dates(conn, ch, agency_id)
    target_date = date if date is not None else (available[0] if available else None)
    if target_date is None:
        return _empty_response(None, available)

    table = await table_holding(ch, agency_id, target_date)
    filed = await filing(conn, ch, agency_id, target_date, target_date, table=table)
    params: dict[str, Any] = {"agency_id": agency_id, "target_date": target_date, **filed.params}

    if from_hour is None:
        probe = await ch.query(build_default_window_probe_sql(filed.stamp, table), parameters=params)
        lo = probe.result_rows[0][0] if probe.result_rows else None
        if lo is None:
            return _empty_response(target_date, available)
        from_hour = int(lo) // 3600
        to_hour = from_hour + MAX_REPLAY_WINDOW_HOURS
    # validate_replay_window already rejected one of the pair given alone,
    # so to_hour is set whenever from_hour was given, and both are now set
    # otherwise by the default-window probe above.
    assert to_hour is not None

    result = await ch.query(
        build_replay_sql(filed.stamp, table),
        parameters={**params, "from_sec": from_hour * 3600, "to_sec": to_hour * 3600},
    )
    trips, stop_ids, route_codes = build_replay_trips(result.result_rows)
    if not trips:
        return _empty_response(target_date, available)

    coords = await _stop_coordinates(conn, agency_id, stop_ids)
    labels = await _route_labels(conn, agency_id, route_codes)
    return ReplayResponse(
        date=target_date.isoformat(),
        from_hour=from_hour,
        to_hour=to_hour,
        available_dates=[d.isoformat() for d in available],
        stops=ReplayStops(
            id=stop_ids,
            lat=[coords.get(s, (None, None))[0] for s in stop_ids],
            lon=[coords.get(s, (None, None))[1] for s in stop_ids],
        ),
        routes=[ReplayRoute(code=code, name=labels.get(code, code), color=None) for code in route_codes],
        trips=trips,
    )
