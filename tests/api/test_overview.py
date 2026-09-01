"""Tests for the 概況 (Overview) tab endpoint.

The Overview backend reads from the pre-aggregated ``agg_daily_trend`` and
``agg_route_hour`` tables (not the row-level ``updates`` table) so cold
loads stay sub-second on multi-month windows. Tests seed both layers:
``updates`` is kept around so any future helpers that still touch it
stay covered, while ``agg_*`` is what the Overview reads.
"""

import os
from datetime import date, datetime, time, timedelta, timezone

import asyncpg
import pytest

from api.range import RangeCtx


async def _seed_agg_daily(
    aconn,
    agency_id: int,
    date_,
    route_code: str,
    service_type: str,
    avg_min: float,
    samples: int,
    sum_late_sec: int | None = None,
):
    """Insert (or upsert) one ``agg_daily_trend`` row.

    ``date_`` may be a :class:`datetime.date` or an ISO string;
    ``agg_daily_trend.date`` is TEXT per schema, so we coerce both shapes.

    ``sum_delay_sec`` is back-derived as ``round(avg_min * 60 * samples)`` —
    the exact raw-seconds sum a real analyze() run would have stored for an
    ``avg_min`` that is itself already exact (true of every value seeded by
    this helper), so the fast path's ``SUM(sum_delay_sec) / SUM(samples)``
    reproduces the same figure these tests were already asserting on.

    ``sum_late_sec`` defaults to ``round(max(avg_min, 0) * 60 * samples)`` —
    exact whenever every observation behind this row shares ``avg_min``'s
    sign (true of every caller that seeds a uniformly early or uniformly
    late day). A caller exercising a day that MIXES early and late trips —
    exactly the case where the per-observation clamped sum and the
    clamped-average approximation this column replaced used to diverge —
    must pass the true value explicitly; there is no way to derive it from
    ``avg_min``/``samples`` alone.
    """
    iso = date_.isoformat() if hasattr(date_, "isoformat") else str(date_)
    samples = int(samples)
    sum_delay_sec = round(float(avg_min) * 60 * samples)
    if sum_late_sec is None:
        sum_late_sec = round(max(float(avg_min), 0.0) * 60 * samples)
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec, sum_late_sec) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (agency_id, date, route_code, service_type) DO UPDATE "
        "SET avg_min = EXCLUDED.avg_min, samples = EXCLUDED.samples, sum_delay_sec = EXCLUDED.sum_delay_sec, "
        "sum_late_sec = EXCLUDED.sum_late_sec",
        agency_id,
        iso,
        route_code,
        service_type,
        float(avg_min),
        samples,
        sum_delay_sec,
        sum_late_sec,
    )


async def _seed_agg_route_hour(
    aconn,
    agency_id: int,
    route_code: str,
    service_type: str,
    scheduled_time: str,
    avg_min: float,
    samples: int,
):
    """Insert one ``agg_route_hour`` row. ``scheduled_time`` is HH:MM text;
    schema column is TIME (post 0011), so we cast on the server side.

    ``sum_delay_sec`` is back-derived as ``round(avg_min * 60 * samples)``, the
    same exact-reconstruction convention ``_seed_agg_daily`` above uses, so
    ``_peak_hour``'s ``SUM(sum_delay_sec) / SUM(samples)`` fast path reproduces
    the same figure these tests were already asserting on.
    """
    sum_delay_sec = round(float(avg_min) * 60 * int(samples))
    await aconn.execute(
        "INSERT INTO agg_route_hour "
        "(agency_id, route_code, service_type, scheduled_time, avg_min, p50_min, p90_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, $3, ($4::text)::time, $5, NULL, NULL, $6, $7) "
        "ON CONFLICT (agency_id, route_code, service_type, scheduled_time) DO UPDATE "
        "SET avg_min = EXCLUDED.avg_min, samples = EXCLUDED.samples, sum_delay_sec = EXCLUDED.sum_delay_sec",
        agency_id,
        route_code,
        service_type,
        scheduled_time,
        float(avg_min),
        int(samples),
        sum_delay_sec,
    )


async def _seed_agg_hour_daily(aconn, agency_id, date_, hour, avg_min, samples, sum_delay_sec=None):
    """Insert one ``agg_hour_daily`` row (per-day, per-hour-of-day).

    sum_delay_sec defaults to the exact seconds-sum an unrounded avg_min/samples
    pair would back, so pooling via SUM(sum_delay_sec)/SUM(samples) (
    _peak_hour_by_dow's fast path) agrees with the plain avg_min this helper's
    callers assert on, unless a test explicitly passes a mismatched value to
    prove the exact-vs-reweighted divergence -- mirrors _seed_agg_route_hour_dow's
    identical pattern.
    """
    if sum_delay_sec is None:
        sum_delay_sec = round(float(avg_min) * 60 * int(samples))
    iso = date_.isoformat() if hasattr(date_, "isoformat") else str(date_)
    await aconn.execute(
        "INSERT INTO agg_hour_daily (agency_id, date, hour, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, ($2::text)::date, $3, $4, $5, $6) "
        "ON CONFLICT (agency_id, date, hour) DO UPDATE "
        "SET avg_min = EXCLUDED.avg_min, samples = EXCLUDED.samples, sum_delay_sec = EXCLUDED.sum_delay_sec",
        agency_id,
        iso,
        int(hour),
        float(avg_min),
        int(samples),
        int(sum_delay_sec),
    )


async def _seed_agg_route_stats(aconn, agency_id, route_code, service_type, avg_min, p90_min, late5_pct, samples):
    await aconn.execute(
        "INSERT INTO agg_route_stats "
        "(agency_id, route_code, service_type, avg_min, p90_min, late5_pct, samples) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) "
        "ON CONFLICT (agency_id, route_code, service_type) DO UPDATE "
        "SET avg_min=EXCLUDED.avg_min, p90_min=EXCLUDED.p90_min, "
        "    late5_pct=EXCLUDED.late5_pct, samples=EXCLUDED.samples",
        agency_id,
        route_code,
        service_type,
        float(avg_min),
        float(p90_min),
        float(late5_pct),
        int(samples),
    )


async def _seed_agg_route_hour_dow(
    aconn, agency_id, route_code, service_type, dow, hour, avg_min, samples, sum_delay_sec=None
):
    # sum_delay_sec defaults to the exact seconds-sum an unrounded avg_min/samples
    # pair would back, so pooling via SUM(sum_delay_sec)/SUM(samples) (peak_hour_
    # breakdown's dow=None path) agrees with the plain avg_min this helper's
    # callers assert on, unless a test explicitly passes a mismatched value to
    # prove the exact-vs-reweighted divergence.
    if sum_delay_sec is None:
        sum_delay_sec = round(float(avg_min) * 60 * int(samples))
    await aconn.execute(
        "INSERT INTO agg_route_hour_dow "
        "(agency_id, route_code, service_type, dow, hour, avg_min, samples, sum_delay_sec) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) "
        "ON CONFLICT (agency_id, route_code, service_type, dow, hour) DO UPDATE "
        "SET avg_min=EXCLUDED.avg_min, samples=EXCLUDED.samples, sum_delay_sec=EXCLUDED.sum_delay_sec",
        agency_id,
        route_code,
        service_type,
        dow,
        hour,
        float(avg_min),
        int(samples),
        int(sum_delay_sec),
    )


async def _seed_agg_route_daily(aconn, agency_id, date_, route_code, service_type, avg_delay_sec, samples):
    await aconn.execute(
        "INSERT INTO agg_route_daily "
        "(agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        " trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
        "ON CONFLICT (agency_id, date, route_code, service_type) DO NOTHING",
        agency_id,
        date_,
        route_code,
        service_type,
        int(avg_delay_sec),
        int(avg_delay_sec),
        1,
        int(samples),
        datetime.combine(date_, time(12, 0), tzinfo=timezone.utc),
        int(avg_delay_sec) * int(samples),
    )


@pytest.mark.asyncio
async def test_overview_endpoint_404s_for_soft_deleted_agency(client, aconn, aagency_id):
    """get_agency() must reject soft-deleted agencies like every other
    agency lookup in the codebase, not just the admin list endpoint."""
    await aconn.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id=$1", aagency_id)
    r = await client.get(f"/api/{aagency_id}/overview/summary?from=2020-01-01&to=2020-01-07")
    assert r.status_code == 404


async def test_overview_endpoint_returns_empty_payload_when_no_data(client, aagency_id):
    """An agency with no `updates` rows in range returns a zero-filled
    OverviewSummary, not a 404 or 500."""
    # overview_summary() now declares ch=Depends(get_ch) alongside conn (Task
    # 8.5's time_band live-fallback) — resolving it needs something present
    # at app.state.ch_client. This test's default ctx never leaves the
    # ctx.time_band == 'all' fast path, so None is a safe default here
    # (same pattern as tests/api/test_reports.py's reports_app fixture).
    from api.main import app

    app.state.ch_client = None
    r = await client.get(f"/api/{aagency_id}/overview/summary?from=2020-01-01&to=2020-01-07")
    assert r.status_code == 200
    body = r.json()
    assert body["headline"]["avg_min"] is None
    assert body["headline"]["baseline_avg_min"] is None
    # Even with no data the headline echoes its 7-day window (last 7d of ctx).
    assert body["headline"]["window_from"] == "2020-01-01"
    assert body["headline"]["window_to"] == "2020-01-07"
    assert body["movers"]["worse"] == []
    assert body["movers"]["better"] == []
    assert body["concentration"]["top_routes"] == []
    assert body["peak_hour"] is None
    assert body["service_split"] == {}
    assert body["sparkline_points"] == []


@pytest.mark.asyncio
async def test_headline_avg_and_samples_from_seeded_rows(aconn, aagency_id):
    """Three observations on 2026-05-24 -> agg row with avg=3.0 min, samples=3.

    The ``updates`` inserts mirror what the live pipeline would emit; the
    matching ``agg_daily_trend`` row is what the Overview actually reads.
    """
    base = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    rows = [
        ("pb1", base, 60, 1),  # 1.0 min
        ("pb2", base + timedelta(hours=1), 180, 2),  # 3.0 min
        ("pb3", base + timedelta(hours=2), 300, 3),  # 5.0 min
    ]
    for fname, cap, dep, seq in rows:
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_x', '平日', '10:00', 'R1', $4, $5)",
            aagency_id,
            fname,
            cap,
            seq,
            dep,
        )

    # Aggregated mirror: one row per (date, route, service).
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 24), "R1", "平日", 3.0, 3)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    h = out["headline"]
    assert h["samples"] == 3
    assert h["avg_min"] == pytest.approx(3.0, abs=0.01)
    # Headline window is anchored to the latest data (2026-05-24), which
    # equals ctx.to_date in this test, so window is last 7 days of ctx.
    assert h["window_from"] == "2026-05-18"
    assert h["window_to"] == "2026-05-24"


