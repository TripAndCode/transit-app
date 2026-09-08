from datetime import datetime, time, timezone

import pytest

from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
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


def test_analyze_hour_daily_sum_delay_sec_is_exact_raw_sum(pg_conn, agency_id, ch_client):
    """agg_hour_daily must carry the exact SUM(dep_delay) alongside its
    rounded avg_min, mirroring the six tables migration 0028 already covers
    (see pipeline/analyze.py's module docstring) -- a downstream reader
    pooling multiple agg_hour_daily rows (pipeline/reports/overview.py's
    _peak_hour_by_dow fast path) divides SUM(sum_delay_sec) / SUM(samples)
    once, rather than re-weighting the already-rounded avg_min.

    3 observations at +601s and 3 at +599s: the rounded avg_min is exactly
    10.0 either way, but the raw seconds sum (3600) is what a correct
    downstream pool must reproduce -- an implementation that instead backs
    sum_delay_sec out of the rounded avg_min*60*samples would also land on
    3600 here, so this asserts the exact raw total directly rather than
    relying on a coincidental match.
    """
    with pg_conn.cursor() as cur:
        i = 0
        for dep in [601, 601, 601, 599, 599, 599]:
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    f"hd{i}.pb",
                    "2026-04-01T09:00:00",
                    f"trip_hd_{i}",
                    "平日",
                    time(9, 0),
                    "HD1",
                    1,
                    dep,
                ),
            )
            i += 1
    pg_conn.commit()
    _analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT samples, avg_min, sum_delay_sec FROM agg_hour_daily "
            "WHERE agency_id = %s AND date = '2026-04-01' AND hour = 9",
            (agency_id,),
        )
        samples, avg_min, sum_delay_sec = cur.fetchone()
    assert samples == 6
    assert float(avg_min) == 10.0
    assert sum_delay_sec == 3600


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


def _set_ingest_strategy(pg_conn, agency_id, strategy):
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE agencies SET ingest_strategy = %s WHERE agency_id = %s", (strategy, agency_id))
    pg_conn.commit()


def _ch_service_delivered_row(
    trip_id,
    captured_at,
    *,
    file_name="f.pb",
    stop_sequence=1,
    schedule_relationship_trip=None,
    schedule_relationship_stop=None,
):
    """One ClickHouse `updates` row shaped for `pipeline.clickhouse.insert_updates`
    (agency_id excluded), with only the fields this builder reads populated."""
    return (
        file_name,
        captured_at,
        trip_id,
        "平日",
        "11:00:00",
        "R1",
        stop_sequence,
        60,
        None,  # stop_id
        None,  # arr_delay
        schedule_relationship_trip,
        schedule_relationship_stop,
        None,  # feed_timestamp
    )


def test_analyze_builds_agg_service_delivered_daily_for_static_join_agency(pg_conn, agency_id, ch_client):
    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    day1 = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)  # 2026-04-01 11:00 JST
    day2 = datetime(2026, 4, 2, 2, 0, tzinfo=timezone.utc)
    rows = [
        _ch_service_delivered_row("T1", day1, file_name="t1.pb", schedule_relationship_trip=0),
        _ch_service_delivered_row("T2", day1, file_name="t2.pb", schedule_relationship_trip=3),  # CANCELED
        _ch_service_delivered_row(
            "T3", day1, file_name="t3.pb", schedule_relationship_trip=0, schedule_relationship_stop=1
        ),  # stop SKIPPED
        _ch_service_delivered_row("T4", day2, file_name="t4.pb", schedule_relationship_trip=0),  # normal
    ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, non_executed_trips FROM agg_service_delivered_daily WHERE agency_id = %s ORDER BY date",
            (agency_id,),
        )
        by_date = {str(d): n for d, n in cur.fetchall()}
    # T2 (CANCELED) + T3 (stop SKIPPED) = 2 on day1; day2's T4 has no
    # cancellation/skip -> the source query emits no row for that day at all.
    assert by_date == {"2026-04-01": 2}


def test_analyze_skips_agg_service_delivered_daily_for_non_static_join_agency(pg_conn, agency_id, ch_client):
    """An agency whose ingest_strategy isn't static_join must get zero rows in
    agg_service_delivered_daily regardless of what schedule_relationship_*
    values happen to be present in `updates` -- the read path's "not
    available" determination keys off ingest_strategy, and this builder must
    not waste a ClickHouse scan on an agency that will never read as
    populated anyway."""
    day1 = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    insert_updates(
        ch_client, agency_id, [_ch_service_delivered_row("T1", day1, file_name="t1.pb", schedule_relationship_trip=3)]
    )
    analyze(agency_id, pg_conn, ch_client)  # agency_id fixture leaves ingest_strategy NULL

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agg_service_delivered_daily WHERE agency_id = %s", (agency_id,))
        count = cur.fetchone()[0]
    assert count == 0


