from datetime import time

from pipeline.analyze import analyze
from tests.conftest import mirror_updates_to_ch


def _analyze(agency_id, pg_conn, ch_client):
    """Mirror this agency's Postgres `updates` rows into ClickHouse (the
    dedup materialization's source as of Task 6) and run analyze().

    Every fixture in this file seeds Postgres `updates` directly (pre-dating
    the ClickHouse migration); mirroring right before analyze() lets those
    seeds keep driving the ClickHouse-sourced aggregates without duplicating
    each one. See tests.conftest.mirror_updates_to_ch.
    """
    mirror_updates_to_ch(ch_client, agency_id)
    analyze(agency_id, pg_conn, ch_client)


def _seed_updates(pg_conn, agency_id):
    """Insert 25 fake rows directly for analyze testing."""
    with pg_conn.cursor() as cur:
        for i in range(25):
            day = (i % 25) + 1
            seq = (i % 3) + 1
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    f"f{i}.pb",
                    f"2026-04-{day:02d}T11:37:00",
                    "平日_11時37分_系統44372",
                    "平日",
                    time(11, 37),  # TIME column after migration 0011 (was "11:37" text).
                    "44372",
                    seq,
                    (seq * 60) + i * 30,
                ),
            )
    pg_conn.commit()


def test_analyze_creates_agg_route_stats(pg_conn, agency_id, ch_client):
    _seed_updates(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_code, service_type, avg_min FROM agg_route_stats WHERE agency_id = %s",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert len(rows) > 0
    assert rows[0][0] == "44372"
    assert rows[0][2] is not None


def _seed_thin_route(pg_conn, agency_id, route_code, n):
    """Insert *n* fake rows for a single (route_code, service_type='平日',
    stop_sequence=1) group — deliberately fewer than both agg_route_stats'
    former per-(route, service_type) gate (`HAVING COUNT(*) > 20`) and
    agg_stop_seq's former per-(route, stop_sequence) gate
    (`HAVING COUNT(*) > 5`). A unique day per row (like _seed_updates) keeps
    every row a distinct post-dedup (trip_id, date, stop_sequence) key, so
    the post-dedup sample count is exactly *n*.
    """
    with pg_conn.cursor() as cur:
        for i in range(n):
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    f"thin{i}.pb",
                    f"2026-04-{i + 1:02d}T09:00:00",
                    f"平日_9時_系統{route_code}",
                    "平日",
                    time(9, 0),
                    route_code,
                    1,
                    30,
                ),
            )
    pg_conn.commit()


def test_analyze_keeps_low_sample_agg_route_stats_row(pg_conn, agency_id, ch_client):
    """analyze() has no insert-time minimum-sample gate on any agg_* table —
    a (route, service_type) group with only 3 samples must still appear in
    agg_route_stats (with samples=3), not be silently dropped the way the
    former `HAVING COUNT(*) > 20` gate would have dropped it."""
    _seed_thin_route(pg_conn, agency_id, "99999", 3)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT samples FROM agg_route_stats WHERE agency_id = %s AND route_code = %s",
            (agency_id, "99999"),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 3


def test_analyze_keeps_low_sample_agg_stop_seq_row(pg_conn, agency_id, ch_client):
    """analyze() has no insert-time minimum-sample gate on any agg_* table —
    a (route, stop_sequence) group with only 3 samples must still appear in
    agg_stop_seq (with samples=3), not be silently dropped the way the former
    `HAVING COUNT(*) > 5` gate would have dropped it."""
    _seed_thin_route(pg_conn, agency_id, "99998", 3)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT samples FROM agg_stop_seq WHERE agency_id = %s AND route_code = %s AND stop_sequence = 1",
            (agency_id, "99998"),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 3


def test_analyze_creates_agg_route_hour(pg_conn, agency_id, ch_client):
    _seed_updates(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agg_route_hour WHERE agency_id = %s",
            (agency_id,),
        )
        count = cur.fetchone()[0]
    assert count > 0


def test_analyze_creates_agg_route_dow(pg_conn, agency_id, ch_client):
    _seed_updates(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT dow FROM agg_route_dow WHERE agency_id = %s",
            (agency_id,),
        )
        dows = {r[0] for r in cur.fetchall()}
    assert dows <= {1, 2, 3, 4, 5, 6, 7}
    assert len(dows) > 0


