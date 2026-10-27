"""Which observed route codes a route list leaves out: retired ones, gone from
the current timetable and unobserved over the last week of the agency's data
in the window, and ones whose feed reports every departure exactly on time."""

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


@pytest.mark.asyncio
async def test_a_timetable_reload_clears_the_cached_set(aconn, aagency_id):
    """The set is cached; a reload that brings a code back must not wait out
    the cache, and the static fetcher clears it right after loading."""
    from pipeline.route_catalog import invalidate_retired_routes

    await _timetable(aconn, aagency_id, "100")
    await _observe(aconn, aagency_id, "100", date(2026, 9, 24), date(2026, 10, 23))
    await _observe(aconn, aagency_id, "OLD", date(2026, 9, 24), date(2026, 9, 30))
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == {"OLD"}
    await _timetable(aconn, aagency_id, "OLD")
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == {"OLD"}
    invalidate_retired_routes()
    assert await retired_route_codes(aconn, aagency_id, WINDOW) == set()


def test_the_static_fetcher_clears_the_set_after_a_load():
    import inspect

    from pipeline import static_fetcher

    source = inspect.getsource(static_fetcher)
    assert source.index("load_static(") < source.index("route_catalog.invalidate_retired_routes()")


async def _report(conn, agency_id, route_code, first: date, last: date, delays):
    """``delays`` on each day from ``first`` to ``last``, in the per-day histogram table."""
    from tests.fixtures.route_dist import seed_route_dist_day

    day = first
    while day <= last:
        await seed_route_dist_day(conn, agency_id, route_code, day, delays)
        day += timedelta(days=1)


@pytest.mark.asyncio
async def test_a_route_reporting_every_departure_exactly_on_time_has_no_delay_signal(aconn, aagency_id):
    from pipeline.route_catalog import zero_delay_route_codes

    await _report(aconn, aagency_id, "ECHO", date(2026, 9, 24), date(2026, 10, 23), [0] * 10)
    # Punctual, but a departure a minute late now and then is a signal.
    await _report(aconn, aagency_id, "PUNCTUAL", date(2026, 9, 24), date(2026, 10, 23), [0] * 9 + [60])
    assert await zero_delay_route_codes(aconn, aagency_id, WINDOW) == {"ECHO"}


@pytest.mark.asyncio
async def test_delays_that_cancel_out_are_still_a_signal(aconn, aagency_id):
    from pipeline.route_catalog import zero_delay_route_codes

    await _report(aconn, aagency_id, "EVEN", date(2026, 9, 24), date(2026, 10, 23), [-60, 60] * 5)
    assert await zero_delay_route_codes(aconn, aagency_id, WINDOW) == set()


@pytest.mark.asyncio
async def test_a_thinly_observed_route_is_not_judged(aconn, aagency_id):
    """A punctual route on a feed that reports whole minutes reads 0 most of
    the time, so a few dozen zeros in a row are chance."""
    from pipeline.route_catalog import ZERO_DELAY_MIN_SAMPLES, zero_delay_route_codes

    await _report(aconn, aagency_id, "THIN", date(2026, 10, 23), date(2026, 10, 23), [0] * (ZERO_DELAY_MIN_SAMPLES - 1))
    await _report(aconn, aagency_id, "ENOUGH", date(2026, 10, 23), date(2026, 10, 23), [0] * ZERO_DELAY_MIN_SAMPLES)
    assert await zero_delay_route_codes(aconn, aagency_id, WINDOW) == {"ENOUGH"}


@pytest.mark.asyncio
async def test_a_short_window_is_judged_over_the_weeks_before_it_ends(aconn, aagency_id):
    """A route that reported delays earlier in the lookback keeps its signal
    through a day or a week of zeros."""
    from pipeline.route_catalog import zero_delay_route_codes

    await _report(aconn, aagency_id, "REAL", date(2026, 10, 1), date(2026, 10, 15), [60] * 10)
    await _report(aconn, aagency_id, "REAL", date(2026, 10, 16), date(2026, 10, 23), [0] * 200)
    await _report(aconn, aagency_id, "ECHO", date(2026, 9, 24), date(2026, 10, 23), [0] * 10)
    for first in (date(2026, 10, 23), date(2026, 10, 17)):
        window = RangeCtx(from_date=first, to_date=date(2026, 10, 23))
        assert await zero_delay_route_codes(aconn, aagency_id, window) == {"ECHO"}, first


@pytest.mark.asyncio
async def test_a_long_window_is_judged_over_all_of_it(aconn, aagency_id):
    """Every flagged route reads exactly 0 throughout the window it is left out of."""
    from pipeline.route_catalog import zero_delay_route_codes

    await _report(aconn, aagency_id, "TURNED", date(2026, 8, 20), date(2026, 8, 25), [120] * 10)
    await _report(aconn, aagency_id, "TURNED", date(2026, 8, 26), date(2026, 10, 23), [0] * 10)
    long_window = RangeCtx(from_date=date(2026, 8, 20), to_date=date(2026, 10, 23))
    assert await zero_delay_route_codes(aconn, aagency_id, long_window) == set()
    assert await zero_delay_route_codes(aconn, aagency_id, WINDOW) == {"TURNED"}


