"""DB-backed tests for the agg-freshness check and the cron integration."""

from datetime import date, time

from pipeline.analyze import analyze
from pipeline.freshness import check_agg_freshness


def _seed_two_days(pg_conn, agency_id):
    """Insert mid-day rows across two completed civil days (well before today).

    Mid-day (11:37) keeps the JST/UTC civil date identical so the test is
    independent of the test connection's session timezone.
    """
    with pg_conn.cursor() as cur:
        for day in (1, 2):
            for seq in (1, 2, 3):
                cur.execute(
                    "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                    "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        agency_id,
                        f"f{day}_{seq}.pb",
                        f"2026-04-0{day}T11:37:00+09:00",
                        "平日_11時37分_系統44372",
                        "平日",
                        time(11, 37),
                        "44372",
                        seq,
                        seq * 60,
                    ),
                )
    pg_conn.commit()


def test_fresh_after_analyze(pg_conn, agency_id):
    _seed_two_days(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    stale = check_agg_freshness(pg_conn, [agency_id])
    assert stale == []


def test_stale_when_latest_agg_day_missing(pg_conn, agency_id):
    _seed_two_days(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    # Drop the newest agg day → aggs now cover only 2026-04-01 while live has 04-02.
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM agg_route_daily WHERE agency_id = %s AND date = "
            "(SELECT MAX(date) FROM agg_route_daily WHERE agency_id = %s)",
            (agency_id, agency_id),
        )
    pg_conn.commit()
    stale = check_agg_freshness(pg_conn, [agency_id])
    assert len(stale) == 1
    assert stale[0].agency_id == agency_id
    assert stale[0].agg_max_day == date(2026, 4, 1)
    assert stale[0].live_max_completed_day == date(2026, 4, 2)


def test_empty_agency_is_fresh(pg_conn, agency_id):
    # No updates rows at all → nothing owed.
    stale = check_agg_freshness(pg_conn, [agency_id])
    assert stale == []


def test_empty_input_returns_empty(pg_conn):
    assert check_agg_freshness(pg_conn, []) == []


def test_multi_agency_only_stale_returned(pg_conn, agency_id):
    # Two agencies seeded + analyzed identically; drop the newest agg day for
    # only the second so it lags while the first stays fresh.
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("test agency 2", "http://example.test/feed2"),
        )
        second_id = cur.fetchone()[0]
    pg_conn.commit()

    _seed_two_days(pg_conn, agency_id)
    _seed_two_days(pg_conn, second_id)
    analyze(agency_id, pg_conn)
    analyze(second_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM agg_route_daily WHERE agency_id = %s AND date = "
            "(SELECT MAX(date) FROM agg_route_daily WHERE agency_id = %s)",
            (second_id, second_id),
        )
    pg_conn.commit()

    stale = check_agg_freshness(pg_conn, [agency_id, second_id])
    assert len(stale) == 1
    assert stale[0].agency_id == second_id


def test_analyze_writes_agg_meta(pg_conn, agency_id):
    _seed_two_days(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT analyzed_at, max_updates_captured_at FROM agg_meta WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is not None  # analyzed_at stamped
    assert row[1] is not None  # max_updates_captured_at populated (rows exist)


def test_cron_path_logs_fresh(pg_conn, agency_id, monkeypatch, caplog):
    """_run_ingest_and_analyze runs the freshness check after analyzing.

    ingest_live is stubbed (no network); analyze runs for real against seeded
    rows. Mid-day rows make the JST/UTC civil dates coincide, so this only
    covers the happy path; test_cron_path_jst_boundary_is_fresh covers the
    day-boundary case where the connection timezone actually matters.
    """
    import logging

    import api.routers.internal as internal
    import pipeline.ingest

    _seed_two_days(pg_conn, agency_id)
    monkeypatch.setattr(pipeline.ingest, "ingest_live", lambda aid, conn: None)

    with caplog.at_level(logging.INFO, logger="api.routers.internal"):
        internal._run_ingest_and_analyze()

    assert "fresh aggregates" in caplog.text
    assert "stale aggregates" not in caplog.text


def test_cron_path_jst_boundary_is_fresh(pg_conn, agency_id, monkeypatch, caplog):
    """An early-JST-morning observation (= prior UTC day) stays fresh via cron.

    01:30 JST on 2026-04-02 is 16:30 UTC on 2026-04-01 — the JST and UTC civil
    dates differ. The cron connection pins JST, so analyze buckets
    agg_route_daily.date to 2026-04-02, matching the JST-based freshness check.
    Without the pin, analyze would bucket to 2026-04-01 (UTC) and the gate would
    false-fire STALE — this test locks that regression.
    """
    import logging

    import api.routers.internal as internal
    import pipeline.ingest

    with pg_conn.cursor() as cur:
        for seq in (1, 2, 3):
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    f"boundary{seq}.pb",
                    "2026-04-02T01:30:00+09:00",  # 16:30 UTC on 2026-04-01
                    "平日_01時30分_系統44372",
                    "平日",
                    time(1, 30),
                    "44372",
                    seq,
                    seq * 60,
                ),
            )
    pg_conn.commit()
    monkeypatch.setattr(pipeline.ingest, "ingest_live", lambda aid, conn: None)

    with caplog.at_level(logging.INFO, logger="api.routers.internal"):
        internal._run_ingest_and_analyze()

    assert "fresh aggregates" in caplog.text
    assert "stale aggregates" not in caplog.text
