"""DB-backed tests for the agg-freshness check and the cron integration."""

from datetime import date, time

from pipeline.analyze import analyze
from pipeline.freshness import check_agg_freshness


class _FakeChClient:
    """Stub ClickHouse client: always reports no `updates` rows.

    Used by the cron-integration tests below, which are really about
    _run_ingest_and_analyze()'s JST-boundary/orchestration behavior, not
    about ClickHouse itself — stubbing this out keeps them independent of
    `make ch-test` while the freshness-specific tests further down use the
    real `ch_client` fixture.
    """

    def query(self, *_args, **_kwargs):
        class _Result:
            result_rows = [(None,)]

        return _Result()


def _seed_two_days(pg_conn, agency_id):
    """Insert mid-day rows across two completed civil days (well before today)
    into Postgres `updates` — the source analyze() reads to build
    agg_route_daily (unmigrated this task; see pipeline/analyze.py).

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


def _seed_two_days_ch(ch_client, agency_id):
    """Insert the same two completed civil days into ClickHouse `updates` —
    the source check_agg_freshness now reads for the live-side max.

    02:37 UTC == 11:37 JST on the same calendar day, so this lands on the
    same civil dates _seed_two_days uses for the Postgres/analyze() side.
    """
    from pipeline.clickhouse import insert_updates

    rows = [
        (
            f"f{day}_{seq}.pb",
            f"2026-04-0{day}T02:37:00Z",
            "平日_11時37分_系統44372",
            "平日",
            "11:37",
            "44372",
            seq,
            seq * 60,
        )
        for day in (1, 2)
        for seq in (1, 2, 3)
    ]
    insert_updates(ch_client, agency_id, rows)


def test_fresh_after_analyze(pg_conn, ch_client, agency_id):
    _seed_two_days(pg_conn, agency_id)
    _seed_two_days_ch(ch_client, agency_id)
    analyze(agency_id, pg_conn)
    stale = check_agg_freshness(pg_conn, ch_client, [agency_id])
    assert stale == []


def test_stale_when_latest_agg_day_missing(pg_conn, ch_client, agency_id):
    _seed_two_days(pg_conn, agency_id)
    _seed_two_days_ch(ch_client, agency_id)
    analyze(agency_id, pg_conn)
    # Drop the newest agg day → aggs now cover only 2026-04-01 while live has 04-02.
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM agg_route_daily WHERE agency_id = %s AND date = "
            "(SELECT MAX(date) FROM agg_route_daily WHERE agency_id = %s)",
            (agency_id, agency_id),
        )
    pg_conn.commit()
    stale = check_agg_freshness(pg_conn, ch_client, [agency_id])
    assert len(stale) == 1
    assert stale[0].agency_id == agency_id
    assert stale[0].agg_max_day == date(2026, 4, 1)
    assert stale[0].live_max_completed_day == date(2026, 4, 2)


def test_empty_agency_is_fresh(pg_conn, ch_client, agency_id):
    # No updates rows at all → nothing owed.
    stale = check_agg_freshness(pg_conn, ch_client, [agency_id])
    assert stale == []


def test_empty_input_returns_empty(pg_conn, ch_client):
    assert check_agg_freshness(pg_conn, ch_client, []) == []


def test_multi_agency_only_stale_returned(pg_conn, ch_client, agency_id):
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
    _seed_two_days_ch(ch_client, agency_id)
    _seed_two_days(pg_conn, second_id)
    _seed_two_days_ch(ch_client, second_id)
    analyze(agency_id, pg_conn)
    analyze(second_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM agg_route_daily WHERE agency_id = %s AND date = "
            "(SELECT MAX(date) FROM agg_route_daily WHERE agency_id = %s)",
            (second_id, second_id),
        )
    pg_conn.commit()

    stale = check_agg_freshness(pg_conn, ch_client, [agency_id, second_id])
    assert len(stale) == 1
    assert stale[0].agency_id == second_id


def test_check_agg_freshness_uses_jst_date_not_utc_date(pg_conn, ch_client, agency_id):
    """Direct regression coverage for the JST/UTC boundary in
    check_agg_freshness's live-side cutoff (same bug class as
    tests/unit/test_db_dedup_ch.py::test_dedup_ch_buckets_by_jst_day_not_utc_day
    proved for the dedup query - and the same class of bug project memory
    "analyze conn must pin JST" hit for real: a UTC-pinned connection
    mis-bucketed ~20% of rows relative to the JST-pinned API).

    2026-01-01T16:30:00Z is 2026-01-02 01:30 JST - a different calendar day
    in each timezone. agg_route_daily is seeded at exactly the UTC date
    (2026-01-01); if check_agg_freshness's cutoff ever regressed from
    `latest.astimezone(_JST).date()` back to a bare UTC `.date()`, live_max
    would equal agg_max (both 2026-01-01) and the agency would NOT be
    flagged stale. The JST-correct answer is one day ahead of the seeded
    agg (2026-01-02), so this only passes with the JST conversion intact.
    """
    from pipeline.clickhouse import insert_updates

    insert_updates(
        ch_client,
        agency_id,
        [("boundary.pb", "2026-01-01T16:30:00Z", "T1", "平日", "01:30", "44372", 1, 60)],
    )
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
            "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at) "
            "VALUES (%s, %s, %s, '平日', 60, 120, 1, 1, %s)",
            (agency_id, date(2026, 1, 1), "44372", "2026-01-01T16:30:00+00:00"),
        )
    pg_conn.commit()

    stale = check_agg_freshness(pg_conn, ch_client, [agency_id])

    assert len(stale) == 1
    assert stale[0].agency_id == agency_id
    assert stale[0].agg_max_day == date(2026, 1, 1)
    assert stale[0].live_max_completed_day == date(2026, 1, 2)  # JST date, not the UTC 2026-01-01


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
    rows. check_agg_freshness's ClickHouse client is stubbed to report no
    live rows (see _FakeChClient) — with nothing live, nothing is owed, so
    the run is always "fresh" regardless of the seeded Postgres rows. This
    test is about the cron loop's orchestration (it calls analyze + the
    freshness check and logs the right summary), not about ClickHouse data;
    tests above cover check_agg_freshness's ClickHouse-sourced behavior
    directly. Mid-day rows still make the JST/UTC civil dates coincide for
    analyze()'s own bucketing; test_cron_path_jst_boundary_is_fresh covers
    the day-boundary case where the connection timezone actually matters.
    """
    import logging

    import api.routers.internal as internal
    import pipeline.clickhouse
    import pipeline.ingest

    _seed_two_days(pg_conn, agency_id)
    monkeypatch.setattr(pipeline.ingest, "ingest_live", lambda aid, conn, ch_client: None)
    monkeypatch.setattr(pipeline.clickhouse, "get_client", lambda: _FakeChClient())

    with caplog.at_level(logging.INFO, logger="api.routers.internal"):
        internal._run_ingest_and_analyze()

    assert "fresh aggregates" in caplog.text
    assert "stale aggregates" not in caplog.text


