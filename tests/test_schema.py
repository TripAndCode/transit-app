EXPECTED_TABLES = [
    "agencies",
    "updates",
    "static_stops",
    "static_stop_times",
    "static_trips",
    "static_routes",
    "static_calendar_dates",
    "agg_route_stats",
    "agg_route_hour",
    "agg_route_dow",
    "agg_daily_trend",
    "agg_stop_seq",
    "rag_chunks",
    "api_keys",
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
    assert {"key", "owner_email", "tier", "created_at"} <= cols


def test_static_shapes_table_exists(pg_conn):
    """0005 migration should create static_shapes with a GIST index."""
    cur = pg_conn.cursor()
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
