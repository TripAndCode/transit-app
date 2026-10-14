"""The scope controls' summary: one read of agg_route_daily_dist per section."""

from datetime import date, timedelta

import pytest

from api.range import RangeCtx
from pipeline.reports.scope_summary import compute_scope_summary

N_BUCKETS = 37


def _hist(bucket: int, n: int) -> list[int]:
    h = [0] * N_BUCKETS
    h[bucket] = n
    return h


async def _seed(aconn, agency_id, route_code, day, samples, sum_delay_sec, hist, service="平日", on_time=0):
    await aconn.execute(
        "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, "
        "samples, sum_delay_sec, on_time_count, late5_count, hist) VALUES ($1,$2,$3,$4,$5,$6,$7,0,$8)",
        agency_id,
        day,
        route_code,
        service,
        samples,
        sum_delay_sec,
        on_time,
        hist,
    )


@pytest.fixture
async def seeded(aconn, aagency_id):
    # R1: every day 9/1..9/28 at 1.0 min, all of it in the [60, 120) bucket,
    # 40 of each day's 100 departures at exactly one minute (on time).
    for i in range(28):
        await _seed(aconn, aagency_id, "R1", date(2026, 9, 1) + timedelta(days=i), 100, 6000, _hist(7, 100), on_time=40)
    # R2: one Saturday at 5.0 min, in the [300, 360) bucket.
    await _seed(aconn, aagency_id, "R2", date(2026, 9, 26), 50, 15000, _hist(11, 50))
    return aconn, aagency_id


def _ctx(**kw) -> RangeCtx:
    return RangeCtx(from_date=date(2026, 9, 20), to_date=date(2026, 9, 28), **kw)


@pytest.mark.asyncio
async def test_spans_the_agencys_data(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(), conn)
    assert body["earliest"] == date(2026, 9, 1)
    assert body["latest"] == date(2026, 9, 28)


@pytest.mark.asyncio
async def test_days_ignore_the_period_and_the_weekday_filter(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(dow="weekday"), conn)
    days = {d["date"]: d for d in body["days"]}
    assert len(days) == 28
    assert days[date(2026, 9, 1)]["avg_min"] == 1.0
    assert days[date(2026, 9, 26)] == {"date": date(2026, 9, 26), "avg_min": 2.33, "samples": 150}


@pytest.mark.asyncio
async def test_days_cover_at_most_the_last_ninety_days(aconn, aagency_id):
    for i in range(120):
        await _seed(aconn, aagency_id, "R1", date(2026, 6, 1) + timedelta(days=i), 10, 600, _hist(7, 10))
    body = await compute_scope_summary(aagency_id, _ctx(), aconn)
    assert len(body["days"]) == 90
    assert body["days"][-1]["date"] == date(2026, 9, 28)
    assert body["earliest"] == date(2026, 6, 1)
    assert body["window_from"] == date(2026, 7, 1)


@pytest.mark.asyncio
async def test_the_window_starts_at_the_first_data_day_when_data_is_young(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(), conn)
    assert body["window_from"] == date(2026, 9, 1)


@pytest.mark.asyncio
async def test_weekdays_cover_the_period_and_ignore_the_weekday_filter(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(dow="weekday"), conn)
    weekdays = {w["dow"]: w for w in body["weekdays"]}
    assert weekdays["sat"] == {"dow": "sat", "avg_min": 2.33, "samples": 150}
    assert weekdays["mon"]["avg_min"] == 1.0
    assert weekdays["mon"]["samples"] == 200  # 9/21 and 9/28


@pytest.mark.asyncio
async def test_routes_cover_the_period_and_ignore_the_routes_filter(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(routes=("R1",)), conn)
    routes = {r["route_code"]: r for r in body["routes"]}
    assert routes["R1"]["avg_min"] == 1.0
    assert routes["R2"] == {"route_code": "R2", "avg_min": 5.0, "samples": 50}


@pytest.mark.asyncio
async def test_tolerance_shares_follow_the_whole_scope(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(dow="weekday"), conn)
    shares = {t["late_sec"]: t["on_time_pct"] for t in body["tolerance"]}
    assert sorted(shares) == list(range(0, 601, 60))
    assert shares[0] == 0.0
    assert shares[120] == 100.0


