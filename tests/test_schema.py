import psycopg2
import pytest

EXPECTED_TABLES = [
    "agencies", "updates",
    "static_stops", "static_stop_times", "static_trips",
    "static_routes", "static_calendar_dates",
    "agg_route_stats", "agg_route_hour", "agg_route_dow",
    "agg_daily_trend", "agg_stop_seq",
    "rag_chunks",
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
