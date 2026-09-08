"""Tests for the v2 reports endpoints (live queries, no snapshots table)."""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from pipeline.reports import dwell_run as dwell_run_module
from pipeline.reports import service_delivered as service_delivered_module
from tests.api.test_network import _seed_service_delivered_daily, _seed_static_schedule, _set_ingest_strategy

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


def _trust_dwell_run(monkeypatch, *agency_ids):
    """See tests/api/test_network.py's `_trust_service_delivered` docstring --
    same reasoning, for the dwell_run report's independent confirmed-set
    gate."""
    monkeypatch.setattr(dwell_run_module, "RT_FIELD_COVERAGE_CONFIRMED_AGENCIES", frozenset(agency_ids))


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
    """The list endpoint returns the canonical 11 report types regardless of data."""
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
        "dwell_run",
        "council_summary",
        "delay_certificate",
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
async def test_on_time_no_params_is_byte_identical_to_legacy(reports_client, ch_client):
    """Passing no tolerance params -- and passing preset=legacy_60s, an
    explicit opt-in spelling of the same thing -- must both still read the
    exact on_time_count column and match the pre-existing percentage,
    unaffected by the new histogram-based tolerance path."""
    client, agency_id, pool = reports_client
    day = "2026-05-15"
    await _seed_route(pool, agency_id, "RLEGACY", "平日", day, [-200] * 8 + [30] * 12 + [90] * 5)
    _run_analyze(agency_id, ch_client)

    baseline = (await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}")).json()["rows"]
    preset = (await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}&preset=legacy_60s")).json()[
        "rows"
    ]
    r_base = next(x for x in baseline if x[0] == "RLEGACY")
    r_preset = next(x for x in preset if x[0] == "RLEGACY")
    assert r_base == r_preset
    assert float(r_base[2]) == 80.0  # (8 + 12) / 25 on-time at the legacy <=60s cutoff


