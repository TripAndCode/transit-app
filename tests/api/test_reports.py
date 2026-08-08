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
    # get_report() now declares ch=Depends(get_ch) alongside conn (Task 8,
    # compare_ranking's time_band-filtered live-fallback) — every report type
    # resolves the dependency regardless of whether it's used, so something
    # must be present at app.state.ch_client. None of this file's tests pass
    # a time_band filter (all exercise the agg-table fast path), so None is
    # a safe default here.
    app.state.ch_client = None
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


def _run_analyze(agency_id, ch_client):
    """Build the agg_* tables (incl. agg_route_daily_dist) from seeded updates.

    analyze()'s dedup materialization now reads ClickHouse (Task 6); this
    file's fixtures seed Postgres `updates` directly (pre-dating that
    migration), so mirror the same rows into ClickHouse first — see
    tests.conftest.mirror_updates_to_ch."""
    import os

    import psycopg2

    from pipeline.analyze import analyze
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        # Match every real analyze() caller (gtfs_pipeline._get_conn, the
        # cron endpoint), which pins Asia/Tokyo — without this, the naive-UTC
        # captured_at values ClickHouse returns get bulk-loaded under this
        # connection's default (UTC) session timezone instead, masking any
        # timezone-handling bug in analyze()'s ClickHouse bulk-load path.
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        analyze(agency_id, conn, ch_client)
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
                f"{route_code}-{day}-trip-{i}",
                route_code,
                service_type,
                time(10, 0),
                1,
                d,
                datetime.fromisoformat(f"{day}T10:{i // 60:02d}:{i % 60:02d}"),
                f"test/{route_code}/{day}/{i}.pb",
            )


@pytest.mark.asyncio
async def test_reports_get_ranking_reads_agg(reports_client, ch_client):
    """ranking now reads agg_route_daily_dist; seed updates → analyze → render.

    HAVING COUNT(*) > 20, so seed 25 rows for route 44 across distinct trips.
    """
    client, agency_id, pool = reports_client
    day = "2026-05-01"
    await _seed_route(pool, agency_id, "44", "平日", day, [300] * 25)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "ranking"
    assert any(r[0] == "44" for r in data["rows"])  # row index 0 = route_code


@pytest.mark.asyncio
async def test_ranking_agg_values_exact_avg_and_approx_pct(reports_client, ch_client):
    """avg/samples are exact from the aggregate; p50/p90 interpolate from the
    histogram (within one bucket of the true value)."""
    client, agency_id, pool = reports_client
    day = "2026-05-02"
    # 30 samples, all 120s late: avg = 2.0 min exactly; percentiles ~2 min.
    await _seed_route(pool, agency_id, "R1", "平日", day, [120] * 30)
    _run_analyze(agency_id, ch_client)
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
async def test_on_time_and_worst_5min_exact_from_agg(reports_client, ch_client):
    """on_time_pct and late5_count are exact (thresholds baked at analyze time)."""
    client, agency_id, pool = reports_client
    day = "2026-05-03"
    # 30 samples: 18 on-time (<=60s), 12 very late (>300s = worst_5min).
    await _seed_route(pool, agency_id, "R2", "平日", day, [30] * 18 + [600] * 12)
    _run_analyze(agency_id, ch_client)

    ot = (await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}")).json()["rows"]
    r = next(x for x in ot if x[0] == "R2")
    assert float(r[2]) == 60.0  # 18/30 = 60.0% on-time, exact

    w5 = (await client.get(f"/api/{agency_id}/reports/worst_5min?from={day}&to={day}")).json()["rows"]
    r = next(x for x in w5 if x[0] == "R2")
    assert r[2] == 12  # exact count of >300s observations


@pytest.mark.asyncio
async def test_ranking_null_service_route_surfaces(reports_client, ch_client):
    """NULL service_type routes must still rank (the '' sentinel maps back to
    None), matching the old live query which never filtered them."""
    client, agency_id, pool = reports_client
    day = "2026-05-04"
    await _seed_route(pool, agency_id, "R_NULL", None, day, [200] * 25)
    _run_analyze(agency_id, ch_client)
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
async def test_reports_dow_weekend_reads_agg(reports_client, ch_client):
    """dow_weekend reads agg_daily_trend (weekend dates only). 2026-05-23 is a
    Saturday; >10 samples to clear the HAVING."""
    client, agency_id, pool = reports_client
    await _seed_route(pool, agency_id, "R_WE", "土日祝", "2026-05-23", [300] * 15)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/dow_weekend?from=2026-05-18&to=2026-05-24")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r = next(x for x in rows if x[0] == "R_WE")
    assert r[2] == "週末"  # dow label
    assert float(r[3]) == 5.0  # 300s = 5.0 min


