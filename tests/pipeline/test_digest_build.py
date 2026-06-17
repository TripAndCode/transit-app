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
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) "
            "VALUES (%s, %s, 3400, 12)",
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