def test_cron_path_jst_boundary_is_fresh(pg_conn, agency_id, monkeypatch, caplog):
    """An early-JST-morning observation (= prior UTC day) stays fresh via cron.

    01:30 JST on 2026-04-02 is 16:30 UTC on 2026-04-01 — the JST and UTC civil
    dates differ. The cron connection pins JST, so analyze buckets
    agg_route_daily.date to 2026-04-02. check_agg_freshness's ClickHouse
    client is stubbed to report no live rows (see _FakeChClient), so nothing
    is owed and the run is fresh regardless — this test only locks that the
    cron loop still reaches "fresh" (no crash/mis-bucket in analyze()) at
    this boundary; the JST-vs-UTC comparison itself is covered directly by
    the check_agg_freshness tests above.
    """
    import logging

    import api.routers.internal as internal
    import pipeline.clickhouse
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
    monkeypatch.setattr(pipeline.ingest, "ingest_live", lambda aid, conn, ch_client: None)
    monkeypatch.setattr(pipeline.clickhouse, "get_client", lambda: _FakeChClient())

    with caplog.at_level(logging.INFO, logger="api.routers.internal"):
        internal._run_ingest_and_analyze()

    assert "fresh aggregates" in caplog.text
    assert "stale aggregates" not in caplog.text
