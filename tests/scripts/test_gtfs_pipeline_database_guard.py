"""The gtfs_pipeline commands refuse a database that lacks this schema.

In development DATABASE_URL is a localhost port, so a stale value resolves to
whatever else is listening — another project's database, or the throwaway test
one. Every write path must reject that before writing, and must name the target
when it does: an UndefinedTable traceback says which table was missing but not
which database it was missing from, and that is the one fact needed to
recognise a wrong DATABASE_URL.

The wrong-target condition is reproduced by connecting with an empty
``search_path`` rather than by creating a database. The guard's probe resolves
``agencies`` through ``search_path`` exactly as the commands' own queries do, so
this yields the identical ``to_regclass('agencies') IS NULL`` on a real live
connection — without needing CREATEDB, and without ``DROP DATABASE``'s
requirement that no other backend be attached, which a second concurrent run
against the shared :5544 server would break.

Lives under ``tests/scripts/`` (not ``tests/unit/``) for the same reason as the
sibling gtfs_pipeline tests: it needs the auto-migrating root conftest and a
real connection to the throwaway server.
"""

import os

import pytest

import gtfs_pipeline

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")

# libpq passes `options` through to the backend, so the connection starts with
# a search_path in which no application table resolves.
SCHEMALESS_URL = f"{DATABASE_URL}?options=-c%20search_path%3Dpg_temp"


@pytest.fixture
def schemaless_target(monkeypatch, apply_schema):
    monkeypatch.setattr(gtfs_pipeline, "DATABASE_URL", SCHEMALESS_URL)
    return SCHEMALESS_URL


def test_rejects_a_target_without_the_schema(schemaless_target):
    with pytest.raises(SystemExit) as excinfo:
        gtfs_pipeline._get_conn()

    message = str(excinfo.value)
    assert "agencies" in message
    assert "not a migrated transit database" in message
    # The target has to be identifiable, and the credentials must not be.
    assert "transit_test" in message
    assert "transit:transit" not in message


def test_migrate_may_still_reach_a_target_without_the_schema(schemaless_target):
    """Creating the schema where there is none is `migrate up`'s job."""
    conn = gtfs_pipeline._get_conn(require_schema=False)
    try:
        with conn.cursor() as cur:
            cur.execute(gtfs_pipeline.SCHEMA_PROBE_SQL)
            assert cur.fetchone()[0] is False
    finally:
        conn.close()


def test_accepts_the_migrated_database(apply_schema):
    conn = gtfs_pipeline._get_conn()
    try:
        with conn.cursor() as cur:
            # Also proves the JST pin survives the added guard.
            cur.execute("SHOW TIME ZONE")
            assert cur.fetchone()[0] == "Asia/Tokyo"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_async_guard_rejects_a_target_without_the_schema(schemaless_target):
    """`guard_async_conn` itself rejects a schemaless connection.

    That the commands actually reach it is a separate claim, asserted by
    `test_asyncpg_commands_refuse_a_target_without_the_schema` below.
    """
    import asyncpg

    conn = await asyncpg.connect(SCHEMALESS_URL)
    try:
        with pytest.raises(SystemExit) as excinfo:
            await gtfs_pipeline.guard_async_conn(conn)
        assert "not a migrated transit database" in str(excinfo.value)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_async_guard_passes_on_the_migrated_database(apply_schema):
    import asyncpg

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await gtfs_pipeline.guard_async_conn(conn)  # must not raise
    finally:
        await conn.close()


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# Exercised through the command entry points, not just the helper: the defect
# these cover is a write command that never reaches the guard at all, which a
# test of the helper alone stays green for.
@pytest.mark.parametrize(
    "command,args",
    [
        ("cmd_prune_query_log", _Args(days=90)),
        ("cmd_build_rag_index", _Args(all_agencies=True, agency_id=None)),
    ],
)
def test_asyncpg_commands_refuse_a_target_without_the_schema(schemaless_target, command, args):
    with pytest.raises(SystemExit) as excinfo:
        getattr(gtfs_pipeline, command)(args)

    assert "not a migrated transit database" in str(excinfo.value)
