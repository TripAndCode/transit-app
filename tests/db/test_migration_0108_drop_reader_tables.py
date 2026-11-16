"""0108 removes the reader account platform: its four tables, the reader
accounts with everything they own, the API-key split of the usage counts and
two retired flag overrides. Admins, and demoted admins that an admin-only
record still names, keep their rows."""

from datetime import date

from api.security import token_hash
from db import migrate
from pipeline.flags import REGISTRY

_VERSION = "0108"
_BELOW = "0107"
_DROPPED_TABLES = {"filter_presets", "user_llm_keys", "user_invites", "api_keys"}
_DAY = date(2026, 1, 5)


def _tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(_DROPPED_TABLES),),
        )
        return {r[0] for r in cur.fetchall()}


def _has_via_api_key(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_name = 'user_activity_daily' AND column_name = 'via_api_key'"
        )
        return cur.fetchone() is not None


def _has_fill_function(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'api_keys_fill_key_hash'")
        return cur.fetchone() is not None


def _user(cur, email, role="user"):
    cur.execute("INSERT INTO users (email, role) VALUES (%s, %s) RETURNING user_id", (email, role))
    return cur.fetchone()[0]


def _seed_reader(cur, agency_id):
    """A reader account with a row in every table that holds reader data."""
    uid = _user(cur, "reader@x")
    cur.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ('h-reader', %s, now() + interval '1 day')",
        (uid,),
    )
    cur.execute("INSERT INTO oauth_identities (provider, provider_sub, user_id) VALUES ('google', 'sub-r', %s)", (uid,))
    cur.execute(
        "INSERT INTO login_events (user_id, actor_id, kind, ip, user_agent)"
        " VALUES (%s, %s, 'login', '203.0.113.9', 'reader-ua')",
        (uid, uid),
    )
    cur.execute(
        "INSERT INTO ask_conversations (user_id, agency_id, title) VALUES (%s, %s, 'mine') RETURNING conversation_id",
        (uid, agency_id),
    )
    (conversation_id,) = cur.fetchone()
    cur.execute("INSERT INTO ask_conversation_messages (conversation_id, role) VALUES (%s, 'user')", (conversation_id,))
    cur.execute(
        "INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)"
        " VALUES (%s, %s, '/api/agencies', 'GET', NULL, false, 1, 0)",
        (uid, _DAY),
    )
    cur.execute(
        "INSERT INTO filter_presets (user_id, agency_id, name, range_ctx) VALUES (%s, %s, 'p', '{}')",
        (uid, agency_id),
    )
    cur.execute(
        "INSERT INTO user_llm_keys (user_id, provider, encrypted_key, key_suffix) VALUES (%s, 'openai', 'x', 'abcd')",
        (uid,),
    )
    cur.execute("INSERT INTO api_keys (key_hash, owner_email, owner_user_id) VALUES ('h-key', 'reader@x', %s)", (uid,))
    cur.execute("INSERT INTO user_invites (email, invited_by) VALUES ('invitee@x', %s)", (uid,))
    return uid


def _seed_demoted_admins(cur, agency_id, someone_else):
    """One former admin per admin-only record that can still name them."""
    named = {}
    named["admin_audit"] = _user(cur, "demoted-audit@x")
    cur.execute(
        "INSERT INTO admin_audit (actor_id, action, target_type) VALUES (%s, 'agency.updated', 'agency')",
        (named["admin_audit"],),
    )
    named["feature_flags"] = _user(cur, "demoted-flag@x")
    cur.execute(
        "INSERT INTO feature_flags (key, value, updated_by) VALUES ('kept_flag', true, %s)", (named["feature_flags"],)
    )
    named["pipeline_runs"] = _user(cur, "demoted-run@x")
    cur.execute(
        "INSERT INTO pipeline_runs (kind, status, requested_by) VALUES ('ingest', 'ok', %s)",
        (named["pipeline_runs"],),
    )
    named["admin_alert_acks"] = _user(cur, "demoted-ack@x")
    cur.execute(
        "INSERT INTO admin_alert_acks (alert_key, acked_by, expires_at) VALUES ('k', %s, now() + interval '1 day')",
        (named["admin_alert_acks"],),
    )
    named["snapshots"] = _user(cur, "demoted-snap@x")
    cur.execute(
        "INSERT INTO snapshots (snapshot_id, created_by, screen, agency_id, agency_name, params, payloads,"
        " method_version) VALUES ('snapshot0108aaaaaaaaaaaaa', %s, 'council_report', %s, 'A', '{}', '{}', 'v1')",
        (named["snapshots"], agency_id),
    )
    named["login_events"] = _user(cur, "demoted-actor@x")
    cur.execute(
        "INSERT INTO login_events (user_id, actor_id, kind) VALUES (%s, %s, 'suspended')",
        (someone_else, named["login_events"]),
    )
    return named