@pytest.mark.asyncio
async def test_the_one_minute_share_is_the_exact_on_time_figure(seeded):
    # Feeds report many delays as whole minutes, which sit on a histogram
    # bucket's lower edge; the one-minute step reads the exact count instead,
    # so it matches the on-time figure the screens show.
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(dow="weekday"), conn)
    assert {t["late_sec"]: t["on_time_pct"] for t in body["tolerance"]}[60] == 40.0


@pytest.mark.asyncio
async def test_tolerance_honours_an_early_bound(seeded):
    conn, agency_id = seeded
    # R3 runs two to three minutes early, which only an early bound excludes.
    await _seed(conn, agency_id, "R3", date(2026, 9, 21), 50, -7500, _hist(3, 50))
    unbounded = await compute_scope_summary(agency_id, _ctx(), conn)
    bounded = await compute_scope_summary(agency_id, _ctx(), conn, early_sec=120)
    assert {t["late_sec"]: t["on_time_pct"] for t in unbounded["tolerance"]}[600] == 100.0
    assert {t["late_sec"]: t["on_time_pct"] for t in bounded["tolerance"]}[600] < 100.0
    assert {t["late_sec"]: t["on_time_pct"] for t in bounded["tolerance"]}[60] < 100.0


@pytest.mark.asyncio
async def test_every_section_honours_the_timetable_days(seeded):
    """The 平日 filter keeps the dates the agency runs its weekday timetable
    whatever service_type the rows carry; with no calendar loaded that is the
    weekday rule, under which the public holidays 9/21-9/23 are 日祝."""
    conn, agency_id = seeded
    await _seed(conn, agency_id, "R9", date(2026, 9, 27), 80, 48000, _hist(11, 80), service="")
    body = await compute_scope_summary(agency_id, _ctx(service="平日"), conn)
    assert {r["route_code"] for r in body["routes"]} == {"R1"}
    # 9/24 (Thu), 9/25 (Fri) and 9/28 (Mon) are the period's weekdays.
    assert {w["dow"] for w in body["weekdays"]} == {"mon", "thu", "fri"}
    days = {d["date"] for d in body["days"]}
    assert date(2026, 9, 24) in days and date(2026, 9, 1) in days
    assert not days & {date(2026, 9, 21), date(2026, 9, 26), date(2026, 9, 27)}
    assert all(d.isoweekday() <= 5 for d in days)

    weekend = await compute_scope_summary(agency_id, _ctx(service="土日祝"), conn)
    assert {r["route_code"] for r in weekend["routes"]} == {"R1", "R2", "R9"}
    assert {d["date"] for d in weekend["days"]} >= {date(2026, 9, 21), date(2026, 9, 26), date(2026, 9, 27)}


@pytest.mark.asyncio
async def test_day_types_are_the_buckets_the_calendar_splits_the_period_into(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(), conn)
    assert body["day_types"] == ["weekday", "saturday", "sunday_holiday"]
    for service_id, days in (("平日", "1111100"), ("土日祝", "0000011")):
        await conn.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
            "saturday, sunday, start_date, end_date, trips) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 1)",
            agency_id,
            service_id,
            *(flag == "1" for flag in days),
            date(2026, 4, 1),
            date(2027, 3, 31),
        )
    # The calendar is cached like the reports it feeds, so rows written behind
    # the loader's back show only once the cache is cleared.
    from pipeline.cache import clear_all

    clear_all()
    body = await compute_scope_summary(agency_id, _ctx(), conn)
    assert body["day_types"] == ["weekday", "weekend_holiday"]


@pytest.mark.asyncio
async def test_an_agency_without_aggregates_answers_empty(aconn, aagency_id):
    body = await compute_scope_summary(aagency_id, _ctx(), aconn)
    assert body == {
        "earliest": None,
        "latest": None,
        "window_from": None,
        "days": [],
        "weekdays": [],
        "routes": [],
        "tolerance": [],
        "day_types": ["weekday", "saturday", "sunday_holiday"],
    }


