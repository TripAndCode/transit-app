"""API tests for GET /api/{agency_id}/weather_delay.

Seeds `agency_weather_stations` + `weather_daily_observations` (migration
0042) and `agg_route_daily` directly via asyncpg. Like
`test_performance_standards.py`, this endpoint only ever reads precomputed
Postgres aggregates plus the observation tables, so exercising
`pipeline.analyze()`'s own population of `agg_route_daily` -- or
`pipeline.weather`'s fetch of the observations -- is other tests' job.

The fixture's numbers are chosen so every pooled average lands on a
hand-checkable integer: each seeded route-day carries 100 samples, and its
`sum_delay_sec` is 100 x the intended per-day average.
"""

import os
from datetime import date, timedelta

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")

_FROM = date(2026, 4, 1)
_TO = date(2026, 4, 30)
_RANGE = f"from={_FROM.isoformat()}&to={_TO.isoformat()}"
_STATION_ID = "99999"


@pytest.fixture
async def weather_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Weather Test Agency",
        "http://weather-test.example.com",
    )
    aid = row["agency_id"]

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid, pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, agg_route_daily, agency_weather_stations, "
            "weather_daily_observations CASCADE"
        )
    await pool.close()


async def _seed_station(pool, aid, *, note="central depot"):
    await pool.execute(
        "INSERT INTO agency_weather_stations (agency_id, station_id, station_name, source, note) "
        "VALUES ($1,$2,$3,'jma_amedas',$4)",
        aid,
        _STATION_ID,
        "Test Station",
        note,
    )


async def _seed_day(
    pool,
    aid,
    day: date,
    *,
    precip_mm: float | None,
    avg_delay_sec: int,
    route_code: str = "R1",
    service_type: str = "平日",
    samples: int = 100,
):
    """One route-day of delay plus that day's observation for the station."""
    await pool.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, avg_delay_sec, "
        "worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now(), $9)",
        aid,
        day,
        route_code,
        service_type,
        avg_delay_sec,
        avg_delay_sec * 2,
        10,
        samples,
        avg_delay_sec * samples,
    )
    if precip_mm is None:
        return
    await pool.execute(
        "INSERT INTO weather_daily_observations (station_id, obs_date, precip_mm, source) "
        "VALUES ($1,$2,$3,'jma_amedas') ON CONFLICT (station_id, obs_date) DO NOTHING",
        _STATION_ID,
        day,
        precip_mm,
    )


async def _seed_wet_dry_window(pool, aid, *, wet_delay=180, dry_delay=120, wet_days=6, dry_days=8):
    """`wet_days` observed-rainy days averaging `wet_delay`, then `dry_days`
    non-rainy ones averaging `dry_delay`. Both sides land above the thin-side
    thresholds so `low_confidence` is False unless a test narrows the window."""
    day = _FROM
    for _ in range(wet_days):
        await _seed_day(pool, aid, day, precip_mm=12.0, avg_delay_sec=wet_delay)
        day += timedelta(days=1)
    for _ in range(dry_days):
        await _seed_day(pool, aid, day, precip_mm=0.0, avg_delay_sec=dry_delay)
        day += timedelta(days=1)


async def test_unavailable_without_a_configured_station(weather_client):
    """No representative station means no metric at all -- never a guessed
    nearest station, and never a fabricated comparison."""
    client, aid, pool = weather_client
    await _seed_day(pool, aid, _FROM, precip_mm=None, avg_delay_sec=120)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["station"] is None
    assert body["delta_sec"] is None


async def test_unavailable_when_no_service_day_matches_an_observation(weather_client):
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM, precip_mm=None, avg_delay_sec=120)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["wet"]["days"] == 0
    assert body["dry"]["days"] == 0


async def test_compares_rainy_and_non_rainy_days(weather_client):
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_wet_dry_window(pool, aid)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    assert r.status_code == 200
    body = r.json()

    assert body["available"] is True
    assert body["station"]["station_id"] == _STATION_ID
    assert body["station"]["note"] == "central depot"
    assert body["wet"]["days"] == 6
    assert body["dry"]["days"] == 8
    assert body["wet"]["avg_delay_sec"] == pytest.approx(180.0, abs=1e-6)
    assert body["dry"]["avg_delay_sec"] == pytest.approx(120.0, abs=1e-6)
    assert body["delta_sec"] == pytest.approx(60.0, abs=1e-6)
    assert body["wet"]["avg_precip_mm"] == pytest.approx(12.0, abs=1e-6)
    assert body["dry"]["avg_precip_mm"] == pytest.approx(0.0, abs=1e-6)
    assert body["low_confidence"] is False


async def test_a_day_just_below_the_threshold_counts_as_not_rainy(weather_client):
    """The wet/dry split is at the response's own `wet_day_threshold_mm`: a
    trace-rain day belongs on the non-rainy side."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM, precip_mm=0.9, avg_delay_sec=300)
    await _seed_day(pool, aid, _FROM + timedelta(days=1), precip_mm=1.0, avg_delay_sec=200)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["wet_day_threshold_mm"] == pytest.approx(1.0, abs=1e-9)
    assert body["wet"]["days"] == 1
    assert body["wet"]["avg_delay_sec"] == pytest.approx(200.0, abs=1e-6)
    assert body["dry"]["days"] == 1
    assert body["dry"]["avg_delay_sec"] == pytest.approx(300.0, abs=1e-6)


async def test_pools_measurements_not_per_day_averages(weather_client):
    """A busy rainy day must outweigh a quiet one: the average is the raw
    seconds sum over the sample count, not a mean of per-day means."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM, precip_mm=12.0, avg_delay_sec=100, samples=900)
    await _seed_day(pool, aid, _FROM + timedelta(days=1), precip_mm=12.0, avg_delay_sec=200, samples=100)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    # (900*100 + 100*200) / 1000 = 110, not the unweighted (100+200)/2 = 150.
    assert body["wet"]["samples"] == 1000
    assert body["wet"]["avg_delay_sec"] == pytest.approx(110.0, abs=1e-6)


