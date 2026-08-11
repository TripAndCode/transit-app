"""SQL helpers backing the LLM tool surface: by_dow per route, compare per route, route_info static metadata."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from api.range import RangeCtx
from pipeline.query.tool_queries import (
    route_compare_service,
    route_dow_breakdown,
    route_info,
)


@pytest.mark.asyncio
async def test_route_dow_breakdown_returns_per_dow_rows(aconn, aagency_id, ch_client, ch_async_client):
    """Three observations across two DOWs for one route. Helper should
    collapse to one row per (service_type, DOW).

    Task 8.5: ``route_dow_breakdown`` always reads live ``updates`` from
    ClickHouse now (there is no agg-table fast path for it), so this seeds
    Postgres `updates` (for readability / consistency with other fixtures)
    then mirrors into ClickHouse before calling the helper with a real `ch`.

    Timestamps are anchored to noon UTC on a known Monday + Tuesday so
    ``toDayOfWeek`` resolves the same DOW regardless of session timezone
    (CI runs UTC; dev machines may run JST).
    """
    today_utc = datetime.now(timezone.utc).date()
    monday_date = today_utc - timedelta(days=today_utc.weekday() + 7)
    monday = datetime.combine(monday_date, time(12, 0), tzinfo=timezone.utc)
    tuesday = monday + timedelta(days=1)
    rows = [
        # (file_name, captured_at, dep_delay)
        ("pb_mon_1", monday, 60),
        ("pb_mon_2", monday + timedelta(hours=1), 120),
        ("pb_tue_1", tuesday, 90),
    ]
    for fname, cap, dep in rows:
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_x', '平日', '10:00', 'R1', 1, $4)",
            aagency_id,
            fname,
            cap,
            dep,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)
    ctx = RangeCtx(from_date=monday_date - timedelta(days=1), to_date=today_utc + timedelta(days=1))
    result = await route_dow_breakdown(aagency_id, ctx, aconn, ch_async_client, route="R1")
    # Expect 2 rows: one for Monday (DOW=1), one for Tuesday (DOW=2).
    dows = {r[2] for r in result}
    assert dows == {1, 2}, f"expected ISODOW set {{1, 2}}, got {dows}"
    # Each row has shape (route_code, service_type, dow, avg_min, samples).
    assert all(r[0] == "R1" for r in result)


@pytest.mark.asyncio
async def test_route_dow_breakdown_half_up_rounding_at_exact_boundary(aconn, aagency_id, ch_client, ch_async_client):
    """Fix 8c regression: ``round(avg(dep_delay) / 60.0, 2)`` was computed in
    ClickHouse SQL, which rounds half-to-even (banker's rounding). Postgres'
    numeric ROUND() (and this codebase's Decimal(ROUND_HALF_UP) helpers, e.g.
    pipeline.reports.rankings._round2) round half away from zero instead. 12
    rows at 127s + 12 rows at 128s average to exactly 127.5s = 2.125min — an
    exact .5 boundary at the 3rd decimal. Half-up rounds to 2.13; ClickHouse's
    native round() would give 2.12.
    """
    # Dedup keys on (route_code, service_type, scheduled_time, trip_id,
    # captured_at::date, stop_sequence) — vary stop_sequence per row so all
    # 24 rows survive dedup as distinct "stop events" instead of collapsing
    # to the single latest-observation row (they'd otherwise share every
    # other dedup-key column: same trip_id/date/route/service/sched_time).
    day = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    for i, dep in enumerate([127] * 12 + [128] * 12):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_half', '平日', '10:00', 'R_HALF', $4, $5)",
            aagency_id,
            f"pb_half_{i}",
            day,
            i + 1,
            dep,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)
    ctx = RangeCtx(from_date=day.date() - timedelta(days=1), to_date=day.date() + timedelta(days=1))
    result = await route_dow_breakdown(aagency_id, ctx, aconn, ch_async_client, route="R_HALF")
    assert len(result) == 1
    avg_min = result[0][3]
    assert str(avg_min) == "2.13"


@pytest.mark.asyncio
async def test_route_compare_service_half_up_rounding_at_exact_boundary(aconn, aagency_id, ch_client, ch_async_client):
    """Same fix 8c regression as test_route_dow_breakdown_half_up_rounding_at_exact_boundary,
    for route_compare_service's identical inline ``round(avg(dep_delay) / 60.0, 2)``.
    """
    # See test_route_dow_breakdown_half_up_rounding_at_exact_boundary for why
    # stop_sequence must vary per row (dedup-key collision otherwise).
    now = datetime.now(timezone(timedelta(hours=9)))
    for i, dep in enumerate([127] * 12 + [128] * 12):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_half2', '平日', '10:00', 'R_HALF2', $4, $5)",
            aagency_id,
            f"pb_half2_{i}",
            now,
            i + 1,
            dep,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)
    today = date.today()
    ctx = RangeCtx(from_date=today - timedelta(days=1), to_date=today + timedelta(days=1))
    result = await route_compare_service(aagency_id, ctx, aconn, ch_async_client, route="R_HALF2")
    assert len(result) == 1
    avg_min = result[0][1]
    assert str(avg_min) == "2.13"


@pytest.mark.asyncio
async def test_route_dow_breakdown_returns_empty_without_ch(aconn, aagency_id):
    """No ClickHouse client attached (``ch=None``, the default) -> empty
    result rather than raising — the same "safe default" convention
    ``pipeline.query.tools._is_route_registered`` already uses, needed so
    callers/tests that only exercise routing logic don't need a real client."""
    ctx = RangeCtx(from_date=date(2020, 1, 1), to_date=date(2020, 1, 2))
    result = await route_dow_breakdown(aagency_id, ctx, aconn, route="R1")
    assert result == []


@pytest.mark.asyncio
async def test_route_compare_service_returns_per_service_type(aconn, aagency_id, ch_client, ch_async_client):
    """One row per service_type for one route (Task 8.5: live ClickHouse path)."""
    now = datetime.now(timezone(timedelta(hours=9)))
    rows = [
        ("pb_h", now, "平日", 60),
        ("pb_k", now, "土日祝", 180),
    ]
    for fname, cap, svc, dep in rows:
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, $4 || '_trip', $4, '10:00', 'R2', 1, $5)",
            aagency_id,
            fname,
            cap,
            svc,
            dep,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)
    today = date.today()
    ctx = RangeCtx(from_date=today - timedelta(days=1), to_date=today + timedelta(days=1))
    result = await route_compare_service(aagency_id, ctx, aconn, ch_async_client, route="R2")
    services = {r[0] for r in result}
    assert services == {"平日", "土日祝"}


