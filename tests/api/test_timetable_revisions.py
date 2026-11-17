"""The timetable revision endpoints read two archived timetables and compare
them stop by stop (api.routers.timetable_revisions).

Timetable A is in force until 2026-03-31 and B from 2026-04-01. Each trip
serves S1 to S4, two minutes apart, so S4, where every trip ends, departs
nothing. B moves the 07:30 weekday trip to 08:10, pulls the last weekday
trip from 21:50 to 21:10, and drops the Saturday trip while keeping the
Saturday service in its calendar. The Sunday trip runs alike in both.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import httpx
import pytest
from httpx import ASGITransport

from pipeline import static_archive
from pipeline.static_loader import load_static

STOPS = ("S1", "S2", "S3", "S4")
WEEKDAY, SATURDAY, SUNDAY = "平日", "土曜", "日祝"
A, B = "gtfs_static_20251201", "gtfs_static_20260315"
REVISION = date(2026, 4, 1)

# trip_id: (service, first departure)
_A = {"w1": (WEEKDAY, "07:00"), "w2": (WEEKDAY, "07:30"), "w3": (WEEKDAY, "21:50"), "s1": (SATURDAY, "09:00")}
_A["h1"] = (SUNDAY, "10:00")
_B = {"w1": (WEEKDAY, "07:00"), "w2": (WEEKDAY, "08:10"), "w3": (WEEKDAY, "21:10"), "h1": (SUNDAY, "10:00")}


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
    load_static(_zip(tmp_path, A, date(2026, 1, 1), date(2026, 12, 31), _A), agency_id, pg_conn, ch_client=ch_client)
    load_static(_zip(tmp_path, B, REVISION, date(2027, 3, 31), _B), agency_id, pg_conn, ch_client=ch_client)
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


async def test_the_revision_list_names_the_day_b_took_over(revised, api):
    response = await api.get(f"/api/{revised}/timetable-revisions")
    assert response.status_code == 200
    [revision] = response.json()["revisions"]
    assert revision["first_day"] == REVISION.isoformat()
    assert (revision["before"]["version"], revision["before"]["last_day"]) == (A, "2026-03-31")
    assert (revision["after"]["version"], revision["after"]["first_day"]) == (B, REVISION.isoformat())
    assert len(revision["after"]["content"]) == 8


async def test_the_diff_lists_each_stops_lost_and_gained_hours_last_bus_and_ended_weekend(revised, api):
    body = (await api.get(f"/api/{revised}/timetable-revisions/diff")).json()
    assert body["available"] is True
    by_type = {s["day_type"]: s for s in body["day_types"]}
    assert (by_type["weekday"]["before_date"], by_type["weekday"]["after_date"]) == ("2026-03-31", "2026-04-01")
    assert (by_type["saturday"]["before_date"], by_type["saturday"]["after_date"]) == ("2026-03-28", "2026-04-04")
    # Three stops depart on each trip; S4 ends every trip.
    assert (by_type["weekday"]["departures_before"], by_type["weekday"]["departures_after"]) == (9, 9)
    assert (by_type["saturday"]["departures_before"], by_type["saturday"]["departures_after"]) == (3, 0)
    assert by_type["saturday"]["stops_ended"] == 3
    assert by_type["weekday"]["last_earlier"] == 3
    assert by_type["sunday_holiday"]["stops_ended"] == 0

    stops = {(s["stop_id"], s["day_type"]): s for s in body["stops"]}
    assert ("S4", "weekday") not in stops
    assert ("S1", "sunday_holiday") not in stops
    s1 = stops[("S1", "weekday")]
    assert s1["stop_name"] == "S1前"
    assert (s1["before"], s1["after"], s1["lost"], s1["gained"]) == (3, 3, 1, 1)
    assert [(h["hour"], h["before"], h["after"]) for h in s1["hours"]] == [(7, 2, 1), (8, 0, 1)]
    assert s1["last_shift_min"] == -40
    saturday = stops[("S2", "saturday")]
    assert saturday["ended"] is True and saturday["after"] == 0


async def test_the_diff_exports_its_stops_as_csv_with_the_versions_and_typical_days(revised, api):
    response = await api.get(f"/api/{revised}/timetable-revisions/diff?on={REVISION.isoformat()}&format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    assert A in rows[0][0] and B in rows[0][0]
    assert "2026-03-28 → 2026-04-04" in rows[1][0]
    header = rows.index(next(r for r in rows if r and r[0] == "停留所ID"))
    data = [dict(zip(rows[header], r, strict=True)) for r in rows[header + 1 :]]
    s1 = next(r for r in data if r["停留所ID"] == "S1" and r["曜日区分"] == "平日")
    assert (s1["改正前の最終"], s1["改正後の最終"], s1["最終の変化(分)"]) == ("21:50", "21:10", "-40")
    assert s1["時間帯ごとの増減"] == "7時台 -1; 8時台 +1"
    assert {r["停留所ID"] for r in data if r["発車がなくなった"] == "はい"} == {"S1", "S2", "S3"}


async def test_a_day_no_revision_starts_on_is_not_found(revised, api):
    response = await api.get(f"/api/{revised}/timetable-revisions/diff?on=2026-04-02")
    assert response.status_code == 404


async def test_an_agency_with_one_timetable_has_no_revision(pg_conn, ch_client, agency_id, tmp_path, api):
    load_static(_zip(tmp_path, A, date(2026, 1, 1), date(2026, 12, 31), _A), agency_id, pg_conn, ch_client=ch_client)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    pg_conn.commit()
    assert (await api.get(f"/api/{agency_id}/timetable-revisions")).json() == {"revisions": []}
    body = (await api.get(f"/api/{agency_id}/timetable-revisions/diff")).json()
    assert (body["available"], body["reason"]) == (False, "no_revision")


async def test_a_side_whose_calendar_is_not_kept_is_unavailable_not_all_cut(revised, ch_client, api):
    ch_client.command(
        f"ALTER TABLE {static_archive.CALENDAR} DELETE WHERE agency_id = {int(revised)} "
        f"AND static_version_id = '{B}' SETTINGS mutations_sync = 1"
    )
    body = (await api.get(f"/api/{revised}/timetable-revisions/diff")).json()
    assert (body["available"], body["reason"]) == (False, "no_calendar")
    assert body["stops"] == []
