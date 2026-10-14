"""The 最新観測 baseline: a route's closed days of today's timetable class."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import jpholiday
import pytest

from api.routers.map import MIN_SAME_CLASS_BASELINE_DAYS, TODAY_BASELINE_LOOKBACK_DAYS, fetch_today_baselines
from pipeline.cache import clear_all
from pipeline.day_types import invalidate_day_types
from pipeline.histogram import LEGACY_SEVERE_LATE_TOLERANCE_SEC, percentile_from_hist
from tests.fixtures.route_dist import hist_of, seed_route_dist_day

SATURDAY = date(2026, 6, 13)
# 海の日: a public holiday on a Monday, with no other holiday in the eight weeks before it.
HOLIDAY_MONDAY = date(2026, 7, 20)


def _half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _expected(delays: list[int]) -> dict:
    late5 = sum(1 for d in delays if d > LEGACY_SEVERE_LATE_TOLERANCE_SEC)
    return {
        "baseline_avg_sec": _half_up(sum(delays) / len(delays)),
        "baseline_p90_sec": _half_up(percentile_from_hist(hist_of(delays), 0.9)),
        "baseline_samples": len(delays),
        "late5_pct": float(Decimal(late5 * 100 / len(delays)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
    }


def _weekdays_before(today: date, n: int) -> list[date]:
    out, d = [], today - timedelta(days=1)
    while len(out) < n:
        if d.isoweekday() <= 5 and not jpholiday.is_holiday(d):
            out.append(d)
        d -= timedelta(days=1)
    return out


def _weekly_before(today: date, isoweekday: int, n: int) -> list[date]:
    d = today - timedelta(days=1)
    while d.isoweekday() != isoweekday:
        d -= timedelta(days=1)
    return [d - timedelta(weeks=i) for i in range(n)]


async def _seed_calendar(conn, agency_id, exceptions=()):
    """A weekday, a Saturday and a Sunday timetable over the whole year."""
    for service_id, days in (("WEEKDAY", "1111100"), ("SATURDAY", "0000010"), ("SUNDAY", "0000001")):
        await conn.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
            "saturday, sunday, start_date, end_date, trips) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 10)",
            agency_id,
            service_id,
            *(flag == "1" for flag in days),
            date(2026, 4, 1),
            date(2027, 3, 31),
        )
    await _seed_exceptions(conn, agency_id, exceptions)
    # The calendar read is cached; rows written behind the loader's back show once it is cleared.
    clear_all()


async def _seed_exceptions(conn, agency_id, exceptions):
    """``calendar_dates.txt`` rows as ``(service_id, date, exception_type)``."""
    for service_id, day, exception_type in exceptions:
        await conn.execute(
            "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1, $2, $3, $4)",
            agency_id,
            service_id,
            day.strftime("%Y%m%d"),
            exception_type,
        )


class Connection:
    """Passes reads through to ``conn``, counting those of the baseline aggregate.

    Named like asyncpg's connection, so the caches key it as any connection,
    not as this one object.
    """

    def __init__(self, conn, reads: list[str]):
        self._conn = conn
        self._reads = reads

    async def fetch(self, sql, *args):
        if "agg_route_daily_dist" in sql:
            self._reads.append(sql)
        return await self._conn.fetch(sql, *args)


@pytest.mark.asyncio
async def test_a_saturday_is_judged_against_past_saturdays_only(aconn, aagency_id):
    saturday_delays: list[int] = []
    for i, day in enumerate(_weekly_before(SATURDAY, 6, 4)):
        delays = [240 + 30 * i, 360, 600]
        saturday_delays += delays
        await seed_route_dist_day(aconn, aagency_id, "R1", day, delays, service_type="1_土曜(共通)")
    for day in _weekdays_before(SATURDAY, 15):
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [30, 60, 90], service_type="11_平日(共通)")
    for day in _weekly_before(SATURDAY, 7, 3):
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [1200, 1500])

    baselines = await fetch_today_baselines(aconn, aagency_id, ["R1"], SATURDAY)

    assert baselines == {"R1": _expected(saturday_delays)}


@pytest.mark.asyncio
async def test_a_route_pools_its_service_ids_and_blank_within_the_class(aconn, aagency_id):
    pooled: list[int] = []
    for day in _weekly_before(SATURDAY, 6, 3):
        for service_type, delays in (("1_土曜(共通)", [120, 180]), ("1_土曜(特)", [300]), ("", [60, 0, 900])):
            pooled += delays
            await seed_route_dist_day(aconn, aagency_id, "R1", day, delays, service_type=service_type)
    for day in _weekdays_before(SATURDAY, 10):
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [1000], service_type="")

    baselines = await fetch_today_baselines(aconn, aagency_id, ["R1"], SATURDAY)

    assert baselines["R1"] == _expected(pooled)


@pytest.mark.asyncio
async def test_a_holiday_monday_the_calendar_runs_as_sunday_uses_the_sunday_pool(aconn, aagency_id):
    assert HOLIDAY_MONDAY.isoweekday() == 1 and jpholiday.is_holiday(HOLIDAY_MONDAY)
    await _seed_calendar(aconn, aagency_id, [("SUNDAY", HOLIDAY_MONDAY, 1), ("WEEKDAY", HOLIDAY_MONDAY, 2)])
    sunday_delays: list[int] = []
    for i, day in enumerate(_weekly_before(HOLIDAY_MONDAY, 7, 4)):
        delays = [400 + 20 * i, 500]
        sunday_delays += delays
        await seed_route_dist_day(aconn, aagency_id, "R1", day, delays)
    for day in _weekly_before(HOLIDAY_MONDAY, 6, 4):
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [200, 220])
    for day in _weekdays_before(HOLIDAY_MONDAY, 20):
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [10, 20])

    baselines = await fetch_today_baselines(aconn, aagency_id, ["R1"], HOLIDAY_MONDAY)

    assert baselines == {"R1": _expected(sunday_delays)}


@pytest.mark.asyncio
async def test_a_holiday_the_calendar_runs_as_a_weekday_uses_the_weekday_pool(aconn, aagency_id):
    """The calendar, not the public-holiday list, names the day's class."""
    await _seed_calendar(aconn, aagency_id)
    weekday_delays: list[int] = []
    for day in _weekdays_before(HOLIDAY_MONDAY, 5):
        weekday_delays += [10, 20]
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [10, 20])
    for day in _weekly_before(HOLIDAY_MONDAY, 7, 4):
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [400, 500])

    baselines = await fetch_today_baselines(aconn, aagency_id, ["R1"], HOLIDAY_MONDAY)

    assert baselines == {"R1": _expected(weekday_delays)}