def test_analyze_creates_agg_route_hour_dow(pg_conn, agency_id, ch_client):
    """Characterization: analyze() derives agg_route_hour_dow (dow × scheduled
    hour) from raw `updates`, not just via direct test-fixture inserts (the
    only prior exercise of this table, in test_forecast_heatmap.py)."""
    _seed_updates(pg_conn, agency_id)  # every row scheduled at 11:37 → hour 11
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT hour FROM agg_route_hour_dow WHERE agency_id = %s",
            (agency_id,),
        )
        hours = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT DISTINCT dow FROM agg_route_hour_dow WHERE agency_id = %s",
            (agency_id,),
        )
        dows = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT bool_and(samples > 0 AND avg_min IS NOT NULL) FROM agg_route_hour_dow WHERE agency_id = %s",
            (agency_id,),
        )
        well_formed = cur.fetchone()[0]
    assert hours == {11}
    assert dows <= {1, 2, 3, 4, 5, 6, 7}
    assert len(dows) > 0
    assert well_formed


def test_analyze_creates_agg_route_daily(pg_conn, agency_id, ch_client):
    """Characterization: analyze() derives agg_route_daily (per-route,
    per-day summary powering the fast today/route-summary path) from raw
    `updates`, not just via direct test-fixture inserts (the only prior
    exercise of this table, in test_overview.py)."""
    _seed_updates(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_code, service_type, avg_delay_sec, worst_delay_sec, trips_observed, samples, "
            "last_seen_at FROM agg_route_daily WHERE agency_id = %s",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert len(rows) > 0
    for route_code, service_type, avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at in rows:
        assert route_code == "44372"
        assert service_type == "平日"
        assert avg_delay_sec is not None
        assert worst_delay_sec is not None
        assert trips_observed > 0
        assert samples > 0
        assert last_seen_at is not None


def test_analyze_creates_agg_stop_seq_with_stop_name(pg_conn, agency_id, ch_client):
    _seed_updates(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_name FROM agg_stop_seq WHERE agency_id = %s LIMIT 1",
            (agency_id,),
        )
        stop_name = cur.fetchone()[0]
    assert stop_name is not None
    assert "番停留所" in stop_name


def test_analyze_agg_stop_seq_with_real_stop_name(pg_conn, agency_id, ch_client):
    _seed_updates(pg_conn, agency_id)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, %s, %s)",
            (agency_id, "S1", "青森駅"),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (agency_id, "平日_11時37分_系統44372", 1, "S1", "11:37"),
        )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_name FROM agg_stop_seq WHERE agency_id = %s AND stop_sequence = 1",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "青森駅"


def test_analyze_creates_agg_daily_trend(pg_conn, agency_id, ch_client):
    _seed_updates(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agg_daily_trend WHERE agency_id = %s",
            (agency_id,),
        )
        count = cur.fetchone()[0]
    assert count > 0