@pytest.mark.asyncio
async def test_headline_pools_exact_sum_delay_sec_not_rounded_avg_min(aconn, aagency_id):
    """_headline_stats' fast path must pool the EXACT sum_delay_sec across
    agg_daily_trend rows, not re-weight each day's own already-rounded
    avg_min.

    Day 1 (3 samples, raw-seconds sum 124 -> analyze() rounds that day's own
    avg_min to 0.69 min) and day 2 (7 samples, raw-seconds sum 700 -> rounds
    to 1.67 min). Pooling the exact sums gives (124+700)/10/60 = 1.37333...
    -> rounds to 1.37; re-weighting the rounded 0.69/1.67 instead (the
    pre-fix pattern) gives (0.69*3 + 1.67*7)/10 = 1.376 -> rounds to 1.38, a
    measurably different (and wrong) answer that exists purely from the
    intermediate rounding.
    """
    for day, avg_min, samples, sum_delay_sec in (
        (date(2026, 5, 23), 0.69, 3, 124),
        (date(2026, 5, 24), 1.67, 7, 700),
    ):
        await aconn.execute(
            "INSERT INTO agg_daily_trend "
            "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
            "VALUES ($1, $2, 'R_HL', '平日', $3, $4, $5)",
            aagency_id,
            day.isoformat(),
            avg_min,
            samples,
            sum_delay_sec,
        )

    from pipeline.reports.overview import _headline_stats

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    avg, samples_out = await _headline_stats(aagency_id, ctx, aconn)
    assert samples_out == 10
    assert avg == 1.37  # NOT the buggy re-weighted 1.38


@pytest.mark.asyncio
async def test_headline_excludes_null_sum_delay_sec_row_from_avg_but_not_samples(aconn, aagency_id):
    """A row with ``samples`` set but ``sum_delay_sec`` still NULL (migration
    0028's column is nullable on every table — any ``agg_daily_trend`` row
    analyze() hasn't rewritten since that migration can be in this state)
    must be excluded from BOTH avg_min's numerator AND denominator, not just
    silently dropped from the numerator while still counted in the
    denominator (which would bias avg_min down). The returned ``samples``
    count, by contrast, stays the TRUE total across every row regardless of
    whether sum_delay_sec is populated — a distinct "how much data backs
    this figure" count, per pipeline/reports/overview.py's _per_route_avg
    docstring convention.

    Day 1 (5 samples, sum_delay_sec NULL) must contribute 0 to avg_min's
    pooling; day 2 (5 samples, raw-seconds sum 300 -> exact avg 1.0 min) is
    the only day that should determine avg_min. Pre-fix, day 1's 5 samples
    would still land in SUM(samples) while contributing nothing to
    SUM(sum_delay_sec), giving (0+300)/10/60=0.5 instead of the correct
    (300)/5/60=1.0.
    """
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, 'R_NULL', '平日', $3, $4, NULL)",
        aagency_id,
        date(2026, 5, 23).isoformat(),
        0.5,  # pre-migration-style rounded avg_min; not used by the fast path
        5,
    )
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, 'R_NULL', '平日', $3, $4, $5)",
        aagency_id,
        date(2026, 5, 24).isoformat(),
        1.0,
        5,
        300,
    )

    from pipeline.reports.overview import _headline_stats

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    avg, samples_out = await _headline_stats(aagency_id, ctx, aconn)
    assert samples_out == 10  # true total across both rows
    assert avg == 1.0  # NOT the buggy 0.5 from counting the NULL row's samples


@pytest.mark.asyncio
async def test_baseline_is_shifted_one_week_back(aconn, aagency_id):
    """This-week avg = 4.0 min; baseline-week avg = 2.0 min ->
    delta = +2.0 min, +100%.

    "This week" is anchored to ctx.to_date so it matches the latest data.
    """
    this_week = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    last_week = this_week - timedelta(days=7)
    # last week: two rows averaging 2 min
    for i, dep in enumerate([60, 180]):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_p', '平日', '10:00', 'R1', $4, $5)",
            aagency_id,
            f"pb_prev_{i}",
            last_week + timedelta(hours=i),
            i + 1,
            dep,
        )
    # this week: two rows averaging 4 min
    for i, dep in enumerate([180, 300]):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_t', '平日', '10:00', 'R1', $4, $5)",
            aagency_id,
            f"pb_cur_{i}",
            this_week + timedelta(hours=i),
            i + 1,
            dep,
        )

    # Aggregated mirror.
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 17), "R1", "平日", 2.0, 2)
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 24), "R1", "平日", 4.0, 2)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    h = out["headline"]
    assert h["avg_min"] == pytest.approx(4.0, abs=0.01)
    assert h["baseline_avg_min"] == pytest.approx(2.0, abs=0.01)
    assert h["delta_min"] == pytest.approx(2.0, abs=0.01)
    assert h["delta_pct"] == pytest.approx(100.0, abs=0.5)


@pytest.mark.asyncio
async def test_baseline_missing_returns_null_delta(aconn, aagency_id):
    """No data in the prior-week window -> delta fields are None."""
    cur = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    await aconn.execute(
        "INSERT INTO updates "
        "(agency_id, file_name, captured_at, trip_id, service_type, "
        " scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1, $2, $3, 'trip_x', '平日', '10:00', 'R1', 1, 120)",
        aagency_id,
        "pb_cur",
        cur,
    )

    # Aggregated mirror — only "current"-window data, no baseline.
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R1", "平日", 2.0, 1)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    h = out["headline"]
    assert h["avg_min"] is not None
    assert h["baseline_avg_min"] is None
    assert h["delta_min"] is None
    assert h["delta_pct"] is None


@pytest.mark.asyncio
async def test_movers_ranks_top_worse_and_better(aconn, aagency_id):
    """Eight routes with varied this-week vs prior-week deltas;
    worsened + improved come out sorted by |delta_min|.

    Each route seeds 10 samples per side (>= 10 sample gate in ``_movers``).
    Current week is anchored to ctx.to_date so it matches the latest data.
    Backend now returns up to 10 entries per side; the card variant on
    the frontend slices to 3, the modal variant uses all 10.
    """
    cur = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    prv = cur - timedelta(days=7)
    # (route, prior_avg_sec, current_avg_sec)
    routes = [
        ("R_A", 60, 600),  # +9 min (worst)
        ("R_B", 60, 480),  # +7 min
        ("R_C", 60, 360),  # +5 min
        ("R_D", 60, 120),  # +1 min (still worse but not top-3)
        ("R_E", 600, 60),  # -9 min (best improvement)
        ("R_F", 480, 60),  # -7 min
        ("R_G", 360, 60),  # -5 min
        ("R_H", 120, 60),  # -1 min
    ]
    SAMPLES_PER_SIDE = 10
    for code, prior_dep, cur_dep in routes:
        for i in range(SAMPLES_PER_SIDE):
            await aconn.execute(
                "INSERT INTO updates "
                "(agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1, $2, $3, 'trip_' || $4, '平日', '10:00', $4, $5, $6)",
                aagency_id,
                f"rs_prv_{code}_{i}",
                prv + timedelta(minutes=i),
                code,
                i + 1,
                prior_dep,
            )
            await aconn.execute(
                "INSERT INTO updates "
                "(agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1, $2, $3, 'trip_' || $4, '平日', '10:00', $4, $5, $6)",
                aagency_id,
                f"rs_cur_{code}_{i}",
                cur + timedelta(minutes=i),
                code,
                i + 1,
                cur_dep,
            )
        # Aggregated mirror — one row per (date, route, service) per side.
        await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 17), code, "平日", prior_dep / 60.0, SAMPLES_PER_SIDE)
        await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 24), code, "平日", cur_dep / 60.0, SAMPLES_PER_SIDE)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    worse_codes = [m["route_code"] for m in out["movers"]["worse"]]
    better_codes = [m["route_code"] for m in out["movers"]["better"]]
    # All four worsened routes are surfaced (limit is now 10), sorted by
    # signed delta_min descending.
    assert worse_codes == ["R_A", "R_B", "R_C", "R_D"]
    # All four improved routes are surfaced, sorted by signed delta_min
    # ascending (most negative first).
    assert better_codes == ["R_E", "R_F", "R_G", "R_H"]


@pytest.mark.asyncio
async def test_movers_suppresses_delta_pct_below_prv_avg_floor(aconn, aagency_id):
    """A near-zero previous-window average must not produce a triple-digit
    delta_pct off a trivial absolute change — delta_pct is suppressed to
    None instead, while delta_min (and the route's place in the ranking)
    is unaffected. A normal-magnitude route in the same response keeps its
    real delta_pct.
    """
    cur = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    prv = cur - timedelta(days=7)
    # (route, prior_avg_sec, current_avg_sec)
    routes = [
        # prior avg = 0.05 min; +0.5 min absolute would otherwise read as a
        # 1000% delta_pct despite being a trivial real-world change.
        ("R_TINY", 3, 33),
        # Normal-magnitude route: prior avg = 2 min, +1 min -> a real 50%.
        ("R_NORM", 120, 180),
    ]
    SAMPLES_PER_SIDE = 10
    for code, prior_dep, cur_dep in routes:
        for i in range(SAMPLES_PER_SIDE):
            await aconn.execute(
                "INSERT INTO updates "
                "(agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1, $2, $3, 'trip_' || $4, '平日', '10:00', $4, $5, $6)",
                aagency_id,
                f"pf_prv_{code}_{i}",
                prv + timedelta(minutes=i),
                code,
                i + 1,
                prior_dep,
            )
            await aconn.execute(
                "INSERT INTO updates "
                "(agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1, $2, $3, 'trip_' || $4, '平日', '10:00', $4, $5, $6)",
                aagency_id,
                f"pf_cur_{code}_{i}",
                cur + timedelta(minutes=i),
                code,
                i + 1,
                cur_dep,
            )
        await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 17), code, "平日", prior_dep / 60.0, SAMPLES_PER_SIDE)
        await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 24), code, "平日", cur_dep / 60.0, SAMPLES_PER_SIDE)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    worse_by_code = {m["route_code"]: m for m in out["movers"]["worse"]}

    tiny = worse_by_code["R_TINY"]
    assert tiny["delta_min"] == pytest.approx(0.5, abs=0.01)
    assert tiny["delta_pct"] is None

    norm = worse_by_code["R_NORM"]
    assert norm["delta_min"] == pytest.approx(1.0, abs=0.01)
    assert norm["delta_pct"] == pytest.approx(50.0, abs=0.5)


@pytest.mark.asyncio
async def test_mover_has_4_week_sparkline_and_streak_count(aconn, aagency_id):
    """A route worsening for 3 of the past 4 weeks reports streak=3
    and 4 ascending sparkline points (oldest-first).

    Base anchor is ctx.to_date so the latest-data anchor matches it.
    """
    base_anchor = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    weekly = [60, 120, 240, 360]  # oldest-first; current week is the last
    SAMPLES_PER_WEEK = 10
    # The helper iterates with weeks_back = 0..3, where weeks_back=0 corresponds
    # to the current 7-day window and reverses ``weekly`` so the LATEST dep
    # value lands on the latest week.
    for weeks_back, dep in enumerate(reversed(weekly)):
        when = base_anchor - timedelta(days=7 * weeks_back)
        for i in range(SAMPLES_PER_WEEK):
            await aconn.execute(
                "INSERT INTO updates "
                "(agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1, $2, $3, 'trip_x', '平日', '10:00', 'R_STR', $4, $5)",
                aagency_id,
                f"pb_str_{weeks_back}_{i}",
                when + timedelta(minutes=i),
                i + 1,
                dep,
            )
        # Aggregated mirror — one row per week, parked on the canonical
        # within-window date for that week.
        d = (base_anchor - timedelta(days=7 * weeks_back)).date()
        await _seed_agg_daily(aconn, aagency_id, d, "R_STR", "平日", dep / 60.0, SAMPLES_PER_WEEK)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    rstr = next((m for m in out["movers"]["worse"] if m["route_code"] == "R_STR"), None)
    assert rstr is not None, f"R_STR missing; movers={out['movers']}"
    assert rstr["streak_weeks"] == 3
    assert len(rstr["sparkline_points"]) == 4
    pts = rstr["sparkline_points"]
    assert pts == sorted(pts)


