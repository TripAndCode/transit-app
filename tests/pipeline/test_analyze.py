from datetime import time

from pipeline.analyze import analyze


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


def test_analyze_creates_agg_route_stats(pg_conn, agency_id):
    _seed_updates(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_code, service_type, avg_min FROM agg_route_stats WHERE agency_id = %s",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert len(rows) > 0
    assert rows[0][0] == "44372"
    assert rows[0][2] is not None


def test_analyze_creates_agg_route_hour(pg_conn, agency_id):
    _seed_updates(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agg_route_hour WHERE agency_id = %s",
            (agency_id,),
        )
        count = cur.fetchone()[0]
    assert count > 0


def test_analyze_creates_agg_route_dow(pg_conn, agency_id):
    _seed_updates(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT dow FROM agg_route_dow WHERE agency_id = %s",
            (agency_id,),
        )
        dows = {r[0] for r in cur.fetchall()}
    assert dows <= {1, 2, 3, 4, 5, 6, 7}
    assert len(dows) > 0


def test_analyze_creates_agg_stop_seq_with_stop_name(pg_conn, agency_id):
    _seed_updates(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_name FROM agg_stop_seq WHERE agency_id = %s LIMIT 1",
            (agency_id,),
        )
        stop_name = cur.fetchone()[0]
    assert stop_name is not None
    assert "番停留所" in stop_name


def test_analyze_agg_stop_seq_with_real_stop_name(pg_conn, agency_id):
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
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_name FROM agg_stop_seq WHERE agency_id = %s AND stop_sequence = 1",
            (agency_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "青森駅"


def test_analyze_creates_agg_daily_trend(pg_conn, agency_id):
    _seed_updates(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agg_daily_trend WHERE agency_id = %s",
            (agency_id,),
        )
        count = cur.fetchone()[0]
    assert count > 0


def test_analyze_creates_agg_hour_daily(pg_conn, agency_id):
    # _seed_updates schedules every row at 11:37 → all land in hour 11.
    _seed_updates(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
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


def test_analyze_buckets_dates_in_jst(pg_conn, agency_id):
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
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date FROM agg_hour_daily WHERE agency_id = %s",
            (agency_id,),
        )
        dates = [str(r[0]) for r in cur.fetchall()]
    assert dates == ["2026-05-20"]  # JST date, not the 2026-05-19 UTC date


def test_analyze_agency_isolated(pg_conn):
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

    analyze(aid_a, pg_conn)
    analyze(aid_b, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT avg_min FROM agg_route_stats WHERE agency_id = %s", (aid_a,))
        avg_a = cur.fetchone()[0]
        cur.execute("SELECT avg_min FROM agg_route_stats WHERE agency_id = %s", (aid_b,))
        avg_b = cur.fetchone()[0]

    assert round(float(avg_a), 1) == round(120 / 60, 1)
    assert round(float(avg_b), 1) == round(600 / 60, 1)


def test_analyze_purges_stale_rows(pg_conn, agency_id):
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

    analyze(agency_id, pg_conn)

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


def test_analyze_skips_null_service_type_without_crashing(pg_conn, agency_id):
    """Rows with a NULL service_type (failed static_join) must not abort analyze.

    Regression for the agency-9 case: a NULL service_type group violated the
    NOT NULL constraint on agg_route_stats.service_type and rolled back the
    whole run, leaving aggregates stale. analyze must drop those rows and
    materialise the rest.
    """
    _seed_route_group(pg_conn, agency_id, "R1", "平日")
    _seed_route_group(pg_conn, agency_id, "R1", None)

    analyze(agency_id, pg_conn)  # must not raise NotNullViolation

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


def test_analyze_builds_agg_stop_daily(pg_conn, agency_id):
    from pipeline.analyze import analyze

    _seed_for_stop_agg(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_id, service_type, time_band, delay_sum, samples FROM agg_stop_daily WHERE agency_id=%s",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    stop_id, svc, band, delay_sum, samples = rows[0]
    assert (stop_id, svc, band) == ("s1", "平日", "morning")
    assert delay_sum == 360 and samples == 3  # 60+120+180; raw count, not deduped


def test_analyze_builds_agg_stop_routes(pg_conn, agency_id):
    from pipeline.analyze import analyze

    _seed_for_stop_agg(pg_conn, agency_id)
    analyze(agency_id, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_codes FROM agg_stop_routes WHERE agency_id=%s AND stop_id='s1'", (agency_id,))
        assert cur.fetchone()[0] == "R1"


def test_agg_stop_daily_skips_null_service_type(pg_conn, agency_id):
    """NULL service_type rows (agency-9 case) must not abort the agg build."""
    from datetime import time

    from pipeline.analyze import analyze

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
    analyze(agency_id, pg_conn)  # must NOT raise NotNullViolation
    with pg_conn.cursor() as cur:
        cur.execute("SELECT service_type, samples FROM agg_stop_daily WHERE agency_id=%s", (agency_id,))
        rows = cur.fetchall()
    # only the non-null service_type group survives; the 3 valid rows, NOT the null one
    assert all(st is not None for st, _ in rows)
    assert sum(s for _, s in rows) == 3