def test_analyze_service_delivered_daily_latest_stop_observation_wins_over_stale_skip(pg_conn, agency_id, ch_client):
    """Regression: a stop flagged SKIPPED on an early poll, corrected back to
    normal on a later poll (higher captured_at), must not count as
    non-executed -- the stop-level subquery's raw WHERE must not filter
    `schedule_relationship_stop IS NOT NULL` before argMax runs, or the
    corrective NULL is dropped before the aggregate ever sees it and the
    stale SKIPPED=1 wins."""
    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    early = datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    rows = [
        # T1: early poll SKIPPED, later poll corrects it back to normal.
        _ch_service_delivered_row(
            "T1", early, file_name="a.pb", schedule_relationship_trip=0, schedule_relationship_stop=1
        ),
        _ch_service_delivered_row(
            "T1", later, file_name="b.pb", schedule_relationship_trip=0, schedule_relationship_stop=None
        ),
        # T2: genuinely SKIPPED, no later correction -- must count.
        _ch_service_delivered_row(
            "T2", early, file_name="c.pb", schedule_relationship_trip=0, schedule_relationship_stop=1
        ),
    ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT non_executed_trips FROM agg_service_delivered_daily WHERE agency_id = %s AND date = '2026-04-01'",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1  # only T2; T1's correction must not resurrect the stale SKIPPED flag


def _ch_dwell_run_row(
    trip_id,
    captured_at,
    stop_sequence,
    dep_delay,
    arr_delay,
    *,
    file_name="f.pb",
    route_code="R1",
    service_type="平日",
):
    """One ClickHouse `updates` row shaped for `pipeline.clickhouse.insert_updates`
    (agency_id excluded), populating just the fields
    `agg_route_daily_dwell_run` reads (dep_delay always; arr_delay per the
    per-row sparsity `pipeline.dwell_run`'s module docstring describes)."""
    return (
        file_name,
        captured_at,
        trip_id,
        service_type,
        "10:00:00",  # scheduled_time -- unused by this builder, must still be a valid time string
        route_code,
        stop_sequence,
        dep_delay,
        "S1",  # stop_id
        arr_delay,
        None,  # schedule_relationship_trip
        None,  # schedule_relationship_stop
        None,  # feed_timestamp
    )


def _seed_dwell_run_schedule(pg_conn, agency_id, trip_id):
    """Static schedule for a 3-stop trip, matching
    tests/unit/test_dwell_run.py's hand-computed fixture: stop 2 has a 60s
    scheduled dwell (10:05:00 arrival / 10:06:00 departure); stops 1 and 3
    have zero scheduled dwell (arrival_time == departure_time)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, 'S1', 'Test Stop')",
            (agency_id,),
        )
        for seq, arr, dep in [
            (1, "10:00:00", "10:00:00"),
            (2, "10:05:00", "10:06:00"),
            (3, "10:10:00", "10:10:00"),
        ]:
            cur.execute(
                "INSERT INTO static_stop_times "
                "(agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
                "VALUES (%s, %s, %s, 'S1', %s, %s)",
                (agency_id, trip_id, seq, arr, dep),
            )
    pg_conn.commit()


def test_analyze_builds_agg_route_daily_dwell_run_for_static_join_agency(pg_conn, agency_id, ch_client):
    """Known synthetic arrival/departure timestamps (mirroring
    tests/unit/test_dwell_run.py's hand-computed fixture) produce the
    expected dwell/running split once run through analyze()'s SQL builder.

    Stop 1 has no `arr_delay` (no arrival ping) -> contributes neither
    dwell nor running. Stop 2: actual arrival 36320s, actual departure
    36410s -> dwell 90s; running (vs. stop 1's actual departure 36030s) 290s.
    Stop 3: actual arrival == actual departure == 36610s -> dwell 0s;
    running (vs. stop 2's actual departure 36410s) 200s.
    """
    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    _seed_dwell_run_schedule(pg_conn, agency_id, "T1")
    day = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)  # 2026-04-01 11:00 JST
    rows = [
        _ch_dwell_run_row("T1", day, 1, 30, None),
        _ch_dwell_run_row("T1", day, 2, 50, 20),
        _ch_dwell_run_row("T1", day, 3, 10, 10),
    ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT dwell_samples, dwell_sum_sec, run_samples, run_sum_sec, "
            "array_length(hist_dwell, 1), array_length(hist_run, 1) "
            "FROM agg_route_daily_dwell_run WHERE agency_id = %s AND route_code = 'R1'",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row is not None
    dwell_samples, dwell_sum_sec, run_samples, run_sum_sec, hist_dwell_len, hist_run_len = row
    assert dwell_samples == 2
    assert dwell_sum_sec == 90
    assert run_samples == 2
    assert run_sum_sec == 490
    assert hist_dwell_len == 26
    assert hist_run_len == 32


def test_analyze_skips_agg_route_daily_dwell_run_for_non_static_join_agency(pg_conn, agency_id, ch_client):
    """An agency whose ingest_strategy isn't static_join must get zero rows
    in agg_route_daily_dwell_run regardless of static schedule -- the read
    path's "not available" determination keys off ingest_strategy, not row
    presence (same convention as agg_service_delivered_daily)."""
    _seed_dwell_run_schedule(pg_conn, agency_id, "T1")
    day = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    rows = [
        _ch_dwell_run_row("T1", day, 1, 30, None),
        _ch_dwell_run_row("T1", day, 2, 50, 20),
    ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)  # agency_id fixture leaves ingest_strategy NULL

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agg_route_daily_dwell_run WHERE agency_id = %s", (agency_id,))
        count = cur.fetchone()[0]
    assert count == 0


def test_analyze_skips_agg_route_daily_dwell_run_without_static_schedule(pg_conn, agency_id, ch_client):
    """static_join alone isn't enough -- without a static schedule loaded
    (no static_stops rows), there's no arrival_time/departure_time to derive
    an actual timestamp from, so this builder must also produce zero rows
    rather than a schedule-less (and therefore meaningless) computation."""
    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    day = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    rows = [_ch_dwell_run_row("T1", day, 1, 30, 20)]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agg_route_daily_dwell_run WHERE agency_id = %s", (agency_id,))
        count = cur.fetchone()[0]
    assert count == 0


def test_analyze_dwell_run_latest_poll_wins_when_arr_delay_goes_null(pg_conn, agency_id, ch_client):
    """Regression: a stop event whose LATEST poll has `arr_delay = NULL`
    (feed stopped sending an arrival estimate for that stop) must be treated
    as unavailable for that stop-visit, not silently resurrect an EARLIER
    poll's non-NULL `arr_delay` -- ClickHouse's `argMax(arg, val)` silently
    SKIPS a row whose `arg` is NULL when picking the row with the maximal
    `val`, so a naive `argMax(u.arr_delay, (captured_at, file_name))` would
    return the earlier, stale value instead of NULL. `build_dedup_ch_sql`
    guards against this with a `coalesce`/`NULLIF` sentinel wrap (mirroring
    the same fix already applied to `schedule_relationship_trip`/
    `schedule_relationship_stop`)."""
    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    _seed_dwell_run_schedule(pg_conn, agency_id, "T1")
    early = datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc)
    later = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    rows = [
        _ch_dwell_run_row("T1", later, 1, 30, None, file_name="s1.pb"),
        # Stop 2: early poll has a real arr_delay; the LATEST poll (higher
        # captured_at) has none -- the true latest-observation-wins answer
        # for this stop-visit is "no arrival estimate", not the early value.
        _ch_dwell_run_row("T1", early, 2, 50, 20, file_name="s2a.pb"),
        _ch_dwell_run_row("T1", later, 2, 50, None, file_name="s2b.pb"),
        _ch_dwell_run_row("T1", later, 3, 10, 10, file_name="s3.pb"),
    ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT dwell_samples, dwell_sum_sec, run_samples, run_sum_sec "
            "FROM agg_route_daily_dwell_run WHERE agency_id = %s AND route_code = 'R1'",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row is not None
    dwell_samples, dwell_sum_sec, run_samples, run_sum_sec = row
    # Only stop 3 (dwell 0, running 200) contributes -- stop 2's dwell/running
    # must NOT be computed from the stale resurrected arr_delay=20 (which
    # would wrongly produce dwell_samples=2/dwell_sum_sec=90/run_samples=2/
    # run_sum_sec=490, identical to the single-poll-per-stop happy path).
    assert dwell_samples == 1
    assert dwell_sum_sec == 0
    assert run_samples == 1
    assert run_sum_sec == 200


def test_analyze_dwell_run_missing_schedule_at_interior_stop_does_not_inflate_next_running(
    pg_conn, agency_id, ch_client
):
    """Regression: a stop lacking a static schedule row (arrival_time/
    departure_time optional for non-timepoint intermediate stops in real
    GTFS feeds) must yield None for its own dwell/running AND make the
    FOLLOWING stop's running time None too -- not silently pair the
    following stop with the departure from two segments back. The SQL
    builder's `visits` CTE must LEFT JOIN `static_stop_times` (not INNER
    JOIN) and must not filter a NULL-schedule row out of `actuals` before
    the `LAG()` window runs, or `LAG()` silently skips past the missing stop
    and pairs the next stop with the wrong previous departure."""
    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, 'S1', 'Test Stop')",
            (agency_id,),
        )
        # Deliberately NO row for stop_sequence 2 -- only stops 1 and 3 have
        # a static schedule, mirroring an interior stop with no
        # arrival_time/departure_time in the source GTFS feed.
        for seq, arr, dep in [(1, "10:00:00", "10:00:00"), (3, "10:10:00", "10:10:00")]:
            cur.execute(
                "INSERT INTO static_stop_times "
                "(agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
                "VALUES (%s, 'T1', %s, 'S1', %s, %s)",
                (agency_id, seq, arr, dep),
            )
    pg_conn.commit()
    day = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    rows = [
        _ch_dwell_run_row("T1", day, 1, 30, None, file_name="s1.pb"),
        # Stop 2 has real RT data but no static schedule row above.
        _ch_dwell_run_row("T1", day, 2, 50, 20, file_name="s2.pb"),
        _ch_dwell_run_row("T1", day, 3, 10, 10, file_name="s3.pb"),
    ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT dwell_samples, dwell_sum_sec, run_samples, run_sum_sec "
            "FROM agg_route_daily_dwell_run WHERE agency_id = %s AND route_code = 'R1'",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row is not None
    dwell_samples, dwell_sum_sec, run_samples, run_sum_sec = row
    # Stop 3's own dwell (0) is unaffected -- it doesn't depend on stop 2.
    # Stop 3's running time DOES depend on stop 2's actual departure, which
    # is None (no schedule) -- run_samples must be 0, not 1 (which the bug
    # would produce by pairing stop 3 with stop 1's departure instead:
    # actual_arr_sec(3) - actual_dep_sec(1) = 36610 - 36030 = 580).
    assert dwell_samples == 1
    assert dwell_sum_sec == 0
    assert run_samples == 0
    assert run_sum_sec == 0


def _seed_static_version(pg_conn, agency_id, version, trip_shape_pairs):
    """Load one static-feed "version" fixture: `static_trips` rows (all
    stamped `static_version_id=version`, mirroring `static_loader.
    load_static()`'s one-value-per-load convention) from
    `trip_shape_pairs` -- a list of `(trip_id, shape_id_or_None)`.

    Also requires `static_stops` to have a row (has_static's gate) -- callers
    that already seeded one elsewhere don't need to call this twice.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, 'S1', 'Test Stop') "
            "ON CONFLICT DO NOTHING",
            (agency_id,),
        )
        for trip_id, shape_id in trip_shape_pairs:
            cur.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id, static_version_id) "
                "VALUES (%s, %s, 'R1', %s, %s)",
                (agency_id, trip_id, shape_id, version),
            )
    pg_conn.commit()


def _seed_static_shape(pg_conn, agency_id, shape_id, points):
    """points: list of (lon, lat) in sequence order."""
    placeholders = ",".join("ST_MakePoint(%s, %s)" for _ in points)
    flat = [v for lon, lat in points for v in (lon, lat)]
    with pg_conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO static_shapes (agency_id, shape_id, geom) "
            f"VALUES (%s, %s, ST_SetSRID(ST_MakeLine(ARRAY[{placeholders}]), 4326))",
            [agency_id, shape_id, *flat],
        )
    pg_conn.commit()


def test_analyze_builds_agg_static_version_summary_with_trip_count_and_vehicle_km(pg_conn, agency_id, ch_client):
    """trip_count counts every static_trips row regardless of shape
    presence; vehicle_km sums only the trips whose shape_id resolves in
    static_shapes (T3 has none -- excluded from the sum, not aborting it)."""
    _seed_static_shape(pg_conn, agency_id, "SH1", [(139.0, 35.0), (139.0, 35.01)])
    _seed_static_version(pg_conn, agency_id, "v1", [("T1", "SH1"), ("T2", "SH1"), ("T3", None)])
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT trip_count, vehicle_km FROM agg_static_version_summary "
            "WHERE agency_id = %s AND static_version_id = 'v1'",
            (agency_id,),
        )
        row = cur.fetchone()
        cur.execute(
            "SELECT ST_Length(ST_SetSRID(ST_MakeLine(ST_MakePoint(139.0, 35.0), "
            "ST_MakePoint(139.0, 35.01)), 4326)::geography) / 1000.0"
        )
        (one_shape_km,) = cur.fetchone()
    assert row is not None
    trip_count, vehicle_km = row
    assert trip_count == 3
    assert vehicle_km == pytest.approx(one_shape_km * 2, rel=1e-9)


