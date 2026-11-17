"""Planned stop supply built from the timetable archive (pipeline.stop_supply)
and read for every operator on one map (GET /api/network/supply).

Timetable A is in force until 2026-03-31 and B from 2026-04-01. Each trip
serves S1 to S4, two minutes apart, so S4, where every trip ends, departs
nothing. A's last weekday trip leaves S1 at 24:20, the same night's. B drops
the Saturday trip while keeping the Saturday service in its calendar.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import pytest
from httpx import ASGITransport

from pipeline import static_archive, stop_supply
from pipeline.static_loader import load_static

STOPS = ("S1", "S2", "S3", "S4")
WEEKDAY, SATURDAY, SUNDAY = "平日", "土曜", "日祝"
A, B, C = "gtfs_static_20251201", "gtfs_static_20260315", "gtfs_static_20260320"
A_FIRST, B_FIRST, B_LAST = date(2026, 1, 1), date(2026, 4, 1), date(2027, 3, 31)

# trip_id: (service, first departure)
_A = {
    "w1": (WEEKDAY, "07:00"),
    "w2": (WEEKDAY, "07:30"),
    "w3": (WEEKDAY, "24:20"),
    "s1": (SATURDAY, "09:00"),
    "h1": (SUNDAY, "10:00"),
}
_B = {"w1": (WEEKDAY, "07:00"), "w2": (WEEKDAY, "08:10"), "h1": (SUNDAY, "10:00")}
_C = {"w1": (WEEKDAY, "07:00"), "h1": (SUNDAY, "10:00")}


def _clock(hhmm: str, stop: int) -> str:
    hh, mm = (int(part) for part in hhmm.split(":"))
    minutes = hh * 60 + mm + 2 * stop
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def _zip(tmp_path, stem: str, first: date, last: date, trips: dict[str, tuple[str, str]]) -> str:
    flags = {WEEKDAY: "1,1,1,1,1,0,0", SATURDAY: "0,0,0,0,0,1,0", SUNDAY: "0,0,0,0,0,0,1"}
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "\n".join(f"{s},{s}前,34.4{i},132.4{i}" for i, s in enumerate(STOPS)),
        "routes.txt": "route_id,route_short_name\nR1,R1",
        "trips.txt": "trip_id,route_id,service_id\n" + "\n".join(f"{t},R1,{svc}" for t, (svc, _) in trips.items()),
        "stop_times.txt": "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
        + "\n".join(
            f"{t},{i + 1},{stop},{_clock(departs, i)},{_clock(departs, i)}"
            for t, (_svc, departs) in trips.items()
            for i, stop in enumerate(STOPS)
        ),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        + "\n".join(f"{svc},{days},{first:%Y%m%d},{last:%Y%m%d}" for svc, days in flags.items()),
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


@pytest.fixture
def revised(pg_conn, ch_client, agency_id, tmp_path) -> int:
    load_static(_zip(tmp_path, A, A_FIRST, date(2026, 12, 31), _A), agency_id, pg_conn, ch_client=ch_client)
    load_static(_zip(tmp_path, B, B_FIRST, B_LAST, _B), agency_id, pg_conn, ch_client=ch_client)
    # load_static keeps the outgoing timetable; analyze keeps the loaded one.
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    pg_conn.commit()
    return agency_id


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


def _days(pg_conn, agency_id: int) -> dict[tuple[str, str], date | None]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT static_version_id, day_type, typical_day FROM agg_stop_supply_days WHERE agency_id = %s",
            (agency_id,),
        )
        return {(stem, day_type): day for stem, day_type, day in cur.fetchall()}


def _hourly(pg_conn, agency_id: int, stem: str, day_type: str) -> dict[tuple[str, int], tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_id, hour, departures, first_sec, last_sec, max_gap_sec FROM agg_stop_supply_hourly "
            "WHERE agency_id = %s AND static_version_id = %s AND day_type = %s",
            (agency_id, stem, day_type),
        )
        return {(stop, hour): tuple(rest) for stop, hour, *rest in cur.fetchall()}


def test_analyze_builds_each_version_in_forces_supply(revised, pg_conn, ch_client):
    from pipeline.analyze import analyze

    analyze(revised, pg_conn, ch_client)
    days = _days(pg_conn, revised)
    assert {stem for stem, _ in days} == {A, B}
    assert all(day is not None for day in days.values())
    weekday = _hourly(pg_conn, revised, A, "weekday")
    assert weekday[("S1", 7)] == (2, 7 * 3600, 7 * 3600 + 30 * 60, 30 * 60)
    # The 24:20 trip is the same night's, hour 24 rather than 0.
    assert weekday[("S1", 24)] == (1, 24 * 3600 + 20 * 60, 24 * 3600 + 20 * 60, None)
    assert not any(stop == "S4" for stop, _ in weekday)
    assert not any(hour < 4 for _, hour in weekday)


def test_a_new_version_is_added_and_one_no_longer_in_force_dropped(revised, pg_conn, ch_client, tmp_path):
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    before = _hourly(pg_conn, revised, A, "weekday")
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=False) is False
    # C covers all of B's days and was fetched later, so B is no longer in force anywhere.
    load_static(_zip(tmp_path, C, B_FIRST, B_LAST, _C), revised, pg_conn, ch_client=ch_client)
    static_archive.archive_loaded(pg_conn, ch_client, revised)
    pg_conn.commit()
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=False)
    assert {stem for stem, _ in _days(pg_conn, revised)} == {A, C}
    assert _hourly(pg_conn, revised, A, "weekday") == before
    assert _hourly(pg_conn, revised, C, "weekday")[("S1", 7)] == (1, 7 * 3600, 7 * 3600, None)


async def test_the_map_reads_the_version_in_force_on_the_date(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    response = await api.get(
        "/api/network/supply", params={"date": "2026-02-10", "day_type": "weekday", "from_hour": 7, "to_hour": 9}
    )
    assert response.status_code == 200
    body = response.json()
    (agency,) = [a for a in body["agencies"] if a["agency_id"] == revised]
    assert agency["status"] == "ok" and agency["static_version_id"] == A
    stops = {s["stop_id"]: s for s in body["stops"] if s["agency_id"] == revised}
    assert set(stops) == {"S1", "S2", "S3"}
    s1 = stops["S1"]
    assert (s1["departures"], s1["per_hour"], s1["longest_gap_min"]) == (2, 1.0, 90.0)
    assert (s1["first_sec"], s1["last_sec"]) == (7 * 3600, 24 * 3600 + 20 * 60)
    assert (s1["lat"], s1["lon"]) == (34.40, 132.40)


async def test_a_day_type_with_no_trip_is_zero_not_missing(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    response = await api.get("/api/network/supply", params={"date": "2026-05-12", "day_type": "saturday"})
    body = response.json()
    (agency,) = [a for a in body["agencies"] if a["agency_id"] == revised]
    assert agency["status"] == "ok" and agency["static_version_id"] == B
    s1 = next(s for s in body["stops"] if s["agency_id"] == revised and s["stop_id"] == "S1")
    assert (s1["departures"], s1["longest_gap_min"], s1["first_sec"]) == (0, 360.0, None)


async def test_an_operator_without_a_version_or_a_build_says_so(revised, api):
    response = await api.get("/api/network/supply", params={"date": "2026-02-10"})
    (agency,) = [a for a in response.json()["agencies"] if a["agency_id"] == revised]
    assert agency["status"] == "not_built"
    response = await api.get("/api/network/supply", params={"date": "2030-01-01"})
    (agency,) = [a for a in response.json()["agencies"] if a["agency_id"] == revised]
    assert agency["status"] == "no_timetable"


async def test_a_band_ending_before_it_starts_is_refused(api):
    response = await api.get("/api/network/supply", params={"from_hour": 16, "to_hour": 10})
    assert response.status_code == 422
