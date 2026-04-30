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
            aagency_id, f"f{i}.pb", f"2026-04-{(i%28)+1:02d}T11:37:00",
            "平日_11時37分_系統44372", "平日", "11:37", "44372", 1, 120 + i * 10,
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
        "Other Agency", "http://other.example.com",
    )
    other_id = row["agency_id"]
    # Seed data for other agency only
    for i in range(25):
        await aconn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            other_id, f"f{i}.pb", f"2026-04-{(i%28)+1:02d}T11:37:00",
            "平日_11時37分_系統44372", "平日", "11:37", "44372", 1, 999,
        )
    intent = {"query_type": "ranking", "unknown": False, "limit": 5, "sort_order": "desc"}
    rows = await execute(intent, aconn, aagency_id)
    # aagency_id has no data, should return empty
    assert rows == []