async def test_a_day_missing_its_observation_is_excluded_from_both_sides(weather_client):
    """A missing measurement is not a measurement of no rain."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM, precip_mm=12.0, avg_delay_sec=180)
    await _seed_day(pool, aid, _FROM + timedelta(days=1), precip_mm=None, avg_delay_sec=600)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["wet"]["days"] == 1
    assert body["dry"]["days"] == 0
    assert body["wet"]["avg_delay_sec"] == pytest.approx(180.0, abs=1e-6)


async def test_a_null_precipitation_observation_is_not_treated_as_dry(weather_client):
    """`precip_mm IS NULL` means "not observed here", so the day is excluded
    rather than counted as a 0 mm dry day."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM, precip_mm=None, avg_delay_sec=600)
    await pool.execute(
        "INSERT INTO weather_daily_observations (station_id, obs_date, precip_mm, temp_avg_c, source) "
        "VALUES ($1,$2,NULL,18.0,'jma_amedas')",
        _STATION_ID,
        _FROM,
    )

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["available"] is False
    assert body["dry"]["days"] == 0
    assert body["wet"]["days"] == 0


async def test_no_rainy_days_is_available_with_no_delta(weather_client):
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_wet_dry_window(pool, aid, wet_days=0, dry_days=8)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["available"] is True
    assert body["dry"]["days"] == 8
    assert body["wet"]["days"] == 0
    assert body["delta_sec"] is None
    assert body["low_confidence"] is True


async def test_thin_side_is_flagged_low_confidence(weather_client):
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_wet_dry_window(pool, aid, wet_days=1, dry_days=8)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["wet"]["days"] == 1
    assert body["delta_sec"] is not None
    assert body["low_confidence"] is True


async def test_route_filter_narrows_the_comparison(weather_client):
    """This metric reads the same aggregates the surrounding page filters, so a
    route filter must narrow both sides rather than being ignored."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM, precip_mm=12.0, avg_delay_sec=180, route_code="R1")
    await _seed_day(pool, aid, _FROM, precip_mm=12.0, avg_delay_sec=600, route_code="R2")
    await _seed_day(pool, aid, _FROM + timedelta(days=1), precip_mm=0.0, avg_delay_sec=120, route_code="R1")

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}&routes=R1")
    body = r.json()
    assert body["wet"]["avg_delay_sec"] == pytest.approx(180.0, abs=1e-6)
    assert body["dry"]["avg_delay_sec"] == pytest.approx(120.0, abs=1e-6)
    assert body["delta_sec"] == pytest.approx(60.0, abs=1e-6)


async def test_date_range_filter_excludes_out_of_range_days(weather_client):
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM - timedelta(days=1), precip_mm=12.0, avg_delay_sec=999)
    await _seed_day(pool, aid, _FROM, precip_mm=12.0, avg_delay_sec=180)

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["wet"]["days"] == 1
    assert body["wet"]["avg_delay_sec"] == pytest.approx(180.0, abs=1e-6)


async def test_sum_delay_sec_absent_falls_back_to_avg_times_samples(weather_client):
    """`agg_route_daily.sum_delay_sec` is nullable for rows built before it
    existed; such a day must still be counted, from its own average."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_day(pool, aid, _FROM, precip_mm=12.0, avg_delay_sec=180)
    await pool.execute(
        "UPDATE agg_route_daily SET sum_delay_sec = NULL WHERE agency_id = $1",
        aid,
    )

    r = await client.get(f"/api/{aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["wet"]["avg_delay_sec"] == pytest.approx(180.0, abs=1e-6)


async def test_response_carries_disclaimer_and_attribution_in_both_locales(weather_client):
    """Both must be server-rendered and localized: the disclaimer denies
    forecast/causation, and the attribution names the source and the
    processing, wherever the figure is shown."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_wet_dry_window(pool, aid)

    ja = (await client.get(f"/api/{aid}/weather_delay?{_RANGE}", headers={"Accept-Language": "ja"})).json()
    en = (await client.get(f"/api/{aid}/weather_delay?{_RANGE}", headers={"Accept-Language": "en"})).json()

    assert "予測" in ja["disclaimer"]
    assert "気象庁" in ja["attribution"]
    assert "forecast" in en["disclaimer"].lower()
    assert "Meteorological Agency" in en["attribution"]
    assert ja["disclaimer"] != en["disclaimer"]


async def test_observations_are_shared_across_agencies_on_one_station(weather_client):
    """Observations are keyed by station, not agency: a second agency pointed
    at the same station reads the same days without a second copy."""
    client, aid, pool = weather_client
    await _seed_station(pool, aid)
    await _seed_wet_dry_window(pool, aid)

    other = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1,$2) RETURNING agency_id",
        "Weather Test Agency 2",
        "http://weather-test-2.example.com",
    )
    other_aid = other["agency_id"]
    await _seed_station(pool, other_aid, note="shares the city station")
    await _seed_day(pool, other_aid, _FROM, precip_mm=12.0, avg_delay_sec=300)

    r = await client.get(f"/api/{other_aid}/weather_delay?{_RANGE}")
    body = r.json()
    assert body["available"] is True
    assert body["station"]["station_id"] == _STATION_ID
    assert body["wet"]["days"] == 1
    assert body["wet"]["avg_delay_sec"] == pytest.approx(300.0, abs=1e-6)
