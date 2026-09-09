"""API tests for GET /api/{agency_id}/weather_impact (item 105).

Seeds `weather_daily` (migration 0042) and `agg_route_daily_dist` directly
via asyncpg: this endpoint only ever reads those two precomputed Postgres
tables, so exercising `pipeline.analyze()`'s own population of the aggregate
is other tests' job. `pipeline.weather`'s ingestion path (revision handling)
is covered in tests/pipeline/test_weather_ingest.py.

Fixture shape (agency-wide, one route, 2026-04-01..08):

    date        precip_mm  samples  on_time  sum_delay_sec  bucket
    2026-04-01       5.0       100       70          12000  wet (boundary)
    2026-04-02      12.0       100       70          12000  wet
    2026-04-03       8.0       100       70          12000  wet
    2026-04-04      20.0       100       70          12000  wet
    2026-04-05       0.0       100       90           6000  dry
    2026-04-06       1.0       100       90           6000  dry
    2026-04-07       4.9       100       90           6000  dry (boundary)
    2026-04-08   no row        100       50          30000  unobserved

so wet pools to 70.0% on-time / 2.0 min average delay, dry to 90.0% / 1.0
min, and the unobserved day's deliberately terrible figures must not reach
either side.
"""

import os
from datetime import date

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")

_TRUNCATE_SQL = "TRUNCATE agencies, agg_route_daily_dist, weather_daily CASCADE"

STATION_ID = "44132"
STATION_NAME = "東京"

# (date, precip_mm) for every observed day in the fixture range.
_OBSERVED = [
    ("2026-04-01", 5.0),
    ("2026-04-02", 12.0),
    ("2026-04-03", 8.0),
    ("2026-04-04", 20.0),
    ("2026-04-05", 0.0),
    ("2026-04-06", 1.0),
    ("2026-04-07", 4.9),
]

# (date, samples, on_time_count, sum_delay_sec) per service day.
_SERVICE = [
    ("2026-04-01", 100, 70, 12000),
    ("2026-04-02", 100, 70, 12000),
    ("2026-04-03", 100, 70, 12000),
    ("2026-04-04", 100, 70, 12000),
    ("2026-04-05", 100, 90, 6000),
    ("2026-04-06", 100, 90, 6000),
    ("2026-04-07", 100, 90, 6000),
    ("2026-04-08", 100, 50, 30000),
]


async def _seed_service_day(pool, aid, day, samples, on_time_count, sum_delay_sec, route_code="R1"):
    await pool.execute(
        "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, "
        "samples, sum_delay_sec, on_time_count, late5_count, hist) "
        "VALUES ($1,$2,$3,'平日',$4,$5,$6,0,$7)",
        aid,
        date.fromisoformat(day),
        route_code,
        samples,
        sum_delay_sec,
        on_time_count,
        [0] * 37,
    )


async def _seed_observation(pool, aid, day, precip_mm, *, temp_max_c=None, station_id=STATION_ID, quality="final"):
    await pool.execute(
        "INSERT INTO weather_daily (agency_id, date, station_id, station_name, precip_mm, temp_max_c, quality) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7)",
        aid,
        date.fromisoformat(day),
        station_id,
        STATION_NAME,
        precip_mm,
        temp_max_c,
        quality,
    )


@pytest.fixture
async def weather_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Weather Test Agency",
        "http://weather-test.example.com",
    )
    aid = row["agency_id"]
    for day, samples, on_time_count, sum_delay_sec in _SERVICE:
        await _seed_service_day(pool, aid, day, samples, on_time_count, sum_delay_sec)
    for day, precip_mm in _OBSERVED:
        await _seed_observation(pool, aid, day, precip_mm)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid, pool
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
    await pool.close()