def _seed_usage(cur, uid):
    """Session and API-key rows for one user: two pairs on the same key, one
    with a NULL agency, a lone API-key row, and a session row whose only
    difference from a pair is its agency."""
    cur.execute(
        "INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)"
        " VALUES (%(u)s, %(d)s, '/a', 'GET', NULL, false, 2, 1),"
        "        (%(u)s, %(d)s, '/a', 'GET', NULL, true,  5, 0),"
        "        (%(u)s, %(d)s, '/b', 'GET', 3,    true,  4, 2),"
        "        (%(u)s, %(d)s, '/c', 'GET', 3,    false, 1, 0),"
        "        (%(u)s, %(d)s, '/c', 'GET', 3,    true,  1, 1),"
        "        (%(u)s, %(d)s, '/c', 'GET', NULL, false, 9, 0)",
        {"u": uid, "d": _DAY},
    )


def test_up_removes_the_reader_platform_and_keeps_admin_attribution(pg_conn):
    """Rolling back to 0107 rolls back 0108 alone; its down loses nothing, so
    it needs no force."""
    try:
        migrate.migrate_down(_BELOW, pg_conn)
        assert _tables(pg_conn) == _DROPPED_TABLES
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('0108', 'http://0108.example.com')"
                " RETURNING agency_id"
            )
            (agency_id,) = cur.fetchone()
            admin = _user(cur, "admin@x", role="admin")
            reader = _seed_reader(cur, agency_id)
            other_reader = _user(cur, "other-reader@x")
            named = _seed_demoted_admins(cur, agency_id, other_reader)
            _seed_usage(cur, admin)
            # The admin's record about the reader stays with the admin action
            # trail; the actor-less one goes with the reader's own events.
            cur.execute(
                "INSERT INTO login_events (user_id, actor_id, kind, ip) VALUES (%s, %s, 'suspended', '198.51.100.1'),"
                " (%s, NULL, 'role_changed', NULL)",
                (reader, admin, reader),
            )
            cur.execute(
                "INSERT INTO feature_flags (key, value)"
                " VALUES ('login_required', true), ('copilot_insight_enabled', false)"
            )
        pg_conn.commit()

        migrate._run_up(_VERSION, pg_conn)

        with pg_conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            users = {r[0] for r in cur.fetchall()}
            assert users == {admin, *named.values()}
            assert reader not in users and other_reader not in users
            for table in ("sessions", "oauth_identities", "ask_conversations", "ask_conversation_messages"):
                cur.execute(f"SELECT count(*) FROM {table}")
                assert cur.fetchone() == (0,), table
            cur.execute("SELECT user_id, actor_id, kind FROM login_events")
            assert set(cur.fetchall()) == {(None, named["login_events"], "suspended"), (None, admin, "suspended")}
            cur.execute(
                "SELECT user_id, route, agency_id, requests, errors FROM user_activity_daily ORDER BY route, agency_id"
            )
            assert cur.fetchall() == [
                (admin, "/a", None, 7, 1),
                (admin, "/b", 3, 4, 2),
                (admin, "/c", 3, 2, 1),
                (admin, "/c", None, 9, 0),
            ]
            cur.execute("SELECT key FROM feature_flags")
            assert {r[0] for r in cur.fetchall()} == {"kept_flag"}
        assert _tables(pg_conn) == set()
        assert not _has_via_api_key(pg_conn)
        assert not _has_fill_function(pg_conn)
    finally:
        pg_conn.rollback()
        migrate.migrate_up(pg_conn)


def test_the_usage_upsert_merges_on_the_new_key(pg_conn):
    with pg_conn.cursor() as cur:
        uid = _user(cur, "upsert@x", role="admin")
        for _ in range(2):
            cur.execute(
                "INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, requests, errors)"
                " VALUES (%s, %s, '/a', 'GET', NULL, 1, 0)"
                " ON CONFLICT (user_id, day, route, method, (COALESCE(agency_id, 0))) DO UPDATE SET"
                " requests = user_activity_daily.requests + EXCLUDED.requests",
                (uid, _DAY),
            )
        cur.execute("SELECT requests FROM user_activity_daily WHERE user_id = %s", (uid,))
        assert cur.fetchall() == [(2,)]


def test_down_restores_the_schema_empty_and_up_removes_it_again(pg_conn):
    try:
        migrate.migrate_down(_BELOW, pg_conn)
        assert _tables(pg_conn) == _DROPPED_TABLES
        assert _has_via_api_key(pg_conn)
        assert _has_fill_function(pg_conn)
        with pg_conn.cursor() as cur:
            # The restored trigger still fills the digest of a raw key.
            cur.execute("INSERT INTO api_keys (key, owner_email) VALUES ('raw', 'o@x') RETURNING key_hash")
            assert cur.fetchone()[0] == token_hash("raw")
            cur.execute("SELECT count(*) FROM user_invites")
            assert cur.fetchone() == (0,)
        pg_conn.rollback()

        migrate.migrate_up(pg_conn)
        assert _tables(pg_conn) == set()
        assert not _has_via_api_key(pg_conn)
        assert not _has_fill_function(pg_conn)
    finally:
        pg_conn.rollback()
        migrate.migrate_up(pg_conn)


def test_the_retired_flags_are_not_registered():
    assert not {"login_required", "copilot_insight_enabled"} & {flag.key for flag in REGISTRY}