def test_analyze_static_version_summary_vehicle_km_null_without_any_shapes(pg_conn, agency_id, ch_client):
    """No shapes.txt loaded at all -> vehicle_km reads NULL (not 0), so a
    reader can distinguish "not computable" from "zero planned distance"."""
    _seed_static_version(pg_conn, agency_id, "v1", [("T1", None)])
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT trip_count, vehicle_km FROM agg_static_version_summary WHERE agency_id = %s",
            (agency_id,),
        )
        trip_count, vehicle_km = cur.fetchone()
    assert trip_count == 1
    assert vehicle_km is None


def test_analyze_static_version_summary_preserves_history_across_reload(pg_conn, agency_id, ch_client):
    """A static reload (static_loader.load_static() DELETEs + replaces
    static_trips for the agency) must not erase the PRIOR version's row
    here -- this table is UPSERT-only, exempt from the wipe-and-rewrite loop
    every other agg_* table follows, specifically so a schedule-revision
    boundary can be explained by a real before/after change in planned
    trips. Two different static GTFS versions with a known change in total
    scheduled trips (2 -> 3) must produce two different, both-still-present
    planned-trip-count rows."""
    _seed_static_version(pg_conn, agency_id, "v1", [("T1", None), ("T2", None)])
    analyze(agency_id, pg_conn, ch_client)

    # Simulate static_loader.load_static() reloading a new version: wipe and
    # replace static_trips for this agency, same as its own DELETE-then-INSERT.
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM static_trips WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    _seed_static_version(pg_conn, agency_id, "v2", [("T3", None), ("T4", None), ("T5", None)])
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT static_version_id, trip_count FROM agg_static_version_summary "
            "WHERE agency_id = %s ORDER BY static_version_id",
            (agency_id,),
        )
        rows = {v: n for v, n in cur.fetchall()}
    assert rows == {"v1": 2, "v2": 3}


