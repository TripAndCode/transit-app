"""The diagnostics drawer's ``calendar_until``: the last date the loaded static
calendar runs any service, from ``calendar.txt`` and ``calendar_dates.txt``
alike."""

from datetime import date

from api import agency_diagnostics as ad


async def _current_version(aconn) -> int:
    """An agency with one recorded static version, loaded as its current one."""
    agency_id = await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ('a', 'http://a') RETURNING agency_id"
    )
    await aconn.execute(
        "INSERT INTO agg_static_version_summary (agency_id, static_version_id, trip_count) VALUES ($1, 'v1', 1)",
        agency_id,
    )
    await aconn.execute(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id, static_version_id) "
        "VALUES ($1, 'T1', 'R1', 'WD', 'v1')",
        agency_id,
    )
    return agency_id


async def _weekly(aconn, agency_id: int, service_id: str, end: date) -> None:
    await aconn.execute(
        "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date) VALUES ($1, $2, true, true, true, true, true, false, false, $3, $4)",
        agency_id,
        service_id,
        date(2026, 4, 1),
        end,
    )


async def _exception(aconn, agency_id: int, service_id: str, gtfs_date: str, exception_type: int) -> None:
    await aconn.execute(
        "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1, $2, $3, $4)",
        agency_id,
        service_id,
        gtfs_date,
        exception_type,
    )


async def _calendar_until(aconn, agency_id: int) -> tuple[str | None, str | None]:
    """The raw column, then the drawer's rendering of it."""
    rows = [dict(r) for r in await aconn.fetch(ad.STATIC_VERSIONS_SQL, agency_id)]
    assert len(rows) == 1 and rows[0]["is_current"]
    return rows[0]["calendar_until"], ad.build_static_versions(rows)[0]["calendar_until"]


async def test_calendar_until_of_a_weekly_calendar_feed_is_its_last_end_date(aconn):
    a = await _current_version(aconn)
    await _weekly(aconn, a, "WD", date(2027, 3, 31))
    await _weekly(aconn, a, "SA", date(2026, 12, 31))

    assert await _calendar_until(aconn, a) == ("20270331", "2027-03-31")


async def test_calendar_until_of_a_mixed_feed_takes_a_later_addition(aconn):
    a = await _current_version(aconn)
    await _weekly(aconn, a, "WD", date(2027, 3, 31))
    await _exception(aconn, a, "WD", "20270405", 1)

    assert await _calendar_until(aconn, a) == ("20270405", "2027-04-05")


async def test_calendar_until_of_a_mixed_feed_ignores_removals_and_malformed_dates(aconn):
    a = await _current_version(aconn)
    await _weekly(aconn, a, "WD", date(2027, 3, 31))
    await _exception(aconn, a, "WD", "20270105", 1)
    await _exception(aconn, a, "WD", "20270601", 2)  # removing a day past the calendar extends nothing
    await _exception(aconn, a, "WD", "garbage", 1)  # sorts after every YYYYMMDD

    assert await _calendar_until(aconn, a) == ("20270331", "2027-03-31")


async def test_calendar_until_of_a_calendar_dates_only_feed_is_its_last_addition(aconn):
    a = await _current_version(aconn)
    await _exception(aconn, a, "WD", "20260401", 1)
    await _exception(aconn, a, "WD", "20260930", 1)

    assert await _calendar_until(aconn, a) == ("20260930", "2026-09-30")