@pytest.mark.asyncio
async def test_reports_compare_ranking_reads_agg(reports_client, ch_client):
    """compare_ranking reads agg_daily_trend: weekday (Tue 05-19) vs weekend
    (Sat 05-23) per-route avg + delta."""
    client, agency_id, pool = reports_client
    await _seed_route(pool, agency_id, "R_CMP", "平日", "2026-05-19", [120] * 15)  # 2.0 min weekday
    await _seed_route(pool, agency_id, "R_CMP", "土日祝", "2026-05-23", [360] * 15)  # 6.0 min weekend
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/compare_ranking?from=2026-05-18&to=2026-05-24")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r = next(x for x in rows if x[0] == "R_CMP")
    assert float(r[1]) == 2.0  # heijitsu (weekday)
    assert float(r[2]) == 6.0  # kyujitsu (weekend)
    assert float(r[3]) == 4.0  # abs delta


@pytest.mark.asyncio
async def test_reports_trend_reads_agg(reports_client, ch_client):
    """trend reads agg_daily_trend (daily series) + agg_hour_daily (hourly cells)."""
    client, agency_id, pool = reports_client
    await _seed_route(pool, agency_id, "R_TR", "平日", "2026-05-19", [180] * 12)
    await _seed_route(pool, agency_id, "R_TR", "平日", "2026-05-20", [240] * 12)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/trend?from=2026-05-18&to=2026-05-24")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    days = rows[0]["days"]
    assert len(days) == 2  # two seeded days
    assert {d["date"] for d in days} == {"2026-05-19", "2026-05-20"}
    # hourly heatmap cells present (scheduled_time 10:00 → hour 10, ≥3 samples)
    hourly = rows[0]["hourly"]
    assert any(c["hour"] == 10 for c in hourly)
    # dow_band: pooled from the same hourly cells, no routes/disclaimer keys
    dow_band = rows[0]["dow_band"]
    assert set(dow_band.keys()) == {"grid", "worst"}
    assert len(dow_band["grid"]) == 35
    # 2026-05-19 is a Tuesday (dow=2), hour 10 -> band "midday"
    tue_midday = next(c for c in dow_band["grid"] if c["dow"] == 2 and c["band"] == "midday")
    assert tue_midday["samples"] > 0


@pytest.mark.asyncio
async def test_reports_dow_keeps_null_service_routes(reports_client, ch_client):
    """NULL-service routes (広島's unmatched rows) must still appear in dow —
    agg_daily_trend keeps them via the '' sentinel, mapped back to None. Guards
    the regression where the typed-dedup agg dropped whole routes."""
    client, agency_id, pool = reports_client
    await _seed_route(pool, agency_id, "R_NULL", None, "2026-05-23", [300] * 15)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/dow_weekend?from=2026-05-18&to=2026-05-24")
    assert resp.status_code == 200
    r = next((x for x in resp.json()["rows"] if x[0] == "R_NULL"), None)
    assert r is not None, "NULL-service route dropped from dow report"
    assert r[1] is None  # '' sentinel mapped back to None


async def _seed_route_at(pool, agency_id, route_code, service_type, day, sched, delays):
    """Like `_seed_route` but with a caller-chosen `scheduled_time` (HH:MM),
    needed to land inside/outside a specific time_band.

    `sched` (with its ':' stripped) is folded into the trip_id/file_name so
    multiple calls for the same (route_code, day) — e.g. one in-band, one
    out-of-band — don't collide on the updates table's unique key.
    """
    from datetime import datetime, time

    hh, mm = (int(x) for x in sched.split(":"))
    tag = sched.replace(":", "")
    async with pool.acquire() as conn:
        for i, d in enumerate(delays):
            await conn.execute(
                "INSERT INTO updates "
                "(agency_id, trip_id, route_code, service_type, scheduled_time, "
                " stop_sequence, dep_delay, captured_at, file_name) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                agency_id,
                f"{route_code}-{day}-tb-{tag}-trip-{i}",
                route_code,
                service_type,
                time(hh, mm),
                1,
                d,
                datetime.fromisoformat(f"{day}T10:{i // 60:02d}:{i % 60:02d}"),
                f"test/{route_code}/{day}/tb/{tag}/{i}.pb",
            )


