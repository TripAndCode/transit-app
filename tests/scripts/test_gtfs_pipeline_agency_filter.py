"""Verify the gtfs_pipeline all-agencies loops skip soft-deleted rows.

Executes ``gtfs_pipeline.ACTIVE_AGENCY_IDS_SQL`` — the actual constant shared by
``cmd_analyze_all``, ``cmd_check_aggs``, and ``cmd_ingest_live`` — rather than a
hand-typed copy of the query, so reverting the real filter fails this test
instead of leaving it green. Lives under ``tests/scripts/`` (not ``tests/unit/``)
because it needs the auto-migrating root conftest: ``tests/unit/conftest.py``
no-ops schema application for pure DB-free tests, but this connects to the
throwaway :5544 DB.
"""

import os

import psycopg2
import pytest

from gtfs_pipeline import ACTIVE_AGENCY_IDS_SQL

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


def test_active_agency_ids_sql_excludes_deleted(pg):
    """The shared query behind analyze-all / check-aggs / ingest-live must
    exclude soft-deleted agencies (and only those)."""
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

    with pg.cursor() as cur:
        cur.execute(ACTIVE_AGENCY_IDS_SQL)
        ids = [r[0] for r in cur.fetchall()]

    assert active_id in ids
    assert deleted_id not in ids
