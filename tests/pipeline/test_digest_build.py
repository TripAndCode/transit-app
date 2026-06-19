"""DB-backed tests for build_digest."""

from datetime import date

from pipeline.digest.build import build_digest

DAY = date(2026, 4, 2)


def _seed(pg_conn, agency_id):
    """One degraded route (anomaly) + one normal route, with baselines + feed-health."""
    with pg_conn.cursor() as cur:
        for route, avg_sec in (("44372", 480), ("12", 120)):
            cur.execute(
                "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
                "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at) "
                "VALUES (%s, %s, %s, '平日', %s, %s, 10, 50, %s)",
                (agency_id, DAY, route, avg_sec, avg_sec * 2, "2026-04-02T11:37:00+09:00"),
            )
        for route, avg_min, p90_min in (("44372", 3.0, 5.0), ("12", 2.0, 4.0)):
            cur.execute(
                "INSERT INTO agg_route_stats (agency_id, route_code, service_type, "
                "avg_min, p50_min, p90_min, late_5min_plus, on_time_pct, late5_pct, samples) "
                "VALUES (%s, %s, '平日', %s, %s, %s, 0, 90.0, 1.0, 500)",
                (agency_id, route, avg_min, avg_min, p90_min),
            )
        cur.execute(
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) VALUES (%s, %s, 3400, 12)",
            (agency_id, DAY),
        )
    pg_conn.commit()


def test_build_movers_headline_and_footer(pg_conn, agency_id):
    _seed(pg_conn, agency_id)
    data = build_digest(pg_conn, DAY)
    assert data.target_day == DAY
    assert data.network_avg_delay_min == 5.0
    section = next(s for s in data.sections if s.agency_id == agency_id)
    assert section.has_data is True
    assert section.avg_delay_min == 5.0
    assert [m.route_code for m in section.movers] == ["44372"]
    assert section.movers[0].deviation_min == 5.0
    assert section.raw_samples == 3400
    assert section.clamp_count == 12
    assert section.is_stale is False


def test_build_empty_day_marks_no_data(pg_conn, agency_id):
    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)
    assert section.has_data is False
    assert section.avg_delay_min is None
    assert data.network_avg_delay_min is None


def _insert_daily(cur, agency_id, route, service_type, avg_sec, samples):
    cur.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 10, %s, %s)",
        (agency_id, DAY, route, service_type, avg_sec, avg_sec * 2, samples, "2026-04-02T11:37:00+09:00"),
    )


def _insert_stats(cur, agency_id, route, service_type, avg_min, p90_min, samples=500):
    cur.execute(
        "INSERT INTO agg_route_stats (agency_id, route_code, service_type, "
        "avg_min, p50_min, p90_min, late_5min_plus, on_time_pct, late5_pct, samples) "
        "VALUES (%s, %s, %s, %s, %s, %s, 0, 90.0, 1.0, %s)",
        (agency_id, route, service_type, avg_min, avg_min, p90_min, samples),
    )