@pytest.mark.asyncio
async def test_concentration_top_routes_and_rest_share(aconn, aagency_id):
    """6 routes all surfaced (limit is now 20); rest_share is 0%.

    Concentration is computed as ``SUM(sum_late_sec) / 60`` per route on
    ``agg_daily_trend`` — directly proportional to total positive delay
    minutes (every row seeded here is uniformly late, so this coincides
    with ``avg_min * samples``; see the mixed-day tests below for where the
    two diverge). The card variant on the frontend slices the top 5; the
    modal variant uses all 20.
    """
    base = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    rows = [
        ("R_X", [300, 300, 300]),  # avg=5.0, n=3 -> total=15
        ("R_Y", [600]),  # avg=10.0, n=1 -> total=10
        ("R_Z", [300, 300]),  # avg=5.0, n=2 -> total=10
        ("R_W", [150, 150]),  # avg=2.5, n=2 -> total=5
        ("R_V", [240, 240]),  # avg=4.0, n=2 -> total=8
        ("R_U", [120, 120]),  # avg=2.0, n=2 -> total=4
    ]
    for code, deps in rows:
        for i, dep in enumerate(deps):
            await aconn.execute(
                "INSERT INTO updates "
                "(agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1, $2, $3, 'trip_' || $4, '平日', '10:00', $4, $5, $6)",
                aagency_id,
                f"cn_{code}_{i}",
                base + timedelta(minutes=i),
                code,
                i + 1,
                dep,
            )
        avg_min = sum(deps) / len(deps) / 60.0
        await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), code, "平日", avg_min, len(deps))

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    conc = out["concentration"]
    codes = [r["route_code"] for r in conc["top_routes"]]
    # All 6 routes are within the top-20 limit.
    assert len(codes) == 6
    assert set(codes) == {"R_X", "R_Y", "R_Z", "R_V", "R_W", "R_U"}
    total_pct = sum(r["share_pct"] for r in conc["top_routes"]) + conc["rest_share_pct"]
    assert total_pct == pytest.approx(100.0, abs=0.5)
    # No routes outside top-20 -> rest_share is 0%.
    assert conc["rest_share_pct"] == pytest.approx(0.0, abs=0.1)
    assert conc["rest_route_count"] == 0


@pytest.mark.asyncio
async def test_concentration_fast_path_tie_break_is_deterministic(aconn, aagency_id):
    """Two routes tied on total_late_min (agg_daily_trend fast path) must
    rank by route_code, ascending, matching its slow-path counterpart;
    without it, Postgres's GROUP BY order for a tie is unspecified.
    """
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_TIE_B", "平日", 5.0, 2)  # total=10
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_TIE_A", "平日", 5.0, 2)  # total=10, tied

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    codes = [r["route_code"] for r in out["concentration"]["top_routes"] if r["route_code"].startswith("R_TIE")]
    assert codes == ["R_TIE_A", "R_TIE_B"]


@pytest.mark.asyncio
async def test_concentration_fast_path_counts_clamped_observations_not_clamped_average(aconn, aagency_id):
    """A day whose trips mix early and late running must score its true
    per-observation clamped lateness, not its already-signed average
    clamped to zero.

    R_MIX runs 5 trips at +10 min and 5 at -8 min: avg_min = +1.0. The old
    ``SUM(GREATEST(avg_min, 0) * samples)`` approximation would have scored
    this as ``1.0 * 10 = 10`` late-minutes. The true clamped sum — what
    ``sum_late_sec`` now stores exactly — is ``5 * 10 = 50`` (the -8 min
    trips contribute 0, never a negative offset). R_OTHER runs uniformly at
    +2 min for the same 10 samples: true total ``2.0 * 10 = 20``.

    Under the old approximation R_OTHER (20) would have outranked R_MIX
    (10) — backwards, since R_MIX's real contribution (50) is more than
    double R_OTHER's. The fix must rank R_MIX first.
    """
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_MIX", "平日", 1.0, 10, sum_late_sec=3000)
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_OTHER", "平日", 2.0, 10)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    top = out["concentration"]["top_routes"]
    assert [r["route_code"] for r in top] == ["R_MIX", "R_OTHER"]
    # grand_total = 50 + 20 = 70 late-minutes.
    assert top[0]["share_pct"] == pytest.approx(50.0 / 70.0 * 100.0, abs=0.05)
    assert top[1]["share_pct"] == pytest.approx(20.0 / 70.0 * 100.0, abs=0.05)


@pytest.mark.asyncio
async def test_concentration_fast_and_slow_paths_agree_on_mixed_early_late_day(
    aconn, aagency_id, ch_client, ch_async_client
):
    """The fast (``time_band='all'``) and slow (any other ``time_band``) paths
    must score an identical mixed early/late day identically — the exact
    case ``SUM(GREATEST(avg_min, 0) * samples)`` used to understate relative
    to the slow path's per-observation ``SUM(GREATEST(dep_delay, 0))``, which
    could rank routes differently depending only on which ``time_band`` a
    request happened to use, not on the underlying data.

    Same R_MIX (5 trips +10 min, 5 trips -8 min -> true total 50 min) /
    R_OTHER (10 trips uniformly +2 min -> true total 20 min) shape as
    :func:`test_concentration_fast_path_counts_clamped_observations_not_clamped_average`,
    seeded into BOTH ``agg_daily_trend`` (what a real analyze() run over
    this data would store) and raw ``updates``/ClickHouse (what the slow
    path reads directly), then asserted to agree.
    """
    when = datetime.combine(date(2026, 5, 20), time(12, 0), tzinfo=timezone.utc)
    for i in range(5):
        await _seed_update(aconn, aagency_id, when + timedelta(minutes=i), "R_MIX", 600, seq=i + 1)
    for i in range(5):
        await _seed_update(aconn, aagency_id, when + timedelta(minutes=5 + i), "R_MIX", -480, seq=6 + i)
    for i in range(10):
        await _seed_update(aconn, aagency_id, when + timedelta(minutes=i), "R_OTHER", 120, seq=100 + i)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 20), "R_MIX", "平日", 1.0, 10, sum_late_sec=3000)
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 20), "R_OTHER", "平日", 2.0, 10)

    from pipeline.reports import compute_overview_summary

    fast_ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    fast_out = await compute_overview_summary(aagency_id, fast_ctx, aconn, "ja")
    slow_ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    slow_out = await compute_overview_summary(aagency_id, slow_ctx, aconn, "ja", ch=ch_async_client)

    for out in (fast_out, slow_out):
        top = out["concentration"]["top_routes"]
        assert [r["route_code"] for r in top] == ["R_MIX", "R_OTHER"]
        assert top[0]["share_pct"] == pytest.approx(50.0 / 70.0 * 100.0, abs=0.05)
        assert top[1]["share_pct"] == pytest.approx(20.0 / 70.0 * 100.0, abs=0.05)


@pytest.mark.asyncio
async def test_top_delayed_routes_ranks_by_absolute_avg_not_share(aconn, aagency_id):
    """Ranked by raw avg_min, not by _concentration()'s total-lateness-contribution
    metric — a route with few samples but a high average outranks a route
    with more samples but a lower average."""
    # R_HIGH: avg 8.0 min, only 2 samples. R_LOW: avg 3.0 min, 20 samples.
    # Under _concentration()'s SUM(avg_min*samples) metric R_LOW would win
    # (60 > 16); under a true average, R_HIGH must win.
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_HIGH", "平日", 8.0, 2)
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_LOW", "平日", 3.0, 20)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    routes = out["top_delayed"]["routes"]
    assert routes[0]["route_code"] == "R_HIGH"
    assert routes[0]["avg_min"] == pytest.approx(8.0, abs=0.05)
    assert routes[1]["route_code"] == "R_LOW"


@pytest.mark.asyncio
async def test_top_delayed_routes_fast_path_tie_break_is_deterministic(aconn, aagency_id):
    """Two routes tied on weighted avg_min (agg_daily_trend fast path) must
    rank by route_code, ascending — same tie-break as concentration's fast
    path; each route here is a single-day row, so its weighted average is
    just its own avg_min.
    """
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_TDTIE_B", "平日", 5.0, 2)
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_TDTIE_A", "平日", 5.0, 3)  # tied avg, diff samples

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    codes = [r["route_code"] for r in out["top_delayed"]["routes"] if r["route_code"].startswith("R_TDTIE")]
    assert codes == ["R_TDTIE_A", "R_TDTIE_B"]


@pytest.mark.asyncio
async def test_top_delayed_routes_delayed_count_excludes_under_threshold(aconn, aagency_id):
    """delayed_count only counts routes at/above the 2.0-min DELAY_RAMP
    'not ok' threshold — matching frontend/src/styles/tokens.ts's boundary."""
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_OK", "平日", 1.5, 10)  # under threshold
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_EDGE", "平日", 2.0, 10)  # exactly at threshold
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_BAD", "平日", 6.0, 10)  # over threshold

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    assert out["top_delayed"]["delayed_count"] == 2


@pytest.mark.asyncio
async def test_top_delayed_routes_limit_and_empty(aconn, aagency_id):
    """Caps at 5 routes even with more seeded; empty dataset returns
    {routes: [], delayed_count: 0}, not an error."""
    from pipeline.reports import compute_overview_summary

    for i in range(7):
        await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), f"R_{i}", "平日", 3.0 + i, 10)

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    routes = out["top_delayed"]["routes"]
    assert len(routes) == 5
    assert routes[0]["route_code"] == "R_6"  # highest avg_min (3.0 + 6)

    # Empty-dataset agency: no seeded rows at all.
    empty_ctx = RangeCtx(from_date=date(2020, 1, 1), to_date=date(2020, 1, 1))
    empty_out = await compute_overview_summary(aagency_id, empty_ctx, aconn, "ja")
    assert empty_out["top_delayed"] == {"routes": [], "delayed_count": 0}


@pytest.mark.asyncio
async def test_top_delayed_routes_falls_back_to_live_under_time_band(aconn, aagency_id, ch_client, ch_async_client):
    """Non-default time_band bypasses agg_daily_trend and reads updates
    directly (ClickHouse, Task 8.5), same fallback _concentration() already
    uses."""
    base = datetime.combine(date(2026, 5, 18), time(8, 0), tzinfo=timezone.utc)
    for i, dep in enumerate([300, 360]):  # 5.0, 6.0 min -> avg 5.5
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, $4, '平日', '08:00', 'R_TD', $5, $6)",
            aagency_id,
            f"td_{i}",
            base + timedelta(minutes=i),
            f"trip_td_{i + 1}",
            i + 1,
            dep,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    routes = out["top_delayed"]["routes"]
    assert len(routes) == 1
    assert routes[0]["route_code"] == "R_TD"
    assert routes[0]["avg_min"] == pytest.approx(5.5, abs=0.05)