def test_analyze_sum_delay_sec_pools_exactly_unlike_reweighted_avg_min(pg_conn, agency_id, ch_client):
    """analyze() must store the exact raw-seconds sum alongside avg_min, so a
    multi-row pool over agg_daily_trend can divide once at the end instead of
    re-weighting each row's own already-rounded avg_min.

    Day 1: 3 observations at 41s/41s/42s -> sum=124s, avg=41.333s (rounds to
    0.69 min). Day 2: 7 observations at 100s each -> sum=700s, avg=100s
    (rounds to 1.67 min). The true combined mean is 824s / 10 / 60 = 1.3733
    min, which rounds to 1.37 -- but re-weighting the two ALREADY-ROUNDED
    per-day avg_min values instead (the pre-fix pattern) gives
    (0.69*3 + 1.67*7) / 10 = 1.376, which rounds to 1.38: a different, wrong
    answer that exists purely because of the intermediate rounding.
    """
    with pg_conn.cursor() as cur:
        i = 0
        for day, delays in (("2026-04-01", [41, 41, 42]), ("2026-04-02", [100] * 7)):
            for dep in delays:
                cur.execute(
                    "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                    "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        agency_id,
                        f"sdw{i}.pb",
                        f"{day}T09:00:00",
                        f"trip_sdw_{i}",
                        "平日",
                        time(9, 0),
                        "SDW1",
                        1,
                        dep,
                    ),
                )
                i += 1
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, samples, sum_delay_sec FROM agg_daily_trend "
            "WHERE agency_id = %s AND route_code = 'SDW1' ORDER BY date",
            (agency_id,),
        )
        rows = cur.fetchall()
        assert len(rows) == 2
        (_d1, n1, s1), (_d2, n2, s2) = rows
        # sum_delay_sec is the exact SUM(dep_delay) behind each row's avg_min --
        # not itself rounded, unlike avg_min.
        assert (n1, s1) == (3, 124)
        assert (n2, s2) == (7, 700)

        # Fixed pooling: divide the exact raw-seconds sums once, at the end.
        cur.execute(
            "SELECT ROUND((SUM(sum_delay_sec)::numeric / SUM(samples) / 60.0), 2) "
            "FROM agg_daily_trend WHERE agency_id = %s AND route_code = 'SDW1'",
            (agency_id,),
        )
        fixed_avg = float(cur.fetchone()[0])

        # The bug this migration fixes: re-weighting each row's own
        # already-rounded avg_min instead of the raw sum.
        cur.execute(
            "SELECT ROUND((SUM(avg_min * samples) / SUM(samples))::numeric, 2) "
            "FROM agg_daily_trend WHERE agency_id = %s AND route_code = 'SDW1'",
            (agency_id,),
        )
        buggy_avg = float(cur.fetchone()[0])

        # From-scratch cross-check directly over the raw per-observation data
        # (mirrors the slow/live path, which averages raw seconds with no
        # intermediate rounding) -- the fixed figure must match this exactly.
        cur.execute(
            "SELECT ROUND((AVG(dep_delay) / 60.0)::numeric, 2) FROM updates "
            "WHERE agency_id = %s AND route_code = 'SDW1'",
            (agency_id,),
        )
        raw_avg = float(cur.fetchone()[0])

    assert fixed_avg == 1.37
    assert buggy_avg == 1.38
    assert fixed_avg != buggy_avg
    assert fixed_avg == raw_avg


def test_analyze_sum_late_sec_is_clamped_per_observation_not_clamped_average(pg_conn, agency_id, ch_client):
    """analyze() must store the exact per-observation clamped sum
    (SUM(GREATEST(dep_delay, 0))) alongside avg_min, so a downstream reader
    computing a route's total lateness contribution never has to clamp the
    already-signed, already-rounded avg_min instead.

    5 observations at +600s (10 min) and 5 at -480s (-8 min): the day's
    signed average is (5*600 + 5*(-480)) / 10 / 60 = 1.0 min. Clamping THAT
    average (the bug this column fixes) would score the day as
    ``1.0 * 10 = 10`` late-minutes. The true per-observation clamped sum is
    ``5 * 600 = 3000`` seconds = 50 minutes -- the -480s trips contribute 0,
    never a negative offset.
    """
    with pg_conn.cursor() as cur:
        i = 0
        for dep in [600, 600, 600, 600, 600, -480, -480, -480, -480, -480]:
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    f"mix{i}.pb",
                    "2026-04-01T09:00:00",
                    f"trip_mix_{i}",
                    "平日",
                    time(9, 0),
                    "MIX1",
                    1,
                    dep,
                ),
            )
            i += 1
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT samples, avg_min, sum_delay_sec, sum_late_sec FROM agg_daily_trend "
            "WHERE agency_id = %s AND route_code = 'MIX1'",
            (agency_id,),
        )
        samples, avg_min, sum_delay_sec, sum_late_sec = cur.fetchone()
    assert samples == 10
    assert float(avg_min) == 1.0
    assert sum_delay_sec == 600
    assert sum_late_sec == 3000


def test_analyze_creates_agg_hour_daily(pg_conn, agency_id, ch_client):
    # _seed_updates schedules every row at 11:37 → all land in hour 11.
    _seed_updates(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT hour FROM agg_hour_daily WHERE agency_id = %s",
            (agency_id,),
        )
        hours = sorted(r[0] for r in cur.fetchall())
        cur.execute(
            "SELECT bool_and(samples > 0 AND avg_min IS NOT NULL) FROM agg_hour_daily WHERE agency_id = %s",
            (agency_id,),
        )
        well_formed = cur.fetchone()[0]
    assert hours == [11]  # every seeded row is at 11:37
    assert well_formed


