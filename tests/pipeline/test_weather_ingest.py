"""DB-backed tests for pipeline.weather's observed-weather ingestion
(migration 0042's `weather_daily`).

What matters here is the revision rule: JMA publishes a preliminary value
first and may revise it before it becomes final, so a re-import has to apply
newer values in place without ever letting an older preliminary export
downgrade an already-final one.
"""

import os
from datetime import date

import asyncpg
import pytest

from pipeline.weather import (
    WeatherObservation,
    parse_observation_csv,
    upsert_weather_observations,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")

_TRUNCATE_SQL = "TRUNCATE agencies, weather_daily CASCADE"

STATION_ID = "44132"
STATION_NAME = "東京"


@pytest.fixture
async def weather_pool(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ($1,$2) RETURNING agency_id",
            "Weather Ingest Agency",
            "http://weather-ingest.example.com",
        )
    yield pool, row["agency_id"]
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
    await pool.close()


async def _upsert(pool, aid, observations):
    async with pool.acquire() as conn:
        return await upsert_weather_observations(conn, aid, STATION_ID, STATION_NAME, observations)


async def _row(pool, aid, day):
    return await pool.fetchrow(
        "SELECT * FROM weather_daily WHERE agency_id = $1 AND date = $2",
        aid,
        day,
    )


async def test_upsert_writes_observations_with_station_and_source(weather_pool):
    pool, aid = weather_pool
    written, stale = await _upsert(
        pool,
        aid,
        [WeatherObservation(date=date(2026, 4, 1), precip_mm=12.5, temp_max_c=18.0, quality="final")],
    )
    assert (written, stale) == (1, 0)

    row = await _row(pool, aid, date(2026, 4, 1))
    assert row["station_id"] == STATION_ID
    assert row["station_name"] == STATION_NAME
    assert row["source"] == "jma"
    assert row["precip_mm"] == 12.5
    assert row["temp_max_c"] == 18.0
    assert row["quality"] == "final"
    assert row["retrieved_at"] is not None


async def test_upsert_is_idempotent(weather_pool):
    pool, aid = weather_pool
    obs = [WeatherObservation(date=date(2026, 4, 1), precip_mm=1.0, quality="final")]
    await _upsert(pool, aid, obs)
    written, stale = await _upsert(pool, aid, obs)

    assert (written, stale) == (1, 0)
    assert await pool.fetchval("SELECT COUNT(*) FROM weather_daily WHERE agency_id = $1", aid) == 1


async def test_final_value_replaces_a_stored_preliminary_one(weather_pool):
    pool, aid = weather_pool
    await _upsert(pool, aid, [WeatherObservation(date=date(2026, 4, 1), precip_mm=3.0, quality="preliminary")])
    written, stale = await _upsert(
        pool, aid, [WeatherObservation(date=date(2026, 4, 1), precip_mm=7.5, quality="final")]
    )

    assert (written, stale) == (1, 0)
    row = await _row(pool, aid, date(2026, 4, 1))
    assert row["precip_mm"] == 7.5
    assert row["quality"] == "final"


async def test_preliminary_value_never_downgrades_a_stored_final_one(weather_pool):
    """Re-running an older preliminary export must leave settled data
    alone, and say so via the stale count rather than silently."""
    pool, aid = weather_pool
    await _upsert(pool, aid, [WeatherObservation(date=date(2026, 4, 1), precip_mm=7.5, quality="final")])
    written, stale = await _upsert(
        pool, aid, [WeatherObservation(date=date(2026, 4, 1), precip_mm=3.0, quality="preliminary")]
    )

    assert (written, stale) == (0, 1)
    row = await _row(pool, aid, date(2026, 4, 1))
    assert row["precip_mm"] == 7.5
    assert row["quality"] == "final"


async def test_preliminary_value_revises_another_preliminary_one(weather_pool):
    pool, aid = weather_pool
    await _upsert(pool, aid, [WeatherObservation(date=date(2026, 4, 1), precip_mm=3.0, quality="preliminary")])
    written, stale = await _upsert(
        pool, aid, [WeatherObservation(date=date(2026, 4, 1), precip_mm=4.5, quality="preliminary")]
    )

    assert (written, stale) == (1, 0)
    assert (await _row(pool, aid, date(2026, 4, 1)))["precip_mm"] == 4.5


async def test_upsert_rejects_an_observation_with_no_measurement(weather_pool):
    pool, aid = weather_pool
    with pytest.raises(ValueError, match="no measurement"):
        await _upsert(pool, aid, [WeatherObservation(date=date(2026, 4, 1))])


async def test_parsed_csv_lands_one_row_per_observed_day(weather_pool):
    """End-to-end: a CSV whose most recent day isn't published yet (blank
    measurements) stores only the days that are actually observed."""
    pool, aid = weather_pool
    text = (
        "date,precip_mm,temp_max_c,temp_min_c,quality\n"
        "2026-04-01,0.0,18.0,7.0,final\n"
        "2026-04-02,6.5,17.0,8.0,final\n"
        "2026-04-03,,,,\n"
    )
    written, stale = await _upsert(pool, aid, parse_observation_csv(text))

    assert (written, stale) == (2, 0)
    days = [r["date"] for r in await pool.fetch("SELECT date FROM weather_daily ORDER BY date")]
    assert days == [date(2026, 4, 1), date(2026, 4, 2)]


async def test_changing_the_representative_station_overwrites_rather_than_duplicates(weather_pool):
    """`weather_daily` holds ONE representative observation per agency-day,
    so the report's 1:1 join can't double-count a day."""
    pool, aid = weather_pool
    obs = [WeatherObservation(date=date(2026, 4, 1), precip_mm=1.0, quality="final")]
    await _upsert(pool, aid, obs)
    async with pool.acquire() as conn:
        await upsert_weather_observations(conn, aid, "OTHER", "横浜", obs)

    rows = await pool.fetch("SELECT station_id FROM weather_daily WHERE agency_id = $1", aid)
    assert [r["station_id"] for r in rows] == ["OTHER"]
