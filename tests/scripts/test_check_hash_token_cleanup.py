"""`scripts/check_hash_token_cleanup.py` against real `sessions` / `api_keys` rows.

A row counts only while it both holds a raw credential and would still be
accepted by the auth middleware; see the script's docstring for why that is
the criterion.
"""

from __future__ import annotations

import sys

import pytest

from api.security import token_hash
from scripts import check_hash_token_cleanup as cleanup
from tests.conftest import DATABASE_URL

_LIVE = "now() + interval '1 day'"
_PAST = "now() - interval '1 minute'"


def _user(cur, email: str = "owner@example.com") -> int:
    cur.execute("INSERT INTO users (email) VALUES (%s) RETURNING user_id", (email,))
    return cur.fetchone()[0]


def _session(cur, user_id: int, *, raw: str | None, expires_at_sql: str) -> None:
    cur.execute(
        f"INSERT INTO sessions (sid, sid_hash, user_id, expires_at) VALUES (%s, %s, %s, {expires_at_sql})",
        (raw, token_hash(raw or f"new-style-{user_id}"), user_id),
    )


def _api_key(
    cur,
    *,
    raw: str | None,
    revoked_at_sql: str = "NULL",
    expires_at_sql: str = "NULL",
    owner_user_id: int | None = None,
) -> None:
    cur.execute(
        "INSERT INTO api_keys (key, key_hash, owner_email, owner_user_id, revoked_at, expires_at) "
        f"VALUES (%s, %s, 'owner@example.com', %s, {revoked_at_sql}, {expires_at_sql})",
        (raw, token_hash(raw or "new-style-key"), owner_user_id),
    )


def test_unexpired_raw_session_counts(pg_conn):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw="raw-live", expires_at_sql=_LIVE)
        assert cleanup.read_state(cur).live_raw_sessions == 1


def test_expired_raw_session_does_not_count(pg_conn):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw="raw-expired", expires_at_sql=_PAST)
        assert cleanup.read_state(cur).live_raw_sessions == 0


def test_hash_only_session_does_not_count(pg_conn):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw=None, expires_at_sql=_LIVE)
        assert cleanup.read_state(cur).live_raw_sessions == 0


@pytest.mark.parametrize("expires_at_sql", ["NULL", _LIVE])
def test_usable_raw_api_key_counts(pg_conn, expires_at_sql):
    with pg_conn.cursor() as cur:
        _api_key(cur, raw="raw-key", expires_at_sql=expires_at_sql)
        assert cleanup.read_state(cur).live_raw_api_keys == 1


def test_revoked_raw_api_key_does_not_count(pg_conn):
    with pg_conn.cursor() as cur:
        _api_key(cur, raw="raw-key", revoked_at_sql="now()")
        assert cleanup.read_state(cur).live_raw_api_keys == 0


def test_expired_raw_api_key_does_not_count(pg_conn):
    with pg_conn.cursor() as cur:
        _api_key(cur, raw="raw-key", expires_at_sql=_PAST)
        assert cleanup.read_state(cur).live_raw_api_keys == 0


def test_hash_only_api_key_does_not_count(pg_conn):
    with pg_conn.cursor() as cur:
        _api_key(cur, raw=None)
        assert cleanup.read_state(cur).live_raw_api_keys == 0


def test_raw_api_key_of_suspended_owner_still_counts(pg_conn):
    """Suspension is reversible and restoring the account restores the key."""
    with pg_conn.cursor() as cur:
        owner = _user(cur)
        cur.execute("UPDATE users SET suspended_at = now() WHERE user_id = %s", (owner,))
        _api_key(cur, raw="raw-key", owner_user_id=owner)
        assert cleanup.read_state(cur).live_raw_api_keys == 1


def test_dropped_raw_column_reads_as_none(pg_conn):
    """A dropped column is reported as such rather than counted, which would fail."""
    with pg_conn.cursor() as cur:
        cur.execute("ALTER TABLE sessions DROP COLUMN sid")
        _api_key(cur, raw="raw-key")
        assert cleanup.read_state(cur) == cleanup.CleanupState(live_raw_sessions=None, live_raw_api_keys=1)
    pg_conn.rollback()


def _run_main(monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["check_hash_token_cleanup.py", "--database-url", DATABASE_URL])
    return cleanup.main()


def test_main_not_due_while_a_raw_session_is_live(pg_conn, monkeypatch):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw="raw-live", expires_at_sql=_LIVE)
    pg_conn.commit()
    assert _run_main(monkeypatch) == 0


def test_main_due_once_no_raw_credential_is_usable(pg_conn, monkeypatch):
    with pg_conn.cursor() as cur:
        user_id = _user(cur)
        _session(cur, user_id, raw="raw-expired", expires_at_sql=_PAST)
        _session(cur, _user(cur, "other@example.com"), raw=None, expires_at_sql=_LIVE)
        _api_key(cur, raw="raw-key", revoked_at_sql="now()")
    pg_conn.commit()
    assert _run_main(monkeypatch) == 1
