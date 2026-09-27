"""A release that predates 0053 keeps working against the hashed schema.

`migrate up` runs while the previous release is still serving, and rolling the
app back does not roll the schema back, so that release's SQL runs against
0053's schema. The statements below are that release's, verbatim: it writes
sessions with only the raw `sid`, and looks sessions and API keys up by the raw
credential alone.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.errors
import pytest

from api.security import token_hash
from db.migrate import migrate_down, migrate_up

DATABASE_URL = os.environ["DATABASE_URL"]

LEGACY_CREATE_SESSION = (
    "INSERT INTO sessions (sid, user_id, expires_at, user_agent, ip) VALUES ($1, $2, $3, $4, $5::inet)"
)
LEGACY_LOAD_SESSION = """
                SELECT s.sid, s.expires_at,
                       u.user_id, u.email, u.name, u.avatar_url, u.role, u.suspended_at,
                       u.llm_approved
                FROM sessions s
                JOIN users u USING (user_id)
                WHERE s.sid = $1
                """
LEGACY_TOUCH_SESSION = "UPDATE sessions SET last_seen_at = now() WHERE sid = $1"
LEGACY_DELETE_EXPIRED_SESSION = "DELETE FROM sessions WHERE sid = $1"
LEGACY_LIST_SESSIONS = (
    "SELECT sid, user_agent, ip::text AS ip, created_at, last_seen_at "
    "FROM sessions WHERE user_id=$1 ORDER BY last_seen_at DESC"
)
LEGACY_FIND_SESSION_BY_PREFIX = "SELECT sid FROM sessions WHERE user_id=$1 AND sid LIKE $2"
# Both logout and the self-service revoke.
LEGACY_DELETE_OWN_SESSION = "DELETE FROM sessions WHERE sid=$1 AND user_id=$2"
# Admin suspend and soft-delete.
LEGACY_DELETE_USER_SESSIONS = "DELETE FROM sessions WHERE user_id=$1"
LEGACY_API_KEY_TIER = "SELECT tier FROM api_keys WHERE key = $1"
# That release has no code path that writes api_keys: an operator provisions a
# key by hand with the raw key and an owner email.
LEGACY_PROVISION_API_KEY = "INSERT INTO api_keys (key, owner_email) VALUES ($1, $2)"

HASH_ONLY_CREATE_SESSION = (
    "INSERT INTO sessions (sid_hash, user_id, expires_at, user_agent, ip) VALUES ($1, $2, $3, $4, $5::inet)"
)
HASH_ONLY_CREATE_API_KEY = "INSERT INTO api_keys (key_hash, owner_email) VALUES ($1, $2)"

# The first has the shape `secrets.token_urlsafe(32)` mints. A backslash and a
# non-ASCII character pin the digest to the UTF-8 bytes of the text as stored,
# which is what `token_hash` hashes.
RAW_CREDENTIALS = pytest.mark.parametrize(
    "raw",
    ["Qm9vdHN0cmFwLXNlc3Npb24taWQtZm9yLXRlc3Rz_-0", "k\\x41-é"],
    ids=["urlsafe", "backslash-non-ascii"],
)


@pytest.fixture
async def uid(aconn):
    return await aconn.fetchval(
        "INSERT INTO users (email, name, role) VALUES ('legacy@test', 'Legacy', 'user') RETURNING user_id"
    )


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=30)


@RAW_CREDENTIALS
async def test_a_raw_only_session_gets_the_token_hash(aconn, uid, raw):
    await aconn.execute(LEGACY_CREATE_SESSION, raw, uid, _expiry(), "legacy-ua", "203.0.113.7")
    assert await aconn.fetchval("SELECT sid_hash FROM sessions WHERE sid = $1", raw) == token_hash(raw)


@RAW_CREDENTIALS
async def test_a_raw_only_api_key_gets_the_token_hash(aconn, raw):
    await aconn.execute(LEGACY_PROVISION_API_KEY, raw, "owner@test")
    assert await aconn.fetchval("SELECT key_hash FROM api_keys WHERE key = $1", raw) == token_hash(raw)


async def test_a_hash_only_session_is_left_alone(aconn, uid):
    digest = token_hash(secrets.token_urlsafe(32))
    await aconn.execute(HASH_ONLY_CREATE_SESSION, digest, uid, _expiry(), None, None)
    row = await aconn.fetchrow("SELECT sid, sid_hash FROM sessions WHERE user_id = $1", uid)
    assert (row["sid"], row["sid_hash"]) == (None, digest)


async def test_a_hash_only_api_key_is_left_alone(aconn):
    digest = token_hash(secrets.token_urlsafe(32))
    await aconn.execute(HASH_ONLY_CREATE_API_KEY, digest, "owner@test")
    row = await aconn.fetchrow("SELECT key, key_hash FROM api_keys WHERE owner_email = 'owner@test'")
    assert (row["key"], row["key_hash"]) == (None, digest)


async def test_a_supplied_session_hash_is_never_replaced(aconn, uid):
    await aconn.execute(
        "INSERT INTO sessions (sid, sid_hash, user_id, expires_at) VALUES ($1, $2, $3, $4)",
        "raw-sid",
        "supplied-digest",
        uid,
        _expiry(),
    )
    assert await aconn.fetchval("SELECT sid_hash FROM sessions WHERE sid = 'raw-sid'") == "supplied-digest"


async def test_a_supplied_api_key_hash_is_never_replaced(aconn):
    await aconn.execute(
        "INSERT INTO api_keys (key, key_hash, owner_email) VALUES ($1, $2, $3)",
        "raw-key",
        "supplied-digest",
        "owner@test",
    )
    assert await aconn.fetchval("SELECT key_hash FROM api_keys WHERE key = 'raw-key'") == "supplied-digest"


async def test_every_legacy_session_statement_works(aconn, uid):
    first, second, third = (secrets.token_urlsafe(32) for _ in range(3))
    for raw in (first, second, third):
        await aconn.execute(LEGACY_CREATE_SESSION, raw, uid, _expiry(), "legacy-ua", "203.0.113.7")

    loaded = await aconn.fetchrow(LEGACY_LOAD_SESSION, first)
    assert (loaded["sid"], loaded["user_id"]) == (first, uid)
    assert await aconn.execute(LEGACY_TOUCH_SESSION, first) == "UPDATE 1"
    assert {r["sid"] for r in await aconn.fetch(LEGACY_LIST_SESSIONS, uid)} == {first, second, third}

    [match] = await aconn.fetch(LEGACY_FIND_SESSION_BY_PREFIX, uid, first[:12] + "%")
    assert await aconn.execute(LEGACY_DELETE_OWN_SESSION, match["sid"], uid) == "DELETE 1"
    assert await aconn.execute(LEGACY_DELETE_EXPIRED_SESSION, second) == "DELETE 1"
    assert await aconn.execute(LEGACY_DELETE_USER_SESSIONS, uid) == "DELETE 1"


async def test_a_legacy_session_is_found_by_its_hash(aconn, uid):
    """A session the old release minted mid-deploy survives the switch to the
    release that looks sessions up by digest."""
    raw = secrets.token_urlsafe(32)
    await aconn.execute(LEGACY_CREATE_SESSION, raw, uid, _expiry(), None, None)
    assert await aconn.fetchval("SELECT user_id FROM sessions WHERE sid_hash = $1", token_hash(raw)) == uid


async def test_the_legacy_api_key_lookup_works(aconn):
    raw = secrets.token_urlsafe(32)
    await aconn.execute(LEGACY_PROVISION_API_KEY, raw, "owner@test")
    assert await aconn.fetchval(LEGACY_API_KEY_TIER, raw) == "pro"


@pytest.mark.parametrize(
    "statement, index",
    [
        (LEGACY_LOAD_SESSION, "idx_sessions_sid"),
        (LEGACY_TOUCH_SESSION, "idx_sessions_sid"),
        (LEGACY_DELETE_EXPIRED_SESSION, "idx_sessions_sid"),
        (LEGACY_API_KEY_TIER, "idx_api_keys_key"),
    ],
    ids=["load-session", "touch-session", "delete-session", "api-key-tier"],
)
def test_raw_lookups_can_use_the_raw_index(pg_conn, statement, index):
    """The partial index is usable for a bound parameter, which is how the
    release issues these (asyncpg prepares every statement). A generic plan
    is the case that proves it: a custom plan sees the literal value.

    The tables are filled and analyzed first. Nearly empty, every index costs
    about the same, so the planner's pick would depend on whatever statistics
    earlier tests left behind rather than on this index being usable."""
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO users (email, name, role) VALUES ('plan@test', 'Plan', 'user') RETURNING user_id")
        (user_id,) = cur.fetchone()
        cur.execute(
            "INSERT INTO sessions (sid, user_id, expires_at) "
            "SELECT 'sid-' || g, %s, now() + interval '1 day' FROM generate_series(1, 2000) g",
            (user_id,),
        )
        cur.execute(
            "INSERT INTO api_keys (key, owner_email) SELECT 'key-' || g, 'plan@test' FROM generate_series(1, 2000) g"
        )
        cur.execute("ANALYZE sessions")
        cur.execute("ANALYZE api_keys")
        cur.execute("SET enable_seqscan = off")
        cur.execute("SET plan_cache_mode = force_generic_plan")
        cur.execute(f"PREPARE legacy_lookup(text) AS {statement}")
        cur.execute("EXPLAIN EXECUTE legacy_lookup('x')")
        plan = "\n".join(r[0] for r in cur.fetchall())
    assert index in plan, plan


_COMPAT_OBJECTS_SQL = """
SELECT 'trigger:' || tgname FROM pg_trigger
 WHERE tgrelid IN ('sessions'::regclass, 'api_keys'::regclass) AND NOT tgisinternal