@pytest.mark.asyncio
async def test_top_delayed_routes_uses_cur_ctx_not_full_ctx(aconn, aagency_id):
    """top_delayed must reflect only the last-7-days-of-ctx window
    (cur_ctx), not the full (wider) ctx — the same window the headline
    already uses. A route with a much higher avg_min outside that 7-day
    window must NOT appear, even though it's inside the full ctx.
    """
    # ctx spans 14 days (2026-05-05..2026-05-18). Latest data is on
    # 2026-05-18, so cur_ctx anchors there and covers 2026-05-12..2026-05-18
    # (last 7 days) — 2026-05-05 is inside ctx but outside cur_ctx.
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 5), "R_OLD", "平日", 9.0, 10)
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 18), "R_NEW", "平日", 4.0, 10)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 5), to_date=date(2026, 5, 18))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    codes = [r["route_code"] for r in out["top_delayed"]["routes"]]
    assert codes == ["R_NEW"]
    # delayed_count must also exclude R_OLD (would otherwise be 2, not 1,
    # if the full ctx were used instead of cur_ctx).
    assert out["top_delayed"]["delayed_count"] == 1


@pytest.mark.asyncio
async def test_peak_hour_picks_hour_with_max_avg_delay(aconn, aagency_id):
    """Rows scheduled at 06:00, 08:00, 17:00; 08:00 has the worst avg.

    Peak hour reads from ``agg_route_hour``; one row per (route, svc,
    scheduled_time). Two rows at 08:00 collapse to one agg row with the
    weighted mean (9.0 min, samples=2).
    """
    base = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    rows = [
        ("06:00", 60),
        ("08:00", 600),
        ("08:00", 480),
        ("17:00", 120),
    ]
    for i, (sched, dep) in enumerate(rows):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_pk_' || $4, '平日', ($5::text)::time, 'R_P', 1, $6)",
            aagency_id,
            f"pk_{i}",
            base + timedelta(minutes=i),
            str(i),
            sched,
            dep,
        )
    # Aggregated mirror in agg_route_hour: one row per scheduled_time.
    await _seed_agg_route_hour(aconn, aagency_id, "R_P", "平日", "06:00", 1.0, 1)
    await _seed_agg_route_hour(aconn, aagency_id, "R_P", "平日", "08:00", 9.0, 2)
    await _seed_agg_route_hour(aconn, aagency_id, "R_P", "平日", "17:00", 2.0, 1)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    pk = out["peak_hour"]
    assert pk is not None
    assert pk["peak_hour"] == 8
    assert len(pk["by_hour"]) == 24
    assert pk["by_hour"][8] == pytest.approx(9.0, abs=0.1)
    assert pk["by_hour"][6] == pytest.approx(1.0, abs=0.1)
    assert pk["by_hour"][3] is None


@pytest.mark.asyncio
async def test_service_split_two_rows_and_sparkline_7_points(aconn, aagency_id):
    """7 daily inserts alternating service_type; service_split has both,
    sparkline returns 7 points oldest-first."""
    base = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    for i in range(7):
        svc = "平日" if i % 2 == 0 else "土日祝"
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_sv_' || $4, $5, '10:00', 'R_S', 1, $6)",
            aagency_id,
            f"sv_{i}",
            base + timedelta(days=i),
            str(i),
            svc,
            60 * (i + 1),
        )
        d = (base + timedelta(days=i)).date()
        await _seed_agg_daily(aconn, aagency_id, d, "R_S", svc, (i + 1) * 1.0, 1)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    ss = out["service_split"]
    assert set(ss.keys()) == {"平日", "土日祝"}
    assert ss["平日"] > 0
    assert len(out["sparkline_points"]) == 7


@pytest.mark.asyncio
async def test_overview_endpoint_full_payload_via_test_client(client, aconn, aagency_id):
    """End-to-end: seed data, hit the endpoint, every top-level key present
    and headline shape is well-populated."""
    # See test_overview_endpoint_returns_empty_payload_when_no_data's comment —
    # default ctx (time_band == 'all') never needs a real ClickHouse client.
    from api.main import app

    app.state.ch_client = None
    base = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    for i in range(5):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_full_' || $4, '平日', '10:00', 'R_F', 1, $5)",
            aagency_id,
            f"full_{i}",
            base + timedelta(hours=i),
            str(i),
            60 + i * 30,
        )
    # Aggregated mirror: 5 samples on 2026-05-18 averaging ~2 min.
    deps = [60 + i * 30 for i in range(5)]
    await _seed_agg_daily(
        aconn,
        aagency_id,
        date(2026, 5, 18),
        "R_F",
        "平日",
        sum(deps) / len(deps) / 60.0,
        len(deps),
    )

    r = await client.get(f"/api/{aagency_id}/overview/summary?from=2026-05-18&to=2026-05-24")
    assert r.status_code == 200
    body = r.json()
    for key in ("headline", "movers", "concentration", "peak_hour", "service_split", "sparkline_points"):
        assert key in body, f"missing key {key}"
    assert body["headline"]["samples"] == 5
    assert body["headline"]["avg_min"] is not None


@pytest.mark.asyncio
async def test_headline_uses_live_path_when_time_band_set(aconn, aagency_id, ch_client, ch_async_client):
    """When ``ctx.time_band != 'all'``, the headline must read from live
    ``updates`` (ClickHouse, Task 8.5) so the hour-of-day filter actually
    applies. The four seeded rows span morning / noon / evening;
    ``time_band='morning'`` must keep only the two scheduled inside
    05:00-09:00."""
    base = datetime.combine(date(2026, 5, 6), time(12, 0), tzinfo=timezone.utc)
    rows = [
        ("06:00", 600),  # morning — included (10 min)
        ("07:30", 300),  # morning — included (5 min)
        ("13:00", 60),  # noon — excluded
        ("19:00", 120),  # evening — excluded
    ]
    for i, (sched, dep) in enumerate(rows):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_tb_' || $4, '平日', ($5::text)::time, 'R_TB', 1, $6)",
            aagency_id,
            f"tb_{i}",
            base + timedelta(minutes=i),
            str(i),
            sched,
            dep,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(
        from_date=date(2026, 5, 6),
        to_date=date(2026, 5, 6),
        time_band="morning",
    )
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    # Morning-only avg = (10 + 5) / 2 = 7.5 min over 2 samples.
    assert out["headline"]["samples"] == 2
    assert out["headline"]["avg_min"] == pytest.approx(7.5, abs=0.1)


@pytest.mark.asyncio
async def test_peak_hour_weekday_weekend_split_from_agg(aconn, aagency_id):
    """``peak_hour_weekday`` / ``peak_hour_weekend`` partition by ISO
    day-of-week, reading the per-day/hour ``agg_hour_daily`` fast path
    (sample-weighted across the range).

    2026-05-19 is a Tuesday (weekday); 2026-05-23 is a Saturday (weekend).
    """
    # Weekday Tue rows at hour 8 (weighted avg 9.0); weekend Sat at hour 17 (5.5).
    await _seed_agg_hour_daily(aconn, aagency_id, date(2026, 5, 19), 8, 9.0, 2)
    await _seed_agg_hour_daily(aconn, aagency_id, date(2026, 5, 23), 17, 5.5, 2)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    pk_wd = out["peak_hour_weekday"]
    pk_we = out["peak_hour_weekend"]
    assert pk_wd is not None
    assert pk_we is not None
    assert pk_wd["peak_hour"] == 8
    assert pk_we["peak_hour"] == 17
    assert pk_wd["by_hour"][8] == pytest.approx(9.0, abs=0.1)
    assert pk_we["by_hour"][17] == pytest.approx(5.5, abs=0.1)
    # Cross-bucket cells stay None.
    assert pk_wd["by_hour"][17] is None
    assert pk_we["by_hour"][8] is None


@pytest.mark.asyncio
async def test_peak_hour_by_dow_pools_exact_sum_delay_sec_not_reweighted_avg(aconn, aagency_id):
    """_peak_hour_by_dow's fast path pools multiple agg_hour_daily days for the
    same hour. Pooling must use SUM(sum_delay_sec)/SUM(samples) (exact), not
    the old SUM(avg_min * samples)/SUM(samples) reweighting of an
    already-rounded per-day average -- mirrors peak_hour_breakdown's identical
    fix for agg_route_hour_dow (migration 0028's sum_delay_sec rollout,
    extended to agg_hour_daily)."""
    # Two weekday Tuesdays at hour 15, with non-round per-row avg_min values
    # whose sum_delay_sec is seeded to the TRUE underlying sum rather than
    # backed out from the rounded avg_min, so the two pooling formulas diverge.
    await _seed_agg_hour_daily(aconn, aagency_id, date(2026, 5, 19), 15, 1.61, 3, sum_delay_sec=290)
    await _seed_agg_hour_daily(aconn, aagency_id, date(2026, 5, 26), 15, 2.0, 1000, sum_delay_sec=100000)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 31))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    pk_wd = out["peak_hour_weekday"]
    assert pk_wd is not None
    # Exact: (290 + 100000) / 60 / 1003 ~= 1.6665 -> 1.67, NOT the reweighted
    # (1.61*3 + 2.0*1000) / 1003 ~= 1.9988 -> 2.00.
    assert pk_wd["by_hour"][15] == pytest.approx(1.67, abs=0.01)


@pytest.mark.asyncio
async def test_peak_hour_agg_sample_weights_across_days(aconn, aagency_id):
    """The agg fast path weights each day's hourly avg by its sample count, not
    a plain mean — two weekday Tuesdays at hour 8 with unequal weights."""
    # (9.0 * 10 + 4.0 * 2) / 12 = 8.1667 -> 8.17, not the plain mean 6.5.
    await _seed_agg_hour_daily(aconn, aagency_id, date(2026, 5, 19), 8, 9.0, 10)
    await _seed_agg_hour_daily(aconn, aagency_id, date(2026, 5, 26), 8, 4.0, 2)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 31))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    pk_wd = out["peak_hour_weekday"]
    assert pk_wd is not None
    assert pk_wd["peak_hour"] == 8
    assert pk_wd["by_hour"][8] == pytest.approx(8.17, abs=0.01)


