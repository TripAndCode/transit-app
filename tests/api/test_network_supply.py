"""Planned stop supply built from the timetable archive (pipeline.stop_supply)
and read for every operator on one map (GET /api/network/supply).

Timetable A is in force until 2026-03-31 and B from 2026-04-01. Each trip
serves S1 to S4, two minutes apart, so S4, where every trip ends, departs
nothing. A's last two weekday trips leave S1 at 24:20, written as GTFS's
extended hour, and at 00:30, written at face value: both the same night's.
B drops the Saturday trip while keeping the Saturday service in its calendar.
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
    "w4": (WEEKDAY, "00:30"),
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


H, MIN = 3600, 60


def _stops(body: dict, agency_id: int) -> dict[str, dict]:
    """The map's columns of one operator as a dict per stop."""
    (agency,) = [a for a in body["agencies"] if a["agency_id"] == agency_id]
    columns = agency["stops"]
    return {
        stop_id: {name: values[i] for name, values in columns.items()} for i, stop_id in enumerate(columns["stop_id"])
    }


def _agency(body: dict, agency_id: int) -> dict:
    (agency,) = [a for a in body["agencies"] if a["agency_id"] == agency_id]
    return agency


def test_analyze_builds_each_version_in_forces_supply(revised, pg_conn, ch_client):
    from pipeline.analyze import analyze

    analyze(revised, pg_conn, ch_client)
    days = _days(pg_conn, revised)
    assert {stem for stem, _ in days} == {A, B}
    assert all(day is not None for day in days.values())
    weekday = _hourly(pg_conn, revised, A, "weekday")
    assert weekday[("S1", 7)] == (2, 7 * H, 7 * H + 30 * MIN, 30 * MIN)
    # 24:20 and a face-value 00:30 are both the same night's hour 24.
    assert weekday[("S1", 24)] == (2, 24 * H + 20 * MIN, 24 * H + 30 * MIN, 10 * MIN)
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
    assert _hourly(pg_conn, revised, C, "weekday")[("S1", 7)] == (1, 7 * H, 7 * H, None)


def _counting_calendar_reads(monkeypatch) -> list:
    reads: list = []
    real = static_archive.archived_calendar
    monkeypatch.setattr(static_archive, "archived_calendar", lambda *a: reads.append(a) or real(*a))
    return reads


def _days_rows(pg_conn, agency_id: int, stem: str) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT day_type, typical_day, segments, calendar_read FROM agg_stop_supply_days "
            "WHERE agency_id = %s AND static_version_id = %s ORDER BY day_type",
            (agency_id, stem),
        )
        return cur.fetchall()


def test_moved_dates_that_leave_the_typical_days_only_record_the_read(revised, pg_conn, ch_client, monkeypatch):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    built = _days_rows(pg_conn, revised, A)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE agg_stop_supply_days SET segments = 'moved' WHERE agency_id = %s AND static_version_id = %s",
            (revised, A),
        )
    reads = _counting_calendar_reads(monkeypatch)
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=False) is False
    assert [args[2] for args in reads] == [A]
    assert _days_rows(pg_conn, revised, A) == built


def test_a_kept_calendar_that_tells_no_day_type_is_read_once(revised, pg_conn, ch_client, monkeypatch):
    monkeypatch.setattr(stop_supply, "typical_days", lambda segments, calendar: dict.fromkeys(stop_supply.DAY_TYPES))
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    assert {(day, read) for _dt, day, _segments, read in _days_rows(pg_conn, revised, A)} == {(None, True)}
    reads = _counting_calendar_reads(monkeypatch)
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=False) is False
    assert reads == []


