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
async def test_on_time_appends_pct_low_confidence_flag(reports_client, ch_client):
    """The on_time report appends a trailing `low_confidence` bool (index 5)
    per row — True when the on-time percentage's 95% Wilson interval is
    wide enough to need a caveat, independent of compute_on_time's own
    samples>20 inclusion gate (see pipeline/stats.py)."""
    client, agency_id, pool = reports_client
    day = "2026-05-09"
    # 25 samples at 80% on-time: comfortably clears the >20 inclusion gate
    # but is still thin enough for a wide Wilson interval.
    await _seed_route(pool, agency_id, "R_UNCERTAIN", "平日", day, [30] * 20 + [600] * 5)
    # 300 samples at 90% on-time: a large-enough baseline that the interval
    # narrows well under the 5pp cutoff.
    await _seed_route(pool, agency_id, "R_CONFIDENT", "平日", day, [30] * 270 + [600] * 30)
    _run_analyze(agency_id, ch_client)

    rows = (await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}")).json()["rows"]
    uncertain = next(x for x in rows if x[0] == "R_UNCERTAIN")
    confident = next(x for x in rows if x[0] == "R_CONFIDENT")
    assert float(uncertain[2]) == 80.0
    assert uncertain[5] is True
    assert float(confident[2]) == 90.0
    assert confident[5] is False


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
async def test_ranking_ties_break_by_route_code(reports_client, ch_client):
    """Two routes tied on avg_min (agg fast path) must sort by route_code,
    ascending — regardless of `sort_order`. Without this, ties fall back to
    whatever order Postgres's GROUP BY happens to return them in, which is
    not guaranteed to be stable run to run.
    """
    client, agency_id, pool = reports_client
    day = "2026-05-06"
    await _seed_route(pool, agency_id, "RTIE_B", "平日", day, [300] * 25)  # avg=5.0
    await _seed_route(pool, agency_id, "RTIE_A", "平日", day, [300] * 25)  # avg=5.0, tied
    _run_analyze(agency_id, ch_client)
    rows = await compute_ranking_rows(client, agency_id, day)
    codes = [r[0] for r in rows if r[0] in ("RTIE_A", "RTIE_B")]
    assert codes == ["RTIE_A", "RTIE_B"]


@pytest.mark.asyncio
async def test_reports_ranking_live_ties_break_by_route_code(reports_client, ch_client, ch_async_client):
    """Same tie-break, live path (time_band filter -> ClickHouse `_ranking_live`)."""
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-05-13"
    await _seed_route_at(pool, agency_id, "RLTIE_B", "平日", day, "08:00", [300] * 25)
    await _seed_route_at(pool, agency_id, "RLTIE_A", "平日", day, "08:00", [300] * 25)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}&time_band=morning")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    codes = [r[0] for r in rows if r[0] in ("RLTIE_A", "RLTIE_B")]
    assert codes == ["RLTIE_A", "RLTIE_B"]


@pytest.mark.asyncio
async def test_on_time_ties_break_by_route_code(reports_client, ch_client):
    """Two routes tied on on_time_pct (agg fast path) must sort by
    route_code, ascending, for a reproducible top-N cut."""
    client, agency_id, pool = reports_client
    day = "2026-05-07"
    await _seed_route(pool, agency_id, "OTIE_B", "平日", day, [30] * 25)
    await _seed_route(pool, agency_id, "OTIE_A", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}")
    rows = resp.json()["rows"]
    codes = [r[0] for r in rows if r[0] in ("OTIE_A", "OTIE_B")]
    assert codes == ["OTIE_A", "OTIE_B"]


@pytest.mark.asyncio
async def test_worst_5min_ties_break_by_route_code(reports_client, ch_client):
    """Two routes tied on late5_count (agg fast path) must sort by
    route_code, ascending, for a reproducible top-N cut."""
    client, agency_id, pool = reports_client
    day = "2026-05-08"
    await _seed_route(pool, agency_id, "WTIE_B", "平日", day, [600] * 25)
    await _seed_route(pool, agency_id, "WTIE_A", "平日", day, [600] * 25)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/worst_5min?from={day}&to={day}")
    rows = resp.json()["rows"]
    codes = [r[0] for r in rows if r[0] in ("WTIE_A", "WTIE_B")]
    assert codes == ["WTIE_A", "WTIE_B"]


