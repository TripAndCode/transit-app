"""Which observed route codes count as retired: gone from the current timetable,
and unobserved over the last week of the agency's data in the window."""

from datetime import date, timedelta

import pytest

from api.range import RangeCtx
from pipeline.route_catalog import retired_route_codes


async def _observe(conn, agency_id, route_code, first: date, last: date):
    day = first
    while day <= last:
        await conn.execute(
            "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
            "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
            "VALUES ($1, $2, $3, 'weekday', 60, 60, 1, 10, NOW(), 600)",
            agency_id,
            day,
            route_code,
        )
        day += timedelta(days=1)


async def _timetable(conn, agency_id, *route_ids):
    for route_id in route_ids:
        await conn.execute("INSERT INTO static_routes (agency_id, route_id) VALUES ($1, $2)", agency_id, route_id)


WINDOW = RangeCtx(from_date=date(2026, 9, 24), to_date=date(2026, 10, 23))


@pytest.mark.asyncio
async def test_an_uncatalogued_code_last_seen_weeks_before_the_data_ends_is_retired(aconn, aagency_id):
    await _timetable(aconn, aagency_id, "100")
    await _observe(aconn, aagency_id, "100", date(2026, 9, 24), date(2026, 10, 23))
    await _observe(aconn, aagency_id, "OLD", date(2026, 9, 24), date(2026, 9, 30))
    await _observe(aconn, aagency_id, "30906 0", date(2026, 10, 1), date(2026, 10, 7))
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == {"OLD", "30906 0"}


@pytest.mark.asyncio
async def test_an_uncatalogued_code_still_running_when_the_data_ends_is_kept(aconn, aagency_id):
    """The agency's data stops before the window does, so its last week is the
    week before its own last day, not the window's."""
    await _timetable(aconn, aagency_id, "100")
    await _observe(aconn, aagency_id, "100", date(2026, 9, 24), date(2026, 10, 14))
    await _observe(aconn, aagency_id, "18142", date(2026, 9, 24), date(2026, 10, 14))
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == set()


@pytest.mark.asyncio
async def test_a_timetable_route_is_never_retired_however_quiet(aconn, aagency_id):
    await _timetable(aconn, aagency_id, "100", "QUIET")
    await _observe(aconn, aagency_id, "100", date(2026, 9, 24), date(2026, 10, 23))
    await _observe(aconn, aagency_id, "QUIET", date(2026, 9, 24), date(2026, 10, 13))
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == set()


@pytest.mark.asyncio
async def test_an_aomori_style_route_id_matches_its_code(aconn, aagency_id):
    """Aomori's route ids carry the code in a parenthesised tail."""
    await _timetable(aconn, aagency_id, "国道・古川線(1021)")
    await _observe(aconn, aagency_id, "1021", date(2026, 9, 24), date(2026, 9, 30))
    await _observe(aconn, aagency_id, "OTHER", date(2026, 9, 24), date(2026, 10, 23))
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == set()


@pytest.mark.asyncio
async def test_an_agency_without_a_timetable_retires_nothing(aconn, aagency_id):
    """No timetable to compare against says nothing about any route."""
    await _observe(aconn, aagency_id, "A", date(2026, 9, 24), date(2026, 10, 23))
    await _observe(aconn, aagency_id, "B", date(2026, 9, 24), date(2026, 9, 30))
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == set()