def test_multi_service_type_dedups_to_one_mover(pg_conn, agency_id):
    """A route with two service_types on the same day yields exactly ONE mover,
    with a sample-weighted blended average."""
    with pg_conn.cursor() as cur:
        # Same route 44372, two service_types on the same day.
        _insert_daily(cur, agency_id, "44372", "平日", 480, 50)
        _insert_daily(cur, agency_id, "44372", "土曜", 600, 30)
        # Baseline per (route, service_type) — well below today's avg → anomaly.
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
        _insert_stats(cur, agency_id, "44372", "土曜", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    # Exactly one mover for the route, not two.
    matching = [m for m in section.movers if m.route_code == "44372"]
    assert len(matching) == 1
    # round((480*50 + 600*30) / 80) = 525 sec; round(525 / 60, 1) = 8.8.
    assert matching[0].avg_delay_min == 8.8


def test_top_5_cap_keeps_highest_deviation(pg_conn, agency_id):
    """Six+ anomaly routes → only the top 5 by deviation are kept, sorted desc."""
    # avg_sec per route well above baseline p90 (300s) → all anomalies.
    routes = [
        ("r1", 360),
        ("r2", 420),
        ("r3", 480),
        ("r4", 540),
        ("r5", 600),
        ("r6", 660),
        ("r7", 720),
    ]
    with pg_conn.cursor() as cur:
        for route, avg_sec in routes:
            _insert_daily(cur, agency_id, route, "平日", avg_sec, 50)
            _insert_stats(cur, agency_id, route, "平日", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    assert len(section.movers) == 5
    devs = [m.deviation_min for m in section.movers]
    assert devs == sorted(devs, reverse=True)
    # The 5 highest-deviation routes are r7..r3 (highest avgs).
    assert [m.route_code for m in section.movers] == ["r7", "r6", "r5", "r4", "r3"]


def test_multi_agency_network_is_sample_weighted(pg_conn, agency_id):
    """network_avg_delay_min weights by samples across agencies (not mean-of-means)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) "
            "VALUES ('digest agency 2','http://example.test/dfeed2') RETURNING agency_id"
        )
        agency_id2 = cur.fetchone()[0]
        # agency 1: avg 480s over 50 samples; agency 2: avg 240s over 150 samples.
        _insert_daily(cur, agency_id, "44372", "平日", 480, 50)
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
        _insert_daily(cur, agency_id2, "99", "平日", 240, 150)
        _insert_stats(cur, agency_id2, "99", "平日", 2.0, 4.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)

    # (480*50 + 240*150) / (50+150) = 60000/200 = 300 sec = 5.0 min.
    # mean-of-section-means would be (8.0 + 4.0)/2 = 6.0, so 5.0 proves weighting.
    assert data.network_avg_delay_min == 5.0


def test_low_confidence_anomaly_caps_at_watch(pg_conn, agency_id):
    """A thin anomaly route (samples < 30) is low_confidence and capped at watch."""
    with pg_conn.cursor() as cur:
        # avg 480s >> baseline p90 (300s) → would be anomaly, but only 10 samples.
        _insert_daily(cur, agency_id, "44372", "平日", 480, 10)
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)
    mover = next(m for m in section.movers if m.route_code == "44372")
    assert mover.low_confidence is True
    assert mover.bucket == "watch"


def test_null_service_route_gets_route_grain_baseline(pg_conn, agency_id):
    """A NULL-service route (stored as '' in agg_route_daily) finds the route's
    overall baseline (aggregated across service_types in agg_route_stats) and is
    triaged as a mover — proving '' rows are no longer dropped by the baseline join."""
    with pg_conn.cursor() as cur:
        # Daily row with empty-string service_type (the COALESCE'd NULL case).
        _insert_daily(cur, agency_id, "44372", "", 480, 50)
        # Baseline only under a TYPED service_type ('平日'); no '' row exists.
        # day avg 480s > baseline p90 (300s) → anomaly once the route baseline matches.
        _insert_stats(cur, agency_id, "44372", "平日", 3.0, 5.0)
    pg_conn.commit()

    data = build_digest(pg_conn, DAY)
    section = next(s for s in data.sections if s.agency_id == agency_id)

    matching = [m for m in section.movers if m.route_code == "44372"]
    assert len(matching) == 1
    assert matching[0].bucket == "anomaly"
    assert matching[0].baseline_avg_min == 3.0


def test_cmd_digest_prints_markdown(pg_conn, agency_id, capsys):
    import gtfs_pipeline

    _seed(pg_conn, agency_id)

    class _Args:
        day = "2026-04-02"
        locale = "ja"

    gtfs_pipeline.cmd_digest(_Args())
    out = capsys.readouterr().out
    assert "日次ダイジェスト 2026-04-02" in out
    assert "44372" in out
