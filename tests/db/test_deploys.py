"""The release record against a real Postgres: a rerun of the newest release
adds no row, and anything else does."""

import psycopg2
import pytest

from pipeline.deploys import record_deploy
from tests.conftest import _database_url

SHA_A = "a" * 40
SHA_B = "b" * 7


def _connect():
    return psycopg2.connect(_database_url())


def _rows(pg_conn) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT git_sha, migrations_applied FROM deploys ORDER BY deploy_id")
        rows = cur.fetchall()
    pg_conn.commit()
    return rows


def test_a_release_is_recorded_once_however_often_it_restarts(pg_conn):
    record_deploy(_connect, SHA_A, ["0113", "0114"])
    record_deploy(_connect, SHA_A, [])
    record_deploy(_connect, SHA_A, [])
    assert _rows(pg_conn) == [(SHA_A, ["0113", "0114"])]


def test_a_new_commit_or_a_migration_is_a_new_release(pg_conn):
    record_deploy(_connect, SHA_A, [])
    record_deploy(_connect, SHA_B, [])
    record_deploy(_connect, SHA_B, ["0115"])
    record_deploy(_connect, SHA_A, [])
    assert _rows(pg_conn) == [(SHA_A, []), (SHA_B, []), (SHA_B, ["0115"]), (SHA_A, [])]


def test_an_unknown_commit_is_a_new_release_only_after_a_known_one(pg_conn):
    record_deploy(_connect, None, [])
    assert _rows(pg_conn) == []
    record_deploy(_connect, SHA_A, [])
    record_deploy(_connect, None, [])
    record_deploy(_connect, None, [])
    assert _rows(pg_conn) == [(SHA_A, []), (None, [])]


@pytest.mark.parametrize("sha", ["ABC1234", "abc123", "main"])
def test_the_table_refuses_what_is_not_a_lowercase_commit_hash(pg_conn, sha):
    with pg_conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO deploys (git_sha) VALUES (%s)", (sha,))
    pg_conn.rollback()
