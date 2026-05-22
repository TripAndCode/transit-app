import os
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.errors
import pytest
from psycopg2 import sql


def _redirect_to_test_db() -> None:
    """Auto-redirect pytest to a sibling ``<dbname>_test`` database.

    Also enables the ``http://test`` CSRF allowance via ``ALLOW_TEST_ORIGIN=1``
    so tests using ASGITransport (base_url=http://test) can pass csrf_guard
    without that origin being trusted in production.

    Every test fixture in this suite TRUNCATEs the schema between tests.
    Sharing the dev DB meant every ``make test`` run nuked the operator's
    map heatmap data — a constant source of "the page is empty" bug
    reports. Redirect once at collection time so pytest never touches the
    dev DB; create the sibling on first run if it's missing.

    Opt-out: set ``DATABASE_URL`` to a name already ending in ``_test``,
    or set ``TEST_DATABASE_URL`` explicitly.

    Robustness notes (review feedback):
    - Parsing via ``urllib.parse.urlsplit`` instead of a hand-rolled regex
      so trailing slashes and querystrings don't silently bypass the
      redirect (which would let pytest nuke the dev DB).
    - ``connect_timeout=5`` so an unreachable cluster fails the test
      session in seconds with a clear error, instead of hanging at
      collection with no context.
    - ``CREATE DATABASE`` uses ``psycopg2.sql.Identifier`` to escape the
      name, and ``DuplicateDatabase`` is caught explicitly so a real
      auth/connection failure isn't silently swallowed by the same
      ``except`` that handles "another xdist worker already created it".
    """
    os.environ.setdefault("ALLOW_TEST_ORIGIN", "1")
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        os.environ["DATABASE_URL"] = explicit
        return

    current = os.environ.get("DATABASE_URL")
    if not current:
        return  # downstream code raises a clearer error

    parts = urlsplit(current)
    db_name = parts.path.lstrip("/").rstrip("/")
    if not db_name or db_name.endswith("_test"):
        return  # already pointing at a test DB or no path component

    new_db = f"{db_name}_test"
    test_url = urlunsplit(parts._replace(path=f"/{new_db}"))
    os.environ["DATABASE_URL"] = test_url

    # Create the sibling DB if missing. Connect to the cluster's `postgres`
    # admin DB, run CREATE DATABASE in autocommit. Idempotent on retry.
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    try:
        conn = psycopg2.connect(admin_url, connect_timeout=5)
    except psycopg2.Error:
        # Cluster unreachable — let the schema fixture surface the real
        # connection error with full context.
        return
    try:
        conn.set_isolation_level(0)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (new_db,))
            if cur.fetchone() is None:
                try:
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(new_db)))
                except psycopg2.errors.DuplicateDatabase:
                    # A parallel xdist worker won the race. Fine — both
                    # workers see the same DB now.
                    pass
    finally:
        conn.close()


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
                static_trips, static_routes, static_calendar_dates, static_shapes,
                agg_route_stats, agg_route_hour, agg_route_dow,
                agg_daily_trend, agg_stop_seq, rag_chunks, api_keys,
                filter_presets, login_events, sessions, oauth_identities, users CASCADE
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
    # Mirror api/main.py _init_connection so `captured_at::date` casts in
    # tests use the same JST civil calendar as production.
    await conn.execute("SET TIME ZONE 'Asia/Tokyo'")
    yield conn
    # clean up
    try:
        await conn.execute("""
            TRUNCATE agencies, updates, static_stops, static_stop_times,
            static_trips, static_routes, static_calendar_dates, static_shapes,
            agg_route_stats, agg_route_hour, agg_route_dow,
            agg_daily_trend, agg_stop_seq, rag_chunks, api_keys,
            filter_presets, login_events, sessions, oauth_identities, users CASCADE
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


@pytest.fixture
async def client(apply_schema):
    """Boot the FastAPI app against the test DB pool and yield an
    httpx.AsyncClient that talks to it via ASGITransport.

    The pool is per-test (created + closed inside the fixture) so
    concurrent tests can't share or step on app.state.pool.
    """
    import asyncpg
    import httpx
    from httpx import ASGITransport

    from api.main import app

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()