@pytest.mark.asyncio
async def test_the_endpoint_answers_with_the_summary_and_its_scope(client, seeded):
    _, agency_id = seeded
    resp = await client.get(f"/api/{agency_id}/scope/summary?from=2026-09-20&to=2026-09-28&dow=weekday")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["earliest"] == "2026-09-01"
    assert body["latest"] == "2026-09-28"
    assert len(body["days"]) == 28
    assert {w["dow"] for w in body["weekdays"]} >= {"sat", "mon"}
    assert body["tolerance"][2] == {"late_sec": 120, "on_time_pct": 100.0}
    assert body["ctx"]["from"] == "2026-09-20"
    assert body["ctx"]["dow"] == "weekday"
    assert body["window_from"] == "2026-09-01"
    applied = body["scope_applied"]
    assert applied["from"] is True and applied["routes"] is True and applied["early"] is True
    assert applied["time_band"] is False and applied["hour"] is False and applied["late"] is False


@pytest.mark.asyncio
async def test_the_endpoint_resolves_the_service_filter_to_the_agencys_days(client, seeded):
    conn, agency_id = seeded
    await _seed(conn, agency_id, "R9", date(2026, 9, 27), 80, 48000, _hist(11, 80), service="")
    resp = await client.get(
        f"/api/{agency_id}/scope/summary", params={"from": "2026-09-20", "to": "2026-09-28", "service": "日祝"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {r["route_code"] for r in body["routes"]} == {"R1", "R9"}
    assert body["ctx"]["service"] == "日祝"
    assert body["day_types"] == ["weekday", "saturday", "sunday_holiday"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("early", "status"), [(0, 200), (3600, 200), (3601, 422), (-1, 422)])
async def test_the_endpoint_bounds_the_early_tolerance(client, seeded, early, status):
    _, agency_id = seeded
    resp = await client.get(f"/api/{agency_id}/scope/summary?from=2026-09-20&to=2026-09-28&early={early}")
    assert resp.status_code == status, resp.text


@pytest.mark.asyncio
async def test_the_endpoint_404s_an_unknown_agency(client, aconn):
    resp = await client.get("/api/999999/scope/summary")
    assert resp.status_code == 404


async def _seed_weekend_split_change(aconn, agency_id):
    """土曜 and 日祝 timetables until Sunday 9/27, one 土日祝 timetable from 9/28.

    The window 10/5-10/18 alone runs two ways; a span reaching back before
    9/28 runs three ways, so the response must pick one split for every section.
    """
    services = [
        ("平日", "1111100", date(2026, 4, 1), date(2027, 3, 31)),
        ("土曜", "0000010", date(2026, 4, 1), date(2026, 9, 27)),
        ("日祝", "0000001", date(2026, 4, 1), date(2026, 9, 27)),
        ("土日祝", "0000011", date(2026, 9, 28), date(2027, 3, 31)),
    ]
    for service_id, days, start, end in services:
        await aconn.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
            "saturday, sunday, start_date, end_date, trips) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 1)",
            agency_id,
            service_id,
            *(flag == "1" for flag in days),
            start,
            end,
        )


@pytest.mark.asyncio
async def test_every_section_reads_one_day_type_resolution(aconn, aagency_id):
    """The 90-day strip reaches back into the three-way weeks, so the whole
    summary splits the agency three ways: the control's choices, the strip and
    the weekday section all file Sunday 10/11 outside 土曜."""
    await _seed_weekend_split_change(aconn, aagency_id)
    for day in (date(2026, 9, 12), date(2026, 10, 6), date(2026, 10, 10), date(2026, 10, 11)):
        await _seed(aconn, aagency_id, "R1", day, 100, 6000, _hist(7, 100))
    ctx = RangeCtx(from_date=date(2026, 10, 5), to_date=date(2026, 10, 18), service="土曜")
    body = await compute_scope_summary(aagency_id, ctx, aconn)
    assert body["day_types"] == ["weekday", "saturday", "sunday_holiday"]
    assert {d["date"] for d in body["days"]} == {date(2026, 9, 12), date(2026, 10, 10)}
    assert {w["dow"] for w in body["weekdays"]} == {"sat"}
