"""transfer_pairs holds what admins typed, which nothing can rebuild, so its
down is destructive; its checks refuse a definition the reader could not use."""

import os

import psycopg2
import pytest

from db import migrate

_VERSION = "0111"
_DOWN = migrate._MIGRATIONS_DIR / f"{_VERSION}_transfer_pairs.down.sql"


def test_rolling_back_loses_the_definitions_so_the_down_is_marked():
    assert migrate.data_losing_tables(_DOWN.read_text()) == ["transfer_pairs"]
    assert migrate.is_destructive_down(_DOWN.read_text())


def _exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('transfer_pairs') IS NOT NULL")
        return cur.fetchone()[0]


def test_rolling_back_drops_the_table_and_migrating_up_restores_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _exists(conn)
        migrate.migrate_down(below, conn, force_destructive=True)
        assert not _exists(conn)
        migrate.migrate_up(conn)
        assert _exists(conn)
    finally:
        conn.rollback()
        conn.close()


_INSERT = (
    "INSERT INTO transfer_pairs (name, from_agency_id, from_route_code, from_stop_ids, to_agency_id, "
    "to_route_code, to_stop_ids, walk_sec, day_types) VALUES (%s, %s, 'A', %s, %s, 'B', %s, %s, %s)"
)


@pytest.mark.parametrize(
    ("name", "from_stops", "walk", "day_types"),
    [
        ("  ", ["s"], 120, ["weekday"]),
        ("ok", [], 120, ["weekday"]),
        ("ok", ["s"], -1, ["weekday"]),
        ("ok", ["s"], 120, []),
        ("ok", ["s"], 120, ["holiday"]),
    ],
)
def test_the_checks_refuse_an_unusable_definition(pg_conn, agency_id, name, from_stops, walk, day_types):
    with pg_conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(_INSERT, (name, agency_id, from_stops, agency_id, ["t"], walk, day_types))
