"""A row is filed under the timetable in force on its service date, whenever
that timetable was loaded.

Each test runs the daily path: timetables load through ``load_static``, polls
arrive through the collector's ingest, closed days are promoted, and analyze
builds the aggregates. The service days sit two days before the one in
progress, so every poll lands on a closed JST day inside ``updates_live``'s
TTL.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, time, timedelta, timezone

import pytest

from pipeline.analyze import analyze
from pipeline.ingest import ingest_live_payload
from pipeline.promote import promote_closed_days
from pipeline.reports.schedule_revision import ScheduleDays
from pipeline.service_day import service_date_of
from pipeline.static_loader import load_static
from tests.fixtures.clock import JST
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed

ROUTE = "R1"
STOPS = ("S1", "S2", "S3", "S4")
SERVICE = "平日"


def _stem(fetched: date) -> str:
    return f"gtfs_static_{fetched:%Y%m%d}"


def _zip(tmp_path, stem: str, first: date, last: date, trips: dict[str, str]) -> str:
    """A timetable whose one service runs every day of [first, last], and
    *trips* (trip_id to first departure, HH:MM) along STOPS two minutes apart."""
    stop_times = []
    for trip_id, departs in trips.items():
        hh, mm = (int(part) for part in departs.split(":"))
        for i, stop in enumerate(STOPS):
            minutes = hh * 60 + mm + 2 * i
            clock = f"{minutes // 60:02d}:{minutes % 60:02d}:00"
            stop_times.append(f"{trip_id},{i + 1},{stop},{clock},{clock}")
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "\n".join(f"{s},{s},34.4,132.4{i}" for i, s in enumerate(STOPS)),
        "routes.txt": f"route_id,route_short_name\n{ROUTE},{ROUTE}",
        "trips.txt": "trip_id,route_id,service_id\n" + "\n".join(f"{t},{ROUTE},{SERVICE}" for t in trips),
        "stop_times.txt": "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n" + "\n".join(stop_times),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        f"{SERVICE},1,1,1,1,1,1,1,{first:%Y%m%d},{last:%Y%m%d}",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    path = tmp_path / f"{stem}.zip"
    path.write_bytes(buf.getvalue())
    return str(path)


def _agency(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('in force', 'https://in-force.example.com/tu.pb', 'static_join') RETURNING agency_id"
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    return agency_id


def _poll(pg_conn, ch_client, agency_id: int, day: date, hh: int, mm: int, trips: list[str]) -> None:
    """One poll at *hh*:*mm* JST on *day*, predicting every stop of each of *trips* a minute late."""
    at = datetime.combine(day, time(hh, mm), tzinfo=JST)
    feed = trip_update_feed(
        *(
            trip_update(
                trip_id,
                *(stop_time_update(i + 1, departure_delay=60, stop_id=stop) for i, stop in enumerate(STOPS)),
                route_id=ROUTE,
                start_date=f"{day:%Y%m%d}",
            )
            for trip_id in trips
        ),
        timestamp=int(at.timestamp()),
    )
    name = f"oracle/{day:%Y%m%d}/TripUpdate_{hh:02d}{mm:02d}00.pb"
    ingest_live_payload(agency_id, feed, at.astimezone(timezone.utc).isoformat(), name, pg_conn, ch_client)


def _promote_and_analyze(pg_conn, ch_client, agency_id: int) -> None:
    promote_closed_days(agency_id, pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()


def _rows(pg_conn, sql: str, *params) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(sql, params)
        return [tuple(row) for row in cur.fetchall()]


def _versions(pg_conn, agency_id: int) -> dict[date, str]:
    return dict(
        _rows(
            pg_conn,
            "SELECT date, static_version_id FROM agg_schedule_revision_daily WHERE agency_id = %s",
            agency_id,
        )
    )


def _boundaries(pg_conn, agency_id: int, first: date, last: date) -> list[str]:
    """The trend report's revision boundaries over [first, last]."""
    rows = _rows(
        pg_conn,
        "SELECT agency_id, date, static_version_id FROM agg_schedule_revision_daily "
        "WHERE agency_id = %s AND date BETWEEN %s AND %s ORDER BY date",
        agency_id,
        first - timedelta(days=1),
        last,
    )
    return ScheduleDays(first, last, rows).boundaries()


