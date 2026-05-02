import os

import psycopg2
import pytest

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    from db.migrate import migrate_up

    conn = psycopg2.connect(DATABASE_URL)
    migrate_up(conn)
    conn.close()


@pytest.fixture
def pg_conn(apply_schema):
    conn = psycopg2.connect(DATABASE_URL)
    yield conn
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE agencies, updates, static_stops, static_stop_times,
                static_trips, static_routes, static_calendar_dates,
                agg_route_stats, agg_route_hour, agg_route_dow,
                agg_daily_trend, agg_stop_seq, rag_chunks, api_keys, snapshots CASCADE
            """)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def agency_id(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("テスト交通", "http://example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()
    return aid


@pytest.fixture
async def aconn(apply_schema):
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    yield conn
    # clean up
    try:
        await conn.execute("""
            TRUNCATE agencies, updates, static_stops, static_stop_times,
            static_trips, static_routes, static_calendar_dates,
            agg_route_stats, agg_route_hour, agg_route_dow,
            agg_daily_trend, agg_stop_seq, rag_chunks, api_keys, snapshots CASCADE
        """)
    except Exception:
        pass
    await conn.close()


@pytest.fixture
async def aagency_id(aconn):
    row = await aconn.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "テスト交通",
        "http://example.com/feed.pb",
    )
    return row["agency_id"]
