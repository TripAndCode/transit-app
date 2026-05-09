import os
import re

import psycopg2
import pytest


def _redirect_to_test_db() -> None:
    """Auto-redirect pytest to a sibling ``<dbname>_test`` database.

    Every test fixture in this suite TRUNCATEs the schema between tests.
    Sharing the dev DB meant every ``make test`` run nuked the operator's
    map heatmap data — a constant source of "the page is empty" bug
    reports. Redirect once at collection time so pytest never touches the
    dev DB; create the sibling on first run if it's missing.

    Opt-out: set ``DATABASE_URL`` to a name already ending in ``_test``,
    or set ``TEST_DATABASE_URL`` explicitly.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        os.environ["DATABASE_URL"] = explicit
        return

    current = os.environ.get("DATABASE_URL")
    if not current:
        return  # downstream code raises a clearer error

    m = re.search(r"/([^/?]+)(?=\?|$)", current)
    if not m:
        return
    db_name = m.group(1)
    if db_name.endswith("_test"):
        return  # already pointing at a test DB

    new_db = f"{db_name}_test"
    test_url = current[: m.start(1)] + new_db + current[m.end(1) :]
    os.environ["DATABASE_URL"] = test_url

    # Create the sibling DB if missing. Connect to the cluster's `postgres`
    # admin DB, run CREATE DATABASE in autocommit. Idempotent on retry.
    admin_url = current[: m.start(1)] + "postgres" + current[m.end(1) :]
    try:
        conn = psycopg2.connect(admin_url)
        conn.set_isolation_level(0)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (new_db,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{new_db}"')
        conn.close()
    except psycopg2.Error:
        # Let downstream tests surface the connection error with full
        # context — masking it here just hides the root cause.
        pass


_redirect_to_test_db()
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
                agg_daily_trend, agg_stop_seq, rag_chunks, api_keys CASCADE
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
            agg_daily_trend, agg_stop_seq, rag_chunks, api_keys CASCADE
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
