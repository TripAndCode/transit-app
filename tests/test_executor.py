from datetime import datetime, time

import pytest

from pipeline.query.executor import execute


@pytest.mark.asyncio
async def test_execute_unknown_intent_returns_empty(aconn, aagency_id):
    intent = {"query_type": "ranking", "unknown": True}
    rows = await execute(intent, aconn, aagency_id)
    assert rows == []


@pytest.mark.asyncio
async def test_execute_invalid_query_type_returns_empty(aconn, aagency_id):
    intent = {"query_type": "does_not_exist", "unknown": False}
    rows = await execute(intent, aconn, aagency_id)
    assert rows == []


@pytest.mark.asyncio
async def test_execute_ranking_empty_db(aconn, aagency_id):
    intent = {"query_type": "ranking", "unknown": False, "limit": 5, "sort_order": "desc"}
    rows = await execute(intent, aconn, aagency_id)
    assert isinstance(rows, list)
    assert rows == []  # no data seeded


@pytest.mark.asyncio
async def test_execute_static_required_no_static_returns_none(aconn, aagency_id):
    intent = {"query_type": "stop_list", "unknown": False, "route": "44372"}
    result = await execute(intent, aconn, aagency_id)
    assert result is None  # static not loaded


@pytest.mark.asyncio
async def test_execute_returns_tuples(aconn, aagency_id):
    # Seed 25 rows so HAVING COUNT(*) > 20 passes
    for i in range(25):
        await aconn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            aagency_id,
            f"f{i}.pb",
            datetime(2026, 4, (i % 28) + 1, 11, 37, 0),
            "平日_11時37分_系統44372",
            "平日",
            time(11, 37),  # Updated 2026-05-22: TIME column (was "11:37" text).
            "44372",
            1,
            120 + i * 10,
        )
    intent = {"query_type": "ranking", "unknown": False, "limit": 5, "sort_order": "desc"}
    rows = await execute(intent, aconn, aagency_id)
    assert len(rows) > 0
    assert isinstance(rows[0], tuple)


@pytest.mark.asyncio
async def test_execute_agency_isolation(aconn, aagency_id):
    # Create a second agency
    row = await aconn.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1,$2) RETURNING agency_id",
        "Other Agency",
        "http://other.example.com",
    )
    other_id = row["agency_id"]
    # Seed data for other agency only
    for i in range(25):
        await aconn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            other_id,
            f"f{i}.pb",
            datetime(2026, 4, (i % 28) + 1, 11, 37, 0),
            "平日_11時37分_系統44372",
            "平日",
            time(11, 37),  # Updated 2026-05-22: TIME column (was "11:37" text).
            "44372",
            1,
            999,
        )
    intent = {"query_type": "ranking", "unknown": False, "limit": 5, "sort_order": "desc"}
    rows = await execute(intent, aconn, aagency_id)
    # aagency_id has no data, should return empty
    assert rows == []


@pytest.mark.asyncio
async def test_exec_by_hour_service_and_time_band(aconn, aagency_id):
    """Bug 1 regression: by_hour with service + time_band must not crash."""
    # Seed agg_route_stats so _agg_loaded() returns True (it checks this table)
    await aconn.execute(
        "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p50_min, "
        "p90_min, late_5min_plus, on_time_pct, late5_pct, samples) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        aagency_id,
        "44372",
        "平日",
        2.5,
        2.0,
        5.0,
        0,
        80.0,
        5.0,
        50,
    )
    await aconn.execute(
        "INSERT INTO agg_route_hour (agency_id, route_code, service_type, scheduled_time, "
        "avg_min, p50_min, p90_min, samples) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        aagency_id,
        "44372",
        "平日",
        time(8, 0),  # Updated 2026-05-22: TIME column (was "08:00" text).
        2.5,
        2.0,
        5.0,
        50,
    )
    intent = {
        "query_type": "by_hour",
        "route": "44372",
        "service": "平日",
        "time_band": "morning",
        "sort_order": "desc",
        "unknown": False,
    }
    rows = await execute(intent, aconn, aagency_id)
    assert isinstance(rows, list)
    # The seeded row IS in morning band (08:00 is between 05:00 and 10:00)
    assert len(rows) > 0


@pytest.mark.asyncio
async def test_exec_on_time_with_route_and_service_raw(aconn, aagency_id):
    """Bug 2 regression: on_time with route + service must not crash on raw path."""
    for i in range(12):
        await aconn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            aagency_id,
            f"on{i}.pb",
            datetime(2026, 4, i + 1, 8, 0, 0),
            "平日_08時00分_系統44372",
            "平日",
            time(8, 0),  # Updated 2026-05-22: TIME column (was "08:00" text).
            "44372",
            1,
            30 + i * 10,
        )
    intent = {
        "query_type": "on_time",
        "route": "44372",
        "service": "平日",
        "sort_order": "desc",
        "limit": 5,
        "unknown": False,
    }
    rows = await execute(intent, aconn, aagency_id)
    assert isinstance(rows, list)
