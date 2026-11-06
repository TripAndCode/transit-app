"""Predict's view of the replica's loaded timetable: the trips it plans on each day it covers, and the
agencies whose trip_ids are the timetable's own."""

from datetime import date

from ml.cli import _read_timetable_plans


async def _seed_weekday_service(aconn, agency_id: int) -> None:
    """Service 'wk' runs T1 and T2 on weekdays of November 2026, except Culture Day (11/3)."""
    await aconn.execute(
        "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date) "
        "VALUES ($1, 'wk', true, true, true, true, true, false, false, '2026-11-01', '2026-11-30')",
        agency_id,
    )
    await aconn.execute(
        "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) "
        "VALUES ($1, 'wk', '20261103', 2)",
        agency_id,
    )
    await aconn.execute(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) "
        "VALUES ($1, 'T1', 'R1', 'wk'), ($1, 'T2', 'R1', 'wk')",
        agency_id,
    )


async def _stamping(aconn, agency_id: int) -> None:
    await aconn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)


async def test_each_covered_day_lists_its_planned_trips(aconn, aagency_id):
    await _stamping(aconn, aagency_id)
    await _seed_weekday_service(aconn, aagency_id)
    planned, _ = await _read_timetable_plans(aconn, [aagency_id], date(2026, 10, 30), date(2026, 11, 4))
    assert planned[(aagency_id, date(2026, 11, 2))] == frozenset({"T1", "T2"})
    # Covered by the calendar, but nothing runs: a Sunday, and the removed holiday.
    assert planned[(aagency_id, date(2026, 11, 1))] == frozenset()
    assert planned[(aagency_id, date(2026, 11, 3))] == frozenset()
    # Before the calendar starts, the timetable says nothing.
    assert (aagency_id, date(2026, 10, 31)) not in planned


async def test_only_a_version_stamping_agency_is_named_and_planned(aconn, aagency_id):
    await _seed_weekday_service(aconn, aagency_id)
    planned, stamping = await _read_timetable_plans(aconn, [aagency_id], date(2026, 11, 1), date(2026, 11, 2))
    assert (planned, stamping) == ({}, set())
    await _stamping(aconn, aagency_id)
    planned, stamping = await _read_timetable_plans(aconn, [aagency_id], date(2026, 11, 1), date(2026, 11, 2))
    assert stamping == {aagency_id}
    assert planned[(aagency_id, date(2026, 11, 2))] == frozenset({"T1", "T2"})