def test_analyze_buckets_dates_in_jst(pg_conn, agency_id, ch_client):
    """`captured_at::date` must bucket on the JST civil day the API reads under,
    not UTC. A 23:30 UTC observation is 08:30 the NEXT day in JST, so it must
    land on that next date in agg_hour_daily — guards the analyze-connection TZ
    pin (the API/tests are JST; the server default is UTC)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                agency_id,
                "tz.pb",
                "2026-05-19T23:30:00+00:00",  # 08:30 JST on 2026-05-20
                "tz_trip",
                "平日",
                time(8, 30),
                "R_TZ",
                1,
                120,
            ),
        )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date FROM agg_hour_daily WHERE agency_id = %s",
            (agency_id,),
        )
        dates = [str(r[0]) for r in cur.fetchall()]
    assert dates == ["2026-05-20"]  # JST date, not the 2026-05-19 UTC date


def test_analyze_agency_isolated(pg_conn, ch_client):
    """analyze() only touches rows for its own agency_id."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency A", "http://a.example.com"),
        )
        aid_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency B", "http://b.example.com"),
        )
        aid_b = cur.fetchone()[0]
    pg_conn.commit()

    for agency_id, delay in [(aid_a, 120), (aid_b, 600)]:
        for i in range(25):
            with pg_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                    "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        agency_id,
                        f"f{i}_{agency_id}.pb",
                        f"2026-04-{(i % 25) + 1:02d}T11:37:00",
                        "平日_11時37分_系統44372",
                        "平日",
                        time(11, 37),  # TIME column after migration 0011 (was "11:37" text).
                        "44372",
                        1,
                        delay,
                    ),
                )
        pg_conn.commit()

    _analyze(aid_a, pg_conn, ch_client)
    _analyze(aid_b, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT avg_min FROM agg_route_stats WHERE agency_id = %s", (aid_a,))
        avg_a = cur.fetchone()[0]
        cur.execute("SELECT avg_min FROM agg_route_stats WHERE agency_id = %s", (aid_b,))
        avg_b = cur.fetchone()[0]

    assert round(float(avg_a), 1) == round(120 / 60, 1)
    assert round(float(avg_b), 1) == round(600 / 60, 1)


def test_analyze_purges_stale_rows(pg_conn, agency_id, ch_client):
    """A row that the current analyze SELECT would NOT produce (e.g. a
    fabricated GHOST route) must be removed from every agg_* table by the
    next analyze run. Pins the wipe-and-rewrite semantics across all the
    tables so a future drift back to plain UPSERT, or a missed table in
    the DELETE loop, is caught."""
    _seed_updates(pg_conn, agency_id)

    ghosts = (
        (
            "agg_route_stats",
            "(agency_id, route_code, service_type, avg_min, p50_min, p90_min, "
            "late_5min_plus, on_time_pct, late5_pct, samples)",
            "(%s, 'GHOST', '平日', 99.9, 99.9, 99.9, 999, 0.0, 100.0, 100)",
        ),
        (
            "agg_route_hour",
            "(agency_id, route_code, service_type, scheduled_time, avg_min, p50_min, p90_min, samples)",
            "(%s, 'GHOST', '平日', '11:30:00', 99.9, 99.9, 99.9, 100)",
        ),
        (
            "agg_route_dow",
            "(agency_id, route_code, service_type, dow, avg_min, samples)",
            "(%s, 'GHOST', '平日', 1, 99.9, 100)",
        ),
        (
            "agg_daily_trend",
            "(agency_id, date, route_code, service_type, avg_min, samples)",
            "(%s, '2099-01-01', 'GHOST', '平日', 99.9, 100)",
        ),
        (
            "agg_hour_daily",
            "(agency_id, date, hour, avg_min, samples)",
            "(%s, '2099-01-01', 11, 99.9, 100)",
        ),
        (
            "agg_stop_seq",
            "(agency_id, route_code, stop_sequence, stop_name, avg_min, samples)",
            "(%s, 'GHOST', 99, 'GHOST STOP', 99.9, 100)",
        ),
        (
            "agg_stop_daily",
            "(agency_id, stop_id, date, service_type, time_band, delay_sum, samples)",
            "(%s, 'GHOST_STOP', '2099-01-01', '平日', 'morning', 999, 100)",
        ),
        (
            "agg_stop_routes",
            "(agency_id, stop_id, route_codes)",
            "(%s, 'GHOST_STOP', 'GHOST_ROUTE')",
        ),
    )
    with pg_conn.cursor() as cur:
        for table, cols, values in ghosts:
            cur.execute(f"INSERT INTO {table} {cols} VALUES {values}", (agency_id,))
        pg_conn.commit()

    _analyze(agency_id, pg_conn, ch_client)

    # Tables keyed by route_code use GHOST; stop tables use GHOST_STOP;
    # agg_hour_daily has neither, so its ghost is the 2099 date.
    _stop_ghost_tables = {"agg_stop_daily", "agg_stop_routes"}
    _date_ghost_tables = {"agg_hour_daily"}
    with pg_conn.cursor() as cur:
        for table, _cols, _values in ghosts:
            if table in _stop_ghost_tables:
                pred = "stop_id = 'GHOST_STOP'"
            elif table in _date_ghost_tables:
                pred = "date = '2099-01-01'"
            else:
                pred = "route_code = 'GHOST'"
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE agency_id = %s AND {pred}", (agency_id,))
            ghost_count = cur.fetchone()[0]
            assert ghost_count == 0, f"stale GHOST row survived analyze in {table}"

        # Sanity: the real route_code from _seed_updates is still present.
        cur.execute(
            "SELECT COUNT(*) FROM agg_route_stats WHERE agency_id = %s AND route_code = '44372'",
            (agency_id,),
        )
        assert cur.fetchone()[0] > 0, "real route 44372 missing after analyze"


def _seed_route_group(pg_conn, agency_id, route_code, service_type, n=25):
    """Insert *n* deduped-distinct observations for one (route, service_type).

    Varies trip_id + day so each row survives the dedup DISTINCT ON. Pass
    ``service_type=None`` to simulate the rows that miss the static_join and
    land with a NULL service_type (the agency-9 case).
    """
    with pg_conn.cursor() as cur:
        for i in range(n):
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    f"{route_code}_{service_type}_{i}.pb",
                    f"2026-04-{(i % 25) + 1:02d}T08:10:00",
                    f"trip_{route_code}_{service_type}_{i}",
                    service_type,
                    time(8, 10),
                    route_code,
                    1,
                    60 + i * 5,
                ),
            )
    pg_conn.commit()