@pytest.mark.asyncio
async def test_reports_ranking_falls_back_to_live_under_time_band(reports_client, ch_client, ch_async_client):
    """Task 8.5: a time_band filter bypasses agg_route_daily_dist and reads
    live `updates` from ClickHouse via `_dedup_cte_ch` / `_ranking_live`.

    25 samples inside the 'morning' band (05:00-09:00) clear the ranking's
    HAVING count(*) > 20 gate; a same-route sample outside the band must be
    excluded from both the average and the sample count.
    """
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-05-10"
    await _seed_route_at(pool, agency_id, "R_TB", "平日", day, "08:00", [300] * 25)  # 5.0 min, in-band
    await _seed_route_at(pool, agency_id, "R_TB", "平日", day, "13:00", [6000])  # way outside the band
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}&time_band=morning")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r = next(x for x in rows if x[0] == "R_TB")
    # (route, service, avg_min, p50_min, p90_min, samples)
    assert float(r[2]) == 5.0
    assert r[5] == 25
    assert 2.0 <= float(r[3]) < 8.0  # p50 within the uniform-300s cluster
    assert 2.0 <= float(r[4]) < 8.0  # p90 within the uniform-300s cluster


@pytest.mark.asyncio
async def test_reports_ranking_half_up_rounding_matches_agg_and_live(reports_client, ch_client, ch_async_client):
    """Fix C regression: ClickHouse's round() is round-half-to-even; Postgres'
    numeric ROUND() (and this codebase's Decimal(ROUND_HALF_UP) helpers) round
    half away from zero. 12 rows at 127s + 12 rows at 128s average to exactly
    127.5s = 2.125min — an exact .5 boundary at the 3rd decimal. Half-up
    rounds to 2.13; ClickHouse's native round() would have given 2.12. Both
    the ClickHouse live fallback (time_band=morning, _ranking_live) and the
    agg fast path (time_band=all, after analyze(), agg_route_daily_dist) must
    agree on 2.13 for the same underlying data.
    """
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-05-10"
    await _seed_route_at(pool, agency_id, "R_HALF", "平日", day, "08:00", [127] * 12 + [128] * 12)

    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    # Live path: time_band forces the ClickHouse fallback.
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}&time_band=morning")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r_live = next(x for x in rows if x[0] == "R_HALF")
    assert float(r_live[2]) == 2.13

    # Fast path: analyze() builds agg_route_daily_dist from the same rows.
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r_agg = next(x for x in rows if x[0] == "R_HALF")
    assert float(r_agg[2]) == 2.13


@pytest.mark.asyncio
async def test_reports_trend_falls_back_to_live_under_time_band(reports_client, ch_client, ch_async_client):
    """Task 8.5: trend's daily series (compute_trend_series) and hourly
    heatmap (compute_hourly_heatmap) both fall back to the ClickHouse live
    scan under a non-default time_band; only the in-band sample counts."""
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-05-11"
    # > 5 samples so compute_trend_series' HAVING count(*) > 5 gate clears;
    # > 3 samples (same rows) also clears compute_hourly_heatmap's HAVING >= 3.
    await _seed_route_at(pool, agency_id, "R_TR2", "平日", day, "06:00", [180] * 6)  # 3.0 min, morning
    await _seed_route_at(pool, agency_id, "R_TR2", "平日", day, "20:00", [6000])  # evening — excluded
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/trend?from={day}&to={day}&time_band=morning")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    days = rows[0]["days"]
    assert len(days) == 1
    assert days[0]["date"] == day
    assert days[0]["samples"] == 6
    assert days[0]["avg_min"] == pytest.approx(3.0, abs=0.05)
    hourly = rows[0]["hourly"]
    assert any(c["hour"] == 6 and c["samples"] == 6 for c in hourly)
    assert not any(c["hour"] == 20 for c in hourly)  # outside the morning band


