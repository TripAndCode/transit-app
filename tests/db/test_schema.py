"""Schema sanity checks: required tables exist and key columns/constraints are wired."""

EXPECTED_TABLES = [
    "agencies",
    "updates",
    "static_stops",
    "static_stop_times",
    "static_trips",
    "static_routes",
    "static_shapes",
    "static_calendar_dates",
    "agg_route_stats",
    "agg_route_hour",
    "agg_daily_trend",
    "rag_chunks",
    "api_keys",
    "users",
    "oauth_identities",
    "sessions",
    "login_events",
    "filter_presets",
    "rt_field_coverage_probes",
]


def test_all_tables_exist(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        existing = {r[0] for r in cur.fetchall()}
    for t in EXPECTED_TABLES:
        assert t in existing, f"Missing table: {t}"


def test_updates_has_agency_id(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'updates' AND column_name = 'agency_id'
        """)
        assert cur.fetchone() is not None


def test_static_stops_has_geom(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'static_stops' AND column_name = 'geom'
        """)
        assert cur.fetchone() is not None


def test_agencies_insert(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("テスト", "http://example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()
    assert isinstance(aid, int)


def test_agencies_has_trip_id_pattern(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'agencies' AND column_name = 'trip_id_pattern'
        """)
        assert cur.fetchone() is not None, "agencies.trip_id_pattern column missing"


def test_api_keys_columns(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'api_keys'
            ORDER BY column_name
        """)
        cols = {r[0] for r in cur.fetchall()}
    assert {"key", "owner_email", "tier", "created_at", "key_hash", "revoked_at", "expires_at"} <= cols


def test_api_keys_are_keyed_by_hash(pg_conn):
    """0052 moves the primary key onto the digest and lets the raw key go NULL,
    so a row can exist that holds no replayable credential."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'api_keys'::regclass AND i.indisprimary
        """)
        assert {r[0] for r in cur.fetchall()} == {"key_hash"}
        cur.execute("""
            SELECT column_name, is_nullable FROM information_schema.columns
            WHERE table_name = 'api_keys' AND column_name IN ('key', 'key_hash')
            ORDER BY column_name
        """)
        assert cur.fetchall() == [("key", "YES"), ("key_hash", "NO")]


def test_sessions_are_keyed_by_hash(pg_conn):
    """The session id itself is only ever in the cookie after 0052."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'sessions'::regclass AND i.indisprimary
        """)
        assert {r[0] for r in cur.fetchall()} == {"sid_hash"}
        cur.execute("""
            SELECT column_name, is_nullable FROM information_schema.columns
            WHERE table_name = 'sessions' AND column_name IN ('sid', 'sid_hash')
            ORDER BY column_name
        """)
        assert cur.fetchall() == [("sid", "YES"), ("sid_hash", "NO")]


def test_static_shapes_table_exists(pg_conn):
    """0005 migration should create static_shapes with a GIST index."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'static_shapes'
            """
        )
        assert cur.fetchone() is not None, "static_shapes table missing"

        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'static_shapes'
              AND indexdef ILIKE '%USING gist%'
            """
        )
        assert cur.fetchone() is not None, "GIST index on static_shapes missing"


def test_users_role_check(pg_conn):
    """``users.role`` CHECK constraint rejects values outside ``user`` / ``admin``."""
    import psycopg2.errors

    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO users (email, role) VALUES ('a@x', 'user')")
        cur.execute("INSERT INTO users (email, role) VALUES ('b@x', 'admin')")
    pg_conn.commit()
    with pg_conn.cursor() as cur:
        try:
            cur.execute("INSERT INTO users (email, role) VALUES ('c@x', 'bogus')")
        except psycopg2.errors.CheckViolation:
            pg_conn.rollback()
        else:
            pg_conn.rollback()
            import pytest

            pytest.fail("CHECK constraint on users.role did not fire")