@pytest.mark.asyncio
async def test_on_time_custom_tolerance_matches_hand_computed_window(reports_client, ch_client):
    """An explicit (early_tolerance_sec, late_tolerance_sec) window reads
    agg_route_daily_dist's histogram instead of the exact on_time_count
    column. Both bounds here (-60s, 60s) land exactly on a histogram bucket
    edge (60 seconds away from LO=-300), so the estimate is exact and
    hand-countable: the -200s group falls outside the tighter early
    tolerance and no longer counts as on-time, unlike the legacy unbounded-
    early default. The late group is seeded at 150s (well clear of the
    ``[60, 120)`` bucket the ``late_tolerance_sec=60`` boundary itself falls
    in) so this stays an exact count rather than a one-bucket estimate."""
    client, agency_id, pool = reports_client
    day = "2026-05-16"
    await _seed_route(pool, agency_id, "RTOL", "平日", day, [-200] * 8 + [30] * 12 + [150] * 5)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(
        f"/api/{agency_id}/reports/on_time?from={day}&to={day}&early_tolerance_sec=60&late_tolerance_sec=60"
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r = next(x for x in rows if x[0] == "RTOL")
    assert float(r[2]) == 48.0  # only the 30s group (12/25) is within [-60, 60]


@pytest.mark.asyncio
async def test_worst_5min_custom_late_tolerance_reads_histogram(reports_client, ch_client):
    """An explicit late_tolerance_sec (even the legacy value, 300) opts into
    the histogram-based estimate instead of the exact late5_count column.
    300 lands on a bucket edge here, so the estimate is exact."""
    client, agency_id, pool = reports_client
    day = "2026-05-17"
    await _seed_route(pool, agency_id, "RSEV", "平日", day, [30] * 8 + [200] * 2 + [400] * 3)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/worst_5min?from={day}&to={day}&late_tolerance_sec=300")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    r = next(x for x in rows if x[0] == "RSEV")
    assert r[2] == 3  # only the 400s group is later than the 300s cutoff


@pytest.mark.asyncio
async def test_on_time_default_response_carries_legacy_definition_metadata(reports_client, ch_client):
    """The JSON response's `definition` block must reflect the legacy_60s
    default when no tolerance params are passed -- the always-visible
    metadata block a comparison view renders."""
    client, agency_id, pool = reports_client
    day = "2026-05-18"
    await _seed_route(pool, agency_id, "RDEF", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}")
    assert resp.status_code == 200
    definition = resp.json()["definition"]
    assert definition["preset"] == "legacy_60s"
    assert definition["early_tolerance_sec"] is None
    assert definition["late_tolerance_sec"] == 60
    assert definition["exclusion_threshold_sec"] == 7200
    assert definition["measurement_point"] == "all_stops_all_observations"
    assert definition["dedup_rule"] == "latest_observation_per_stop_event"


@pytest.mark.asyncio
async def test_on_time_custom_tolerance_response_carries_exact_custom_definition(reports_client, ch_client):
    """Exporting a report with non-default tolerances must show those EXACT
    values in the metadata block, not the legacy_60s defaults."""
    client, agency_id, pool = reports_client
    day = "2026-05-19"
    await _seed_route(pool, agency_id, "RCUS", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(
        f"/api/{agency_id}/reports/on_time?from={day}&to={day}&early_tolerance_sec=30&late_tolerance_sec=120"
    )
    assert resp.status_code == 200
    definition = resp.json()["definition"]
    assert definition["preset"] == "custom"
    assert definition["early_tolerance_sec"] == 30
    assert definition["late_tolerance_sec"] == 120


@pytest.mark.asyncio
async def test_worst_5min_custom_tolerance_response_carries_exact_custom_definition(reports_client, ch_client):
    client, agency_id, pool = reports_client
    day = "2026-05-20"
    await _seed_route(pool, agency_id, "RW5", "平日", day, [400] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/worst_5min?from={day}&to={day}&late_tolerance_sec=180")
    assert resp.status_code == 200
    definition = resp.json()["definition"]
    assert definition["preset"] == "custom"
    assert definition["early_tolerance_sec"] is None
    assert definition["late_tolerance_sec"] == 180


@pytest.mark.asyncio
async def test_reports_without_tolerance_concept_still_carry_dedup_and_exclusion_metadata(reports_client, ch_client):
    """ranking has no on-time/late tolerance concept (preset/tolerances are
    None), but the shared dedup rule and exclusion threshold still apply to
    every aggregate it reads, so the metadata block must still surface
    them."""
    client, agency_id, pool = reports_client
    day = "2026-05-21"
    await _seed_route(pool, agency_id, "RRANK", "平日", day, [120] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={day}&to={day}")
    assert resp.status_code == 200
    definition = resp.json()["definition"]
    assert definition["preset"] is None
    assert definition["early_tolerance_sec"] is None
    assert definition["late_tolerance_sec"] is None
    assert definition["exclusion_threshold_sec"] == 7200
    assert definition["dedup_rule"] == "latest_observation_per_stop_event"


@pytest.mark.asyncio
async def test_csv_export_definition_metadata_preamble_reflects_custom_tolerance(reports_client, ch_client):
    """Exporting a CSV with non-default tolerances must render those exact
    values in the leading definition-metadata row, not the legacy_60s
    defaults."""
    import csv
    import io

    client, agency_id, pool = reports_client
    day = "2026-05-22"
    await _seed_route(pool, agency_id, "RCSV", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(
        f"/api/{agency_id}/reports/on_time?from={day}&to={day}&early_tolerance_sec=45&late_tolerance_sec=90&format=csv"
    )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    preamble = rows[0][0]
    assert "45秒" in preamble
    assert "90秒" in preamble
    assert "custom" in preamble
    assert "legacy_60s" not in preamble
    assert "7200" in preamble
    # The header row (系統コード, ...) must still be exactly the second row.
    assert rows[1] == ["系統コード", "種別", "定時率(%)", "平均遅延(分)", "観測数", "確信度低"]


@pytest.mark.asyncio
async def test_csv_export_definition_metadata_preamble_defaults_to_legacy(reports_client, ch_client):
    client, agency_id, pool = reports_client
    day = "2026-05-23"
    await _seed_route(pool, agency_id, "RCSVDEF", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}&format=csv")
    assert resp.status_code == 200
    import csv
    import io

    rows = list(csv.reader(io.StringIO(resp.text)))
    preamble = rows[0][0]
    assert "legacy_60s" in preamble


@pytest.mark.asyncio
async def test_reports_unknown_preset_is_rejected(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/on_time?preset=nope")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reports_preset_combined_with_explicit_tolerance_is_rejected(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/on_time?preset=legacy_60s&late_tolerance_sec=90")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reports_early_tolerance_sec_rejected_for_worst_5min(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/worst_5min?early_tolerance_sec=60")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reports_late_tolerance_sec_rejected_for_ranking(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/ranking?late_tolerance_sec=90")
    assert resp.status_code == 400


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
async def test_on_time_csv_export_renders_low_confidence_marker(reports_client, ch_client):
    """format=csv for the on_time report must render the trailing
    `low_confidence` flag as the same human-readable marker used by
    frontend/src/components/ReportTable.tsx's fmtConfidence and
    frontend/src/tabs/ask/RichResult.tsx (a short mark when true, blank
    when false), not csv.writer's literalized "True"/"False" string form
    of the raw Python bool."""
    import csv
    import io

    client, agency_id, pool = reports_client
    day = "2026-05-14"
    await _seed_route(pool, agency_id, "R_UNCERTAIN", "平日", day, [30] * 20 + [600] * 5)
    await _seed_route(pool, agency_id, "R_CONFIDENT", "平日", day, [30] * 270 + [600] * 30)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/on_time?from={day}&to={day}&format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "True" not in body
    assert "False" not in body

    rows = list(csv.reader(io.StringIO(body)))
    # rows[0] is the definition-metadata preamble line (see
    # test_csv_export_definition_metadata_preamble_reflects_custom_tolerance
    # below) -- the column header is the second row.
    header, data_rows = rows[1], rows[2:]
    assert header[-1] == "確信度低"
    uncertain = next(r for r in data_rows if r[0] == "R_UNCERTAIN")
    confident = next(r for r in data_rows if r[0] == "R_CONFIDENT")
    assert uncertain[-1] == "幅あり"
    assert confident[-1] == ""


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
    # sum_delay_sec is the exact raw-seconds total behind that cell's
    # avg_min (agg_hour_daily's own column, populated by analyze()) — 12
    # observations at 180s each, on 2026-05-19.
    cell_19 = next(c for c in hourly if c["date"] == "2026-05-19" and c["hour"] == 10)
    assert cell_19["sum_delay_sec"] == 2160
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
    # The ClickHouse live-fallback branch also reports the exact raw-seconds
    # total (6 observations at 180s each) alongside the rounded avg_min.
    cell_6 = next(c for c in hourly if c["hour"] == 6)
    assert cell_6["sum_delay_sec"] == 1080


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


def _run_analyze_from_ch(agency_id, ch_client):
    """Like `_run_analyze`, but for dwell_run tests that seed ClickHouse
    `updates` directly (via `insert_updates`) instead of mirroring from
    Postgres `updates` -- `arr_delay` exists only in the ClickHouse `updates`
    schema (Postgres `updates` has zero production readers and was never
    extended to carry it -- see the `transit-app-gotchas` skill), so
    `mirror_updates_to_ch` can't carry an `arr_delay` value through."""
    import os

    import psycopg2

    from pipeline.analyze import analyze

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        analyze(agency_id, conn, ch_client)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_dwell_run_not_available_for_non_static_join_agency(reports_client):
    """The fixture agency's ingest_strategy is left NULL (not static_join),
    so the decomposition must render an explicit 'not available' state,
    never a zero/blank one."""
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/dwell_run")
    assert resp.status_code == 200
    payload = resp.json()["rows"][0]
    assert payload["available"] is False
    assert payload["routes"] == []


@pytest.mark.asyncio
async def test_dwell_run_not_available_for_unconfirmed_static_join_agency(reports_client, monkeypatch):
    """`ingest_strategy == 'static_join'` alone is not sufficient trust for
    arr_delay-derived dwell/running times: an agency outside
    `RT_FIELD_COVERAGE_CONFIRMED_AGENCIES` must still render 'not available'
    even though its ingest_strategy matches. Explicitly empties the confirmed
    set so this holds regardless of which real agency_ids happen to be in
    it."""
    client, agency_id, pool = reports_client
    _trust_dwell_run(monkeypatch)  # empty set -- no agency_id is trusted
    await pool.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/dwell_run")
    assert resp.status_code == 200
    payload = resp.json()["rows"][0]
    assert payload["available"] is False
    assert payload["routes"] == []


@pytest.mark.asyncio
async def test_dwell_run_time_band_filter_is_explicitly_unsupported(reports_client, monkeypatch):
    """Even for an available (static_join, confirmed-set) agency, a time_band
    filter isn't servable by this decomposition (no live-scan fallback) --
    must say so explicitly rather than silently ignoring the filter."""
    client, agency_id, pool = reports_client
    _trust_dwell_run(monkeypatch, agency_id)
    await pool.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/dwell_run?time_band=morning")
    assert resp.status_code == 200
    payload = resp.json()["rows"][0]
    assert payload["available"] is True
    assert payload["time_band_supported"] is False
    assert payload["routes"] == []


@pytest.mark.asyncio
async def test_dwell_run_csv_export_not_available_says_so_instead_of_empty(reports_client):
    """format=csv for a non-static_join agency must render the same explicit
    'not available' message the JSON/text response does -- an empty
    (header-only) CSV would be indistinguishable from "genuinely zero
    observations", exactly the misleading blank the feature exists to
    avoid."""
    import csv
    import io

    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/dwell_run?format=csv")
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    # row 0: definition preamble, row 1: column header, row 2: the message.
    assert len(rows) == 3
    assert rows[2][0]  # non-empty explanatory message, not silently absent


@pytest.mark.asyncio
async def test_dwell_run_csv_export_time_band_unsupported_says_so_instead_of_empty(reports_client, monkeypatch):
    """Same as the not-available case, but for the time_band_supported=False
    state (still an available static_join, confirmed-set agency)."""
    import csv
    import io

    client, agency_id, pool = reports_client
    _trust_dwell_run(monkeypatch, agency_id)
    await pool.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
    resp = await client.get(f"/api/{agency_id}/reports/dwell_run?time_band=morning&format=csv")
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert len(rows) == 3
    assert rows[2][0]


@pytest.mark.asyncio
async def test_dwell_run_reads_agg_with_known_synthetic_values(reports_client, ch_client, monkeypatch):
    """End-to-end: seed a static schedule + ClickHouse `arr_delay`/`dep_delay`
    for a 3-stop trip (same hand-computed fixture as
    tests/unit/test_dwell_run.py and tests/pipeline/test_analyze.py's
    agg_route_daily_dwell_run test), analyze(), then read it back through the
    HTTP endpoint and check the exact averages."""
    from datetime import datetime, timezone

    from pipeline.clickhouse import insert_updates

    client, agency_id, pool = reports_client
    _trust_dwell_run(monkeypatch, agency_id)
    await pool.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
    await pool.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES ($1, 'S1', 'Test Stop')", agency_id
    )
    for seq, arr, dep in [(1, "10:00:00", "10:00:00"), (2, "10:05:00", "10:06:00"), (3, "10:10:00", "10:10:00")]:
        await pool.execute(
            "INSERT INTO static_stop_times "
            "(agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES ($1, 'T1', $2, 'S1', $3, $4)",
            agency_id,
            seq,
            arr,
            dep,
        )

    day = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)  # 2026-04-01 11:00 JST
    # (file_name, captured_at, trip_id, service_type, scheduled_time, route_code,
    #  stop_sequence, dep_delay, stop_id, arr_delay, sched_rel_trip, sched_rel_stop, feed_timestamp)
    rows = [
        ("f.pb", day, "T1", "平日", "10:00:00", "44", 1, 30, "S1", None, None, None, None),
        ("f.pb", day, "T1", "平日", "10:00:00", "44", 2, 50, "S1", 20, None, None, None),
        ("f.pb", day, "T1", "平日", "10:00:00", "44", 3, 10, "S1", 10, None, None, None),
    ]
    insert_updates(ch_client, agency_id, rows)
    _run_analyze_from_ch(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/dwell_run?from=2026-04-01&to=2026-04-01")
    assert resp.status_code == 200
    payload = resp.json()["rows"][0]
    assert payload["available"] is True
    assert payload["time_band_supported"] is True
    routes = payload["routes"]
    assert len(routes) == 1
    r = routes[0]
    assert r["route_code"] == "44"
    assert r["dwell_samples"] == 2
    assert r["dwell_avg_sec"] == pytest.approx(45.0)  # (90 + 0) / 2
    assert r["run_samples"] == 2
    assert r["run_avg_sec"] == pytest.approx(245.0)  # (290 + 200) / 2


# ---------------------------------------------------------------------------
# council_summary / delay_certificate
# ---------------------------------------------------------------------------
# The report list itself is covered by test_reports_list_returns_static_metadata
# above (now asserting the full 11-type set), so no separate listing test here.


@pytest.mark.asyncio
async def test_council_summary_pools_on_time_across_routes_not_a_naive_average(reports_client, ch_client):
    """The pooled on-time rate must be sample-weighted across every route,
    not an unweighted mean of each route's own rate: R1 (25 samples, 60%
    on-time) and R2 (10 samples, 100% on-time) pool to 71.4% ((15+10)/35),
    not the naive average of the two rates (80%)."""
    client, agency_id, pool = reports_client
    day = "2026-06-10"
    await _seed_route(pool, agency_id, "R1", "平日", day, [30] * 15 + [90] * 10)  # 15/25 on-time (<=60s)
    await _seed_route(pool, agency_id, "R2", "平日", day, [30] * 10)  # 10/10 on-time
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/council_summary?from={day}&to={day}")
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    # (on_time_pct, avg_delay_min, samples, planned_trips, executed_trips, service_delivered_pct)
    assert row[2] == 35
    assert float(row[0]) == 71.4  # (15+10)/35*100 = 71.428... -> 71.4, not the naive-average 80.0


@pytest.mark.asyncio
async def test_council_summary_custom_tolerance_pools_histogram_not_naive_average(reports_client, ch_client):
    """Same pooling guarantee as the legacy-preset test above, but on the
    query-time histogram-estimate path (an explicit early/late tolerance).
    R1 (-200/30/150 split, mirroring the exact-bucket-edge on_time test) has
    12/25 on-time within [-60, 60]; R2 (10 samples, all at 30s) has 10/10.
    Pooled: 22/35 = 62.857...% -> 62.9, not the naive average (74.0)."""
    client, agency_id, pool = reports_client
    day = "2026-06-11"
    await _seed_route(pool, agency_id, "R1", "平日", day, [-200] * 8 + [30] * 12 + [150] * 5)
    await _seed_route(pool, agency_id, "R2", "平日", day, [30] * 10)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(
        f"/api/{agency_id}/reports/council_summary?from={day}&to={day}&early_tolerance_sec=60&late_tolerance_sec=60"
    )
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row[2] == 35
    assert float(row[0]) == 62.9


@pytest.mark.asyncio
async def test_council_summary_footnotes_change_with_custom_tolerance(reports_client, ch_client):
    """The report template's rendered footnotes must show the exact
    tolerance/preset actually used, not the legacy_60s defaults -- mirrors
    the CSV preamble's own guarantee (pipeline.reports.definition)."""
    client, agency_id, pool = reports_client
    day = "2026-06-12"
    await _seed_route(pool, agency_id, "R1", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    default_resp = await client.get(f"/api/{agency_id}/reports/council_summary?from={day}&to={day}")
    custom_resp = await client.get(
        f"/api/{agency_id}/reports/council_summary?from={day}&to={day}&early_tolerance_sec=45&late_tolerance_sec=90"
    )
    assert default_resp.status_code == custom_resp.status_code == 200
    default_text = default_resp.json()["text"]
    custom_text = custom_resp.json()["text"]
    assert "legacy_60s" in default_text
    assert "45秒" in custom_text
    assert "90秒" in custom_text
    assert "custom" in custom_text
    assert "legacy_60s" not in custom_text


@pytest.mark.asyncio
async def test_council_summary_service_delivered_not_available_footnote(reports_client, ch_client):
    """An agency with no static schedule (planned_trips == 0, the default
    reports_client fixture's agency) must show "not available", never a
    misleading 100%, and the footnotes must say so explicitly."""
    client, agency_id, pool = reports_client
    day = "2026-06-13"
    await _seed_route(pool, agency_id, "R1", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/council_summary?from={day}&to={day}")
    assert resp.status_code == 200
    body = resp.json()
    row = body["rows"][0]
    assert row[4] is None  # executed_trips
    assert row[5] is None  # service_delivered_pct
    assert "計測できません" in body["text"]


@pytest.mark.asyncio
async def test_council_summary_service_delivered_available_when_static_join_and_scheduled(
    reports_client, ch_client, monkeypatch
):
    client, agency_id, pool = reports_client
    monkeypatch.setattr(service_delivered_module, "RT_FIELD_COVERAGE_CONFIRMED_AGENCIES", frozenset({agency_id}))
    day = "2026-06-14"
    await _seed_route(pool, agency_id, "R1", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)
    await _seed_static_schedule(
        pool, agency_id, service_id="WD", trip_ids=["T1", "T2", "T3", "T4"], svc_date="20260614"
    )
    await _set_ingest_strategy(pool, agency_id, "static_join")
    await _seed_service_delivered_daily(pool, agency_id, [("2026-06-14", 1)])

    resp = await client.get(f"/api/{agency_id}/reports/council_summary?from={day}&to={day}")
    assert resp.status_code == 200
    body = resp.json()
    row = body["rows"][0]
    assert row[3] == 4  # planned_trips
    assert row[4] == 3  # executed_trips
    assert row[5] == 75.0  # service_delivered_pct
    assert "計測できません" not in body["text"]


@pytest.mark.asyncio
async def test_council_summary_csv_export_includes_extra_footnote_rows(reports_client, ch_client):
    import csv
    import io

    client, agency_id, pool = reports_client
    day = "2026-06-15"
    await _seed_route(pool, agency_id, "R1", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    resp = await client.get(f"/api/{agency_id}/reports/council_summary?from={day}&to={day}&format=csv")
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    # rows[0] = definition preamble, rows[1..] = extra footnotes, then the
    # column header row, matched by locating it rather than a fixed index --
    # the number of footnote rows (freshness/quality/service-delivered
    # caveats) can legitimately vary by scenario.
    header_idx = rows.index(["定時率(%)", "平均遅延(分)", "観測数", "計画本数", "運行本数", "運行実績率(%)"])
    assert header_idx >= 1
    footnote_cells = [r[0] for r in rows[1:header_idx]]
    assert any("計測できません" in c for c in footnote_cells)
    data_row = rows[header_idx + 1]
    assert data_row[2] == "25"  # samples


@pytest.mark.asyncio
async def test_delay_certificate_excludes_at_threshold_includes_one_above(reports_client, ch_client, ch_async_client):
    """Core boundary check: a synthetic trip's dep_delay exactly AT the
    threshold must be excluded ("exceeds", not ">="); one second above must
    be included."""
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-06-20"
    threshold = 300
    await _seed_route(pool, agency_id, "RCERT", "平日", day, [threshold, threshold + 1])
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec={threshold}")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    cert_rows = [r for r in rows if r[1] == "RCERT"]
    delays = [r[6] for r in cert_rows]
    assert threshold not in delays
    assert (threshold + 1) in delays
    assert len(cert_rows) == 1


@pytest.mark.asyncio
async def test_delay_certificate_uses_default_threshold_when_omitted(reports_client, ch_client, ch_async_client):
    from api.main import app
    from pipeline.reports import DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-06-21"
    await _seed_route(
        pool,
        agency_id,
        "RDEF",
        "平日",
        day,
        [DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC, DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC + 1],
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}")
    assert resp.status_code == 200
    rows = [r for r in resp.json()["rows"] if r[1] == "RDEF"]
    assert len(rows) == 1
    assert rows[0][6] == DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC + 1


@pytest.mark.asyncio
async def test_delay_certificate_row_shape_and_actual_time_shift(reports_client, ch_client, ch_async_client):
    """Row shape is (agency_name, route_code, service_type, date,
    scheduled_time, actual_time, dep_delay); actual_time is scheduled_time
    shifted by dep_delay seconds. _seed_route schedules every row at 10:00:00."""
    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-06-22"
    await _seed_route(pool, agency_id, "RSHIFT", "平日", day, [400])
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec=300")
    assert resp.status_code == 200
    rows = [r for r in resp.json()["rows"] if r[1] == "RSHIFT"]
    assert len(rows) == 1
    _agency_name, route_code, service_type, date_str, scheduled_time, actual_time, dep_delay = rows[0]
    assert route_code == "RSHIFT"
    assert service_type == "平日"
    assert date_str == day
    assert scheduled_time == "10:00:00"
    assert dep_delay == 400
    assert actual_time == "10:06:40"  # 10:00:00 + 400s


@pytest.mark.asyncio
async def test_delay_certificate_csv_export(reports_client, ch_client, ch_async_client):
    import csv
    import io

    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-06-23"
    await _seed_route(pool, agency_id, "RCSV", "平日", day, [400])
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(
        f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec=300&format=csv"
    )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    header_idx = rows.index(["事業者名", "系統コード", "種別", "日付", "定刻", "実績時刻", "遅延(秒)"])
    data = [r for r in rows[header_idx + 1 :] if r and r[1] == "RCSV"]
    assert len(data) == 1
    assert data[0][6] == "400"


@pytest.mark.asyncio
async def test_delay_certificate_csv_export_includes_threshold_footnote(reports_client, ch_client, ch_async_client):
    import csv
    import io

    from api.main import app

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-06-26"
    await _seed_route(pool, agency_id, "RFOOT", "平日", day, [400])
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(
        f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec=300&format=csv"
    )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    header_idx = rows.index(["事業者名", "系統コード", "種別", "日付", "定刻", "実績時刻", "遅延(秒)"])
    footnote_cells = [r[0] for r in rows[1:header_idx]]
    assert any("300" in c for c in footnote_cells)


@pytest.mark.asyncio
async def test_delay_certificate_uses_origin_stop_delay_per_trip(reports_client, ch_client, ch_async_client):
    """A single trip_id spans many stop events; compute_delay_certificate
    must collapse them to ONE row per (trip_id, date) using the origin
    stop's (lowest stop_sequence) departure delay, not emit one row per
    stop event."""
    from datetime import datetime, time

    from api.main import app
    from tests.conftest import mirror_updates_to_ch

    client, agency_id, pool = reports_client
    app.state.ch_client = ch_async_client
    day = "2026-06-27"
    trip_id = f"RMULTI-{day}-trip-0"
    async with pool.acquire() as conn:
        # stop_sequence=1 (origin): dep_delay=400, exceeds the 300s threshold.
        # stop_sequence=2 (a later stop on the same trip): dep_delay=100,
        # which alone would NOT exceed the threshold.
        for seq, delay in [(1, 400), (2, 100)]:
            await conn.execute(
                "INSERT INTO updates "
                "(agency_id, trip_id, route_code, service_type, scheduled_time, "
                " stop_sequence, dep_delay, captured_at, file_name) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                agency_id,
                trip_id,
                "RMULTI",
                "平日",
                time(10, 0),
                seq,
                delay,
                datetime.fromisoformat(f"{day}T10:{seq:02d}:00"),
                f"test/RMULTI/{day}/{seq}.pb",
            )
    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec=300")
    assert resp.status_code == 200
    rows = [r for r in resp.json()["rows"] if r[1] == "RMULTI"]
    assert len(rows) == 1
    assert rows[0][6] == 400  # origin stop's dep_delay, not the later stop's


@pytest.mark.asyncio
async def test_delay_certificate_rejects_on_time_only_params(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?early_tolerance_sec=60")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_threshold_sec_rejected_for_non_delay_certificate_report(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/on_time?threshold_sec=100")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delay_certificate_requires_a_clickhouse_client():
    """Unlike every other report in this package, delay_certificate has no
    fast path at all -- it must raise rather than silently return an empty
    export when no ClickHouse client is available."""
    from pipeline.reports.council import compute_delay_certificate

    with pytest.raises(RuntimeError):
        await compute_delay_certificate(1, object(), object(), None)


@pytest.mark.asyncio
async def test_council_summary_degrades_is_stale_when_clickhouse_freshness_probe_fails(reports_client, ch_client):
    """Same degrade shape as pipeline.reports.network.compute_network_summary:
    a ClickHouse hiccup on the freshness-only probe must not fail the whole
    report -- is_stale degrades to False (agg_day, None) rather than 500ing
    the on-time/service-delivered numbers, which come entirely from Postgres."""
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports.council import compute_council_summary

    _client, agency_id, pool = reports_client
    day = "2026-06-16"
    await _seed_route(pool, agency_id, "R1", "平日", day, [30] * 25)
    _run_analyze(agency_id, ch_client)

    class _BrokenCh:
        async def query(self, *args, **kwargs):
            raise RuntimeError("simulated ClickHouse outage")

    ctx = RangeCtx(from_date=date(2026, 6, 16), to_date=date(2026, 6, 16))
    async with pool.acquire() as conn:
        payload = await compute_council_summary(agency_id, ctx, conn, _BrokenCh())
    assert payload["on_time_pct"] == 100.0
    assert payload["is_stale"] is False