@pytest.mark.asyncio
async def test_peak_hour_falls_back_to_live_under_service_filter(aconn, aagency_id, ch_client, ch_async_client):
    """A service filter (the agg has no service dimension) routes peak-hour to
    the live scan (ClickHouse, Task 8.5), which still partitions weekday/weekend
    correctly."""
    weekday_dt = datetime.combine(date(2026, 5, 19), time(8, 0), tzinfo=timezone.utc)
    weekend_dt = datetime.combine(date(2026, 5, 23), time(17, 0), tzinfo=timezone.utc)
    rows = [
        (weekday_dt, "08:00", 600),
        (weekday_dt + timedelta(minutes=1), "08:00", 480),
        (weekend_dt, "17:00", 300),
        (weekend_dt + timedelta(minutes=1), "17:00", 360),
    ]
    for i, (cap, sched, dep) in enumerate(rows):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_pw_' || $4, '平日', ($5::text)::time, 'R_PW', $6, $7)",
            aagency_id,
            f"pw_{i}",
            cap,
            str(i),
            sched,
            i + 1,
            dep,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    # service != 'all' forces the live path (agg_hour_daily has no service column).
    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), service="平日")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    pk_wd = out["peak_hour_weekday"]
    pk_we = out["peak_hour_weekend"]
    assert pk_wd is not None and pk_we is not None
    assert pk_wd["peak_hour"] == 8
    assert pk_we["peak_hour"] == 17
    assert pk_wd["by_hour"][8] == pytest.approx(9.0, abs=0.1)
    assert pk_we["by_hour"][17] == pytest.approx(5.5, abs=0.1)


@pytest.mark.asyncio
async def test_service_split_daily_returns_per_date_rows(aconn, aagency_id):
    """``service_split_daily`` returns one row per date with weekday +
    weekend slots populated independently."""
    inserts = [
        (date(2026, 5, 18), "平日", 60),  # 1 min weekday
        (date(2026, 5, 19), "平日", 180),  # 3 min weekday
        (date(2026, 5, 19), "土日祝", 300),  # 5 min weekend (same date)
        (date(2026, 5, 20), "土日祝", 240),  # 4 min weekend
    ]
    for i, (d, svc, dep) in enumerate(inserts):
        when = datetime.combine(d, time(12, 0), tzinfo=timezone.utc)
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_ssd_' || $4, $5, '10:00', 'R_SSD', 1, $6)",
            aagency_id,
            f"ssd_{i}",
            when,
            str(i),
            svc,
            dep,
        )
        await _seed_agg_daily(aconn, aagency_id, d, "R_SSD", svc, dep / 60.0, 1)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    daily = out["service_split_daily"]
    by_date = {row["date"]: row for row in daily}
    assert by_date["2026-05-18"]["weekday"] == pytest.approx(1.0, abs=0.05)
    assert by_date["2026-05-18"]["weekend"] is None
    assert by_date["2026-05-19"]["weekday"] == pytest.approx(3.0, abs=0.05)
    assert by_date["2026-05-19"]["weekend"] == pytest.approx(5.0, abs=0.05)
    assert by_date["2026-05-20"]["weekday"] is None
    assert by_date["2026-05-20"]["weekend"] == pytest.approx(4.0, abs=0.05)


@pytest.mark.asyncio
async def test_pool_path_matches_sequential_path(aconn, aagency_id):
    """Pool-gather path and sequential path return identical payloads.

    Seeds a representative dataset (agg_daily_trend + agg_route_hour +
    updates for the live-path callers) then calls compute_overview_summary
    twice on the same seeded data: once with conn only (sequential) and
    once with pool=<real pool> (gather). Both results must be equal.
    """
    # Seed agg_daily_trend rows for headline / movers / concentration /
    # service_split / sparkline (fast-path callers).
    for i in range(4):
        d = date(2026, 5, 18) + timedelta(days=i)
        await _seed_agg_daily(aconn, aagency_id, d, "R_PP", "平日", 2.0 + i * 0.5, 5)
    # One baseline-week row so delta is non-None.
    await _seed_agg_daily(aconn, aagency_id, date(2026, 5, 11), "R_PP", "平日", 1.5, 5)

    # Seed agg_route_hour for _peak_hour (fast-path).
    await _seed_agg_route_hour(aconn, aagency_id, "R_PP", "平日", "08:00", 4.0, 5)
    await _seed_agg_route_hour(aconn, aagency_id, "R_PP", "平日", "17:00", 2.0, 3)

    # Seed updates rows for _peak_hour_by_dow (live-path only callers).
    # 2026-05-19 is Tuesday (weekday), 2026-05-23 is Saturday (weekend).
    weekday_dt = datetime.combine(date(2026, 5, 19), time(8, 0), tzinfo=timezone.utc)
    weekend_dt = datetime.combine(date(2026, 5, 23), time(17, 0), tzinfo=timezone.utc)
    for i, (cap, sched, dep) in enumerate(
        [
            (weekday_dt, "08:00", 480),
            (weekday_dt + timedelta(minutes=1), "08:00", 360),
            (weekend_dt, "17:00", 300),
            (weekend_dt + timedelta(minutes=1), "17:00", 240),
        ]
    ):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_pp_' || $4, '平日', ($5::text)::time, 'R_PP', $6, $7)",
            aagency_id,
            f"pp_{i}",
            cap,
            str(i),
            sched,
            i + 1,
            dep,
        )

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 11), to_date=date(2026, 5, 24))

    # Sequential path (pool=None, the existing default).
    seq_out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")

    # Pool-gather path — spin up a fresh pool against the same test DB.
    # Use _init_connection (SET TIME ZONE 'Asia/Tokyo') so pooled conns
    # mirror production setup exactly.
    from api.main import _init_connection

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], init=_init_connection)
    try:
        pool_out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", pool=pool)
    finally:
        await pool.close()

    # Both paths must produce structurally identical payloads.
    assert pool_out["headline"] == seq_out["headline"]
    assert pool_out["movers"] == seq_out["movers"]
    assert pool_out["concentration"] == seq_out["concentration"]
    assert pool_out["top_delayed"] == seq_out["top_delayed"]
    assert pool_out["peak_hour"] == seq_out["peak_hour"]
    assert pool_out["peak_hour_weekday"] == seq_out["peak_hour_weekday"]
    assert pool_out["peak_hour_weekend"] == seq_out["peak_hour_weekend"]
    assert pool_out["service_split"] == seq_out["service_split"]
    assert pool_out["service_split_daily"] == seq_out["service_split_daily"]
    assert pool_out["sparkline_points"] == seq_out["sparkline_points"]


@pytest.mark.asyncio
async def test_route_summary_includes_late5_pct(client, aconn, aagency_id, ch_async_client):
    from datetime import date

    from api.main import app

    app.state.ch_client = ch_async_client
    d = date.today()
    await _seed_agg_route_daily(aconn, aagency_id, d, "K31", "平日", 360, 100)
    await _seed_agg_route_stats(aconn, aagency_id, "K31", "平日", 6.0, 8.0, 23.5, 100)
    r = await client.get(f"/api/{aagency_id}/today/route-summary")
    assert r.status_code == 200
    routes = r.json()["routes"]
    k31 = next((x for x in routes if x["route_code"] == "K31"), None)
    assert k31 is not None
    assert k31["late5_pct"] == pytest.approx(23.5, rel=1e-4)


@pytest.mark.asyncio
async def test_route_summary_late5_pct_null_when_no_stats(client, aconn, aagency_id, ch_async_client):
    from datetime import date

    from api.main import app

    app.state.ch_client = ch_async_client
    d = date.today()
    await _seed_agg_route_daily(aconn, aagency_id, d, "K99", "平日", 120, 5)
    # No agg_route_stats row → late5_pct must be None
    r = await client.get(f"/api/{aagency_id}/today/route-summary")
    assert r.status_code == 200
    routes = r.json()["routes"]
    k99 = next((x for x in routes if x["route_code"] == "K99"), None)
    assert k99 is not None
    assert k99["late5_pct"] is None


