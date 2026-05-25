"""Tests for the 概況 (Overview) tab endpoint.

The Overview backend reads from the pre-aggregated ``agg_daily_trend`` and
``agg_route_hour`` tables (not the row-level ``updates`` table) so cold
loads stay sub-second on multi-month windows. Tests seed both layers:
``updates`` is kept around so any future helpers that still touch it
stay covered, while ``agg_*`` is what the Overview reads.
"""

from datetime import date, datetime, time, timedelta, timezone

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
async def test_movers_ranks_top_3_worse_and_top_3_better(aconn, aagency_id):
    """Eight routes with varied this-week vs prior-week deltas;
    top 3 worsened + top 3 improved come out sorted by |delta_min|.

    Each route seeds 10 samples per side (>= 10 sample gate in ``_movers``).
    Current week is anchored to ctx.to_date so it matches the latest data.
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
    assert worse_codes == ["R_A", "R_B", "R_C"]
    assert better_codes == ["R_E", "R_F", "R_G"]


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
async def test_concentration_top_3_and_rest_share(aconn, aagency_id):
    """4 routes; top 3 absorb ~87.5%; the 4th absorbs ~12.5%.

    Concentration is computed as ``SUM(GREATEST(avg_min, 0) * samples)``
    per route on ``agg_daily_trend`` — directly proportional to total
    positive delay minutes.
    """
    base = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    rows = [
        ("R_X", [300, 300, 300]),  # avg=5.0, n=3 -> total=15
        ("R_Y", [600]),  # avg=10.0, n=1 -> total=10
        ("R_Z", [300, 300]),  # avg=5.0, n=2 -> total=10
        ("R_W", [150, 150]),  # avg=2.5, n=2 -> total=5
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
    assert len(codes) == 3
    assert set(codes) == {"R_X", "R_Y", "R_Z"}
    total_pct = sum(r["share_pct"] for r in conc["top_routes"]) + conc["rest_share_pct"]
    assert total_pct == pytest.approx(100.0, abs=0.5)
    assert conc["rest_share_pct"] == pytest.approx(12.5, abs=0.5)


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
