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
from datetime import date, timedelta

import httpx
import pytest
from httpx import ASGITransport

from api.routers.timetable_revisions import UNAVAILABLE_CSV
from pipeline import static_archive
from pipeline.reports.timetable_diff import Unavailable
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


def _dates_only(first: date, last: date) -> str:
    """calendar_dates.txt alone, listing the dates each service runs: the weekday rule, holidays ignored."""
    rows, day = [], first
    while day <= last:
        service = WEEKDAY if day.weekday() < 5 else SATURDAY if day.weekday() == 5 else SUNDAY
        rows.append(f"{service},{day:%Y%m%d},1")
        day += timedelta(days=1)
    return "service_id,date,exception_type\n" + "\n".join(rows)


def _zip(
    tmp_path, stem: str, first: date, last: date, trips: dict[str, tuple[str, str]], *, dates_only: bool = False
) -> str:
    flags = {WEEKDAY: "1,1,1,1,1,0,0", SATURDAY: "0,0,0,0,0,1,0", SUNDAY: "0,0,0,0,0,0,1"}
    calendar = (
        {"calendar_dates.txt": _dates_only(first, last)}
        if dates_only
        else {
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            + "\n".join(f"{svc},{days},{first:%Y%m%d},{last:%Y%m%d}" for svc, days in flags.items())
        }
    )
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
        **calendar,
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


async def test_the_diff_lists_one_day_types_stops_when_asked_and_keeps_every_total(revised, api):
    body = (await api.get(f"/api/{revised}/timetable-revisions/diff?day_type=saturday")).json()
    assert {s["day_type"] for s in body["stops"]} == {"saturday"}
    assert [s["day_type"] for s in body["day_types"]] == ["weekday", "saturday", "sunday_holiday"]


async def test_lang_applies_only_to_the_csv(revised, api):
    response = await api.get(f"/api/{revised}/timetable-revisions/diff?lang=en")
    assert response.status_code == 400


def _csv_rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))


@pytest.mark.parametrize(
    ("lang", "message"),
    [
        ("ja", "改正の前後どちらかの運行日カレンダーが保存されていないため、比べられません。"),
        (
            "en",
            "The service calendar of the timetable before or after the revision is not kept, so it cannot be compared.",
        ),
    ],
)
async def test_a_csv_with_no_comparison_names_the_revision_and_says_why_in_the_readers_language(
    revised, ch_client, api, lang, message
):
    assert UNAVAILABLE_CSV[(Unavailable.NO_CALENDAR, lang)] == message
    ch_client.command(
        f"ALTER TABLE {static_archive.CALENDAR} DELETE WHERE agency_id = {int(revised)} "
        f"AND static_version_id = '{B}' SETTINGS mutations_sync = 1"
    )
    rows = _csv_rows(await api.get(f"/api/{revised}/timetable-revisions/diff?format=csv&lang={lang}"))
    assert A in rows[0][0] and B in rows[0][0]
    assert rows[-1] == [message]


@pytest.mark.parametrize(
    ("lang", "message"),
    [
        ("ja", "この事業者の時刻表には、比べられる改正がまだありません。"),
        ("en", "This agency's timetables have no revision to compare yet."),
    ],
)
async def test_a_csv_with_no_revision_says_so_in_the_readers_language(agency_id, ch_async_client, api, lang, message):
    rows = _csv_rows(await api.get(f"/api/{agency_id}/timetable-revisions/diff?format=csv&lang={lang}"))
    assert rows[-1] == [message]
    assert rows[0][0] == "停留所ID"


async def test_a_calendar_dates_only_timetable_that_drops_sunday_reads_as_a_known_empty_sunday(
    pg_conn, ch_client, agency_id, tmp_path, api
):
    load_static(_zip(tmp_path, A, date(2026, 1, 1), date(2026, 12, 31), _A), agency_id, pg_conn, ch_client=ch_client)
    no_sunday = {t: trip for t, trip in _B.items() if trip[0] != SUNDAY}
    zip_b = _zip(tmp_path, B, REVISION, date(2026, 6, 30), no_sunday, dates_only=True)
    load_static(zip_b, agency_id, pg_conn, ch_client=ch_client)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    pg_conn.commit()
    # B's span ends 06-30, after which A is in force again: read the revision B starts.
    body = (await api.get(f"/api/{agency_id}/timetable-revisions/diff?on={REVISION.isoformat()}")).json()
    sunday = next(s for s in body["day_types"] if s["day_type"] == "sunday_holiday")
    assert sunday["after_date"] == "2026-04-05"
    assert (sunday["departures_before"], sunday["departures_after"]) == (3, 0)
    assert sunday["stops_ended"] == 3