@pytest.mark.asyncio
async def test_peak_hour_breakdown_returns_top_routes(client, aconn, aagency_id):
    await _seed_agg_route_hour_dow(aconn, aagency_id, "K31", "平日", 5, 8, 6.5, 50)
    await _seed_agg_route_hour_dow(aconn, aagency_id, "K37", "平日", 5, 8, 5.2, 30)
    await _seed_agg_route_hour_dow(aconn, aagency_id, "C12", "平日", 5, 8, 4.1, 10)
    # Different hour — must not appear
    await _seed_agg_route_hour_dow(aconn, aagency_id, "W53", "平日", 5, 9, 9.0, 100)
    r = await client.get(f"/api/{aagency_id}/peak-hour-breakdown", params={"hour": 8, "dow": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["hour"] == 8
    assert body["dow"] == 5
    codes = [x["route_code"] for x in body["routes"]]
    assert codes == ["K31", "K37", "C12"]
    assert body["routes"][0]["avg_min"] == pytest.approx(6.5, rel=1e-4)


@pytest.mark.asyncio
async def test_peak_hour_breakdown_excludes_low_samples(client, aconn, aagency_id):
    await _seed_agg_route_hour_dow(aconn, aagency_id, "X1", "平日", 1, 7, 8.0, 2)  # samples < 3
    await _seed_agg_route_hour_dow(aconn, aagency_id, "X2", "平日", 1, 7, 7.0, 3)  # samples == 3, included
    r = await client.get(f"/api/{aagency_id}/peak-hour-breakdown", params={"hour": 7, "dow": 1})
    assert r.status_code == 200
    codes = [x["route_code"] for x in r.json()["routes"]]
    assert "X1" not in codes
    assert "X2" in codes


@pytest.mark.asyncio
async def test_peak_hour_breakdown_no_dow_aggregates_all(client, aconn, aagency_id):
    # Without dow param: routes from any DOW at that hour should appear
    await _seed_agg_route_hour_dow(aconn, aagency_id, "Z1", "平日", 1, 12, 5.0, 10)
    await _seed_agg_route_hour_dow(aconn, aagency_id, "Z1", "平日", 2, 12, 3.0, 10)
    r = await client.get(f"/api/{aagency_id}/peak-hour-breakdown", params={"hour": 12})
    assert r.status_code == 200
    codes = [x["route_code"] for x in r.json()["routes"]]
    assert "Z1" in codes


@pytest.mark.asyncio
async def test_peak_hour_breakdown_no_dow_pools_exact_sum_delay_sec_not_reweighted_avg(client, aconn, aagency_id):
    """Without dow, peak_hour_breakdown pools multiple dow rows for the same
    route/service/hour. Pooling must use SUM(sum_delay_sec)/SUM(samples)
    (exact), not the old SUM(avg_min * samples)/SUM(samples) reweighting of
    an already-rounded per-row average -- mirrors forecast_heatmap's
    identical fix (migration 0028's sum_delay_sec rollout)."""
    await _seed_agg_route_hour_dow(aconn, aagency_id, "Q1", "平日", 3, 15, 1.61, 3, sum_delay_sec=290)
    await _seed_agg_route_hour_dow(aconn, aagency_id, "Q1", "平日", 4, 15, 2.0, 1000, sum_delay_sec=100000)
    r = await client.get(f"/api/{aagency_id}/peak-hour-breakdown", params={"hour": 15})
    assert r.status_code == 200
    routes = {x["route_code"]: x for x in r.json()["routes"]}
    q1 = routes["Q1"]
    assert q1["samples"] == 1003
    # Exact: (290 + 100000) / 60 / 1003 ~= 1.6665 -> 1.67, NOT the reweighted
    # (1.61*3 + 2.0*1000) / 1003 ~= 1.9988 -> 2.00.
    assert q1["avg_min"] == pytest.approx(1.67, abs=1e-9)


@pytest.mark.asyncio
async def test_peak_hour_breakdown_no_dow_skips_all_null_sum_delay_sec_group(client, aconn, aagency_id):
    """A (route, service_type) group at this hour whose every contributing
    row has sum_delay_sec NULL (migration 0028's column is nullable on every
    table) still passes the unfiltered ``HAVING SUM(samples) >= 3`` gate, so
    the FILTER-guarded exact-sum SQL returns avg_min=NULL for that group.
    Pre-fix, rounding that NULL into a non-optional Pydantic field raised an
    unhandled TypeError; the group must instead be omitted, while a normal
    group at the same hour still comes through.

    Seeds 20 distinct all-NULL groups -- one per LIMIT 20 slot -- so this
    also proves ``ORDER BY avg_min DESC NULLS LAST``: without NULLS LAST,
    Postgres's default NULLS FIRST for DESC would let these NULL groups fill
    the entire LIMIT and push R_OK out entirely, which a single-NULL-group
    seed can't distinguish from "correctly excluded" since both rows would
    fit under the limit regardless of sort order."""
    await aconn.executemany(
        "INSERT INTO agg_route_hour_dow "
        "(agency_id, route_code, service_type, dow, hour, avg_min, samples, sum_delay_sec) "
        "VALUES ($1, $2, '平日', 1, 20, 0.5, 5, NULL)",
        [(aagency_id, f"R_NULL{i}") for i in range(20)],
    )
    await _seed_agg_route_hour_dow(aconn, aagency_id, "R_OK", "平日", 1, 20, 4.0, 10)
    r = await client.get(f"/api/{aagency_id}/peak-hour-breakdown", params={"hour": 20})
    assert r.status_code == 200
    codes = [x["route_code"] for x in r.json()["routes"]]
    assert "R_OK" in codes
    assert not any(c.startswith("R_NULL") for c in codes)


# ---------------------------------------------------------------------------
# Consolidated slow path (ctx.time_band != 'all') — one shared ClickHouse grain
#
# Every slow-path stage helper used to run its OWN dedup scan of `updates`
# (~12 per request), each one slow enough on its own to risk blowing the
# ClickHouse client's 30s max_execution_time. They now all derive from a
# single `_fetch_grain` round trip. These tests pin both halves of that: the
# round-trip count, and the semantics that the consolidation had to preserve
# (per-consumer date windows, per-consumer DOW, hour-of-day extraction).
# ---------------------------------------------------------------------------


class _CountingCh:
    """Async ClickHouse client proxy that records every query it forwards."""

    def __init__(self, inner):
        self._inner = inner
        self.queries = []

    async def query(self, sql, parameters=None, **kwargs):
        self.queries.append(sql)
        return await self._inner.query(sql, parameters=parameters, **kwargs)


async def _seed_update(aconn, agency_id, when, route_code, dep_delay, *, sched="08:00", service="平日", seq=1):
    """Insert one `updates` row (Postgres); mirror to ClickHouse afterwards."""
    await aconn.execute(
        "INSERT INTO updates "
        "(agency_id, file_name, captured_at, trip_id, service_type, "
        " scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1, $2, $3, $4, $5, ($6::text)::time, $7, $8, $9)",
        agency_id,
        f"f_{route_code}_{when.isoformat()}_{sched}_{seq}_{dep_delay}",
        when,
        f"trip_{route_code}_{when.date().isoformat()}_{sched}_{seq}",
        service,
        sched,
        route_code,
        seq,
        dep_delay,
    )


@pytest.mark.asyncio
async def test_slow_path_uses_a_single_clickhouse_query(aconn, aagency_id, ch_client, ch_async_client):
    """The whole slow-path payload comes from ONE ClickHouse round trip.

    Dataset deliberately has no movers (a single route, and no route with
    >= 10 samples in BOTH the current and baseline 7-day windows), so
    `_route_weekly_history` — the one helper that keeps its own wider-span
    scan — short-circuits before querying. Everything else must be served
    from the shared grain.
    """
    for i, dep in enumerate([120, 240, 360]):
        await _seed_update(
            aconn,
            aagency_id,
            datetime.combine(date(2026, 5, 20), time(12, 0), tzinfo=timezone.utc),
            "R_G1",
            dep,
            seq=i + 1,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    counting = _CountingCh(ch_async_client)
    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=counting)

    assert len(counting.queries) == 1, f"expected 1 ClickHouse query, got {len(counting.queries)}"
    # ...and it is the grain query: one dedup CTE grouped to the shared grain.
    assert "GROUP BY date, route_code, service_type" in counting.queries[0]
    # The payload is still fully populated off that single query.
    assert out["headline"]["samples"] == 3
    assert out["headline"]["avg_min"] == pytest.approx(4.0, abs=0.01)  # (2+4+6)/3
    assert out["sparkline_points"] == [4.0]
    assert out["service_split"] == {"平日": 4.0}
    assert out["top_delayed"]["routes"][0]["route_code"] == "R_G1"
    assert out["concentration"]["top_routes"][0]["route_code"] == "R_G1"
    assert out["peak_hour_weekday"]["peak_hour"] == 8


@pytest.mark.asyncio
async def test_slow_path_movers_add_exactly_one_more_query(aconn, aagency_id, ch_client, ch_async_client):
    """With movers present the count is 2, not 12: the shared grain plus
    `_route_weekly_history`'s own scan, which deliberately keeps its wider
    4-week span (it reaches further back than the grain and depends on
    route_codes only known after the movers deltas are computed)."""
    cur_day = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    prv_day = cur_day - timedelta(days=7)
    for i in range(10):
        await _seed_update(aconn, aagency_id, prv_day + timedelta(minutes=i), "R_MV", 60, seq=i + 1)
        await _seed_update(aconn, aagency_id, cur_day + timedelta(minutes=i), "R_MV", 600, seq=i + 1)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    counting = _CountingCh(ch_async_client)
    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=counting)

    assert len(counting.queries) == 2, f"expected 2 ClickHouse queries, got {len(counting.queries)}"
    worse = out["movers"]["worse"]
    assert [m["route_code"] for m in worse] == ["R_MV"]
    assert worse[0]["delta_min"] == pytest.approx(9.0, abs=0.01)  # 10 min - 1 min


@pytest.mark.asyncio
async def test_slow_path_baseline_window_reaches_before_ctx_from_date(aconn, aagency_id, ch_client, ch_async_client):
    """The grain must span 7 days BEFORE ctx.from_date.

    The baseline window is the 7 days immediately before the current one, and
    the current window is clamped to start no earlier than ctx.from_date — so
    the baseline can legitimately fall entirely outside ctx. A grain that only
    covered ctx would silently report `baseline_avg_min: None`.
    """
    # ctx is a single day; its baseline is 2026-05-11..2026-05-17, wholly before it.
    await _seed_update(
        aconn, aagency_id, datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc), "R_BL", 600
    )
    await _seed_update(
        aconn, aagency_id, datetime.combine(date(2026, 5, 14), time(12, 0), tzinfo=timezone.utc), "R_BL", 300, seq=2
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    assert out["headline"]["avg_min"] == pytest.approx(10.0, abs=0.01)
    assert out["headline"]["baseline_avg_min"] == pytest.approx(5.0, abs=0.01)
    assert out["headline"]["delta_min"] == pytest.approx(5.0, abs=0.01)
    # The pre-ctx baseline day must NOT leak into the ctx-windowed surfaces.
    assert out["sparkline_points"] == [10.0]


@pytest.mark.asyncio
async def test_slow_path_dow_applies_per_consumer(aconn, aagency_id, ch_client, ch_async_client):
    """`ctx.dow` gates the ctx-windowed helpers, but `peak_hour_weekday` /
    `peak_hour_weekend` must IGNORE it and re-partition weekday vs weekend.

    This is why the grain carries no DOW filter of its own: baking `ctx.dow`
    into the shared WHERE would make the weekend peak permanently empty
    whenever the user narrowed to weekdays.
    """
    # 2026-05-21 Thu (weekday), 2026-05-23 Sat (weekend).
    await _seed_update(
        aconn,
        aagency_id,
        datetime.combine(date(2026, 5, 21), time(12, 0), tzinfo=timezone.utc),
        "R_DW",
        120,
        sched="07:00",
    )
    await _seed_update(
        aconn,
        aagency_id,
        datetime.combine(date(2026, 5, 23), time(12, 0), tzinfo=timezone.utc),
        "R_DW",
        600,
        sched="08:00",
        seq=2,
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning", dow="weekday")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)

    # ctx.dow='weekday' keeps only the Thursday row for the headline/sparkline.
    assert out["headline"]["samples"] == 1
    assert out["headline"]["avg_min"] == pytest.approx(2.0, abs=0.01)
    assert out["sparkline_points"] == [2.0]
    # ...but both peak-hour splits still see their own day-of-week slice.
    assert out["peak_hour_weekday"]["peak_hour"] == 7
    assert out["peak_hour_weekday"]["by_hour"][7] == pytest.approx(2.0, abs=0.01)
    assert out["peak_hour_weekend"] is not None, "ctx.dow must not suppress the weekend peak"
    assert out["peak_hour_weekend"]["peak_hour"] == 8
    assert out["peak_hour_weekend"]["by_hour"][8] == pytest.approx(10.0, abs=0.01)


@pytest.mark.asyncio
async def test_slow_path_concentration_ignores_early_running(aconn, aagency_id, ch_client, ch_async_client):
    """Concentration measures contribution to LATENESS: the grain's
    `sum_late_sec` is `SUM(GREATEST(dep_delay, 0))`, so a route that ran early
    contributes zero rather than cancelling another route's lateness out."""
    when = datetime.combine(date(2026, 5, 20), time(12, 0), tzinfo=timezone.utc)
    await _seed_update(aconn, aagency_id, when, "R_LATE", 600)
    await _seed_update(aconn, aagency_id, when, "R_EARLY", -600, seq=2)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    top = out["concentration"]["top_routes"]
    assert [t["route_code"] for t in top] == ["R_LATE", "R_EARLY"]
    assert top[0]["share_pct"] == pytest.approx(100.0, abs=0.05)
    assert top[1]["share_pct"] == pytest.approx(0.0, abs=0.05)
    # The signed average still nets the two out (-10 and +10 over 2 samples).
    assert out["headline"]["avg_min"] == pytest.approx(0.0, abs=0.01)


@pytest.mark.asyncio
async def test_slow_path_movers_tie_break_is_deterministic(aconn, aagency_id, ch_client, ch_async_client):
    """Routes with genuinely equal deltas rank by route_code, ascending, in
    BOTH the worse and better lists.

    `_movers` ranks on `(raw delta_min, route_code)` — a total order, since
    route_codes are distinct — which is what makes the ranking independent of
    the order `set(cur) & set(prv)` happens to be iterated in. It used to rank
    on the ROUNDED delta and lean on a stable sort, so Python's per-process
    string-hash randomization decided which routes made the top-10 and in what
    order; the same request against the same data returned different movers
    after an app restart.
    """
    cur_day = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    prv_day = cur_day - timedelta(days=7)
    # Seeded in scrambled route_code order so the seed order can't be what
    # produces a sorted answer. Three routes tie at +9.0 min, three at -9.0 min.
    for code, prv_dep, cur_dep in (
        ("R_W3", 60, 600),
        ("R_B1", 600, 60),
        ("R_W1", 60, 600),
        ("R_B3", 600, 60),
        ("R_W2", 60, 600),
        ("R_B2", 600, 60),
    ):
        for i in range(10):
            await _seed_update(aconn, aagency_id, prv_day + timedelta(minutes=i), code, prv_dep, seq=i + 1)
            await _seed_update(aconn, aagency_id, cur_day + timedelta(minutes=i), code, cur_dep, seq=i + 1)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    worse = out["movers"]["worse"]
    better = out["movers"]["better"]
    assert [m["delta_min"] for m in worse] == [pytest.approx(9.0, abs=0.01)] * 3
    assert [m["delta_min"] for m in better] == [pytest.approx(-9.0, abs=0.01)] * 3
    # Both lists resolve ties the same way: ascending route_code, never hash order.
    assert [m["route_code"] for m in worse] == ["R_W1", "R_W2", "R_W3"]
    assert [m["route_code"] for m in better] == ["R_B1", "R_B2", "R_B3"]


@pytest.mark.asyncio
async def test_slow_path_movers_excludes_null_route_code(aconn, aagency_id, ch_client, ch_async_client):
    """A NULL route_code (both ingest strategies can produce a row with no
    resolvable route — see db/clickhouse/schema.sql) must not reach
    `_route_weekly_history`'s `route_code IN {rw_route_codes:Array(String)}`
    parameter: a `None` element there isn't valid `Array(String)` and raises
    a ClickHouse DatabaseError, 500ing the whole /overview/summary request.
    `_per_route_avg`'s slow path must drop it before it becomes a mover."""
    cur_day = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    prv_day = cur_day - timedelta(days=7)
    for i in range(10):
        await _seed_update(aconn, aagency_id, prv_day + timedelta(minutes=i), None, 60, seq=i + 1)
        await _seed_update(aconn, aagency_id, cur_day + timedelta(minutes=i), None, 600, seq=i + 1)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    assert out["movers"]["worse"] == []
    assert out["movers"]["better"] == []


@pytest.mark.asyncio
async def test_slow_path_movers_rank_on_raw_not_rounded_delta(aconn, aagency_id, ch_client, ch_async_client):
    """Two routes whose deltas both round to the same 2dp value must still be
    ordered by their TRUE deltas, not tie-broken by route_code.

    Ranking on the rounded delta manufactured ties that don't exist in the
    data (found on live agency-1 data: -1.7244 and -1.7203 both round to
    -1.72), turning the top-10 cutoff between two genuinely different routes
    into an arbitrary pick. Here `R_AA` improves slightly LESS than `R_BB`, so
    a raw-delta ranking puts `R_BB` first — the opposite of what an
    ascending-route_code tie-break on the rounded value would give.
    """
    cur_day = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    prv_day = cur_day - timedelta(days=7)
    # 10 samples/side, baseline 600s (10.0 min) for both. Current totals differ
    # by 3 seconds: R_AA 3625s -> 6.041667 min (delta -3.958333), R_BB 3622s ->
    # 6.036667 min (delta -3.963333). Distinct raw deltas, identical to 2 dp.
    for code, cur_total in (("R_AA", 3625), ("R_BB", 3622)):
        for i in range(10):
            await _seed_update(aconn, aagency_id, prv_day + timedelta(minutes=i), code, 600, seq=i + 1)
        # Spread cur_total across 10 samples: 9 equal + 1 remainder.
        per = cur_total // 10
        rem = cur_total - per * 9
        for i in range(9):
            await _seed_update(aconn, aagency_id, cur_day + timedelta(minutes=i), code, per, seq=i + 1)
        await _seed_update(aconn, aagency_id, cur_day + timedelta(minutes=9), code, rem, seq=10)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)
    better = out["movers"]["better"]
    assert len(better) == 2
    # Both display the SAME rounded delta_min...
    assert better[0]["delta_min"] == better[1]["delta_min"]
    # ...but R_BB's raw delta is more negative, so it must rank first. An
    # ascending-route_code tie-break on the rounded value would say "R_AA".
    assert [m["route_code"] for m in better] == ["R_BB", "R_AA"]


@pytest.mark.asyncio
async def test_slow_path_rejects_a_window_the_grain_does_not_cover(aconn, aagency_id, ch_client, ch_async_client):
    """A consumer window outside the grain's span must raise, not silently
    aggregate over a truncated window and return a plausible wrong average."""
    await _seed_update(
        aconn, aagency_id, datetime.combine(date(2026, 5, 20), time(12, 0), tzinfo=timezone.utc), "R_CV", 120
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from pipeline.reports.overview import _fetch_grain, _headline_stats, _peak_hour_by_dow

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    grain = await _fetch_grain(aagency_id, ctx, ch_async_client)
    # The grain reaches exactly 7 days before ctx.from_date, and no further.
    assert grain.from_date == date(2026, 5, 11)
    assert grain.to_date == date(2026, 5, 24)

    too_early = RangeCtx(from_date=date(2026, 5, 10), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="does not cover"):
        await _headline_stats(aagency_id, too_early, aconn, ch=ch_async_client, grain=grain)

    too_late = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 25), time_band="morning")
    with pytest.raises(RuntimeError, match="does not cover"):
        await _peak_hour_by_dow(aagency_id, too_late, aconn, "weekday", ch=ch_async_client, grain=grain)


