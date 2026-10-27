"""Planned trips from the static calendar: the weekly ``calendar.txt`` services
plus the ``calendar_dates.txt`` additions, minus its removals.

April 2026 starts on a Wednesday: the 6th and 13th are Mondays, the 12th and
19th Sundays. ``static_calendar.trips`` is left at its default of 0 throughout,
so a count read from that column rather than from ``static_trips`` fails.
"""

from datetime import date

import pytest

from pipeline.reports.service_delivered import compute_service_delivered_by_agency


async def _agency(aconn, name: str) -> int:
    return await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id", name, f"http://{name}"
    )


async def _trips(aconn, agency_id: int, service_id: str, count: int) -> None:
    for i in range(count):
        await aconn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1, $2, 'R1', $3)",
            agency_id,
            f"{service_id}-{i}",
            service_id,
        )


async def _weekly(aconn, agency_id: int, service_id: str, days: str, start: date, end: date) -> None:
    """One ``calendar.txt`` row; ``days`` is its Monday-first ``"1111100"`` flags."""
    await aconn.execute(
        "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        agency_id,
        service_id,
        *(flag == "1" for flag in days),
        start,
        end,
    )


async def _exception(aconn, agency_id: int, service_id: str, gtfs_date: str, exception_type: int) -> None:
    """One ``calendar_dates.txt`` row, its date the feed's raw text."""
    await aconn.execute(
        "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1, $2, $3, $4)",
        agency_id,
        service_id,
        gtfs_date,
        exception_type,
    )


async def _planned(aconn, agency_ids: list[int], from_date: date, to_date: date) -> dict[int, int]:
    result = await compute_service_delivered_by_agency(aconn, agency_ids, from_date, to_date)
    return {aid: row["planned_trips"] for aid, row in result.items()}


async def test_weekly_service_counts_each_matching_weekday(aconn):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "WD", "1111100", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, a, "WD", 3)
    await _weekly(aconn, a, "SU", "0000001", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, a, "SU", 2)

    # Monday 6th to Sunday 19th: ten weekdays and two Sundays.
    assert await _planned(aconn, [a], date(2026, 4, 6), date(2026, 4, 19)) == {a: 10 * 3 + 2 * 2}


async def test_removal_takes_the_day_out_of_a_weekly_service(aconn):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "WD", "1111100", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, a, "WD", 3)
    await _exception(aconn, a, "WD", "20260408", 2)

    assert await _planned(aconn, [a], date(2026, 4, 6), date(2026, 4, 10)) == {a: 4 * 3}


async def test_addition_on_a_day_the_weekly_flags_skip_adds_it(aconn):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "WD", "1111100", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, a, "WD", 3)
    await _exception(aconn, a, "WD", "20260411", 1)  # a Saturday

    assert await _planned(aconn, [a], date(2026, 4, 6), date(2026, 4, 12)) == {a: 6 * 3}


async def test_addition_on_a_day_the_weekly_service_already_runs_counts_once(aconn):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "WD", "1111100", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, a, "WD", 3)
    await _exception(aconn, a, "WD", "20260407", 1)  # a Tuesday

    assert await _planned(aconn, [a], date(2026, 4, 6), date(2026, 4, 10)) == {a: 5 * 3}


@pytest.mark.parametrize(
    ("from_date", "to_date", "days"),
    [
        (date(2026, 4, 1), date(2026, 4, 30), 8),  # the whole service, both ends inclusive
        (date(2026, 4, 1), date(2026, 4, 8), 1),  # the range ends on start_date
        (date(2026, 4, 15), date(2026, 4, 30), 1),  # the range starts on end_date
        (date(2026, 4, 10), date(2026, 4, 12), 3),  # inside the service
        (date(2026, 4, 1), date(2026, 4, 7), 0),  # before it
        (date(2026, 4, 16), date(2026, 4, 30), 0),  # after it
    ],
)
async def test_weekly_service_is_clipped_to_its_date_range(aconn, from_date, to_date, days):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "ALL", "1111111", date(2026, 4, 8), date(2026, 4, 15))
    await _trips(aconn, a, "ALL", 2)

    assert await _planned(aconn, [a], from_date, to_date) == {a: days * 2}


async def test_calendar_dates_only_feed_counts_its_additions(aconn):
    a = await _agency(aconn, "a")
    await _trips(aconn, a, "X", 4)
    await _exception(aconn, a, "X", "20260401", 1)
    await _exception(aconn, a, "X", "20260402", 1)
    await _exception(aconn, a, "X", "20260501", 1)  # outside the range
    await _exception(aconn, a, "X", "20260403", 2)  # a removal of a day it never ran

    assert await _planned(aconn, [a], date(2026, 4, 1), date(2026, 4, 30)) == {a: 2 * 4}


async def test_malformed_calendar_dates_rows_count_nothing(aconn):
    a = await _agency(aconn, "a")
    await _trips(aconn, a, "X", 4)
    await _exception(aconn, a, "X", "2026041", 1)  # sorts between 20260401 and 20260430
    await _exception(aconn, a, "X", "garbage", 1)

    assert await _planned(aconn, [a], date(2026, 4, 1), date(2026, 4, 30)) == {a: 0}


async def test_service_with_no_trips_counts_nothing(aconn):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "WD", "1111100", date(2026, 4, 1), date(2026, 4, 30))

    assert await _planned(aconn, [a], date(2026, 4, 6), date(2026, 4, 10)) == {a: 0}