async def _get(client, aid, query="from=2026-04-01&to=2026-04-08", **kwargs):
    r = await client.get(f"/api/{aid}/weather_impact?{query}", **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


async def test_wet_and_dry_sides_pool_their_own_days(weather_client):
    """The threshold is inclusive: 5.0 mm is wet, 4.9 mm is dry."""
    client, aid, _ = weather_client
    body = await _get(client, aid)

    assert body["available"] is True
    assert body["wet_threshold_mm"] == 5.0
    assert body["wet"] == {"days": 4, "samples": 400, "on_time_pct": 70.0, "avg_delay_min": 2.0}
    assert body["dry"] == {"days": 3, "samples": 300, "on_time_pct": 90.0, "avg_delay_min": 1.0}


async def test_gaps_are_positive_when_wet_days_ran_worse(weather_client):
    client, aid, _ = weather_client
    body = await _get(client, aid)

    # dry minus wet on-time, wet minus dry delay -- both positive in the
    # same "wet days were worse" direction.
    assert body["on_time_gap_pt"] == pytest.approx(20.0)
    assert body["avg_delay_gap_min"] == pytest.approx(1.0)


async def test_day_with_no_observation_is_unobserved_never_dry(weather_client):
    """2026-04-08 has no published observation. Folding it into the dry side
    would both fabricate a dry day and drag the dry figures down with its
    50%/5-minute service."""
    client, aid, _ = weather_client
    body = await _get(client, aid)

    assert body["unobserved_days"] == 1
    assert body["observed_days"] == 7
    assert body["coverage_pct"] == 87.5
    assert body["dry"]["days"] == 3
    assert body["dry"]["samples"] == 300


async def test_temperature_only_observation_is_unobserved_precipitation(weather_client):
    """A row can exist with temperature but no precipitation (element
    outage) -- that day still has no observed precipitation to classify."""
    client, aid, pool = weather_client
    await _seed_observation(pool, aid, "2026-04-08", None, temp_max_c=21.0)

    body = await _get(client, aid)
    assert body["unobserved_days"] == 1
    assert body["dry"]["days"] == 3
    assert body["coverage_pct"] == 87.5


async def test_thin_side_suppresses_its_figures_and_both_gaps(weather_client):
    """2026-04-01..05 leaves the dry side one day short of the minimum. Its
    counts stay visible, its figures and both gaps go null, and `available`
    is false -- one day is noise, not a weather comparison."""
    client, aid, _ = weather_client
    body = await _get(client, aid, query="from=2026-04-01&to=2026-04-05")

    assert body["available"] is False
    assert body["dry"]["days"] == 1
    assert body["dry"]["samples"] == 100
    assert body["dry"]["on_time_pct"] is None
    assert body["dry"]["avg_delay_min"] is None
    assert body["on_time_gap_pt"] is None
    assert body["avg_delay_gap_min"] is None
    # The wet side is fine on its own and keeps reporting.
    assert body["wet"]["on_time_pct"] == 70.0


async def test_agency_with_no_observations_reports_unavailable_not_a_comparison(weather_client):
    client, aid, pool = weather_client
    await pool.execute("DELETE FROM weather_daily WHERE agency_id = $1", aid)

    body = await _get(client, aid)
    assert body["available"] is False
    assert body["observed_days"] == 0
    assert body["unobserved_days"] == 8
    assert body["coverage_pct"] == 0.0
    assert body["station_id"] is None
    assert body["station_name"] is None
    assert body["latest_observed_date"] is None
    assert body["wet"] == {"days": 0, "samples": 0, "on_time_pct": None, "avg_delay_min": None}


async def test_range_with_no_service_data_reports_no_coverage_rather_than_zero_percent(weather_client):
    client, aid, _ = weather_client
    body = await _get(client, aid, query="from=2026-05-01&to=2026-05-10")

    assert body["available"] is False
    assert body["observed_days"] == 0
    assert body["unobserved_days"] == 0
    assert body["coverage_pct"] is None


async def test_response_names_the_representative_station_and_its_reach(weather_client):
    client, aid, _ = weather_client
    body = await _get(client, aid)

    assert body["station_id"] == STATION_ID
    assert body["station_name"] == STATION_NAME
    assert body["latest_observed_date"] == "2026-04-07"


async def test_station_named_is_the_one_most_of_the_range_rests_on(weather_client):
    """A representative station can change mid-range; the panel names one,
    so it names the one carrying most of the observed days."""
    client, aid, pool = weather_client
    await pool.execute("DELETE FROM weather_daily WHERE agency_id = $1 AND date >= $2", aid, date(2026, 4, 6))
    await _seed_observation(pool, aid, "2026-04-06", 1.0, station_id="OTHER")
    await _seed_observation(pool, aid, "2026-04-07", 4.9, station_id="OTHER")

    body = await _get(client, aid)
    assert body["station_id"] == STATION_ID  # 5 days vs OTHER's 2


async def test_route_filter_narrows_the_service_side(weather_client):
    """The page-level route filter applies to the service aggregate; a
    filter matching no route leaves nothing to compare."""
    client, aid, _ = weather_client
    body = await _get(client, aid, query="from=2026-04-01&to=2026-04-08&routes=R_MISSING")

    assert body["available"] is False
    assert body["observed_days"] == 0
    assert body["wet"]["samples"] == 0


async def test_response_carries_jma_attribution_in_both_locales(weather_client):
    """JMA's terms require the source and the fact this system processed the
    data to travel with the figures, localized per Accept-Language."""
    client, aid, _ = weather_client
    ja = await _get(client, aid, headers={"Accept-Language": "ja"})
    en = await _get(client, aid, headers={"Accept-Language": "en"})

    assert "気象庁" in ja["attribution"]
    assert "Japan Meteorological Agency" in en["attribution"]
    assert ja["attribution"] != en["attribution"]
