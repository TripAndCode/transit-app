"""A revision's effect on each route, read from the archive and from the
aggregates analyze builds out of real ingested polls
(api.routers.timetable_revisions, revision_routes).

Timetable A is in force until the day before the revision and B from it,
both weekdays only. R1 runs hourly from 07:00 to 12:00 under A; B drops its
09:00 trip, so R1 is cut and the longest gap at its main stop doubles. R2
runs alike in both. Before the revision the feed reports R1 400 seconds late
at every stop, after it 30 seconds; R2 is 30 seconds late throughout. The
polls are ingested from an archive after B is loaded, so the days before the
revision are filed under A only through analyze.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, time, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.range import jst_today
from pipeline import static_archive
from pipeline.analyze import analyze
from pipeline.ingest import ingest
from pipeline.static_loader import load_static
from tests.fixtures.clock import JST
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed

STOPS = ("S1", "S2", "S3", "S4", "S5", "S6")
SERVICE = "平日"
LATE, PUNCTUAL = 400, 30

_A = {f"r1_{hh}": ("R1", hh) for hh in (7, 8, 9, 10, 11, 12)} | {f"r2_{hh}": ("R2", hh) for hh in (7, 8, 10, 11, 12)}
_B = {trip: run for trip, run in _A.items() if trip != "r1_9"}


def _clock(hh: int, stop: int) -> str:
    return f"{hh:02d}:{2 * stop:02d}:00"


def _zip(tmp_path, stem: str, first: date, last: date, trips: dict[str, tuple[str, int]]) -> str:
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "\n".join(f"{s},{s}前,34.4{i},132.4{i}" for i, s in enumerate(STOPS)),
        "routes.txt": "route_id,route_short_name\nR1,R1\nR2,R2",
        "trips.txt": "trip_id,route_id,service_id\n" + "\n".join(f"{t},{r},{SERVICE}" for t, (r, _) in trips.items()),
        "stop_times.txt": "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
        + "\n".join(
            f"{t},{i + 1},{stop},{_clock(hh, i)},{_clock(hh, i)}"
            for t, (_r, hh) in trips.items()
            for i, stop in enumerate(STOPS)
        ),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        f"{SERVICE},1,1,1,1,1,0,0,{first:%Y%m%d},{last:%Y%m%d}",
        "feed_info.txt": "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date\n"
        f"test,https://example.com,ja,{first:%Y%m%d},{last:%Y%m%d}",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    path = tmp_path / f"{stem}.zip"
    path.write_bytes(buf.getvalue())
    return str(path)


def _write_poll(folder, day: date, trip_id: str, route: str, hh: int, delay: int) -> None:
    """One poll five minutes before *trip_id* leaves, predicting every stop *delay* seconds late."""
    at = datetime.combine(day, time(hh - 1, 55), tzinfo=JST)
    feed = trip_update_feed(
        trip_update(
            trip_id,
            *(stop_time_update(i + 1, departure_delay=delay, stop_id=stop) for i, stop in enumerate(STOPS)),
            route_id=route,
            start_date=f"{day:%Y%m%d}",
        ),
        timestamp=int(at.timestamp()),
    )
    utc = at.astimezone(timezone.utc)
    path = folder / f"{utc:%Y%m%d}" / f"TripUpdate_{utc:%H%M%S}_{trip_id}.pb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(feed)


@pytest.fixture
def revised(pg_conn, ch_client, tmp_path) -> tuple[int, date]:
    """The revision a week and a day ago, with every weekday from a week
    before it through yesterday polled and analyzed."""
    yesterday = jst_today() - timedelta(days=1)
    revision = yesterday - timedelta(days=7)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('revision routes', 'https://revision-routes.example.com/tu.pb', 'static_join') "
            "RETURNING agency_id"
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    a_stem = f"gtfs_static_{revision - timedelta(days=61):%Y%m%d}"
    b_stem = f"gtfs_static_{revision - timedelta(days=10):%Y%m%d}"
    a = _zip(tmp_path, a_stem, revision - timedelta(days=60), revision + timedelta(days=300), _A)
    b = _zip(tmp_path, b_stem, revision, revision + timedelta(days=300), _B)
    load_static(a, agency_id, pg_conn, ch_client=ch_client)
    load_static(b, agency_id, pg_conn, ch_client=ch_client)
    folder = tmp_path / "archive"
    day = revision - timedelta(days=7)
    while day <= yesterday:
        if day.weekday() < 5:
            for trip_id, (route, hh) in (_A if day < revision else _B).items():
                late = route == "R1" and day < revision
                _write_poll(folder, day, trip_id, route, hh, LATE if late else PUNCTUAL)
        day += timedelta(days=1)
    ingest(str(folder), agency_id, pg_conn, ch_client, strict=True)
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()
    return agency_id, revision


@pytest.fixture
async def api(ch_async_client):
    from api.main import app
    from api.middleware.ratelimit import limiter
    from tests.conftest import _test_pool

    saved = {name: getattr(app.state, name, None) for name in ("pool", "ch_client")}
    pool = await _test_pool()
    app.state.pool, app.state.ch_client = pool, ch_async_client
    limiter.reset()
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        await pool.close()
        for name, value in saved.items():
            setattr(app.state, name, value)


async def test_each_routes_timetable_and_observed_shift_across_the_revision(revised, api):
    agency_id, revision = revised
    response = await api.get(f"/api/{agency_id}/timetable-revisions/routes?on={revision.isoformat()}")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True and body["day_type_known"] is True
    assert body["full_through"] == (revision + timedelta(days=27)).isoformat()
    window = body["window"]
    assert (window["weeks"], window["complete"]) == (1, False)
    assert window["before_last"] == (revision - timedelta(days=1)).isoformat()
    assert window["after_first"] == revision.isoformat()

    routes = {r["route_code"]: r for r in body["routes"]}
    r1, r2 = routes["R1"], routes["R2"]
    assert (r1["route_class"], r1["trips_before"], r1["trips_after"]) == ("cut", 6, 5)
    assert (r1["main_stop_id"], r1["main_stop_name"]) == ("S1", "S1前")
    assert (r1["longest_gap_before"], r1["longest_gap_after"]) == (3600, 7200)
    assert (r1["last_before"], r1["last_after"]) == (12 * 3600, 12 * 3600)
    assert r2["route_class"] == "unchanged"
    assert [r["route_code"] for r in body["routes"]] == ["R1", "R2"]

    # The aggregates' route codes join the archive's: R1's observations moved with it.
    assert r1["observed_before"]["on_time_pct"] == 0
    assert r1["observed_after"]["on_time_pct"] == 100
    assert r1["observed_before"]["mean_delay_sec"] == LATE
    assert r2["observed_before"]["on_time_pct"] == r2["observed_after"]["on_time_pct"] == 100

    classes = {c["route_class"]: c for c in body["classes"]}
    assert classes["cut"]["routes"] == 1
    assert (classes["cut"]["before"]["on_time_pct"], classes["cut"]["after"]["on_time_pct"]) == (0, 100)
    assert classes["unchanged"]["routes"] == 1
    assert classes["added"]["routes"] == 0
    assert body["definition"]["value_basis"] == "feed_prediction"
    assert body["definition"]["preset"] == "standard_m1_p5"


async def test_a_day_type_the_timetables_run_nothing_on_lists_no_route(revised, api):
    agency_id, revision = revised
    body = (await api.get(f"/api/{agency_id}/timetable-revisions/routes?on={revision}&day_type=saturday")).json()
    assert body["day_type_known"] is True
    assert body["routes"] == []


async def test_before_a_whole_week_after_there_is_no_observed_side(pg_conn, ch_client, tmp_path, api):
    revision = jst_today() - timedelta(days=3)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('fresh revision', 'https://fresh.example.com/tu.pb', 'static_join') RETURNING agency_id"
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    a = _zip(tmp_path, "gtfs_static_20250101", revision - timedelta(days=60), revision + timedelta(days=300), _A)
    b = _zip(tmp_path, f"gtfs_static_{revision:%Y%m%d}", revision, revision + timedelta(days=300), _B)
    load_static(a, agency_id, pg_conn, ch_client=ch_client)
    load_static(b, agency_id, pg_conn, ch_client=ch_client)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    pg_conn.commit()
    body = (await api.get(f"/api/{agency_id}/timetable-revisions/routes")).json()
    assert body["available"] is True
    assert body["window"] is None and body["classes"] == [] and body["definition"] is None
    assert {r["route_code"]: r["observed_before"] for r in body["routes"]} == {"R1": None, "R2": None}


async def test_an_agency_with_no_revision_says_so(agency_id, ch_async_client, api):
    body = (await api.get(f"/api/{agency_id}/timetable-revisions/routes")).json()
    assert (body["available"], body["reason"], body["routes"]) == (False, "no_revision", [])


@pytest.mark.parametrize(("route_id", "code"), [("国道・古川線(1021)", "1021"), ("R1", "R1"), ("11(急行)", "11(急行)")])
def test_clickhouse_and_postgres_read_one_route_code_from_a_route_id(pg_conn, ch_client, route_id, code):
    from pipeline.route_catalog import ROUTE_CODE_PATTERN, ROUTE_CODE_REPLACEMENT, route_code_sql

    parameters = {"id": route_id, "pattern": ROUTE_CODE_PATTERN, "replacement": ROUTE_CODE_REPLACEMENT}
    ch_code = ch_client.query(
        "SELECT replaceRegexpOne({id:String}, {pattern:String}, {replacement:String})", parameters=parameters
    ).result_rows[0][0]
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT {route_code_sql('%s')}", (route_id,))
        pg_code = cur.fetchone()[0]
    assert ch_code == pg_code == code