def test_a_version_built_without_a_calendar_is_read_once_one_is_kept(revised, pg_conn, ch_client, monkeypatch):
    kept = static_archive.calendar_versions
    without_a = lambda ch, agency: {key for key in kept(ch, agency) if key[0] != A}  # noqa: E731
    monkeypatch.setattr(static_archive, "calendar_versions", without_a)
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    assert {(day, read) for _dt, day, _segments, read in _days_rows(pg_conn, revised, A)} == {(None, False)}
    monkeypatch.setattr(static_archive, "calendar_versions", kept)
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=False)
    rows = _days_rows(pg_conn, revised, A)
    assert all(day is not None and read for _dt, day, _segments, read in rows)
    assert _hourly(pg_conn, revised, A, "weekday")[("S1", 7)][0] == 2
    reads = _counting_calendar_reads(monkeypatch)
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=False) is False
    assert reads == []


def test_a_run_with_nothing_moved_reads_no_calendar(revised, pg_conn, ch_client, monkeypatch):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    reads = _counting_calendar_reads(monkeypatch)
    assert stop_supply.rebuild(pg_conn, ch_client, revised, full=False) is False
    assert reads == []


async def test_the_map_reads_the_version_in_force_on_the_date(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    response = await api.get(
        "/api/network/supply", params={"date": "2026-02-10", "day_type": "weekday", "from_hour": 7, "to_hour": 9}
    )
    assert response.status_code == 200
    body = response.json()
    agency = _agency(body, revised)
    assert agency["status"] == "ok" and agency["static_version_id"] == A
    stops = _stops(body, revised)
    assert set(stops) == {"S1", "S2", "S3"}
    assert stops["S1"] == {"stop_id": "S1", "lat": 34.4, "lon": 132.4, "departures": 2, "longest_gap_min": 90.0}
    from api.routers.network import SupplyMapResponse

    SupplyMapResponse.model_validate(body)


async def test_an_opened_stop_names_itself_and_its_first_and_last_departure(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    params = {"agency_id": revised, "stop_id": "S1", "date": "2026-02-10", "from_hour": 23, "to_hour": 26}
    response = await api.get("/api/network/supply/stop", params=params)
    assert response.status_code == 200
    stop = response.json()
    assert stop["stop_name"] == "S1前"
    assert (stop["departures"], stop["per_hour"], stop["longest_gap_min"]) == (2, 0.67, 90.0)
    # The face-value 00:30 is the day's last departure, not its first.
    assert (stop["first_sec"], stop["last_sec"]) == (7 * H, 24 * H + 30 * MIN)
    missing = await api.get("/api/network/supply/stop", params={**params, "stop_id": "S4"})
    assert missing.status_code == 404


async def test_a_day_type_with_no_trip_is_zero_not_missing(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    response = await api.get("/api/network/supply", params={"date": "2026-05-12", "day_type": "saturday"})
    body = response.json()
    agency = _agency(body, revised)
    assert agency["status"] == "ok" and agency["static_version_id"] == B
    s1 = _stops(body, revised)["S1"]
    assert (s1["departures"], s1["longest_gap_min"]) == (0, 360.0)
    stop = await api.get(
        "/api/network/supply/stop",
        params={"agency_id": revised, "stop_id": "S1", "date": "2026-05-12", "day_type": "saturday"},
    )
    assert (stop.json()["first_sec"], stop.json()["last_sec"]) == (None, None)


async def test_each_band_and_day_type_is_its_own_answer(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    base = {"date": "2026-02-10", "from_hour": 7, "to_hour": 9}
    weekday = await api.get("/api/network/supply", params=base)
    sunday = await api.get("/api/network/supply", params={**base, "day_type": "sunday_holiday"})
    night = await api.get("/api/network/supply", params={**base, "from_hour": 24, "to_hour": 25})
    assert _stops(weekday.json(), revised)["S1"]["departures"] == 2
    assert _stops(sunday.json(), revised)["S1"]["departures"] == 0
    assert _stops(night.json(), revised)["S1"]["departures"] == 2


async def test_the_map_is_read_again_once_analyze_rewrites_a_table(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    params = {"date": "2026-02-10", "from_hour": 7, "to_hour": 9}
    first = await api.get("/api/network/supply", params=params)
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM agg_stop_supply_hourly WHERE agency_id = %s AND stop_id = 'S2'", (revised,))
    pg_conn.commit()
    cached = await api.get("/api/network/supply", params=params)
    assert cached.json() == first.json()
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_table_state (agency_id, table_name, fingerprint, built_at) "
            "VALUES (%s, 'agg_stop_supply_hourly', 'test', clock_timestamp()) "
            "ON CONFLICT (agency_id, table_name) DO UPDATE SET built_at = clock_timestamp()",
            (revised,),
        )
    pg_conn.commit()
    rebuilt = await api.get("/api/network/supply", params=params)
    assert set(_stops(rebuilt.json(), revised)) == {"S1", "S3"}


async def test_an_operator_without_a_version_or_a_build_says_so(revised, api):
    response = await api.get("/api/network/supply", params={"date": "2026-02-10"})
    agency = _agency(response.json(), revised)
    assert (agency["status"], agency["stops"]) == ("not_built", None)
    response = await api.get("/api/network/supply", params={"date": "2030-01-01"})
    assert _agency(response.json(), revised)["status"] == "no_timetable"


async def test_a_band_ending_before_it_starts_is_refused(api):
    response = await api.get("/api/network/supply", params={"from_hour": 16, "to_hour": 10})
    assert response.status_code == 422


def _built_at(pg_conn, agency_id: int) -> dict[str, object]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, built_at FROM agg_table_state WHERE agency_id = %s AND table_name = ANY(%s)",
            (agency_id, [stop_supply.DAYS_TABLE, stop_supply.TABLE]),
        )
        return dict(cur.fetchall())


def _seed_ledger(pg_conn, ch_client, agency_id: int) -> None:
    """A few observations, so analyze keeps a ledger and a later run rebuilds
    only what moved rather than every table."""
    from datetime import time

    from tests.conftest import mirror_updates_to_ch

    with pg_conn.cursor() as cur:
        for i, delay in enumerate([60, 120, 180]):
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES (%s,%s,'2026-06-09T08:10:00','w1','平日',%s,'R1',1,%s)",
                (agency_id, f"f{i}.pb", time(8, 10), delay),
            )
    pg_conn.commit()
    mirror_updates_to_ch(ch_client, agency_id)


async def test_analyze_adds_a_new_version_and_the_map_shows_it(revised, pg_conn, ch_client, tmp_path, api, caplog):
    import logging

    from pipeline.analyze import analyze

    _seed_ledger(pg_conn, ch_client, revised)
    analyze(revised, pg_conn, ch_client)
    before = _built_at(pg_conn, revised)
    params = {"date": "2026-05-12", "from_hour": 7, "to_hour": 9}
    assert _agency((await api.get("/api/network/supply", params=params)).json(), revised)["static_version_id"] == B
    # C covers all of B's days and was fetched later, so it is in force there now.
    load_static(_zip(tmp_path, C, B_FIRST, B_LAST, _C), revised, pg_conn, ch_client=ch_client)
    pg_conn.commit()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(revised, pg_conn, ch_client)
    # The supply tables were left standing by the plan: the builder's own
    # rebuild is what has to mark them rewritten.
    scopes = {r.getMessage().strip() for r in caplog.records}
    assert f"scope {stop_supply.TABLE}: unchanged" in scopes
    assert f"scope {stop_supply.DAYS_TABLE}: unchanged" in scopes
    after = _built_at(pg_conn, revised)
    assert set(after) == {stop_supply.DAYS_TABLE, stop_supply.TABLE}
    assert all(after[name] > before[name] for name in after)
    body = (await api.get("/api/network/supply", params=params)).json()
    assert _agency(body, revised)["static_version_id"] == C
    assert _stops(body, revised)["S1"]["departures"] == 1


async def test_a_stop_of_a_removed_operator_is_not_found(revised, pg_conn, ch_client, api):
    stop_supply.rebuild(pg_conn, ch_client, revised, full=True)
    pg_conn.commit()
    params = {"agency_id": revised, "stop_id": "S1", "date": "2026-02-10"}
    assert (await api.get("/api/network/supply/stop", params=params)).status_code == 200
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = %s", (revised,))
    pg_conn.commit()
    assert (await api.get("/api/network/supply/stop", params=params)).status_code == 404