def test_analyze_skips_null_service_type_without_crashing(pg_conn, agency_id, ch_client):
    """Rows with a NULL service_type (failed static_join) must not abort analyze.

    Regression for the agency-9 case: a NULL service_type group violated the
    NOT NULL constraint on agg_route_stats.service_type and rolled back the
    whole run, leaving aggregates stale. analyze must drop those rows and
    materialise the rest.
    """
    _seed_route_group(pg_conn, agency_id, "R1", "平日")
    _seed_route_group(pg_conn, agency_id, "R1", None)

    _analyze(agency_id, pg_conn, ch_client)  # must not raise NotNullViolation

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT service_type FROM agg_route_stats WHERE agency_id = %s",
            (agency_id,),
        )
        service_types = [r[0] for r in cur.fetchall()]
    assert service_types, "expected at least the non-null service_type group"
    assert all(st is not None for st in service_types), "NULL service_type leaked into agg_route_stats"
    assert "平日" in service_types


def _seed_for_stop_agg(pg_conn, agency_id):
    from datetime import time

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, geom) "
            "VALUES (%s,'s1','駅前',ST_SetSRID(ST_MakePoint(140.74,40.82),4326))",
            (agency_id,),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES (%s,'T',1,'s1')",
            (agency_id,),
        )
        for i, delay in enumerate([60, 120, 180]):
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES (%s,%s,'2026-06-09T08:10:00','T','平日',%s,'R1',1,%s)",
                (agency_id, f"f{i}.pb", time(8, 10), delay),
            )
    pg_conn.commit()


def test_analyze_builds_agg_stop_daily(pg_conn, agency_id, ch_client):

    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_id, service_type, time_band, delay_sum, samples FROM agg_stop_daily WHERE agency_id=%s",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    stop_id, svc, band, delay_sum, samples = rows[0]
    assert (stop_id, svc, band) == ("s1", "平日", "morning")
    # The 3 rows are repeated polls of ONE trip-stop event (same dedup key) → they
    # collapse to a single observation carrying the latest estimate (180s).
    assert delay_sum == 180 and samples == 1