@pytest.mark.asyncio
async def test_reports_compare_ranking_falls_back_to_live_under_time_band(reports_client, ch_client, ch_async_client):
    """Task 8: a time_band filter bypasses agg_daily_trend and reads live
    `updates` from ClickHouse via `_route_avg_by_dow_ch` / `_compare_ranking_live`.

    Hand-computable repro (same numbers as the original review's standalone
    verification): route R1 gets 15 weekday (Tue 2026-05-19) observations at
    120s (2.00 min) and 15 weekend (Sat 2026-05-23) observations at 300s
    (5.00 min), both inside the 'noon' band (12:00-14:00) — expected
    (heijitsu, kyujitsu, abs_delta, signed_delta) = (2.00, 5.00, 3.00, 3.00).

    Two things must NOT leak into that average:
    - An extra R1 weekday observation scheduled at 08:00 (outside 'noon')
      with a wildly different delay (99999s) — if the time_band filter
      weren't applied, this would blow the weekday average far past 2.00.
    - A second route (R_THIN) with only 5 weekday/5 weekend in-band
      observations — below the ``> 10`` minimum-sample-count gate
      (`_route_avg_by_dow_ch`'s dedup HAVING-equivalent) — must not appear
      in the results at all.
    """
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client

    # R1: 15+15 in-band observations with known, distinct averages.
    await _seed_route_at(pool, agency_id, "R1", "平日", "2026-05-19", "12:30", [120] * 15)
    await _seed_route_at(pool, agency_id, "R1", "土日祝", "2026-05-23", "12:30", [300] * 15)
    # Out-of-band R1 weekday observation — must be excluded from the average.
    await _seed_route_at(pool, agency_id, "R1", "平日", "2026-05-19", "08:00", [99999])
    # R_THIN: only 5 in-band observations per side — below the minimum sample
    # count, must not appear in the output at all.
    await _seed_route_at(pool, agency_id, "R_THIN", "平日", "2026-05-19", "12:30", [999] * 5)
    await _seed_route_at(pool, agency_id, "R_THIN", "土日祝", "2026-05-23", "12:30", [999] * 5)

    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(f"/api/{agency_id}/reports/compare_ranking?from=2026-05-18&to=2026-05-24&time_band=noon")
    assert resp.status_code == 200
    rows = resp.json()["rows"]

    codes = {r[0] for r in rows}
    assert "R_THIN" not in codes, "route below the minimum-sample-count gate leaked into the results"

    r = next(x for x in rows if x[0] == "R1")
    # (route_code, heijitsu_min, kyujitsu_min, abs_delta, signed_delta)
    assert float(r[1]) == 2.0  # heijitsu (weekday) — the out-of-band 99999s observation must not skew this
    assert float(r[2]) == 5.0  # kyujitsu (weekend)
    assert float(r[3]) == 3.0  # abs delta
    assert float(r[4]) == 3.0  # signed delta (kyujitsu - heijitsu, both positive here)


@pytest.mark.asyncio
async def test_reports_unknown_agency_returns_404(reports_client):
    client, _, _ = reports_client
    resp = await client.get("/api/99999/reports")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# ch=None on a live-fallback path must raise a clear RuntimeError, not a bare
# AttributeError from `ch.query(...)`. All six compute_* functions in
# pipeline/reports/rankings.py accept ch=None (for fast-path-only callers);
# these guard the live branch instead of letting it fail deep inside
# clickhouse_connect. No agg/ClickHouse seeding needed — the guard fires
# before any query is issued.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_ranking_live_path_without_ch_raises(aconn, aagency_id):
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_ranking

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_ranking(aagency_id, ctx, aconn, ch=None)


@pytest.mark.asyncio
async def test_compute_on_time_live_path_without_ch_raises(aconn, aagency_id):
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_on_time

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_on_time(aagency_id, ctx, aconn, ch=None)


@pytest.mark.asyncio
async def test_compute_worst_5min_live_path_without_ch_raises(aconn, aagency_id):
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_worst_5min

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_worst_5min(aagency_id, ctx, aconn, ch=None)


@pytest.mark.asyncio
async def test_compute_dow_ranking_live_path_without_ch_raises(aconn, aagency_id):
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_dow_ranking

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_dow_ranking(aagency_id, ctx, aconn, "weekend", ch=None)


@pytest.mark.asyncio
async def test_compute_compare_ranking_live_path_without_ch_raises(aconn, aagency_id):
    """Also covers compute_compare_ranking's signature fix: ``ch`` used to be
    a required positional arg (the only one of the six siblings without a
    default); it now defaults to None like the rest, so this call is valid
    without a ch at all."""
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_compare_ranking

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_compare_ranking(aagency_id, ctx, aconn)


@pytest.mark.asyncio
async def test_compute_hourly_heatmap_live_path_without_ch_raises(aconn, aagency_id):
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_hourly_heatmap

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_hourly_heatmap(aagency_id, ctx, aconn, ch=None)


@pytest.mark.asyncio
async def test_compute_trend_series_live_path_without_ch_raises(aconn, aagency_id):
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_trend_series

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_trend_series(aagency_id, ctx, aconn, ch=None)
