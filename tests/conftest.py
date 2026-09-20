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

    Every test fixture in this suite empties the schema between tests.
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

# Origin that ASGITransport's default `base_url="http://test"` emits when tests
# set it. csrf_guard's ALLOW_TEST_ORIGIN path trusts this exact value when
# ALLOW_TEST_ORIGIN=1 (set by `_redirect_to_test_db` above). Use this constant
# in test files instead of hard-coding the string in headers.
TEST_ORIGIN = "http://test"


def _database_url() -> str:
    """Read ``DATABASE_URL`` lazily, only when a DB fixture is actually used.

    This module is imported (as pytest's parent conftest) for every test
    under ``tests/``, including ``tests/unit``, which has no DB dependency
    and overrides the fixtures below to no-ops (see ``tests/unit/conftest.py``).
    Reading the env var eagerly at import time would make ``pytest tests/unit``
    crash with ``DATABASE_URL`` unset even though nothing here ever uses it.
    """
    try:
        return os.environ["DATABASE_URL"]
    except KeyError:
        raise RuntimeError(
            "DATABASE_URL is not set. Tests that touch Postgres require it; "
            "tests/unit does not (see tests/unit/conftest.py)."
        ) from None


def __getattr__(name: str) -> str:
    """Lazy module attribute for backward-compatible ``from tests.conftest
    import DATABASE_URL`` in test files that need the resolved (possibly
    ``_test``-redirected) URL directly."""
    if name == "DATABASE_URL":
        return _database_url()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    from db.migrate import migrate_up

    conn = psycopg2.connect(_database_url())
    migrate_up(conn)
    conn.close()