def test_analyze_builds_agg_stop_routes(pg_conn, agency_id, ch_client):

    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_codes FROM agg_stop_routes WHERE agency_id=%s AND stop_id='s1'", (agency_id,))
        assert cur.fetchone()[0] == "R1"


def test_analyze_builds_agg_stop_routes_comma_joins_multiple_routes(pg_conn, agency_id, ch_client):
    """A stop served by 2+ distinct routes must comma-join them, alphabetically
    ordered — guards the ClickHouse-sourced _analyze_raw_keys JOIN path, not
    just the single-route case test_analyze_builds_agg_stop_routes already
    covers."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, geom) "
            "VALUES (%s,'s2','二番停留所',ST_SetSRID(ST_MakePoint(140.75,40.83),4326))",
            (agency_id,),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES "
            "(%s,'TA',1,'s2'),(%s,'TB',1,'s2')",
            (agency_id, agency_id),
        )
        for trip, route, delay in [("TA", "R_A", 60), ("TB", "R_B", 90)]:
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES (%s,%s,'2026-06-09T08:10:00',%s,'平日',%s,%s,1,%s)",
                (agency_id, f"{trip}.pb", trip, time(8, 10), route, delay),
            )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_codes FROM agg_stop_routes WHERE agency_id=%s AND stop_id='s2'", (agency_id,))
        route_codes = cur.fetchone()[0]
    assert route_codes == "R_A,R_B"  # comma-joined, alphabetically ordered


def test_analyze_agg_stop_routes_keeps_route_with_only_null_delay_observations(pg_conn, agency_id, ch_client):
    """A (route_code, trip_id, stop_sequence) whose every observed dep_delay
    is NULL (arrival-only StopTimeUpdates — common at a route's last stop in
    GTFS-RT, or a degraded poll) must still show up in agg_stop_routes: this
    table is about which routes serve a stop, independent of whether any
    observation happened to carry a numeric delay.

    Regression: agg_stop_routes was derived from _analyze_deduped for one
    perf-motivated commit, which pre-filters `dep_delay IS NOT NULL` — that
    silently dropped this exact case (a real, non-trivial share of keys,
    enough to lose stops' entire route coverage in practice). Restored to
    the ClickHouse-sourced
    _analyze_raw_keys path, which reads route_code/trip_id/stop_sequence
    only and never touches dep_delay."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, geom) "
            "VALUES (%s,'s3','三番停留所',ST_SetSRID(ST_MakePoint(140.75,40.83),4326))",
            (agency_id,),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES (%s,'TC',1,'s3')",
            (agency_id,),
        )
        cur.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES (%s,'TC.pb','2026-06-09T08:10:00','TC','平日',%s,'R_NULL_DELAY',1,NULL)",
            (agency_id, time(8, 10)),
        )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_codes FROM agg_stop_routes WHERE agency_id=%s AND stop_id='s3'", (agency_id,))
        row = cur.fetchone()
    assert row is not None, "stop s3 lost all route coverage — the NULL-delay row was dropped"
    assert row[0] == "R_NULL_DELAY"


def test_agg_stop_daily_keeps_null_service_type_as_sentinel(pg_conn, agency_id, ch_client):
    """NULL service_type rows (agency-9 case) must not abort the agg build,
    and — matching agg_route_stop_daily's '' sentinel treatment — must not be
    silently dropped either: a stop whose traffic is entirely NULL-service
    would otherwise read as zero activity on the default (no route filter)
    heatmap while still showing up on the route-filtered view."""
    from datetime import time

    _seed_for_stop_agg(pg_conn, agency_id)  # 3 valid rows, stop s1, service 平日
    with pg_conn.cursor() as cur:
        # a NULL-service_type observation for the same stop/trip
        cur.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES (%s,'fnull.pb','2026-06-09T08:10:00','T',NULL,%s,'R1',1,240)",
            (agency_id, time(8, 10)),
        )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)  # must NOT raise NotNullViolation
    with pg_conn.cursor() as cur:
        cur.execute("SELECT service_type, samples FROM agg_stop_daily WHERE agency_id=%s", (agency_id,))
        rows = cur.fetchall()
    by_svc = dict(rows)
    assert by_svc["平日"] == 1  # 3 polls of one event dedup to latest
    assert by_svc[""] == 1  # NULL service KEPT as '' sentinel, not dropped


