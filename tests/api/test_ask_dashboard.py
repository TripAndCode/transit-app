"""Endpoint tests for /ask/dashboard/{heatmap,anomalies,movers}."""

from __future__ import annotations

import os
from datetime import date, timedelta

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def dash_app(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM updates WHERE agency_id IN (SELECT agency_id FROM agencies WHERE feed_url = 'http://dash-t')"
        )
        await c.execute(
            "DELETE FROM static_routes WHERE agency_id IN (SELECT agency_id FROM agencies WHERE feed_url = 'http://dash-t')"
        )
        await c.execute("DELETE FROM agencies WHERE feed_url = 'http://dash-t'")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://dash-t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await c.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES "
            "($1, 'R1', 'R1'), ($1, 'R2', 'R2')",
            agency_id,
        )
        # Seed enough rows for the queries to return non-empty results
        from datetime import datetime, time, timezone

        today = date(2026, 5, 31)
        for i in range(20):
            d = today - timedelta(days=i)
            for route in ("R1", "R2"):
                for h in (8, 17):
                    ts = datetime(d.year, d.month, d.day, h, 0, tzinfo=timezone.utc)
                    await c.execute(
                        """INSERT INTO updates (agency_id, route_code, service_type, scheduled_time, trip_id,
                               stop_sequence, dep_delay, captured_at, file_name)
                           VALUES ($1, $2, $3, $4, $5, 1, $6, $7, 'test.pb')""",
                        agency_id,
                        route,
                        "平日" if d.weekday() < 5 else "土日祝",
                        time(h, 0),
                        f"T_{route}_{d.isoformat()}_{h}",
                        (60 if route == "R1" else 30),
                        ts,
                    )
    app.state.pool = pool
    yield app, agency_id, pool
    async with pool.acquire() as c:
        await c.execute("DELETE FROM updates WHERE agency_id = $1", agency_id)
        await c.execute("DELETE FROM static_routes WHERE agency_id = $1", agency_id)
        await c.execute("DELETE FROM agencies WHERE agency_id = $1", agency_id)
    await pool.close()


@pytest.mark.asyncio
async def test_heatmap_dow_returns_shape(dash_app):
    app, agency, _ = dash_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency}/ask/dashboard/heatmap?dimension=dow&from=2026-05-01&to=2026-05-31")
    assert r.status_code == 200
    body = r.json()
    assert {"routes", "dimensions", "cells", "baseline_min"} <= set(body.keys())
    assert len(body["dimensions"]) == 7
    # 2 seeded routes
    assert len(body["routes"]) == 2
    assert all(len(row) == 7 for row in body["cells"])


@pytest.mark.asyncio
async def test_heatmap_hour_band(dash_app):
    app, agency, _ = dash_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency}/ask/dashboard/heatmap?dimension=hour_band&from=2026-05-01&to=2026-05-31")
    body = r.json()
    assert len(body["dimensions"]) == 4


@pytest.mark.asyncio
async def test_heatmap_invalid_dimension(dash_app):
    app, agency, _ = dash_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency}/ask/dashboard/heatmap?dimension=garbage")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_anomalies_returns_series(dash_app):
    app, agency, _ = dash_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency}/ask/dashboard/anomalies?from=2026-05-01&to=2026-05-31")
    assert r.status_code == 200
    body = r.json()
    assert {"series", "mean", "std", "anomalies"} <= set(body.keys())
    assert len(body["series"]) >= 15  # 20 days seeded


@pytest.mark.asyncio
async def test_movers_returns_rows(dash_app):
    app, agency, _ = dash_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency}/ask/dashboard/movers?from=2026-05-01&to=2026-05-31&window_days=7&top=10")
    assert r.status_code == 200
    body = r.json()
    assert "rows" in body
    expected_keys = {"route_code", "label", "current_avg", "previous_avg", "delta", "delta_pct", "samples"}
    assert all(expected_keys.issubset(r.keys()) for r in body["rows"])