@pytest.mark.asyncio
async def test_dow_ranking_ties_break_by_route_code(reports_client, ch_client):
    """Two routes tied on avg_min (dow_weekend, agg fast path) must sort by
    route_code, ascending, for a reproducible top-N cut."""
    client, agency_id, pool = reports_client
    await _seed_route(pool, agency_id, "DTIE_B", "土日祝", "2026-05-23", [300] * 15)
    await _seed_route(pool, agency_id, "DTIE_A", "土日祝", "2026-05-23", [300] * 15)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/dow_weekend?from=2026-05-18&to=2026-05-24")
    rows = resp.json()["rows"]
    codes = [r[0] for r in rows if r[0] in ("DTIE_A", "DTIE_B")]
    assert codes == ["DTIE_A", "DTIE_B"]


@pytest.mark.asyncio
async def test_dow_ranking_pools_exact_sum_not_rounded_avg_min(reports_client, ch_client):
    """compute_dow_ranking's fast path must pool each day's EXACT sum_delay_sec
    across agg_daily_trend rows, not re-weight each day's own already-rounded
    avg_min.

    Two weekdays for the same route/service: day 1 (6 obs, raw-seconds sum
    248 -> analyze() rounds that day's own avg_min to 0.69 min) and day 2 (14
    obs, raw-seconds sum 1400 -> rounds to 1.67 min). Pooling the exact sums
    gives (248+1400)/20/60 = 1.37333... -> rounds to 1.37; re-weighting the
    rounded 0.69/1.67 instead (the pre-fix pattern) gives
    (0.69*6 + 1.67*14)/20 = 1.376 -> rounds to 1.38 -- a measurably different
    (and wrong) answer that exists purely from the intermediate rounding.
    """
    client, agency_id, pool = reports_client
    await _seed_route(pool, agency_id, "R_POOL", "平日", "2026-05-04", [41, 41, 41, 41, 42, 42])
    await _seed_route(pool, agency_id, "R_POOL", "平日", "2026-05-05", [100] * 14)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/dow_weekday?from=2026-05-01&to=2026-05-07")
    rows = resp.json()["rows"]
    row = next(r for r in rows if r[0] == "R_POOL")
    assert float(row[3]) == 1.37  # avg_min column -- NOT the buggy re-weighted 1.38