async def test_another_agencys_calendar_and_trips_do_not_leak_in(aconn):
    a = await _agency(aconn, "a")
    b = await _agency(aconn, "b")
    # Both agencies name their service WD; only b runs it weekly.
    await _trips(aconn, a, "WD", 3)
    await _exception(aconn, a, "WD", "20260406", 1)
    await _weekly(aconn, b, "WD", "1111111", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, b, "WD", 5)
    await _exception(aconn, b, "WD", "20260407", 2)

    assert await _planned(aconn, [a], date(2026, 4, 6), date(2026, 4, 12)) == {a: 3}
    assert await _planned(aconn, [a, b], date(2026, 4, 6), date(2026, 4, 12)) == {a: 3, b: 6 * 5}


async def test_counts_only_the_days_a_timetable_covers_and_says_how_many(aconn):
    """A timetable loaded mid-window plans only the days it covers. The
    non-executed trips are counted over the same days, so the ratio compares
    like with like, and the result says how much of the window it covers."""
    from tests.conftest import confirm_rt_field_coverage

    a = await _agency(aconn, "a")
    await aconn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", a)
    await confirm_rt_field_coverage(aconn, a)
    # Every day from Monday the 13th; the window opens on Monday the 6th.
    await _weekly(aconn, a, "ALL", "1111111", date(2026, 4, 13), date(2026, 6, 30))
    await _trips(aconn, a, "ALL", 10)
    for day, missed in ((date(2026, 4, 8), 4), (date(2026, 4, 15), 2)):
        await aconn.execute(
            "INSERT INTO agg_service_delivered_daily (agency_id, date, non_executed_trips) VALUES ($1, $2, $3)",
            a,
            day,
            missed,
        )

    result = (await compute_service_delivered_by_agency(aconn, [a], date(2026, 4, 6), date(2026, 4, 19)))[a]
    assert result["planned_trips"] == 7 * 10
    # The 8th predates the timetable, so its missed trips are not taken from
    # a plan that never counted that day.
    assert result["executed_trips"] == 70 - 2
    assert result["covered_days"] == 7
    assert result["window_days"] == 14


async def test_a_window_inside_the_timetable_is_fully_covered(aconn):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "WD", "1111100", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, a, "WD", 3)
    result = (await compute_service_delivered_by_agency(aconn, [a], date(2026, 4, 6), date(2026, 4, 19)))[a]
    # Weekends carry no trips but are still covered by the timetable.
    assert result["covered_days"] == result["window_days"] == 14


async def _saved(aconn, agency_id: int, day: date, planned: int) -> None:
    await aconn.execute(
        "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips) VALUES ($1, $2, $3)",
        agency_id,
        day,
        planned,
    )


async def test_a_saved_day_counts_where_the_loaded_timetable_no_longer_covers_it(aconn):
    """A reload replaced the timetable on the 13th, after the days before it
    were saved. Those days plan from their saved counts, count toward the
    covered days, and have their non-executed trips subtracted."""
    from tests.conftest import confirm_rt_field_coverage

    a = await _agency(aconn, "a")
    await aconn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", a)
    await confirm_rt_field_coverage(aconn, a)
    await _weekly(aconn, a, "ALL", "1111111", date(2026, 4, 13), date(2026, 6, 30))
    await _trips(aconn, a, "ALL", 10)
    for offset in range(7):
        await _saved(aconn, a, date(2026, 4, 6 + offset), 8)
    for day, missed in ((date(2026, 4, 8), 4), (date(2026, 4, 15), 2)):
        await aconn.execute(
            "INSERT INTO agg_service_delivered_daily (agency_id, date, non_executed_trips) VALUES ($1, $2, $3)",
            a,
            day,
            missed,
        )

    result = (await compute_service_delivered_by_agency(aconn, [a], date(2026, 4, 6), date(2026, 4, 19)))[a]
    assert result["planned_trips"] == 7 * 8 + 7 * 10
    assert result["executed_trips"] == 7 * 8 + 7 * 10 - 4 - 2
    assert result["covered_days"] == result["window_days"] == 14


async def test_a_saved_day_wins_over_the_loaded_timetable(aconn):
    a = await _agency(aconn, "a")
    await _weekly(aconn, a, "ALL", "1111111", date(2026, 4, 1), date(2026, 4, 30))
    await _trips(aconn, a, "ALL", 10)
    await _saved(aconn, a, date(2026, 4, 7), 4)

    result = (await compute_service_delivered_by_agency(aconn, [a], date(2026, 4, 6), date(2026, 4, 8)))[a]
    assert result["planned_trips"] == 10 + 4 + 10
    assert result["covered_days"] == 3


async def test_saved_days_outside_the_window_or_of_another_agency_do_not_count(aconn):
    a = await _agency(aconn, "a")
    b = await _agency(aconn, "b")
    await _saved(aconn, a, date(2026, 4, 5), 8)
    await _saved(aconn, a, date(2026, 4, 6), 8)
    await _saved(aconn, b, date(2026, 4, 7), 9)

    result = await compute_service_delivered_by_agency(aconn, [a, b], date(2026, 4, 6), date(2026, 4, 6))
    assert (result[a]["planned_trips"], result[a]["covered_days"]) == (8, 1)
    assert (result[b]["planned_trips"], result[b]["covered_days"]) == (0, 0)