@pytest.mark.asyncio
async def test_the_lookback_ends_on_the_agencys_last_observed_day(aconn, aagency_id):
    """The agency's data stops before the window does, so the weeks judged end
    on its own last day, not the window's."""
    from pipeline.route_catalog import zero_delay_route_codes

    await _report(aconn, aagency_id, "REAL", date(2026, 9, 18), date(2026, 9, 18), [180] * 10)
    await _report(aconn, aagency_id, "REAL", date(2026, 9, 19), date(2026, 10, 14), [0] * 10)
    window = RangeCtx(from_date=date(2026, 10, 10), to_date=date(2026, 10, 23))
    assert await zero_delay_route_codes(aconn, aagency_id, window) == set()


@pytest.mark.asyncio
async def test_a_retired_zero_delay_code_is_left_out_as_retired(aconn, aagency_id):
    """Each left-out code has one reason, so a list counts it once."""
    from pipeline.route_catalog import RETIRED, ZERO_DELAY, left_out_route_codes

    await _timetable(aconn, aagency_id, "100", "ECHO")
    for code, first, last, delay in (
        ("100", date(2026, 9, 24), date(2026, 10, 23), 60),
        ("ECHO", date(2026, 9, 24), date(2026, 10, 23), 0),
        ("OLD_ECHO", date(2026, 9, 24), date(2026, 9, 30), 0),
    ):
        await _observe(aconn, aagency_id, code, first, last)
        await _report(aconn, aagency_id, code, first, last, [delay] * 20)
    assert await left_out_route_codes(aconn, aagency_id, WINDOW) == {
        RETIRED: {"OLD_ECHO"},
        ZERO_DELAY: {"ECHO"},
    }


LAST_DAY = date(2026, 10, 23)


@pytest.mark.asyncio
async def test_a_code_retired_however_long_ago_is_retired_among_the_codes_asked_about(aconn, aagency_id):
    """``retired_among`` judges each code it is given on the agency's last
    days alone, so a code last seen years before still reads as retired, a
    current code does not, and an uncatalogued code still running is kept."""
    from pipeline.route_catalog import retired_among

    await _timetable(aconn, aagency_id, "100")
    await _observe(aconn, aagency_id, "100", date(2026, 10, 1), LAST_DAY)
    await _observe(aconn, aagency_id, "LONG_AGO", date(2024, 3, 1), date(2024, 3, 7))
    await _observe(aconn, aagency_id, "RUNNING", date(2026, 10, 17), LAST_DAY)
    codes = frozenset({"100", "LONG_AGO", "RUNNING"})
    assert await retired_among(aconn, aagency_id, codes, LAST_DAY) == {"LONG_AGO"}


@pytest.mark.asyncio
async def test_retired_among_matches_the_window_rule_at_its_edge(aconn, aagency_id):
    """A code last seen exactly RECENT_DAYS before the last day is retired,
    one seen a day later is not, the same edge retired_route_codes draws."""
    from pipeline.route_catalog import RECENT_DAYS, retired_among

    await _timetable(aconn, aagency_id, "100")
    await _observe(aconn, aagency_id, "100", date(2026, 10, 1), LAST_DAY)
    edge = LAST_DAY - timedelta(days=RECENT_DAYS)
    await _observe(aconn, aagency_id, "EDGE", date(2026, 10, 1), edge)
    await _observe(aconn, aagency_id, "INSIDE", date(2026, 10, 1), edge + timedelta(days=1))
    assert await retired_among(aconn, aagency_id, frozenset({"100", "EDGE", "INSIDE"}), LAST_DAY) == {"EDGE"}
    window = RangeCtx(from_date=date(2026, 10, 1), to_date=LAST_DAY)
    assert await retired_route_codes(aconn, aagency_id, window) == {"EDGE"}


@pytest.mark.asyncio
async def test_retired_among_retires_nothing_without_a_timetable(aconn, aagency_id):
    from pipeline.route_catalog import retired_among

    await _observe(aconn, aagency_id, "A", date(2026, 10, 1), LAST_DAY)
    await _observe(aconn, aagency_id, "B", date(2024, 3, 1), date(2024, 3, 7))
    assert await retired_among(aconn, aagency_id, frozenset({"A", "B"}), LAST_DAY) == set()


@pytest.mark.asyncio
async def test_a_timetable_reload_clears_the_cached_codes_asked_about(aconn, aagency_id):
    from pipeline.route_catalog import invalidate_retired_routes, retired_among

    await _timetable(aconn, aagency_id, "100")
    await _observe(aconn, aagency_id, "100", date(2026, 10, 1), LAST_DAY)
    await _observe(aconn, aagency_id, "OLD", date(2024, 3, 1), date(2024, 3, 7))
    assert await retired_among(aconn, aagency_id, frozenset({"100", "OLD"}), LAST_DAY) == {"OLD"}
    await _timetable(aconn, aagency_id, "OLD")
    invalidate_retired_routes()
    assert await retired_among(aconn, aagency_id, frozenset({"100", "OLD"}), LAST_DAY) == set()
