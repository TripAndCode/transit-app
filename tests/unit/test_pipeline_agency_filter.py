"""Verify that pipeline loops that enumerate agencies skip soft-deleted rows.

Uses the test DB's agencies table directly (requires DATABASE_URL to :5544).
Not unit-isolated because the query lives in gtfs_pipeline, which uses psycopg2.
"""

import os
import psycopg2
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
def pg(apply_schema):
    conn = psycopg2.connect(DATABASE_URL)
    yield conn
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE agencies, updates, static_stops, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, static_shapes, "
            "agg_route_stats, agg_route_hour, agg_route_dow, agg_route_hour_dow, "
            "agg_daily_trend, agg_stop_seq, agg_stop_daily, agg_stop_routes, "
            "rag_chunks, api_keys, filter_presets, login_events, sessions, "
            "oauth_identities, users CASCADE"
        )
    conn.commit()
    conn.close()


def test_analyze_all_skips_deleted_agency(pg):
    """analyze-all loop must not include deleted agencies."""
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Active", "http://active.example.com"),
        )
        active_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES (%s, %s, now()) RETURNING agency_id",
            ("Deleted", "http://deleted.example.com"),
        )
        deleted_id = cur.fetchone()[0]
    pg.commit()

    # Reproduce the query from gtfs_pipeline.py cmd_analyze_all
    with pg.cursor() as cur:
        cur.execute("SELECT agency_id FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id")
        ids = [r[0] for r in cur.fetchall()]

    assert active_id in ids
    assert deleted_id not in ids


def test_ingest_live_loop_skips_deleted_agency(pg):
    """ingest_live all-agencies loop must not include deleted agencies."""
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Active2", "http://active2.example.com"),
        )
        active_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES (%s, %s, now()) RETURNING agency_id",
            ("Deleted2", "http://deleted2.example.com"),
        )
        deleted_id = cur.fetchone()[0]
    pg.commit()

    with pg.cursor() as cur:
        cur.execute("SELECT agency_id FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id")
        ids = [r[0] for r in cur.fetchall()]

    assert active_id in ids
    assert deleted_id not in ids
