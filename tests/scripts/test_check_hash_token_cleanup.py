"""`scripts/check_hash_token_cleanup.py` against real `sessions` rows.

A row counts only while it both holds a raw session id and would still be
accepted by the session middleware; see the script's docstring for why that
is the criterion.
"""

from __future__ import annotations

import sys

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


def test_unexpired_raw_session_counts(pg_conn):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw="raw-live", expires_at_sql=_LIVE)
        assert cleanup.read_live_raw_sessions(cur) == 1


def test_expired_raw_session_does_not_count(pg_conn):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw="raw-expired", expires_at_sql=_PAST)
        assert cleanup.read_live_raw_sessions(cur) == 0


def test_hash_only_session_does_not_count(pg_conn):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw=None, expires_at_sql=_LIVE)
        assert cleanup.read_live_raw_sessions(cur) == 0


def test_raw_session_of_suspended_user_still_counts(pg_conn):
    """Suspension is reversible and lifting it revives the session."""
    with pg_conn.cursor() as cur:
        owner = _user(cur)
        cur.execute("UPDATE users SET suspended_at = now() WHERE user_id = %s", (owner,))
        _session(cur, owner, raw="raw-live", expires_at_sql=_LIVE)
        assert cleanup.read_live_raw_sessions(cur) == 1


def test_dropped_raw_column_reads_as_none(pg_conn):
    """A dropped column is reported as such rather than counted, which would fail."""
    with pg_conn.cursor() as cur:
        cur.execute("ALTER TABLE sessions DROP COLUMN sid")
        assert cleanup.read_live_raw_sessions(cur) is None
    pg_conn.rollback()


def _run_main(monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["check_hash_token_cleanup.py", "--database-url", DATABASE_URL])
    return cleanup.main()


def test_main_not_due_while_a_raw_session_is_live(pg_conn, monkeypatch):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw="raw-live", expires_at_sql=_LIVE)
    pg_conn.commit()
    assert _run_main(monkeypatch) == 0


_COMPAT_OBJECTS = [
    "function sessions_fill_sid_hash()",
    "index idx_sessions_sid",
    "trigger sessions_fill_sid_hash",
]


def test_compat_objects_are_read_while_they_exist(pg_conn):
    """The drop migration must remove 0062's objects too, so the report lists what is left."""
    with pg_conn.cursor() as cur:
        assert cleanup.read_compat_objects(cur) == _COMPAT_OBJECTS
        cur.execute("DROP TRIGGER sessions_fill_sid_hash ON sessions")
        assert cleanup.read_compat_objects(cur) == ["function sessions_fill_sid_hash()", "index idx_sessions_sid"]
    pg_conn.rollback()


def test_main_due_once_no_raw_session_is_usable(pg_conn, monkeypatch, capsys):
    with pg_conn.cursor() as cur:
        _session(cur, _user(cur), raw="raw-expired", expires_at_sql=_PAST)
        _session(cur, _user(cur, "other@example.com"), raw=None, expires_at_sql=_LIVE)
    pg_conn.commit()
    assert _run_main(monkeypatch) == 1
    assert "trigger sessions_fill_sid_hash" in capsys.readouterr().out