async def test_day_type_applies_only_to_the_json(revised, api):
    response = await api.get(f"/api/{revised}/timetable-revisions/diff?day_type=saturday&format=csv")
    assert response.status_code == 400


async def test_a_face_value_after_midnight_departure_is_the_nights_last_bus(
    pg_conn, ch_client, agency_id, tmp_path, api
):
    # 00:30 and 00:10 written at face value are the same night's 24:30 and 24:10.
    before = {"w1": (WEEKDAY, "07:00"), "n1": (WEEKDAY, "00:30")}
    after = {"w1": (WEEKDAY, "07:00"), "n1": (WEEKDAY, "00:10")}
    load_static(
        _zip(tmp_path, A, date(2026, 1, 1), date(2026, 12, 31), before), agency_id, pg_conn, ch_client=ch_client
    )
    load_static(_zip(tmp_path, B, REVISION, date(2027, 3, 31), after), agency_id, pg_conn, ch_client=ch_client)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    pg_conn.commit()
    body = (await api.get(f"/api/{agency_id}/timetable-revisions/diff")).json()
    s1 = next(s for s in body["stops"] if s["stop_id"] == "S1" and s["day_type"] == "weekday")
    assert (s1["first_before"], s1["first_after"]) == (7 * 3600, 7 * 3600)
    assert (s1["last_before"], s1["last_after"]) == (24 * 3600 + 30 * 60, 24 * 3600 + 10 * 60)
    assert s1["last_shift_min"] == -20
    assert s1["hours"] == []


def _new_gaps_url(agency_id: int, query: str) -> str:
    return f"/api/{agency_id}/timetable-revisions/new-gaps?{query}"


async def test_new_gaps_list_the_stops_whose_longest_gap_in_the_band_crossed_the_threshold(revised, api):
    # Between 21:00 and 23:00, S1's last weekday trip moves from 21:50 to 21:10,
    # so the stretch after it grows from 70 to 110 minutes; S2 and S3 follow
    # two and four minutes later.
    body = (await api.get(_new_gaps_url(revised, "from_hour=21&to_hour=23&gap_min=90"))).json()
    assert body["available"] is True
    assert (body["from_hour"], body["to_hour"], body["gap_min"]) == (21, 23, 90)
    gaps = [(s["stop_id"], s["day_type"], s["longest_gap_before"], s["longest_gap_after"]) for s in body["stops"]]
    assert gaps == [
        ("S1", "weekday", 70 * 60, 110 * 60),
        ("S2", "weekday", 68 * 60, 108 * 60),
        ("S3", "weekday", 66 * 60, 106 * 60),
    ]
    s1 = body["stops"][0]
    assert (s1["stop_name"], s1["departures_before"], s1["departures_after"]) == ("S1前", 1, 1)
    counts = [(d["day_type"], d["stops"]) for d in body["day_types"]]
    assert counts == [("weekday", 3), ("saturday", 0), ("sunday_holiday", 0)]


async def test_a_gap_exactly_at_the_threshold_before_the_revision_was_not_over_it(revised, api):
    # The 09:00 Saturday trip is dropped. Over 08:00-10:00, S1's departure at
    # 09:00 left two 60-minute stretches; S2's and S3's at 09:02 and 09:04 had
    # already left a stretch over 60 minutes.
    body = (await api.get(_new_gaps_url(revised, "from_hour=8&to_hour=10&gap_min=60"))).json()
    gaps = [
        (s["stop_id"], s["day_type"], s["longest_gap_before"], s["longest_gap_after"], s["departures_after"])
        for s in body["stops"]
    ]
    assert gaps == [("S1", "saturday", 60 * 60, 120 * 60, 0)]