@pytest.mark.asyncio
async def test_slow_path_pool_and_sequential_agree(aconn, aagency_id, ch_client, ch_async_client):
    """Pool-gather and sequential paths must produce identical payloads on the
    slow path too — both read the same prefetched grain, which is now built
    once before either branch runs."""
    cur_day = datetime.combine(date(2026, 5, 22), time(12, 0), tzinfo=timezone.utc)
    prv_day = cur_day - timedelta(days=7)
    for i in range(10):
        await _seed_update(aconn, aagency_id, prv_day + timedelta(minutes=i), "R_PS", 120, seq=i + 1)
        await _seed_update(aconn, aagency_id, cur_day + timedelta(minutes=i), "R_PS", 480, sched="07:00", seq=i + 1)
    await _seed_update(
        aconn, aagency_id, datetime.combine(date(2026, 5, 23), time(12, 0), tzinfo=timezone.utc), "R_PS", 300, seq=99
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)

    from api.main import _init_connection
    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 11), to_date=date(2026, 5, 24), time_band="morning")
    seq_out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=ch_async_client)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], init=_init_connection)
    try:
        pool_out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", pool=pool, ch=ch_async_client)
    finally:
        await pool.close()

    assert pool_out == seq_out
    # Sanity: the fixture really did exercise the slow path end-to-end. The
    # headline window anchors on the latest day WITH data (2026-05-23) and
    # spans the 7 days back to 2026-05-17, so it covers the ten 05-22 rows
    # plus the single 05-23 one.
    assert seq_out["headline"]["samples"] == 11
    assert seq_out["peak_hour_weekday"]["peak_hour"] == 7


# ---------------------------------------------------------------------------
# ch=None on a live-fallback path must raise a clear RuntimeError, not a bare
# AttributeError from `ch.query(...)`. These are the 3 spots in
# pipeline/reports/overview.py that predate the shared _Grain/_require_grain
# guard pattern. No agg/ClickHouse seeding needed — each guard fires before
# any query is issued.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_overview_summary_slow_path_without_ch_raises(aconn, aagency_id):
    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=None)


@pytest.mark.asyncio
async def test_peak_hour_by_dow_service_filtered_live_path_without_ch_raises(aconn, aagency_id):
    """ctx.time_band == 'all' but a service filter is set, so agg_hour_daily
    can't serve it and no grain was prefetched — the third (live-scan)
    branch of _peak_hour_by_dow must guard on a missing ch too."""
    from pipeline.reports.overview import _peak_hour_by_dow

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), service="平日")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await _peak_hour_by_dow(aagency_id, ctx, aconn, "weekday", ch=None, grain=None)


@pytest.mark.asyncio
async def test_route_weekly_history_slow_path_without_ch_raises(aconn, aagency_id):
    from pipeline.reports.overview import _route_weekly_history

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    with pytest.raises(RuntimeError, match="ClickHouse client"):
        await _route_weekly_history(aagency_id, ["R1"], ctx, aconn, ch=None)


# ---------------------------------------------------------------------------
# _peak_hour_by_dow's live scan is the ONLY ClickHouse work in an otherwise
# all-Postgres request shape (`time_band == 'all'` + a service/routes filter),
# so a ClickHouse hiccup there must degrade that one field, not 500 the whole
# payload. Same degrade shape as pipeline.reports.network.compute_network_summary
# and pipeline.health.aggregate_freshness.
# ---------------------------------------------------------------------------


class _FailingCh:
    """ClickHouse client stub whose every query raises. Counts attempts."""

    def __init__(self):
        self.attempts = 0

    async def query(self, sql, parameters=None, **kwargs):
        self.attempts += 1
        raise RuntimeError("simulated ClickHouse failure")


@pytest.mark.asyncio
async def test_peak_hour_by_dow_live_scan_degrades_on_ch_failure(aconn, aagency_id):
    """A ClickHouse failure in `_peak_hour_by_dow`'s live branch degrades that
    one field to None, exactly as `_peak_from_hour_rows` does for "no data"."""
    from pipeline.reports.overview import _peak_hour_by_dow

    # time_band == 'all' but a service filter is set: agg_hour_daily can't
    # serve it (aggregated across all services) and no grain was prefetched
    # (nothing else in this request needs live `updates`), so this is the live
    # third branch.
    ctx = RangeCtx(from_date=date(2026, 9, 1), to_date=date(2026, 9, 30), service="平日")
    failing = _FailingCh()
    assert await _peak_hour_by_dow(aagency_id, ctx, aconn, "weekday", ch=failing, grain=None) is None
    assert await _peak_hour_by_dow(aagency_id, ctx, aconn, "weekend", ch=failing, grain=None) is None
    assert failing.attempts == 2, "both dow groups must have actually attempted the query"


