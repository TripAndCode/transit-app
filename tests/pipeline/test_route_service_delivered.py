"""A route's service-delivered rate: the route's planned trips, less the trips
the feed reported as not run under it, over the days whose plan is split by
route. Its routes reconcile with the agency's rate: on the days every route
is counted, their planned trips and trips not run sum to the agency's.

April 2026 starts on a Wednesday: the 6th is a Monday.
"""

import json
from datetime import date, timedelta

from pipeline.reports.service_delivered import (
    compute_service_delivered_by_agency,
    compute_service_delivered_by_route,
)
from tests.conftest import confirm_rt_field_coverage

FIRST, LAST = date(2026, 4, 6), date(2026, 4, 10)


async def _reporting_agency(aconn) -> int:
    agency_id = await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES ('a', 'http://a', 'static_join') "
        "RETURNING agency_id"
    )
    await confirm_rt_field_coverage(aconn, agency_id)
    return agency_id


async def _timetable(aconn, agency_id: int, trips: dict[str | None, int], start: date = date(2026, 4, 1)) -> None:
    """A weekday service from *start* to the 30th with *trips* per route_id."""
    await aconn.execute(
        "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date) VALUES ($1, 'WD', true, true, true, true, true, false, false, $2, $3)",
        agency_id,
        start,
        date(2026, 4, 30),
    )
    for route_id, count in trips.items():
        for i in range(count):
            await aconn.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1, $2, $3, 'WD')",
                agency_id,
                f"{route_id}-{i}",
                route_id,
            )


async def _missed(aconn, agency_id: int, day: date, route_code: str, count: int) -> None:
    await aconn.execute(
        "INSERT INTO agg_service_delivered_daily (agency_id, date, route_code, non_executed_trips) "
        "VALUES ($1, $2, $3, $4)",
        agency_id,
        day,
        route_code,
        count,
    )


async def _observed(aconn, agency_id: int, day: date, route_code: str, samples: int) -> None:
    await aconn.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        "trips_observed, samples, last_seen_at) VALUES ($1, $2, $3, '', 0, 0, 1, $4, NOW())",
        agency_id,
        day,
        route_code,
        samples,
    )


async def _saved(aconn, agency_id: int, day: date, planned: int, by_route: dict[str, int] | None) -> None:
    await aconn.execute(
        "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips, planned_trips_by_route) "
        "VALUES ($1, $2, $3, $4::jsonb)",
        agency_id,
        day,
        planned,
        None if by_route is None else json.dumps(by_route),
    )


def _days():
    return [FIRST + timedelta(days=i) for i in range((LAST - FIRST).days + 1)]


async def test_each_route_is_read_on_its_own_trips_and_the_routes_sum_to_the_agency(aconn):
    a = await _reporting_agency(aconn)
    await _timetable(aconn, a, {"R1": 30, "R2": 10})
    for day in _days():
        await _observed(aconn, a, day, "R1", 40)
    await _missed(aconn, a, date(2026, 4, 7), "R1", 3)
    await _missed(aconn, a, date(2026, 4, 8), "R2", 5)
    await _missed(aconn, a, date(2026, 4, 8), "", 1)

    reading = await compute_service_delivered_by_route(aconn, a, FIRST, LAST)
    agency = (await compute_service_delivered_by_agency(aconn, [a], FIRST, LAST))[a]

    assert reading.delivered_missing is None
    assert reading.split_known is True
    r1, r2 = reading.routes["R1"], reading.routes["R2"]
    assert (r1.planned_trips, r1.service_delivered_pct, r1.observations) == (150, 98.0, 200)
    assert (r2.planned_trips, r2.service_delivered_pct, r2.observations) == (50, 90.0, 0)
    # The trip no report named a route for counts for the agency, under no route.
    assert (reading.routes[""].planned_trips, reading.routes[""].service_delivered_pct) == (0, None)
    assert sum(r.planned_trips for r in reading.routes.values()) == agency["planned_trips"] == 200
    not_run = await aconn.fetchval(
        "SELECT SUM(non_executed_trips) FROM agg_service_delivered_daily WHERE agency_id = $1", a
    )
    assert agency["executed_trips"] == 200 - not_run == 200 - (3 + 5 + 1)


async def test_a_saved_split_wins_over_the_loaded_timetable_on_its_day(aconn):
    a = await _reporting_agency(aconn)
    await _timetable(aconn, a, {"R1": 30, "R2": 10})
    await _saved(aconn, a, FIRST, 25, {"R1": 20, "R9": 5})

    reading = await compute_service_delivered_by_route(aconn, a, FIRST, LAST)
    agency = (await compute_service_delivered_by_agency(aconn, [a], FIRST, LAST))[a]

    assert reading.split_known is True
    assert reading.routes["R1"].planned_trips == 20 + 4 * 30
    assert reading.routes["R2"].planned_trips == 4 * 10
    assert reading.routes["R9"].planned_trips == 5
    assert sum(r.planned_trips for r in reading.routes.values()) == agency["planned_trips"]


async def test_a_day_saved_without_its_split_leaves_the_routes_unread(aconn):
    """A planned day whose split was never saved cannot be attributed: its
    timetable may be gone. Each route's figure would then cover fewer days
    than the period, so none is read."""
    a = await _reporting_agency(aconn)
    await _timetable(aconn, a, {"R1": 30})
    await _saved(aconn, a, FIRST, 30, None)

    reading = await compute_service_delivered_by_route(aconn, a, FIRST, LAST)

    assert reading.split_known is False
    assert (reading.plan_days, reading.split_days) == (5, 4)


async def test_a_day_saved_planning_nothing_needs_no_split(aconn):
    a = await _reporting_agency(aconn)
    await _timetable(aconn, a, {"R1": 30})
    await _saved(aconn, a, FIRST, 0, None)

    reading = await compute_service_delivered_by_route(aconn, a, FIRST, LAST)

    assert reading.split_known is True
    assert reading.routes["R1"].planned_trips == 4 * 30


async def test_trips_not_run_count_only_on_planned_days(aconn):
    a = await _reporting_agency(aconn)
    await _timetable(aconn, a, {"R1": 30}, start=date(2026, 4, 7))
    await _missed(aconn, a, date(2026, 4, 7), "R1", 2)
    # Before the window, and on a day the window holds but no plan covers.
    await _missed(aconn, a, date(2026, 4, 3), "R1", 7)
    await _missed(aconn, a, FIRST, "R1", 4)

    reading = await compute_service_delivered_by_route(aconn, a, FIRST, LAST)

    assert reading.plan_days == 4
    assert reading.routes["R1"].planned_trips == 4 * 30
    assert reading.routes["R1"].service_delivered_pct == round((4 * 30 - 2) / (4 * 30) * 100, 1)


async def test_the_agencys_reason_stands_for_every_route(aconn):
    """A route's rate needs everything the agency's does: a feed that has
    reported a trip not run, among them."""
    a = await _reporting_agency(aconn)
    await _timetable(aconn, a, {"R1": 30})

    reading = await compute_service_delivered_by_route(aconn, a, FIRST, LAST)

    assert reading.delivered_missing == "feed_reports_no_cancellations"