async def test_new_gaps_list_one_day_type_when_asked_and_keep_every_count(revised, api):
    body = (await api.get(_new_gaps_url(revised, "from_hour=21&to_hour=23&gap_min=90&day_type=saturday"))).json()
    assert body["stops"] == []
    counts = [(d["day_type"], d["stops"]) for d in body["day_types"]]
    assert counts == [("weekday", 3), ("saturday", 0), ("sunday_holiday", 0)]


async def test_a_day_type_either_side_has_no_typical_day_of_has_no_count(pg_conn, ch_client, agency_id, tmp_path, api):
    load_static(_zip(tmp_path, A, date(2026, 1, 1), date(2026, 12, 31), _A), agency_id, pg_conn, ch_client=ch_client)
    # B's calendar spans Wednesday to Friday, so it has no Saturday or Sunday.
    load_static(_zip(tmp_path, B, REVISION, date(2026, 4, 3), _B), agency_id, pg_conn, ch_client=ch_client)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    pg_conn.commit()
    query = f"on={REVISION.isoformat()}&from_hour=21&to_hour=23&gap_min=90"
    body = (await api.get(_new_gaps_url(agency_id, query))).json()
    counts = [(d["day_type"], d["stops"]) for d in body["day_types"]]
    assert counts == [("weekday", 3), ("saturday", None), ("sunday_holiday", None)]


async def test_new_gaps_export_as_csv_with_the_versions_typical_days_and_criterion(revised, api):
    query = f"on={REVISION.isoformat()}&from_hour=21&to_hour=23&gap_min=90&format=csv"
    response = await api.get(_new_gaps_url(revised, query))
    assert response.status_code == 200
    assert "timetable_revision_new_gaps_2026-04-01.csv" in response.headers["content-disposition"]
    rows = _csv_rows(response)
    assert A in rows[0][0] and B in rows[0][0]
    assert "2026-03-31 → 2026-04-01" in rows[1][0]
    assert rows[2] == [
        "新たな運行空白：21:00〜23:00 の発車のない最も長い間隔が、改正前は90分以下で、改正後は90分を超えた停留所"
    ]
    header = rows.index(next(r for r in rows if r and r[0] == "停留所ID"))
    data = [dict(zip(rows[header], r, strict=True)) for r in rows[header + 1 :]]
    assert [
        (r["停留所ID"], r["曜日区分"], r["改正前の最も長い間隔(分)"], r["改正後の最も長い間隔(分)"]) for r in data
    ] == [("S1", "平日", "70", "110"), ("S2", "平日", "68", "108"), ("S3", "平日", "66", "106")]
    assert (data[0]["停留所名"], data[0]["改正後の発車数(時間帯内)"]) == ("S1前", "1")


async def test_a_new_gaps_csv_with_no_comparison_states_its_criterion_and_says_why(revised, ch_client, api):
    ch_client.command(
        f"ALTER TABLE {static_archive.CALENDAR} DELETE WHERE agency_id = {int(revised)} "
        f"AND static_version_id = '{B}' SETTINGS mutations_sync = 1"
    )
    body = (await api.get(_new_gaps_url(revised, "gap_min=90"))).json()
    assert (body["available"], body["reason"], body["gap_min"]) == (False, "no_calendar", 90)
    rows = _csv_rows(await api.get(_new_gaps_url(revised, "format=csv&lang=en")))
    assert A in rows[0][0] and B in rows[0][0]
    assert rows[1][0].startswith("新たな運行空白：10:00〜16:00")
    assert rows[-1] == [UNAVAILABLE_CSV[(Unavailable.NO_CALENDAR, "en")]]


@pytest.mark.parametrize(("query", "status"), [("lang=en", 400), ("day_type=saturday&format=csv", 400)])
async def test_new_gaps_take_lang_and_day_type_as_the_diff_does(revised, api, query, status):
    assert (await api.get(_new_gaps_url(revised, query))).status_code == status


@pytest.mark.parametrize(
    "query",
    [
        "from_hour=16&to_hour=10",
        # No gap exceeds the band, so a threshold as long as it lists nothing.
        "from_hour=10&to_hour=11&gap_min=60",
        "from_hour=3",
        "to_hour=29",
        "gap_min=0",
    ],
)
async def test_a_band_or_threshold_that_can_list_nothing_is_rejected(revised, api, query):
    assert (await api.get(_new_gaps_url(revised, query))).status_code == 422