@pytest.fixture(scope="session")
def reset_sql(apply_schema) -> str:
    """One statement that empties every table the schema owns.

    This runs between every test in the suite, so its fixed cost is paid
    thousands of times per run. ``TRUNCATE`` is the wrong tool for that:
    its cost is per-relation file work, flat in the tens of milliseconds
    however few rows a table holds, where ``DELETE`` over the same tables
    costs single-digit milliseconds at the row counts tests actually
    create — and stays there, because tests seed small.

    The list is read from the catalog rather than spelled out. A written
    list drifts silently: the one this replaced named two dozen tables and
    leaned on ``CASCADE`` to reach the rest through their references to
    ``agencies`` and ``users``, which left any table holding neither
    reference never reset between tests at all.

    Extension-owned tables are excluded — PostGIS's ``spatial_ref_sys``
    lives in this schema and is reference data, not test data.

    FK triggers are off for the duration so the deletes need no
    topological order, and back on before the statement ends, so no test
    body ever runs with them disabled.
    """
    conn = psycopg2.connect(_database_url())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND c.relname <> 'schema_migrations'
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend d
                      WHERE d.classid = 'pg_class'::regclass
                        AND d.objid = c.oid
                        AND d.deptype = 'e'
                  )
                ORDER BY c.relname
                """
            )
            tables = [r[0] for r in cur.fetchall()]
            if not tables:
                raise RuntimeError("no tables found to reset — is the schema applied?")
            deletes = "; ".join(f"DELETE FROM {sql.Identifier(t).as_string(conn)}" for t in tables)
    finally:
        conn.close()
    return f"SET session_replication_role = replica; {deletes}; SET session_replication_role = origin"


@pytest.fixture(autouse=True)
def _clear_compute_caches():
    """Clear every async_lru_cache before each test.

    Module-level caches outlive the per-test reset: a test that seeds
    different rows under the same (agency_id, ctx) key as an earlier test
    would otherwise read the earlier test's stale cached result. Agency-id
    churn usually hides this, but it is order-dependent — clear globally.
    """
    from pipeline.cache import clear_all

    clear_all()
    yield


@pytest.fixture
def pg_conn(apply_schema, reset_sql):
    conn = psycopg2.connect(_database_url())
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
            cur.execute(reset_sql)
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


@pytest.fixture(scope="session")
def _ch_schema() -> None:
    """Create the ClickHouse `updates` table once per test session.

    A no-op when RUN_CH_INTEGRATION isn't set, so requesting `ch_client`
    still skips cleanly instead of attempting a connection — the check must
    live here too, not just in `ch_client`, since a session-scoped fixture
    runs before the test-scoped fixture that depends on it.
    """
    if os.environ.get("RUN_CH_INTEGRATION") != "1":
        return
    client = _ch_test_client()
    try:
        client.command("DROP TABLE IF EXISTS updates")
        _apply_ch_schema(client)
    finally:
        client.close()


@pytest.fixture
def ch_client(_ch_schema):
    """ClickHouse client against the throwaway `make ch-test` instance.

    Lives in the root conftest, not a subdirectory one, because analyze()'s
    dedup materialization means tests/api/ and tests/query/ need a ClickHouse
    client too, not just tests/pipeline/ — a root conftest fixture is visible
    to every subdirectory. Truncate (not drop+recreate) before each test for
    isolation, since ClickHouse has no transactional rollback to
    lean on like the pg_conn fixture does — the schema itself never changes
    mid-session, so only `_ch_schema` needs to pay MergeTree's CREATE TABLE
    cost, once. The skip (rather than a file-level pytestmark) lives here so
    pure, DB-free tests elsewhere in the suite still run without
    `make ch-test` — only tests that actually request this fixture are
    gated behind RUN_CH_INTEGRATION.
    """
    if os.environ.get("RUN_CH_INTEGRATION") != "1":
        pytest.skip("requires `make ch-test` (RUN_CH_INTEGRATION=1)")
    client = _ch_test_client()
    client.command("TRUNCATE TABLE IF EXISTS updates")
    yield client
    client.close()


@pytest.fixture
async def ch_async_client(ch_client):
    """Async ClickHouse client for wiring into a test FastAPI app's
    ``app.state.ch_client`` — the async counterpart of `ch_client`, for the
    endpoints and tool-layer functions that read live `updates` through the
    async `get_ch` dependency rather than Postgres.

    Depends on `ch_client` (not a duplicate schema drop/apply of its own) so
    schema setup happens exactly once and ordering is deterministic: the sync
    fixture's DROP+CREATE always runs before this connects, and a test using
    both `ch_client` (e.g. to call `mirror_updates_to_ch`) and this fixture
    shares the same underlying ClickHouse instance/database.
    """
    client = await clickhouse_connect.get_async_client(
        host="localhost",
        port=int(os.environ.get("CLICKHOUSE_TEST_PORT", "8124")),
        username="transit",
        password="transit",
        database="transit_test",
    )
    yield client
    await client.close()


def mirror_updates_to_ch(ch_client, agency_id) -> None:
    """Copy *agency_id*'s Postgres `updates` rows into ClickHouse.

    analyze() now reads ALL of its `updates` access from ClickHouse (dedup
    materialization, agg_feed_health, agg_stop_routes' _analyze_raw_keys,
    agg_meta's max_updates_captured_at — see pipeline/analyze.py); nothing
    in it reads Postgres `updates` any more. Many fixtures across this
    suite pre-date that migration and still seed Postgres `updates` directly
    (often via asyncpg, in ways that would be invasive to rewrite one-for-one
    into ClickHouse inserts). Rather than duplicate every such seed, mirror
    whatever Postgres already has for this agency into ClickHouse right
    before calling analyze() — calling this first is required, not optional,
    for any test that seeds via Postgres and then calls analyze().
    """
    from pipeline.clickhouse import insert_updates

    conn = psycopg2.connect(_database_url())
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
async def aconn(apply_schema, reset_sql):
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    # Mirror api/main.py _init_connection so `captured_at::date` casts in
    # tests use the same JST civil calendar as production.
    await conn.execute("SET TIME ZONE 'Asia/Tokyo'")
    yield conn
    # clean up
    try:
        await conn.execute(reset_sql)
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


async def confirm_rt_field_coverage(conn, *agency_ids, confirmed=True, expires_at=None):
    """Record an RT field-coverage probe verdict for each of *agency_ids*,
    as `scripts/probe_rt_field_coverage.py --record` would.

    Production gates every RT-optional-field reader on a live verdict in
    `rt_field_coverage_probes` intersected with a `RT_INGEST_STRATEGIES`
    ingest_strategy (see
    `pipeline.strategies.static_join.rt_field_coverage_confirmed`), so a
    freshly-inserted test agency is untrusted until a test says otherwise —
    which is exactly the production default and needs no setup at all.

    Deliberately does NOT touch `agencies.ingest_strategy`: that is the
    gate's other, independent half, and a caller must set it explicitly so a
    test asserting one half can't be satisfied by the other.
    `expires_at=None` records a non-expiring verdict; pass a past timestamp
    to exercise the staleness path. Accepts an asyncpg connection or pool
    (both expose `execute`).
    """
    from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

    for aid in agency_ids:
        for field in RT_COVERAGE_FIELDS:
            await conn.execute(
                "INSERT INTO rt_field_coverage_probes "
                "(agency_id, field_name, confirmed, source_feed, expires_at) "
                "VALUES ($1, $2, $3, 'test://probe', $4) "
                "ON CONFLICT (agency_id, field_name) DO UPDATE SET "
                "confirmed = EXCLUDED.confirmed, expires_at = EXCLUDED.expires_at",
                aid,
                field,
                confirmed,
                expires_at,
            )


async def _test_pool(*, min_size=1, **kw):
    """`asyncpg.create_pool` against the test DB, pre-warming 1 connection.

    `min_size` defaults to 1 (asyncpg's own default is 10): this suite runs
    one request at a time per test/fixture, so pre-warming asyncpg's default
    10 connections on every single test buys no concurrency headroom here
    and only pays for it in connection-setup latency. A caller whose test
    fires concurrent requests can raise `min_size` explicitly so every
    request already holds a live connection. `max_size` stays at asyncpg's
    default (pass it via `**kw` to override) so a handler that does need
    more than one connection at once still can. Centralized so a future test
    author copying an existing fixture doesn't reintroduce the slow default
    by hand-rolling `asyncpg.create_pool(...)` again.
    """
    import asyncpg

    return await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=min_size, **kw)


@pytest.fixture
async def client(apply_schema):
    """Boot the FastAPI app against the test DB pool and yield an
    httpx.AsyncClient that talks to it via ASGITransport.

    The pool is per-test (created + closed inside the fixture) so
    concurrent tests can't share or step on app.state.pool.
    """
    import httpx
    from httpx import ASGITransport

    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()