@pytest.fixture
def days() -> tuple[date, date]:
    """The day a revision is published, and the next day, its first."""
    day = service_date_of(datetime.now(timezone.utc)) - timedelta(days=2)
    return day, day + timedelta(days=1)


def test_a_revision_loaded_the_day_before_it_starts_leaves_that_day_on_its_own_timetable(
    pg_conn, ch_client, tmp_path, days
):
    day, first_day = days
    agency_id = _agency(pg_conn)
    a, b = _stem(day - timedelta(days=30)), _stem(day)
    # The revision moves trip b from 12:30 to 13:10 and drops trip c.
    load_static(
        _zip(
            tmp_path, a, day - timedelta(days=30), day + timedelta(days=150), {"a": "07:00", "b": "12:30", "c": "12:42"}
        ),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    _poll(pg_conn, ch_client, agency_id, day, 7, 10, ["a"])
    _promote_and_analyze(pg_conn, ch_client, agency_id)

    # Published at 10:00, from the next day.
    load_static(
        _zip(tmp_path, b, first_day, day + timedelta(days=180), {"a": "07:00", "b": "13:10", "d": "13:00"}),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    for mm in (31, 35, 39):
        _poll(pg_conn, ch_client, agency_id, day, 12, mm, ["b"])
    _poll(pg_conn, ch_client, agency_id, day, 12, 49, ["c"])
    _poll(pg_conn, ch_client, agency_id, first_day, 7, 10, ["a"])
    _poll(pg_conn, ch_client, agency_id, first_day, 13, 17, ["b", "d"])
    _promote_and_analyze(pg_conn, ch_client, agency_id)

    # Trip times: b departs in hour 12 on the day before, as A has it, and c
    # keeps its departures; on the first day b departs in hour 13, as B has it.
    assert _rows(
        pg_conn, "SELECT date, hour, samples FROM agg_hour_daily WHERE agency_id = %s ORDER BY date, hour", agency_id
    ) == [(day, 7, 4), (day, 12, 8), (first_day, 7, 4), (first_day, 13, 8)]
    # Day type: c is filed under its service, not under none.
    assert _rows(
        pg_conn,
        "SELECT date, service_type, samples FROM agg_route_daily WHERE agency_id = %s ORDER BY date, service_type",
        agency_id,
    ) == [(day, SERVICE, 12), (first_day, SERVICE, 12)]
    # Stops: every stop is visited by a, b and c on the day before.
    assert _rows(
        pg_conn,
        "SELECT stop_id, sum(samples)::int FROM agg_stop_daily WHERE agency_id = %s AND date = %s "
        "GROUP BY stop_id ORDER BY stop_id",
        agency_id,
        day,
    ) == [(stop, 3) for stop in STOPS]
    assert _versions(pg_conn, agency_id) == {day: a, first_day: b}
    assert _boundaries(pg_conn, agency_id, day, first_day) == [first_day.isoformat()]


def test_a_revision_loaded_after_its_first_day_refiles_the_days_it_covers(pg_conn, ch_client, tmp_path, days):
    day, first_day = days
    agency_id = _agency(pg_conn)
    a, b = _stem(day - timedelta(days=30)), _stem(first_day + timedelta(days=1))
    load_static(
        _zip(tmp_path, a, day - timedelta(days=30), day + timedelta(days=150), {"a": "07:00", "b": "12:30"}),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    for service_day in (day, first_day):
        _poll(pg_conn, ch_client, agency_id, service_day, 7, 10, ["a"])
        _poll(pg_conn, ch_client, agency_id, service_day, 12, 39, ["b"])
    _promote_and_analyze(pg_conn, ch_client, agency_id)
    assert _versions(pg_conn, agency_id) == {day: a, first_day: a}

    # Published the day after it took effect, moving trip b from 12:30 to 13:10.
    load_static(
        _zip(tmp_path, b, first_day, day + timedelta(days=180), {"a": "07:00", "b": "13:10"}),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    _promote_and_analyze(pg_conn, ch_client, agency_id)

    assert _rows(
        pg_conn, "SELECT date, hour, samples FROM agg_hour_daily WHERE agency_id = %s ORDER BY date, hour", agency_id
    ) == [(day, 7, 4), (day, 12, 4), (first_day, 7, 4), (first_day, 13, 4)]
    assert _versions(pg_conn, agency_id) == {day: a, first_day: b}
    assert _boundaries(pg_conn, agency_id, day, first_day) == [first_day.isoformat()]


def test_a_trip_a_revision_adds_polled_before_the_revision_loads_takes_its_schedule(pg_conn, ch_client, tmp_path, days):
    """Ingest finds no timetable for the added trip, so its rows carry no
    version: once the revision is loaded and in force, they are read with its
    schedule."""
    day, first_day = days
    agency_id = _agency(pg_conn)
    a, b = _stem(day - timedelta(days=30)), _stem(first_day + timedelta(days=1))
    load_static(
        _zip(tmp_path, a, day - timedelta(days=30), day + timedelta(days=150), {"a": "07:00"}),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    _poll(pg_conn, ch_client, agency_id, first_day, 7, 10, ["a"])
    _poll(pg_conn, ch_client, agency_id, first_day, 9, 9, ["d"])
    _promote_and_analyze(pg_conn, ch_client, agency_id)

    # Published after its first day, adding trip d.
    load_static(
        _zip(tmp_path, b, first_day, day + timedelta(days=180), {"a": "07:00", "d": "09:00"}),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    _promote_and_analyze(pg_conn, ch_client, agency_id)

    assert _rows(
        pg_conn, "SELECT date, hour, samples FROM agg_hour_daily WHERE agency_id = %s ORDER BY date, hour", agency_id
    ) == [(first_day, 7, 4), (first_day, 9, 4)]
    assert _rows(
        pg_conn,
        "SELECT date, service_type, samples FROM agg_route_daily WHERE agency_id = %s ORDER BY date, service_type",
        agency_id,
    ) == [(first_day, SERVICE, 8)]
    assert _rows(
        pg_conn,
        "SELECT stop_id, sum(samples)::int FROM agg_stop_daily WHERE agency_id = %s AND date = %s "
        "GROUP BY stop_id ORDER BY stop_id",
        agency_id,
        first_day,
    ) == [(stop, 2) for stop in STOPS]
    assert _versions(pg_conn, agency_id) == {first_day: b}


def test_analyze_refuses_to_start_without_the_in_force_record(pg_conn, ch_client, tmp_path, days, monkeypatch):
    """A store that predates the record fails before anything is rebuilt, not
    after: a full rebuild would otherwise be thrown away at its end."""
    from pipeline import analyze as analyze_mod

    day, _ = days
    agency_id = _agency(pg_conn)
    load_static(
        _zip(tmp_path, _stem(day), day - timedelta(days=30), day + timedelta(days=150), {"a": "07:00"}),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    _poll(pg_conn, ch_client, agency_id, day, 7, 10, ["a"])
    promote_closed_days(agency_id, pg_conn, ch_client)
    monkeypatch.setattr(analyze_mod, "IN_FORCE_TABLE", "agg_timetable_in_force_never_migrated", raising=False)
    with pytest.raises(RuntimeError, match="make migrate"):
        analyze(agency_id, pg_conn, ch_client)
    pg_conn.rollback()
    assert _rows(pg_conn, "SELECT count(*) FROM agg_feed_health WHERE agency_id = %s", agency_id) == [(0,)]


class _StoreWithout:
    """A ClickHouse client whose store lacks *missing*: only the table listing is answered."""

    def __init__(self, missing: str):
        from pipeline import ch_aggregates

        self._names = [name for name in ch_aggregates.STORE_TABLES if name != missing]

    def query(self, sql, parameters=None):
        class _Result:
            result_rows = [(name,) for name in self._names]

        return _Result()


def test_a_load_refuses_to_start_without_the_span_store(pg_conn, ch_client, tmp_path, days):
    """Nothing is saved or replaced: not even the outgoing timetable's planned trips."""
    from pipeline import static_archive

    day, first_day = days
    agency_id = _agency(pg_conn)
    load_static(
        _zip(tmp_path, _stem(day), day - timedelta(days=30), day + timedelta(days=150), {"a": "07:00"}),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
    with pytest.raises(RuntimeError, match="make ch-bootstrap"):
        load_static(
            _zip(tmp_path, _stem(first_day), first_day, day + timedelta(days=180), {"d": "09:00"}),
            agency_id,
            pg_conn,
            ch_client=_StoreWithout(static_archive.SPANS),
        )
    pg_conn.rollback()
    assert _rows(pg_conn, "SELECT count(*) FROM planned_trips_daily WHERE agency_id = %s", agency_id) == [(0,)]
    assert _rows(pg_conn, "SELECT trip_id FROM static_trips WHERE agency_id = %s", agency_id) == [("a",)]


class _BoundSpy:
    """Wraps the real client, keeping each read's SQL and bound parameters."""

    def __init__(self, inner):
        self._inner = inner
        self.reads: list[tuple[str, dict]] = []

    def query(self, sql, parameters=None):
        self.reads.append((sql, parameters or {}))
        return self._inner.query(sql, parameters=parameters)

    def query_row_block_stream(self, sql, parameters=None):
        self.reads.append((sql, parameters or {}))
        return self._inner.query_row_block_stream(sql, parameters=parameters)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_full_rebuild_reads_visits_and_headways_one_months_versions_at_a_time(pg_conn, ch_client, tmp_path):
    """A version in force each month: neither single-shot scan may bind every
    version's schedule at once, which a full rebuild of an agency's history
    would otherwise do."""
    from pipeline.clickhouse import insert_updates
    from pipeline.service_day import service_day_start_utc

    agency_id = _agency(pg_conn)
    months = [date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)]
    for first in months:
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        load_static(_zip(tmp_path, _stem(first), first, last, {"a": "07:00"}), agency_id, pg_conn, ch_client=ch_client)
    rows = []
    for first in months:
        day = first + timedelta(days=14)
        at = service_day_start_utc(day) + timedelta(hours=3, minutes=10)
        for i, stop in enumerate(STOPS):
            sec = 7 * 3600 + 120 * i
            rows.append(
                (
                    f"oracle/{day:%Y%m%d}/TripUpdate_071000.pb",
                    at.isoformat().replace("+00:00", "Z"),
                    "a",
                    SERVICE,
                    f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:00",
                    ROUTE,
                    i + 1,
                    60,
                    stop,
                    None,
                    None,
                    None,
                    None,
                    sec,
                    _stem(first),
                )
            )
    insert_updates(ch_client, agency_id, rows)
    spy = _BoundSpy(ch_client)
    analyze(agency_id, pg_conn, spy)

    visits = [params["force_stems"] for sql, params in spy.reads if "visit_dep" in sql and "force_stems" in params]
    headways = [params["force_stems"] for sql, params in spy.reads if "per_event" in sql and "force_stems" in params]
    expected = sorted([_stem(first)] for first in months)
    assert sorted(visits) == expected
    assert sorted(headways) == expected
