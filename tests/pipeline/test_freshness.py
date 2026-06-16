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
