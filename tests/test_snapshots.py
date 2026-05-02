from pipeline.snapshots import write_snapshots


def test_write_snapshots_empty_agg_writes_nothing(pg_conn, agency_id):
    # With empty agg tables, no snapshots are written
    write_snapshots(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM snapshots WHERE agency_id=%s", (agency_id,))
        count = cur.fetchone()[0]
    assert count == 0


def test_write_snapshots_with_route_stats_data(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO agg_route_stats "
            "(agency_id, route_code, service_type, avg_min, p50_min, p90_min, "
            " late_5min_plus, on_time_pct, late5_pct, samples) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (agency_id, "44", "平日", 4.2, 3.1, 6.8, 5, 75.0, 10.0, 1000),
                (agency_id, "44", "土日祝", 6.8, 5.0, 9.0, 10, 60.0, 20.0, 500),
                (agency_id, "22", "平日", 2.1, 1.5, 3.5, 1, 90.0, 5.0, 800),
            ],
        )
    pg_conn.commit()

    write_snapshots(agency_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT report_type, text FROM snapshots WHERE agency_id=%s ORDER BY report_type",
            (agency_id,),
        )
        snaps = {r[0]: r[1] for r in cur.fetchall()}

    assert "ranking" in snaps
    assert "系統44" in snaps["ranking"]
    assert "ranking_best" in snaps
    assert "on_time" in snaps
    assert "worst_5min" in snaps


def test_write_snapshots_idempotent(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_route_stats "
            "(agency_id, route_code, service_type, avg_min, p50_min, p90_min, "
            " late_5min_plus, on_time_pct, late5_pct, samples) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (agency_id, "44", "平日", 4.2, 3.1, 6.8, 5, 75.0, 10.0, 1000),
        )
    pg_conn.commit()

    write_snapshots(agency_id, pg_conn)
    write_snapshots(agency_id, pg_conn)  # second call must not fail (ON CONFLICT DO UPDATE)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM snapshots WHERE agency_id=%s AND report_type='ranking'",
            (agency_id,),
        )
        count = cur.fetchone()[0]
    assert count == 1
