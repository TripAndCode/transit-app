import pytest
import httpx
import asyncpg
import os
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def reports_app(apply_schema):
    from api.main import app
    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Reports Test Agency", "http://reports-test.example.com",
    )
    agency_id = row["agency_id"]
    yield app, agency_id, pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, static_stops, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, "
            "agg_route_stats, agg_route_hour, agg_route_dow, "
            "agg_daily_trend, agg_stop_seq, rag_chunks, api_keys, snapshots CASCADE"
        )
    await pool.close()


@pytest.fixture
async def reports_client(reports_app):
    app, agency_id, pool = reports_app
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, agency_id, pool


@pytest.mark.asyncio
async def test_reports_list_empty_before_analyze(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_reports_list_returns_inserted_snapshot(reports_client):
    client, agency_id, pool = reports_client
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO snapshots (agency_id, report_type, rendered_at, text) "
            "VALUES ($1, $2, NOW(), $3)",
            agency_id, "ranking",
            "【遅延ランキング上位100系統】\n1位: 系統44 平均4.2分（平日3.1分・土日祝6.8分、計1204件）",
        )
    resp = await client.get(f"/api/{agency_id}/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["report_type"] == "ranking"
    assert "rendered_at" in data[0]


@pytest.mark.asyncio
async def test_reports_get_unknown_type_returns_404(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/nonexistent_type")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reports_get_returns_text_and_rows(reports_client):
    client, agency_id, pool = reports_client
    snapshot_text = (
        "【遅延ランキング上位100系統】\n"
        "1位: 系統44 平均4.2分（平日3.1分・土日祝6.8分、計1204件）\n"
        "2位: 系統22 平均3.1分（平日2.5分・土日祝4.0分、計500件）"
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO snapshots (agency_id, report_type, rendered_at, text) "
            "VALUES ($1, $2, NOW(), $3)",
            agency_id, "ranking", snapshot_text,
        )
    resp = await client.get(f"/api/{agency_id}/reports/ranking")
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "ranking"
    assert data["text"] == snapshot_text
    assert "rendered_at" in data
    assert data["rows"] == []  # no agg data seeded; live query returns empty


@pytest.mark.asyncio
async def test_reports_get_limit_truncates_text(reports_client):
    client, agency_id, pool = reports_client
    lines = ["【遅延ランキング上位100系統】"] + [
        f"{i}位: 系統{i} 平均{i}.0分（平日{i}.0分・土日祝{i}.0分、計100件）"
        for i in range(1, 11)
    ]
    snapshot_text = "\n".join(lines)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO snapshots (agency_id, report_type, rendered_at, text) "
            "VALUES ($1, $2, NOW(), $3)",
            agency_id, "ranking", snapshot_text,
        )
    resp = await client.get(f"/api/{agency_id}/reports/ranking?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    returned_lines = data["text"].split("\n")
    assert len(returned_lines) == 4  # header + 3 data lines
    assert returned_lines[0] == "【遅延ランキング上位100系統】"
    assert "1位" in returned_lines[1]
    assert "3位" in returned_lines[3]


@pytest.mark.asyncio
async def test_reports_unknown_agency_returns_404(reports_client):
    client, _, _ = reports_client
    resp = await client.get("/api/99999/reports")
    assert resp.status_code == 404