@pytest.mark.asyncio
async def test_route_info_returns_static_metadata(aconn, aagency_id):
    """Helper joins static_routes + static_trips + static_stop_times."""
    await aconn.execute(
        "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, 'route_X (R3)', 'Test Route')",
        aagency_id,
    )
    await aconn.execute(
        "INSERT INTO static_trips (agency_id, trip_id, route_id) VALUES ($1, 'trip_a', 'route_X (R3)')",
        aagency_id,
    )
    await aconn.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name) "
        "VALUES ($1, 'stop_1', 'First Stop'), ($1, 'stop_2', 'Last Stop')",
        aagency_id,
    )
    await aconn.execute(
        "INSERT INTO static_stop_times "
        "(agency_id, trip_id, stop_sequence, stop_id, departure_time) "
        "VALUES "
        "  ($1, 'trip_a', 1, 'stop_1', '08:00:00'),"
        "  ($1, 'trip_a', 2, 'stop_2', '08:30:00')",
        aagency_id,
    )
    result = await route_info(aagency_id, aconn, route="R3")
    assert result is not None
    # (route_id, route_short_name, stop_count, first_dep, last_dep, trip_count)
    assert result[0] == "route_X (R3)"
    assert result[1] == "Test Route"
    assert result[2] == 2  # stop_count
    assert result[3] == "08:00:00"  # first_dep
    assert result[4] == "08:30:00"  # last_dep
    assert result[5] == 1  # trip_count


@pytest.mark.asyncio
async def test_route_info_returns_none_when_route_missing(aconn, aagency_id):
    result = await route_info(aagency_id, aconn, route="NOPE")
    assert result is None