UNION ALL
SELECT 'function:' || proname FROM pg_proc
 WHERE proname IN ('sessions_fill_sid_hash', 'api_keys_fill_key_hash')
UNION ALL
SELECT 'index:' || indexname FROM pg_indexes
 WHERE indexname IN ('idx_sessions_sid', 'idx_api_keys_key')
"""

_COMPAT_OBJECTS = {
    "trigger:sessions_fill_sid_hash",
    "trigger:api_keys_fill_key_hash",
    "function:sessions_fill_sid_hash",
    "function:api_keys_fill_key_hash",
    "index:idx_sessions_sid",
    "index:idx_api_keys_key",
}


def _compat_objects(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_COMPAT_OBJECTS_SQL)
        return {r[0] for r in cur.fetchall()}


def test_down_then_up_round_trips():
    """`force_destructive` because the range is everything above 0061, and this
    database is thrown away between runs."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        assert _compat_objects(conn) == _COMPAT_OBJECTS
        conn.rollback()
        migrate_down("0061", conn, force_destructive=True)
        assert _compat_objects(conn) == set()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (email) VALUES ('rollback@test') RETURNING user_id")
            uid = cur.fetchone()[0]
            with pytest.raises(psycopg2.errors.NotNullViolation):
                cur.execute(
                    "INSERT INTO sessions (sid, user_id, expires_at) VALUES (%s, %s, now() + interval '1 day')",
                    ("raw-sid", uid),
                )
        conn.rollback()
    finally:
        migrate_up(conn)
    try:
        assert _compat_objects(conn) == _COMPAT_OBJECTS
    finally:
        conn.close()