@pytest.mark.asyncio
async def test_compare_ranking_ties_break_by_route_code(reports_client, ch_client):
    """Two routes tied on abs_delta (agg fast path) must sort by route_code,
    ascending, for a reproducible top-N cut."""
    client, agency_id, pool = reports_client
    await _seed_route(pool, agency_id, "CTIE_B", "平日", "2026-05-19", [120] * 15)
    await _seed_route(pool, agency_id, "CTIE_B", "土日祝", "2026-05-23", [360] * 15)
    await _seed_route(pool, agency_id, "CTIE_A", "平日", "2026-05-19", [120] * 15)
    await _seed_route(pool, agency_id, "CTIE_A", "土日祝", "2026-05-23", [360] * 15)
    _run_analyze(agency_id, ch_client)
    resp = await client.get(f"/api/{agency_id}/reports/compare_ranking?from=2026-05-18&to=2026-05-24")
    rows = resp.json()["rows"]
    codes = [r[0] for r in rows if r[0] in ("CTIE_A", "CTIE_B")]
    assert codes == ["CTIE_A", "CTIE_B"]


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
    excluded from both the average and the sample count. Values are spread
    (240..360s, mean 300s) rather than identical: an all-tied partition
    makes PERCENT_RANK's min-rank tie handling give p50/p90 both None (see
    _ranking_live's docstring) — a real, correct edge case, just not the one
    this test is checking.
    """
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-05-10"
    await _seed_route_at(pool, agency_id, "R_TB", "平日", day, "08:00", list(range(240, 361, 5)))  # mean 5.0 min
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
    assert 2.0 <= float(r[3]) < 8.0  # p50 within the 240-360s spread
    assert 2.0 <= float(r[4]) < 8.0  # p90 within the 240-360s spread


@pytest.mark.asyncio
async def test_reports_ranking_live_percentile_matches_percent_rank_tie_semantics(
    reports_client, ch_client, ch_async_client
):
    """Regression: `_ranking_live` (this endpoint's `time_band`-filtered
    ClickHouse fallback) intentionally still reproduces the OLD min-rank-tie
    `PERCENT_RANK()` formula, via `rank()`/`count()` window functions — NOT
    ClickHouse's `quantileExact`, which is a pure positional pick
    (`sorted[floor(q*n)]`). This is a known, accepted divergence from the
    Postgres aggregate path (`agg_route_stats`/`agg_route_hour`), which has
    since migrated to `PERCENTILE_DISC` and would give a different answer on
    the same tied data — see `_ranking_live`'s own docstring.

    95 rows at 0s + 5 at 600s (n=100): the old min-rank tie handling this
    function reproduces gives every 0s row rank=1 (pct=0) and every 600s row
    rank=96 (pct=95/99≈0.960). That's the only group clearing >=0.5 AND
    >=0.9, so both p50 and p90 must read 10.0 (600s/60). quantileExact(0.5)/
    (0.9) would instead pick position floor(0.5*100)=50 and
    floor(0.9*100)=90 — both still inside the 95-row 0s run — giving 0.0 for
    both. (`PERCENTILE_DISC`, the current Postgres aggregate path, would also
    give 0.0 here — the same divergence from this function's 10.0.)
    """
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-05-12"
    await _seed_route_at(pool, agency_id, "R_TIE_PR", "平日", day, "08:00", [0] * 95 + [600] * 5)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}&time_band=morning")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r = next(x for x in rows if x[0] == "R_TIE_PR")
    assert r[5] == 100
    assert float(r[3]) == 10.0  # p50 — quantileExact would have given 0.0
    assert float(r[4]) == 10.0  # p90 — quantileExact would have given 0.0


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


@pytest.mark.asyncio
async def test_compute_trend_series_top_offenders_tie_break_is_deterministic(aconn, aagency_id):
    """Two routes tied on avg_min within the same bucket (agg_daily_trend
    fast path) must rank in top_offenders by route_code, ascending —
    `per_day` comes from a GROUP BY with no ordering guarantee.
    """
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_trend_series

    day = date(2026, 5, 18)
    for route_code in ("R_TR_Z", "R_TR_A"):
        await aconn.execute(
            "INSERT INTO agg_daily_trend "
            "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (agency_id, date, route_code, service_type) DO UPDATE "
            "SET avg_min = EXCLUDED.avg_min, samples = EXCLUDED.samples, sum_delay_sec = EXCLUDED.sum_delay_sec",
            aagency_id,
            day.isoformat(),
            route_code,
            "平日",
            5.0,
            10,
            round(5.0 * 60 * 10),
        )

    ctx = RangeCtx(from_date=day, to_date=day)
    out = await compute_trend_series(aagency_id, ctx, aconn, top_offenders=2)
    offenders = out["days"][0]["top_offenders"]
    codes = [o["route_code"] for o in offenders if o["route_code"].startswith("R_TR_")]
    assert codes == ["R_TR_A", "R_TR_Z"]


@pytest.mark.asyncio
async def test_compute_trend_series_week_bucket_pools_exact_sum_not_rounded_avg_min(aconn, aagency_id):
    """A 'week' bucket pools MULTIPLE agg_daily_trend rows (one per day) for
    the same route/service. This must divide the exact raw-seconds sums once
    at the end, not re-weight each day's own already-rounded avg_min.

    Two days in the same ISO week for the same route/service: day 1 (3
    samples, raw-seconds sum 124 -> analyze() rounds that day's own avg_min
    to 0.69 min) and day 2 (7 samples, raw-seconds sum 700 -> rounds to 1.67
    min). Pooling the exact sums gives (124+700)/10/60 = 1.37333... ->
    rounds to 1.37; re-weighting the rounded 0.69/1.67 instead (the pre-fix
    pattern) gives (0.69*3 + 1.67*7)/10 = 1.376 -> rounds to 1.38, a
    measurably different (and wrong) answer that exists purely from the
    intermediate rounding.
    """
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_trend_series

    # 2026-05-18 (Mon) and 2026-05-19 (Tue) fall in the same ISO week.
    for day, avg_min, samples, sum_delay_sec in (
        (date(2026, 5, 18), 0.69, 3, 124),
        (date(2026, 5, 19), 1.67, 7, 700),
    ):
        await aconn.execute(
            "INSERT INTO agg_daily_trend "
            "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
            "VALUES ($1, $2, 'R_WK', '平日', $3, $4, $5)",
            aagency_id,
            day.isoformat(),
            avg_min,
            samples,
            sum_delay_sec,
        )

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 19))
    out = await compute_trend_series(aagency_id, ctx, aconn, granularity="week")
    days = out["days"]
    assert len(days) == 1
    assert days[0]["avg_min"] == 1.37  # NOT the buggy re-weighted 1.38
    offender = next(o for o in days[0]["top_offenders"] if o["route_code"] == "R_WK")
    assert offender["avg_min"] == 1.37


@pytest.mark.asyncio
async def test_compute_trend_series_avg_min_smoothed_is_trailing_pooled_mean(aconn, aagency_id):
    """`avg_min_smoothed` sits alongside the raw per-day `avg_min` — a
    trailing pooled mean over the last (up to 7) OBSERVED days, computed
    from each day's exact raw-seconds sum (not by re-weighting the
    already-rounded daily avg_min values)."""
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_trend_series

    days_seed = [
        (date(2026, 5, 18), 1.0, 10, 600),
        (date(2026, 5, 19), 2.0, 10, 1200),
        (date(2026, 5, 20), 3.0, 10, 1800),
    ]
    for day, avg_min, samples, sum_delay_sec in days_seed:
        await aconn.execute(
            "INSERT INTO agg_daily_trend "
            "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
            "VALUES ($1, $2, 'R_SMOOTH', '平日', $3, $4, $5)",
            aagency_id,
            day.isoformat(),
            avg_min,
            samples,
            sum_delay_sec,
        )

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 20))
    out = await compute_trend_series(aagency_id, ctx, aconn)
    days = out["days"]
    assert len(days) == 3
    # Day 1: window is just day 1 itself -> 600/10/60 = 1.0.
    assert days[0]["avg_min"] == 1.0
    assert days[0]["avg_min_smoothed"] == 1.0
    # Day 2: pooled over days 1-2 -> (600+1200)/20/60 = 1.5.
    assert days[1]["avg_min_smoothed"] == 1.5
    # Day 3: pooled over all 3 days (fewer than the 7-day window) ->
    # (600+1200+1800)/30/60 = 2.0 -- NOT a re-weighted mean of 1.0/2.0/3.0
    # (which would also happen to be 2.0 here since samples are equal; the
    # exact-sum pooling is what matters for unequal-samples days elsewhere).
    assert days[2]["avg_min"] == 3.0
    assert days[2]["avg_min_smoothed"] == 2.0

    # Week/month buckets are already smoothed by their own width.
    out_week = await compute_trend_series(aagency_id, ctx, aconn, granularity="week")
    assert out_week["days"][0]["avg_min_smoothed"] is None


async def test_compute_trend_series_excludes_null_sum_delay_sec_group_from_bucket_avg(aconn, aagency_id):
    """A bucket's Python-side pooling (``by_date_samples`` /
    ``by_date_weighted_sec``) must exclude a (route, service) group whose
    ``sum_delay_sec`` is still NULL (migration 0028's column is nullable —
    any ``agg_daily_trend`` row analyze() hasn't rewritten since that
    migration can be in this state) from BOTH the numerator and the
    denominator, not just the numerator.

    Same day, two routes each clearing the ``HAVING SUM(samples) > 5`` gate
    on their own: R_NULL (6 samples, sum_delay_sec NULL) must contribute 0
    to the bucket average; R_OK (6 samples, raw-seconds sum 360 -> exact
    1.0 min) is the only group that should determine it. Pre-fix, R_NULL's
    6 samples would still land in ``by_date_samples`` while contributing
    nothing to ``by_date_weighted_sec``, giving (0+360)/12/60=0.5 instead of
    the correct 360/6/60=1.0.
    """
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_trend_series

    day = date(2026, 5, 20)
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, 'R_NULL', '平日', $3, $4, NULL)",
        aagency_id,
        day.isoformat(),
        0.5,  # pre-migration-style rounded avg_min; not used by the fast path
        6,
    )
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, 'R_OK', '平日', $3, $4, $5)",
        aagency_id,
        day.isoformat(),
        1.0,
        6,
        360,
    )

    ctx = RangeCtx(from_date=day, to_date=day)
    out = await compute_trend_series(aagency_id, ctx, aconn)
    days = out["days"]
    assert len(days) == 1
    assert days[0]["samples"] == 6  # R_NULL's samples excluded, not counted alongside R_OK's
    assert days[0]["avg_min"] == 1.0  # NOT the buggy 0.5 from counting R_NULL's samples


async def test_compute_trend_series_week_bucket_sql_excludes_null_sum_delay_sec_date(aconn, aagency_id):
    """A week/month bucket's own SQL-level pooling (SUM(sum_delay_sec) /
    SUM(samples) per bucket/route/service, BEFORE the Python-side pooling
    across route/service groups) must also FILTER both sides to the same
    row population. This is a distinct guard from
    ``test_compute_trend_series_excludes_null_sum_delay_sec_group_from_bucket_avg``
    above: that test covers pooling ACROSS route/service groups within one
    bucket; this one covers pooling ACROSS DATES within one route/service
    group for a 'week'/'month' bucket, which happens one layer earlier, at
    the SQL GROUP BY itself.

    Same ISO week, same route/service, two dates: day 1 (6 samples,
    sum_delay_sec NULL) must contribute 0 to this row's own avg_min; day 2
    (6 samples, raw-seconds sum 360 -> exact 1.0 min) is the only date that
    should determine it. Pre-fix, day 1's 6 samples would still land in the
    SQL's own SUM(samples) while contributing nothing to SUM(sum_delay_sec),
    giving (0+360)/12/60=0.5 instead of the correct 360/6/60=1.0 -- silently
    reproducing the exact bug this whole item exists to eliminate, one
    aggregation layer beneath where the Python-side fix already guards.
    """
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.rankings import compute_trend_series

    # 2026-05-18 (Mon) and 2026-05-19 (Tue) fall in the same ISO week.
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, 'R_WKNULL', '平日', $3, $4, NULL)",
        aagency_id,
        date(2026, 5, 18).isoformat(),
        0.5,  # pre-migration-style rounded avg_min; not used by the fast path
        6,
    )
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, 'R_WKNULL', '平日', $3, $4, $5)",
        aagency_id,
        date(2026, 5, 19).isoformat(),
        1.0,
        6,
        360,
    )

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 19))
    out = await compute_trend_series(aagency_id, ctx, aconn, granularity="week")
    days = out["days"]
    assert len(days) == 1
    offender = next(o for o in days[0]["top_offenders"] if o["route_code"] == "R_WKNULL")
    assert offender["samples"] == 6  # the NULL date's samples excluded
    assert offender["avg_min"] == 1.0  # NOT the buggy 0.5 from counting the NULL date's samples


# ---------------------------------------------------------------------------
# GET /reports/suggest -- the Insight Panel's single rule-based pick. Both
# tests use ch_client directly (the sync fixture _run_analyze/mirror_updates_
# to_ch already expect) rather than app.state.ch_client: compute_suggestion's
# RangeCtx calls never set a time_band, so they always take the agg-table
# fast path and never touch `ch` at all -- app.state.ch_client stays at
# reports_app's default None, and get_ch's lazy-503 stand-in is never
# exercised.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggest_returns_on_time_fallback_when_no_anomaly(reports_client, ch_client):
    client, agency_id, pool = reports_client
    from datetime import timedelta

    from api.range import jst_today

    today = jst_today()
    for offset in range(6, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed_route(pool, agency_id, "GOOD", "weekday", day, [30] * 25)
        await _seed_route(pool, agency_id, "BAD", "weekday", day, [600] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/suggest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_type"] == "on_time"
    assert body["route_code"] == "BAD"
    assert body.get("reason_text")


@pytest.mark.asyncio
async def test_suggest_exclude_param_narrows_candidates(reports_client, ch_client):
    client, agency_id, pool = reports_client
    from datetime import timedelta

    from api.range import jst_today

    today = jst_today()
    for offset in range(6, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed_route(pool, agency_id, "ONLY", "weekday", day, [600] * 25)
    _run_analyze(agency_id, ch_client)

    # A malformed entry (no ":") alongside the well-formed one must be
    # silently ignored at the HTTP boundary, not 500 -- the endpoint only
    # keeps entries it can split into (report_type, route_code).
    resp = await client.get(f"/api/{agency_id}/reports/suggest?exclude=on_time:ONLY&exclude=garbage")
    assert resp.status_code == 200
    assert resp.json() is None
