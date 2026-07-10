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
):
    """Insert (or upsert) one ``agg_daily_trend`` row.

    ``date_`` may be a :class:`datetime.date` or an ISO string;
    ``agg_daily_trend.date`` is TEXT per schema, so we coerce both shapes.
    """
    iso = date_.isoformat() if hasattr(date_, "isoformat") else str(date_)
    await aconn.execute(
        "INSERT INTO agg_daily_trend "
        "(agency_id, date, route_code, service_type, avg_min, samples) "
        "VALUES ($1, $2, $3, $4, $5, $6) "
        "ON CONFLICT (agency_id, date, route_code, service_type) DO UPDATE "
        "SET avg_min = EXCLUDED.avg_min, samples = EXCLUDED.samples",
        agency_id,
        iso,
        route_code,
        service_type,
        float(avg_min),
        int(samples),
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
    schema column is TIME (post 0011), so we cast on the server side."""
    await aconn.execute(
        "INSERT INTO agg_route_hour "
        "(agency_id, route_code, service_type, scheduled_time, avg_min, p50_min, p90_min, samples) "
        "VALUES ($1, $2, $3, ($4::text)::time, $5, NULL, NULL, $6) "
        "ON CONFLICT (agency_id, route_code, service_type, scheduled_time) DO UPDATE "
        "SET avg_min = EXCLUDED.avg_min, samples = EXCLUDED.samples",
        agency_id,
        route_code,
        service_type,
        scheduled_time,
        float(avg_min),
        int(samples),
    )


async def _seed_agg_hour_daily(aconn, agency_id, date_, hour, avg_min, samples):
    """Insert one ``agg_hour_daily`` row (per-day, per-hour-of-day)."""
    iso = date_.isoformat() if hasattr(date_, "isoformat") else str(date_)
    await aconn.execute(
        "INSERT INTO agg_hour_daily (agency_id, date, hour, avg_min, samples) "
        "VALUES ($1, ($2::text)::date, $3, $4, $5) "
        "ON CONFLICT (agency_id, date, hour) DO UPDATE "
        "SET avg_min = EXCLUDED.avg_min, samples = EXCLUDED.samples",
        agency_id,
        iso,
        int(hour),
        float(avg_min),
        int(samples),
    )


async def _seed_agg_route_stats(aconn, agency_id, route_code, service_type, avg_min, p90_min, late5_pct, samples):
    await aconn.execute(
        "INSERT INTO agg_route_stats "
        "(agency_id, route_code, service_type, avg_min, p90_min, late5_pct, samples) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) "
        "ON CONFLICT (agency_id, route_code, service_type) DO UPDATE "
        "SET avg_min=EXCLUDED.avg_min, p90_min=EXCLUDED.p90_min, "
        "    late5_pct=EXCLUDED.late5_pct, samples=EXCLUDED.samples",
        agency_id, route_code, service_type,
        float(avg_min), float(p90_min), float(late5_pct), int(samples),
    )


async def _seed_agg_route_hour_dow(aconn, agency_id, route_code, service_type, dow, hour, avg_min, samples):
    await aconn.execute(
        "INSERT INTO agg_route_hour_dow "
        "(agency_id, route_code, service_type, dow, hour, avg_min, samples) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) "
        "ON CONFLICT (agency_id, route_code, service_type, dow, hour) DO UPDATE "
        "SET avg_min=EXCLUDED.avg_min, samples=EXCLUDED.samples",
        agency_id, route_code, service_type, dow, hour,
        float(avg_min), int(samples),
    )


async def _seed_agg_route_daily(aconn, agency_id, date_, route_code, service_type, avg_delay_sec, samples):
    await aconn.execute(
        "INSERT INTO agg_route_daily "
        "(agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        " trips_observed, samples, last_seen_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) "
        "ON CONFLICT (agency_id, date, route_code, service_type) DO NOTHING",
        agency_id, date_, route_code, service_type,
        int(avg_delay_sec), int(avg_delay_sec), 1, int(samples),
        datetime.combine(date_, time(12, 0), tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_overview_endpoint_returns_empty_payload_when_no_data(client, aagency_id):
    """An agency with no `updates` rows in range returns a zero-filled
    OverviewSummary, not a 404 or 500."""
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

    Concentration is computed as ``SUM(GREATEST(avg_min, 0) * samples)``
    per route on ``agg_daily_trend`` — directly proportional to total
    positive delay minutes. The card variant on the frontend slices the
    top 5; the modal variant uses all 20.
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
async def test_top_delayed_routes_falls_back_to_live_under_time_band(aconn, aagency_id):
    """Non-default time_band bypasses agg_daily_trend and reads updates
    directly, same fallback _concentration() already uses."""
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

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 18), time_band="morning")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    routes = out["top_delayed"]["routes"]
    assert len(routes) == 1
    assert routes[0]["route_code"] == "R_TD"
    assert routes[0]["avg_min"] == pytest.approx(5.5, abs=0.05)


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
async def test_headline_uses_live_path_when_time_band_set(aconn, aagency_id):
    """When ``ctx.time_band != 'all'``, the headline must read from live
    ``updates`` so the hour-of-day filter actually applies. The four
    seeded rows span morning / noon / evening; ``time_band='morning'``
    must keep only the two scheduled inside 05:00-09:00."""
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

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(
        from_date=date(2026, 5, 6),
        to_date=date(2026, 5, 6),
        time_band="morning",
    )
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
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
async def test_peak_hour_falls_back_to_live_under_service_filter(aconn, aagency_id):
    """A service filter (the agg has no service dimension) routes peak-hour to
    the live scan, which still partitions weekday/weekend correctly."""
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

    from pipeline.reports import compute_overview_summary

    # service != 'all' forces the live path (agg_hour_daily has no service column).
    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), service="平日")
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
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
async def test_route_summary_includes_late5_pct(client, aconn, aagency_id):
    from datetime import date
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
async def test_route_summary_late5_pct_null_when_no_stats(client, aconn, aagency_id):
    from datetime import date
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