def _ch_schedule_revision_row(trip_id, captured_at, static_version_id, *, file_name):
    """One ClickHouse `updates` row stamped with a given static_version_id
    (agency_id excluded, shaped for `pipeline.clickhouse.insert_updates`) --
    everything else is a plausible-but-unused filler."""
    return (
        file_name,
        captured_at,
        trip_id,
        "平日",
        "11:00:00",
        "R1",
        1,
        60,
        None,  # stop_id
        None,  # arr_delay
        None,  # schedule_relationship_trip
        None,  # schedule_relationship_stop
        None,  # feed_timestamp
        None,  # scheduled_sec
        static_version_id,
    )


def test_analyze_builds_agg_schedule_revision_daily_dominant_version_per_day(pg_conn, agency_id, ch_client):
    """Per-day dominant static_version_id is the version stamped on the MOST
    rows that day (mode), not the latest single observation -- day2 mixes 1
    'v1' row with 2 'v2' rows (simulating a mid-day reload), so 'v2' wins."""
    day1 = datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc)  # 2026-05-01 11:00 JST
    day2 = datetime(2026, 5, 2, 2, 0, tzinfo=timezone.utc)
    rows = [
        _ch_schedule_revision_row("T1", day1, "v1", file_name="d1t1.pb"),
        _ch_schedule_revision_row("T2", day2, "v1", file_name="d2t1.pb"),
        _ch_schedule_revision_row("T3", day2, "v2", file_name="d2t2.pb"),
        _ch_schedule_revision_row("T4", day2, "v2", file_name="d2t3.pb"),
    ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, static_version_id FROM agg_schedule_revision_daily WHERE agency_id = %s ORDER BY date",
            (agency_id,),
        )
        by_date = {str(d): v for d, v in cur.fetchall()}
    assert by_date == {"2026-05-01": "v1", "2026-05-02": "v2"}


def test_analyze_skips_agg_schedule_revision_daily_when_static_version_id_always_null(pg_conn, agency_id, ch_client):
    """An ingest strategy that never sets static_version_id (e.g.
    aomori_regex) -- or any day predating item 88's rollout -- must get NO
    row here, never a NULL-version row a boundary could be misdrawn against."""
    day1 = datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc)
    rows = [_ch_schedule_revision_row("T1", day1, None, file_name="d1t1.pb")]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agg_schedule_revision_daily WHERE agency_id = %s", (agency_id,))
        count = cur.fetchone()[0]
    assert count == 0