@pytest.mark.asyncio
async def test_thin_same_class_history_falls_back_to_every_class_in_the_lookback(aconn, aagency_id):
    everything: list[int] = []
    # Fewer Saturdays than the floor; two service ids on one date are still one day.
    for day in _weekly_before(SATURDAY, 6, MIN_SAME_CLASS_BASELINE_DAYS - 1):
        for service_type in ("1_土曜(共通)", ""):
            everything += [600, 660]
            await seed_route_dist_day(aconn, aagency_id, "R1", day, [600, 660], service_type=service_type)
    for day in _weekdays_before(SATURDAY, 6):
        everything += [60, 120]
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [60, 120], service_type="11_平日(共通)")

    baselines = await fetch_today_baselines(aconn, aagency_id, ["R1"], SATURDAY)

    assert baselines == {"R1": _expected(everything)}


@pytest.mark.asyncio
async def test_only_closed_days_inside_the_lookback_count(aconn, aagency_id):
    saturdays = _weekly_before(SATURDAY, 6, 3)
    for day in saturdays:
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [100])
    too_old = SATURDAY - timedelta(weeks=TODAY_BASELINE_LOOKBACK_DAYS // 7 + 1)
    await seed_route_dist_day(aconn, aagency_id, "R1", too_old, [1700] * 50)
    await seed_route_dist_day(aconn, aagency_id, "R1", SATURDAY, [1700] * 50)

    baselines = await fetch_today_baselines(aconn, aagency_id, ["R1"], SATURDAY)

    assert baselines == {"R1": _expected([100] * len(saturdays))}


@pytest.mark.asyncio
async def test_a_route_without_history_or_not_asked_for_has_no_baseline(aconn, aagency_id):
    for day in _weekly_before(SATURDAY, 6, 3):
        await seed_route_dist_day(aconn, aagency_id, "OTHER", day, [100])

    assert await fetch_today_baselines(aconn, aagency_id, ["R1"], SATURDAY) == {}


@pytest.mark.asyncio
async def test_one_baseline_read_serves_every_poll_of_the_day(aconn, aagency_id):
    saturdays = _weekly_before(SATURDAY, 6, 3)
    for day in saturdays:
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [100])
        await seed_route_dist_day(aconn, aagency_id, "R2", day, [200])
    reads: list[str] = []

    morning = await fetch_today_baselines(Connection(aconn, reads), aagency_id, ["R1"], SATURDAY)
    # A later poll, on another connection: R2 has reported since, and the list comes in another order.
    later = await fetch_today_baselines(Connection(aconn, reads), aagency_id, ["R2", "R1"], SATURDAY)

    assert len(reads) == 1
    assert morning == {"R1": _expected([100] * len(saturdays))}
    assert later == {"R1": _expected([100] * len(saturdays)), "R2": _expected([200] * len(saturdays))}


@pytest.mark.asyncio
async def test_a_timetable_reload_that_refiles_today_is_read_again(aconn, aagency_id):
    await _seed_calendar(aconn, aagency_id)
    for day in _weekdays_before(HOLIDAY_MONDAY, 5):
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [10, 20])
    sundays = _weekly_before(HOLIDAY_MONDAY, 7, 4)
    for day in sundays:
        await seed_route_dist_day(aconn, aagency_id, "R1", day, [400, 500])
    before = await fetch_today_baselines(aconn, aagency_id, ["R1"], HOLIDAY_MONDAY)

    # The reloaded timetable runs the holiday on the Sunday timetable; a load
    # invalidates the day types, and nothing else.
    await _seed_exceptions(aconn, aagency_id, [("SUNDAY", HOLIDAY_MONDAY, 1), ("WEEKDAY", HOLIDAY_MONDAY, 2)])
    invalidate_day_types()
    after = await fetch_today_baselines(aconn, aagency_id, ["R1"], HOLIDAY_MONDAY)

    assert before == {"R1": _expected([10, 20] * 5)}
    assert after == {"R1": _expected([400, 500] * len(sundays))}
