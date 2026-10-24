"""Predict's day types from the replica's feed calendars, and its fallback to
the weekday rule where the replica has no calendar to read."""

from datetime import date

from ml.cli import _read_day_types

FIRST, LAST = date(2026, 10, 3), date(2026, 11, 7)
CULTURE_DAY = date(2026, 11, 3)


async def _seed_weekday_service(aconn, agency_id: int) -> None:
    await aconn.execute(
        "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date, trips) "
        "VALUES ($1, 'wk', true, true, true, true, true, false, false, '2026-10-01', '2027-03-31', 10)",
        agency_id,
    )


async def test_an_agency_with_a_calendar_is_typed_by_it(aconn, aagency_id):
    # The feed runs its weekday service on Culture Day with no removal for it.
    await _seed_weekday_service(aconn, aagency_id)
    types = await _read_day_types(aconn, [aagency_id], FIRST, LAST)
    assert types[aagency_id].of(CULTURE_DAY) == "weekday"


async def test_an_agency_whose_calendar_is_empty_falls_back_to_the_weekday_rule(aconn, aagency_id):
    types = await _read_day_types(aconn, [aagency_id], FIRST, LAST)
    assert types[aagency_id].of(CULTURE_DAY) == "sunday_holiday"
    assert types[aagency_id].of(date(2026, 11, 7)) == "saturday"


async def test_a_replica_without_the_calendar_table_gives_no_day_types(aconn, aagency_id):
    # pg_temp holds no static_calendar, so to_regclass resolves it as a
    # replica whose migrations predate the table does.
    async with aconn.transaction():
        await aconn.execute("SET LOCAL search_path TO pg_temp")
        assert await _read_day_types(aconn, [aagency_id], FIRST, LAST) == {}