@pytest.mark.asyncio
async def test_overview_summary_survives_peak_hour_ch_failure(aconn, aagency_id):
    """The whole /overview/summary payload must survive a ClickHouse hiccup in
    `_peak_hour_by_dow`'s live branch.

    In this request shape (`time_band == 'all'` + a service filter) every other
    surface — headline, movers, concentration, top_delayed, peak_hour,
    service_split, sparkline — is served from Postgres `agg_*` tables, so
    letting the exception propagate would sink an otherwise complete response.
    `peak_hour_weekday`/`peak_hour_weekend` are already `PeakHour | None = None`
    in api.routers.overview, so degrading them to null is free.
    """
    # Baseline week (06-15..06-21) at 2.0 min, current week (06-22..06-28) at
    # 5.0 min, >= 10 samples per side so the route qualifies as a mover.
    for d in range(15, 22):
        await _seed_agg_daily(aconn, aagency_id, date(2026, 6, d), "R_DEG", "平日", 2.0, 20)
    for d in range(22, 29):
        await _seed_agg_daily(aconn, aagency_id, date(2026, 6, d), "R_DEG", "平日", 5.0, 20)
    # agg_route_hour backs `peak_hour` (the non-DOW one) — it must still come
    # through, proving only the two CH-derived fields degraded.
    await _seed_agg_route_hour(aconn, aagency_id, "R_DEG", "平日", "08:00", 7.0, 30)
    await _seed_agg_route_hour(aconn, aagency_id, "R_DEG", "平日", "17:00", 3.0, 30)

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 6, 1), to_date=date(2026, 6, 30), service="平日")
    failing = _FailingCh()
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=failing)

    # The two ClickHouse-derived fields degrade to null...
    assert out["peak_hour_weekday"] is None
    assert out["peak_hour_weekend"] is None
    assert failing.attempts == 2
    # ...and every Postgres-served surface is fully populated.
    assert out["headline"]["avg_min"] == pytest.approx(5.0, abs=0.01)
    assert out["headline"]["baseline_avg_min"] == pytest.approx(2.0, abs=0.01)
    assert out["headline"]["samples"] == 140
    assert out["headline"]["window_from"] == "2026-06-22"
    assert out["headline"]["window_to"] == "2026-06-28"
    assert [m["route_code"] for m in out["movers"]["worse"]] == ["R_DEG"]
    assert out["movers"]["worse"][0]["delta_min"] == pytest.approx(3.0, abs=0.01)
    assert out["concentration"]["top_routes"][0]["route_code"] == "R_DEG"
    assert out["top_delayed"]["routes"][0]["route_code"] == "R_DEG"
    assert out["peak_hour"]["peak_hour"] == 8
    assert out["service_split"] == {"平日": pytest.approx(3.5, abs=0.01)}
    assert len(out["sparkline_points"]) == 14


# ---------------------------------------------------------------------------
# `_route_weekly_history` derives its 4-week sparkline buckets from the shared
# grain whenever the grain's span reaches back far enough, instead of issuing a
# second full dedup scan. It keeps the live scan for a `ctx` window too narrow
# for the grain to cover.
# ---------------------------------------------------------------------------


async def _seed_weekly_history_fixture(aconn, ch_client, agency_id):
    """Seed a route with data in 3 of the 4 weekly buckets ending 2026-05-24.

    Buckets are keyed off `(2026-05-24 - date).days // 7`: wk0 = 05-18..05-24,
    wk1 = 05-11..05-17, wk2 = 05-04..05-10, wk3 = 04-27..05-03 (left empty, so
    the leading None is exercised too).
    """
    cur_day = datetime.combine(date(2026, 5, 24), time(12, 0), tzinfo=timezone.utc)
    prv_day = cur_day - timedelta(days=7)
    for i in range(10):
        await _seed_update(aconn, agency_id, prv_day + timedelta(minutes=i), "R_WH", 60, seq=i + 1)
        await _seed_update(aconn, agency_id, cur_day + timedelta(minutes=i), "R_WH", 600, seq=i + 1)
    # wk2, deliberately a non-round average (1090/3 s = 6.0555... min) so any
    # rounding divergence between the grain-derived and ClickHouse forms shows.
    old_day = datetime.combine(date(2026, 5, 6), time(12, 0), tzinfo=timezone.utc)
    for i, dep in enumerate((300, 421, 369)):
        await _seed_update(aconn, agency_id, old_day + timedelta(minutes=i), "R_WH", dep, seq=i + 1)
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)


@pytest.mark.asyncio
async def test_route_weekly_history_grain_matches_live_exactly(aconn, aagency_id, ch_client, ch_async_client):
    """The grain-derived weekly buckets must equal the ClickHouse ones EXACTLY
    (not approximately): the grain's sums are integer seconds and the single
    trailing `/ 60.0` reproduces `avg(dep_delay) / 60.0` bit-for-bit."""
    await _seed_weekly_history_fixture(aconn, ch_client, aagency_id)

    from pipeline.reports.overview import _fetch_grain, _grain_covers, _route_weekly_history

    # 24-day ctx: grain spans 2026-04-24..2026-05-24, the weekly-history span is
    # 2026-04-27..2026-05-24 — covered.
    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 24), time_band="morning")
    grain = await _fetch_grain(aagency_id, ctx, ch_async_client)
    assert _grain_covers(grain, date(2026, 4, 27), date(2026, 5, 24))

    counting = _CountingCh(ch_async_client)
    derived = await _route_weekly_history(aagency_id, ["R_WH"], ctx, aconn, ch=counting, grain=grain)
    assert counting.queries == [], "grain covers the span, so no ClickHouse query may be issued"

    live = await _route_weekly_history(aagency_id, ["R_WH"], ctx, aconn, ch=ch_async_client, grain=None)
    assert derived == live, f"grain-derived {derived} != ClickHouse {live}"
    # Sanity: the fixture really produced 3 populated buckets + a leading gap.
    assert derived["R_WH"][0] is None  # wk3, no data
    assert derived["R_WH"][1] == pytest.approx(1090 / 3 / 60.0, abs=1e-12)  # wk2
    assert derived["R_WH"][2] == pytest.approx(1.0, abs=1e-12)  # wk1
    assert derived["R_WH"][3] == pytest.approx(10.0, abs=1e-12)  # wk0


@pytest.mark.asyncio
async def test_route_weekly_history_grain_matches_live_with_dow_filter(aconn, aagency_id, ch_client, ch_async_client):
    """`ctx.dow` is applied server-side in the live branch and in Python in the
    grain branch (`_dow_matches`) — the two must agree exactly."""
    await _seed_weekly_history_fixture(aconn, ch_client, aagency_id)

    from pipeline.reports.overview import _fetch_grain, _route_weekly_history

    for dow in ("weekday", "weekend"):
        ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 24), time_band="morning", dow=dow)
        grain = await _fetch_grain(aagency_id, ctx, ch_async_client)
        counting = _CountingCh(ch_async_client)
        derived = await _route_weekly_history(aagency_id, ["R_WH"], ctx, aconn, ch=counting, grain=grain)
        assert counting.queries == []
        live = await _route_weekly_history(aagency_id, ["R_WH"], ctx, aconn, ch=ch_async_client, grain=None)
        assert derived == live, f"dow={dow}: grain-derived {derived} != ClickHouse {live}"
    # 2026-05-24 is a Sunday and 2026-05-17 a Sunday too, so the weekend slice
    # keeps wk0/wk1 while the weekday slice keeps only the 2026-05-06 (Wed) rows.
    assert derived["R_WH"][3] == pytest.approx(10.0, abs=1e-12)


@pytest.mark.asyncio
async def test_route_weekly_history_falls_back_when_grain_too_narrow(aconn, aagency_id, ch_client, ch_async_client):
    """A `ctx` window the grain can't cover must still take the live scan — and
    give the same answer a wide-window grain-derived run gives."""
    await _seed_weekly_history_fixture(aconn, ch_client, aagency_id)

    from pipeline.reports.overview import _fetch_grain, _grain_covers, _route_weekly_history

    # 7-day ctx: grain spans 2026-05-11..2026-05-24, weekly-history needs
    # 2026-04-27 — NOT covered.
    narrow = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning")
    narrow_grain = await _fetch_grain(aagency_id, narrow, ch_async_client)
    assert not _grain_covers(narrow_grain, date(2026, 4, 27), date(2026, 5, 24))

    counting = _CountingCh(ch_async_client)
    fallback = await _route_weekly_history(aagency_id, ["R_WH"], narrow, aconn, ch=counting, grain=narrow_grain)
    assert len(counting.queries) == 1, "an uncovered span must still issue its live scan"
    assert "intDiv(dateDiff" in counting.queries[0]

    # Same `to_date`, so the same four buckets — the grain-derived answer for a
    # wide ctx must match the live answer for the narrow one.
    wide = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 24), time_band="morning")
    wide_grain = await _fetch_grain(aagency_id, wide, ch_async_client)
    derived = await _route_weekly_history(aagency_id, ["R_WH"], wide, aconn, ch=ch_async_client, grain=wide_grain)
    assert fallback == derived, f"live fallback {fallback} != grain-derived {derived}"


@pytest.mark.asyncio
async def test_route_weekly_history_grain_boundary_coverage(aconn, aagency_id, ch_client, ch_async_client):
    """The coverage boundary: with `weeks_back=4` the grain reaches the span iff
    `ctx.to_date - ctx.from_date >= 20` (grain start = from_date - 7, span start
    = to_date - 27), assuming the headline anchor lands on `ctx.to_date`."""
    await _seed_weekly_history_fixture(aconn, ch_client, aagency_id)

    from pipeline.reports.overview import _fetch_grain, _grain_covers, _route_weekly_history

    span_from, to_date = date(2026, 4, 27), date(2026, 5, 24)
    just_under = RangeCtx(from_date=date(2026, 5, 5), to_date=to_date, time_band="morning")  # 19 days
    at_boundary = RangeCtx(from_date=date(2026, 5, 4), to_date=to_date, time_band="morning")  # 20 days

    under_grain = await _fetch_grain(aagency_id, just_under, ch_async_client)
    assert not _grain_covers(under_grain, span_from, to_date)
    boundary_grain = await _fetch_grain(aagency_id, at_boundary, ch_async_client)
    assert _grain_covers(boundary_grain, span_from, to_date)
    assert boundary_grain.from_date == span_from  # exactly reaches, not past

    counting_under = _CountingCh(ch_async_client)
    under = await _route_weekly_history(aagency_id, ["R_WH"], just_under, aconn, ch=counting_under, grain=under_grain)
    assert len(counting_under.queries) == 1

    counting_boundary = _CountingCh(ch_async_client)
    boundary = await _route_weekly_history(
        aagency_id, ["R_WH"], at_boundary, aconn, ch=counting_boundary, grain=boundary_grain
    )
    assert counting_boundary.queries == []
    # Identical `to_date` => identical buckets, whichever side of the boundary.
    assert under == boundary, f"live {under} != grain-derived {boundary} at the coverage boundary"


@pytest.mark.asyncio
async def test_slow_path_wide_window_movers_stay_at_one_query(aconn, aagency_id, ch_client, ch_async_client):
    """A default-width slow-path request with movers is back to ONE ClickHouse
    round trip: `_route_weekly_history`'s 4-week span fits inside the grain.

    The narrow-`ctx` counterpart (`test_slow_path_movers_add_exactly_one_more_query`)
    still measures 2, which is correct — the grain can't reach that far back there.
    """
    await _seed_weekly_history_fixture(aconn, ch_client, aagency_id)

    from pipeline.reports import compute_overview_summary

    counting = _CountingCh(ch_async_client)
    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 24), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja", ch=counting)

    assert len(counting.queries) == 1, f"expected 1 ClickHouse query, got {len(counting.queries)}"
    assert "GROUP BY date, route_code, service_type" in counting.queries[0]
    # The sparkline still comes through, populated off the grain.
    worse = out["movers"]["worse"]
    assert [m["route_code"] for m in worse] == ["R_WH"]
    assert worse[0]["delta_min"] == pytest.approx(9.0, abs=0.01)
    assert worse[0]["sparkline_points"] == [
        pytest.approx(1090 / 3 / 60.0, abs=1e-9),
        pytest.approx(1.0, abs=1e-9),
        pytest.approx(10.0, abs=1e-9),
    ]
