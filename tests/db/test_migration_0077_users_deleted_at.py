"""users.deleted_at marks an admin soft-delete. The up backfills it for rows the
old soft-delete already anonymized, keeps each of them suspended, and masks and
revokes those users' keys."""

from datetime import datetime, timezone

from db import migrate

_DELETED_EVENT_AT = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
_UPDATED_AT = datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc)
_REVOKED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0077_users_deleted_at.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def _insert_user(cur, email):
    cur.execute("INSERT INTO users (email, suspended_at) VALUES (%s, now()) RETURNING user_id", (email,))
    return cur.fetchone()[0]


def _anonymize(cur, uid):
    cur.execute(
        "UPDATE users SET email = 'deleted-' || user_id || '@local', updated_at = %s WHERE user_id = %s",
        (_UPDATED_AT, uid),
    )


def _insert_key(cur, key_hash, owner, email, revoked_at=None):
    cur.execute(
        "INSERT INTO api_keys (key_hash, owner_user_id, owner_email, revoked_at) VALUES (%s, %s, %s, %s)",
        (key_hash, owner, email, revoked_at),
    )


def test_up_backfills_deleted_users_and_retires_their_keys(pg_conn):
    """Rolling back to 0076 also rolls back every later migration; this test is
    about 0077's round trip alone."""
    try:
        migrate.migrate_down("0076", pg_conn, force_destructive=True)
        with pg_conn.cursor() as cur:
            with_event = _insert_user(cur, "with-event@x")
            _anonymize(cur, with_event)
            cur.execute(
                "INSERT INTO login_events (user_id, kind, created_at) VALUES (%s, 'deleted', %s)",
                (with_event, _DELETED_EVENT_AT),
            )
            without_event = _insert_user(cur, "without-event@x")
            _anonymize(cur, without_event)
            resumed = _insert_user(cur, "resumed@x")
            _anonymize(cur, resumed)
            cur.execute("UPDATE users SET suspended_at = NULL WHERE user_id = %s", (resumed,))
            # The shape, but another row's id: an address anyone could hold.
            decoy = _insert_user(cur, "decoy@x")
            cur.execute("UPDATE users SET email = %s WHERE user_id = %s", (f"deleted-{decoy + 1000}@local", decoy))
            live = _insert_user(cur, "live@x")
            _insert_key(cur, "h-live-key", with_event, "with-event@x")
            _insert_key(cur, "h-revoked-key", with_event, "with-event@x", _REVOKED_AT)
            _insert_key(cur, "h-decoy-key", decoy, "decoy@x")
            _insert_key(cur, "h-other-key", live, "live@x")
        pg_conn.commit()

        migrate.migrate_up(pg_conn)

        with pg_conn.cursor() as cur:
            cur.execute("SELECT user_id, deleted_at FROM users")
            deleted_at = dict(cur.fetchall())
            cur.execute("SELECT suspended_at FROM users WHERE user_id = %s", (resumed,))
            resumed_suspended_at = cur.fetchone()[0]
            cur.execute("SELECT key_hash, owner_email, revoked_at FROM api_keys")
            keys = {k: (email, revoked) for k, email, revoked in cur.fetchall()}
        assert deleted_at[with_event] == _DELETED_EVENT_AT
        assert deleted_at[without_event] == _UPDATED_AT
        assert resumed_suspended_at == deleted_at[resumed] == _UPDATED_AT
        assert deleted_at[decoy] is None
        assert deleted_at[live] is None
        assert keys["h-live-key"][0] == f"deleted-{with_event}@local"
        assert keys["h-live-key"][1] is not None
        assert keys["h-revoked-key"] == (f"deleted-{with_event}@local", _REVOKED_AT)
        assert keys["h-decoy-key"] == ("decoy@x", None)
        assert keys["h-other-key"] == ("live@x", None)
    finally:
        pg_conn.rollback()
        migrate.migrate_up(pg_conn)
