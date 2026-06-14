"""Tests for the v2 reports endpoints (live queries, no snapshots table)."""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def reports_app(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Reports Test Agency",
        "http://reports-test.example.com",
    )
    agency_id = row["agency_id"]
    yield app, agency_id, pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, static_stops, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, "
            "agg_route_stats, agg_route_hour, agg_route_dow, "
            "agg_daily_trend, agg_route_daily_dist, agg_stop_seq, rag_chunks, api_keys CASCADE"
        )
    await pool.close()


def _run_analyze(agency_id):
    """Build the agg_* tables (incl. agg_route_daily_dist) from seeded updates."""
    import os

    import psycopg2

    from pipeline.analyze import analyze

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        analyze(agency_id, conn)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
async def reports_client(reports_app):
    app, agency_id, pool = reports_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id, pool


@pytest.mark.asyncio
async def test_reports_list_returns_static_metadata(reports_client):
    """The list endpoint returns the canonical 8 report types regardless of data."""
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports")
    assert resp.status_code == 200
    data = resp.json()
    types = {r["report_type"] for r in data}
    assert types == {
        "ranking",
        "ranking_best",
        "on_time",
        "worst_5min",
        "trend",
        "compare_ranking",
        "dow_weekend",
        "dow_weekday",
    }
    for r in data:
        assert "rendered_at" in r


@pytest.mark.asyncio
async def test_reports_get_unknown_type_returns_404(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/nonexistent_type")
    assert resp.status_code == 404


async def _seed_route(pool, agency_id, route_code, service_type, day, delays):
    """Insert one update per delay (distinct trips so dedup keeps them all)."""
    from datetime import datetime, time

    async with pool.acquire() as conn:
        for i, d in enumerate(delays):
            await conn.execute(
                "INSERT INTO updates "
                "(agency_id, trip_id, route_code, service_type, scheduled_time, "
                " stop_sequence, dep_delay, captured_at, file_name) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                agency_id,
                f"{route_code}-trip-{i}",
                route_code,
                service_type,
                time(10, 0),
                1,
                d,
                datetime.fromisoformat(f"{day}T10:{i // 60:02d}:{i % 60:02d}"),
                f"test/{route_code}/{i}.pb",
            )


@pytest.mark.asyncio
async def test_reports_get_ranking_reads_agg(reports_client):
    """ranking now reads agg_route_daily_dist; seed updates → analyze → render.

    HAVING COUNT(*) > 20, so seed 25 rows for route 44 across distinct trips.
    """
    client, agency_id, pool = reports_client
    day = "2026-05-01"
    await _seed_route(pool, agency_id, "44", "平日", day, [300] * 25)
    _run_analyze(agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "ranking"
    assert any(r[0] == "44" for r in data["rows"])  # row index 0 = route_code


@pytest.mark.asyncio
async def test_ranking_agg_values_exact_avg_and_approx_pct(reports_client):
    """avg/samples are exact from the aggregate; p50/p90 interpolate from the
    histogram (within one bucket of the true value)."""
    client, agency_id, pool = reports_client
    day = "2026-05-02"
    # 30 samples, all 120s late: avg = 2.0 min exactly; percentiles ~2 min.
    await _seed_route(pool, agency_id, "R1", "平日", day, [120] * 30)
    _run_analyze(agency_id)
    rows = await compute_ranking_rows(client, agency_id, day)
    r = next(x for x in rows if x[0] == "R1")
    # (route, service, avg_min, p50_min, p90_min, samples)
    assert r[1] == "平日"
    assert float(r[2]) == 2.0  # exact mean
    assert r[5] == 30  # exact samples
    # 120s falls in the [120,180) bucket -> interpolated p50/p90 in [2.0, 3.0) min
    assert 2.0 <= float(r[3]) < 3.0
    assert 2.0 <= float(r[4]) < 3.0


@pytest.mark.asyncio
async def test_on_time_and_worst_5min_exact_from_agg(reports_client):
    """on_time_pct and late5_count are exact (thresholds baked at analyze time)."""
    client, agency_id, pool = reports_client
    day = "2026-05-03"
    # 30 samples: 18 on-time (<=60s), 12 very late (>300s = worst_5min).
    await _seed_route(pool, agency_id, "R2", "平日", day, [30] * 18 + [600] * 12)
    _run_analyze(agency_id)

    ot = (await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}")).json()["rows"]
    r = next(x for x in ot if x[0] == "R2")
    assert float(r[2]) == 60.0  # 18/30 = 60.0% on-time, exact

    w5 = (await client.get(f"/api/{agency_id}/reports/worst_5min?from={day}&to={day}")).json()["rows"]
    r = next(x for x in w5 if x[0] == "R2")
    assert r[2] == 12  # exact count of >300s observations


@pytest.mark.asyncio
async def test_ranking_null_service_route_surfaces(reports_client):
    """NULL service_type routes must still rank (the '' sentinel maps back to
    None), matching the old live query which never filtered them."""
    client, agency_id, pool = reports_client
    day = "2026-05-04"
    await _seed_route(pool, agency_id, "R_NULL", None, day, [200] * 25)
    _run_analyze(agency_id)
    rows = await compute_ranking_rows(client, agency_id, day)
    r = next(x for x in rows if x[0] == "R_NULL")
    assert r[1] is None  # '' sentinel -> None


async def compute_ranking_rows(client, agency_id, day):
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}")
    assert resp.status_code == 200
    return resp.json()["rows"]


@pytest.mark.asyncio
async def test_reports_get_empty_aggregates_renders_no_data(reports_client):
    """With no agg data seeded, the report renders gracefully (text + empty rows)."""
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/ranking")
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "ranking"
    assert data["rows"] == []
    assert isinstance(data["text"], str) and len(data["text"]) > 0


@pytest.mark.asyncio
async def test_reports_unknown_agency_returns_404(reports_client):
    client, _, _ = reports_client
    resp = await client.get("/api/99999/reports")
    assert resp.status_code == 404
