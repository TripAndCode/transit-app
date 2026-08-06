import os
from urllib.parse import urlsplit, urlunsplit

import clickhouse_connect
import psycopg2
import psycopg2.errors
import pytest
from psycopg2 import sql

from db.clickhouse.bootstrap import apply_schema as _apply_ch_schema


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

# Origin that ASGITransport's default `base_url="http://test"` emits when tests
# set it. csrf_guard's ALLOW_TEST_ORIGIN path trusts this exact value when
# ALLOW_TEST_ORIGIN=1 (set by `_redirect_to_test_db` above). Use this constant
# in test files instead of hard-coding the string in headers.
TEST_ORIGIN = "http://test"


@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    from db.migrate import migrate_up

    conn = psycopg2.connect(DATABASE_URL)
    migrate_up(conn)
    conn.close()


@pytest.fixture(autouse=True)
def _clear_compute_caches():
    """Clear every async_lru_cache before each test.

    Module-level caches outlive the per-test TRUNCATE: a test that seeds
    different rows under the same (agency_id, ctx) key as an earlier test
    would otherwise read the earlier test's stale cached result. Agency-id
    churn usually hides this, but it is order-dependent — clear globally.
    """
    from pipeline.cache import clear_all

    clear_all()
    yield


@pytest.fixture
def pg_conn(apply_schema):
    conn = psycopg2.connect(DATABASE_URL)
    # Mirror api/main.py _init_connection (and the aconn fixture) so
    # `captured_at::date` casts in psycopg2-path tests use the same JST
    # civil calendar as production. Without this, tests that depend on
    # JST date boundaries flake near 15:00 UTC (00:00 JST).
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Tokyo'")
    yield conn
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE agencies, updates, static_stops, static_stop_times,
                static_trips, static_routes, static_calendar_dates, static_shapes,
                agg_route_stats, agg_route_hour, agg_route_dow, agg_route_hour_dow,
                agg_daily_trend, agg_stop_seq, agg_stop_daily, agg_stop_routes,
                agg_feed_health, agg_meta, rag_chunks, api_keys,
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


def _ch_test_client():
    return clickhouse_connect.get_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )


@pytest.fixture
def ch_client():
    """ClickHouse client against the throwaway `make ch-test` instance.

    Hoisted here (from tests/pipeline/conftest.py, Task 5) because Task 6
    (analyze()'s dedup materialization) needs it from tests/api/ and
    tests/query/ too, not just tests/pipeline/ — a root conftest fixture is
    visible to every subdirectory. Drop + reapply the schema before each test
    for isolation, since ClickHouse has no transactional rollback to lean on
    like the pg_conn fixture does. The skip (rather than a file-level
    pytestmark) lives here so pure, DB-free tests elsewhere in the suite still
    run without `make ch-test` — only tests that actually request this
    fixture are gated behind RUN_CH_INTEGRATION.
    """
    if os.environ.get("RUN_CH_INTEGRATION") != "1":
        pytest.skip("requires `make ch-test` (RUN_CH_INTEGRATION=1)")
    client = _ch_test_client()
    client.command("DROP TABLE IF EXISTS updates")
    _apply_ch_schema(client)
    yield client
    client.close()


def mirror_updates_to_ch(ch_client, agency_id) -> None:
    """Copy *agency_id*'s Postgres `updates` rows into ClickHouse.

    Task 6 moved analyze()'s dedup materialization to read from ClickHouse
    instead of Postgres. Many fixtures across this suite pre-date that
    migration and still seed Postgres `updates` directly (often via asyncpg,
    in ways that would be invasive to rewrite one-for-one into ClickHouse
    inserts). Rather than duplicate every such seed, mirror whatever Postgres
    already has for this agency into ClickHouse right before calling
    analyze() — analyze()'s three still-Postgres-reading blocks
    (agg_feed_health, agg_stop_routes, agg_meta's max_updates_captured_at;
    see pipeline/analyze.py) keep reading the original Postgres rows
    unchanged, so both sources agree on the same fixture data.
    """
    from pipeline.clickhouse import insert_updates

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_name, captured_at, trip_id, service_type, scheduled_time, "
                "route_code, stop_sequence, dep_delay FROM updates WHERE agency_id = %s",
                (agency_id,),
            )
            pg_rows = cur.fetchall()
    finally:
        conn.close()
    if not pg_rows:
        return
    ch_rows = []
    for file_name, captured_at, trip_id, service_type, scheduled_time, route_code, stop_sequence, dep_delay in pg_rows:
        ch_rows.append(
            (
                file_name,
                captured_at,
                trip_id,
                service_type,
                scheduled_time.strftime("%H:%M:%S") if scheduled_time is not None else None,
                route_code,
                stop_sequence,
                dep_delay,
            )
        )
    insert_updates(ch_client, agency_id, ch_rows)


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
            agg_route_stats, agg_route_hour, agg_route_dow, agg_route_hour_dow,
            agg_daily_trend, agg_stop_seq, agg_stop_daily, agg_stop_routes,
            agg_feed_health, agg_meta, rag_chunks, api_keys,
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