def test_analyze_builds_agg_route_stop_daily(pg_conn, agency_id, ch_client):
    """Route-stop aggregate keeps route_code in the key and, unlike agg_stop_daily,
    KEEPS NULL service_type as '' sentinel (parity with the live route heatmap)."""
    from datetime import time

    _seed_for_stop_agg(pg_conn, agency_id)  # R1, service 平日, 3 rows on s1 (delay 60/120/180)
    with pg_conn.cursor() as cur:
        # a NULL-service observation for the same stop/route/band
        cur.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES (%s,'fnull.pb','2026-06-09T08:10:00','T',NULL,%s,'R1',1,240)",
            (agency_id, time(8, 10)),
        )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_code, service_type, time_band, delay_sum, samples "
            "FROM agg_route_stop_daily WHERE agency_id=%s ORDER BY service_type",
            (agency_id,),
        )
        rows = cur.fetchall()
    by_svc = {svc: (delay_sum, samples) for _, svc, _, delay_sum, samples in rows}
    assert all(rc == "R1" for rc, *_ in rows)
    assert by_svc["平日"] == (180, 1)  # 3 polls of one event dedup to latest (180s)
    assert by_svc[""] == (240, 1)  # NULL service KEPT as '' sentinel, not dropped


def test_heatmap_aggs_clamp_implausible_delays(pg_conn, agency_id, ch_client):
    """A frozen-feed spike (|delay| > MAX_PLAUSIBLE_DELAY_SEC) is excluded from
    BOTH heatmap aggregates, so it can't hijack the per-stop mean. Regression for
    the 2026-06-07 馬木料金所前 72-min false reading."""
    from datetime import time

    from pipeline.analyze import MAX_PLAUSIBLE_DELAY_SEC

    _seed_for_stop_agg(pg_conn, agency_id)  # 3 plausible rows (60/120/180s), stop s1, route R1, 平日
    with pg_conn.cursor() as cur:
        # an implausible spike well over the ceiling (e.g. a stuck 16h feed value)
        cur.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES (%s,'fspike.pb','2026-06-09T08:10:00','T','平日',%s,'R1',1,%s)",
            (agency_id, time(8, 10), MAX_PLAUSIBLE_DELAY_SEC + 1),
        )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT delay_sum, samples FROM agg_stop_daily WHERE agency_id=%s", (agency_id,))
        # spike clamped out; the 3 valid polls dedup to one observation (latest 180s)
        assert cur.fetchone() == (180, 1)
        cur.execute(
            "SELECT delay_sum, samples FROM agg_route_stop_daily WHERE agency_id=%s AND service_type='平日'",
            (agency_id,),
        )
        assert cur.fetchone() == (180, 1)  # spike excluded + deduped here too


def test_analyze_builds_agg_feed_health(pg_conn, agency_id, ch_client):
    """agg_feed_health persists per-day raw vs implausible-delay counts as a
    data-quality signal (agency-wide; does not require static data)."""
    from pipeline.analyze import MAX_PLAUSIBLE_DELAY_SEC

    seed = [
        ("2026-06-09T08:10:00", 120),  # normal
        ("2026-06-09T08:11:00", 180),  # normal
        ("2026-06-09T08:12:00", MAX_PLAUSIBLE_DELAY_SEC + 1),  # implausible spike
        ("2026-06-10T08:10:00", 90),  # normal, different day
    ]
    with pg_conn.cursor() as cur:
        for i, (ts, d) in enumerate(seed):
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES (%s,%s,%s,%s,'平日',%s,'R1',1,%s)",
                (agency_id, f"f{i}.pb", ts, f"T{i}", time(8, 10), d),
            )
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, raw_samples, clamp_count FROM agg_feed_health WHERE agency_id=%s ORDER BY date",
            (agency_id,),
        )
        by_date = {str(d): (raw, clamp) for d, raw, clamp in cur.fetchall()}
    assert by_date["2026-06-09"] == (3, 1)  # 3 raw observations, 1 implausible
    assert by_date["2026-06-10"] == (1, 0)
