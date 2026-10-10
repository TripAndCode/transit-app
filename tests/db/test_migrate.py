import os

import psycopg2
import pytest

from db.migrate import migrate_down, migrate_up

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="module", autouse=True)
def ensure_migrated():
    """Run migrate_up so schema_migrations is populated before any test in this module."""
    conn = psycopg2.connect(DATABASE_URL)
    migrate_up(conn)
    conn.close()


def test_migrate_up_creates_schema_migrations_table(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'schema_migrations' ORDER BY column_name
        """)
        cols = {r[0] for r in cur.fetchall()}
    assert {"version", "applied_at"} <= cols


def test_migrate_up_records_all_versions(pg_conn):
    """Every up migration on disk must have a row in schema_migrations.

    Asserting against the disk listing instead of a hardcoded list means
    new migrations don't break this test.
    """
    from db.migrate import _versions_on_disk

    expected = sorted(_versions_on_disk())
    with pg_conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [r[0] for r in cur.fetchall()]
    assert versions == expected


def test_migrate_up_idempotent(pg_conn):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_up(conn)  # already up to date — should print message and return
    finally:
        conn.close()


def test_migrate_down_and_up(pg_conn):
    """Round-trip: roll back to "0002", then migrate_up restores everything.

    The previous version pinned itself to "0003 is the latest" and broke
    every time a new migration landed (and worse, left the schema
    half-applied for downstream tests). Using target="0002" keeps the
    rollback deterministic regardless of how many migrations exist
    above it; the unconditional migrate_up in `finally` ensures a clean
    schema even if the assertions fail.

    `force_destructive=True` because the range spans every `-- DESTRUCTIVE`
    down migration there is, and this database is thrown away between runs --
    which is the case that flag exists for. Without it the gate refuses the
    whole rollback, correctly: on anything but a disposable database, losing
    what 0053 and 0056 discard is what it is there to prevent.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_down("0002", conn, force_destructive=True)  # roll back everything above 0002
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='api_keys'")
            assert cur.fetchone() is None, "api_keys (0003) should be gone after rollback"
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            versions = [r[0] for r in cur.fetchall()]
        assert versions == ["0001", "0002"]
    finally:
        # Always restore the schema even if the assertions above failed,
        # so downstream tests in the session see a fully-migrated DB.
        migrate_up(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='api_keys'")
            assert cur.fetchone() is not None, "api_keys table should exist after migrate_up"
        conn.close()


def test_migration_0006_adds_strategy_columns(pg_conn):
    """0006 adds ingest_strategy + static_strategy on agencies; loosens updates NOT NULL."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'agencies'
              AND column_name IN ('ingest_strategy', 'static_strategy')
            ORDER BY column_name
        """)
        rows = cur.fetchall()
    assert rows == [
        ("ingest_strategy", "YES"),
        ("static_strategy", "YES"),
    ]

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'updates'
              AND column_name IN ('service_type', 'scheduled_time', 'route_code')
            ORDER BY column_name
        """)
        rows = cur.fetchall()
    # all three must be nullable after 0006
    assert all(is_nullable == "YES" for _, is_nullable in rows), rows


def test_migration_0007_adds_service_id_to_static_trips(pg_conn):
    """0007 adds service_id TEXT (nullable) to static_trips."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_name = 'static_trips' AND column_name = 'service_id'
        """)
        rows = cur.fetchall()
    assert rows == [("service_id", "YES", "text")]


def test_migration_0053_backfill_digest_matches_token_hash(pg_conn):
    """0053 digests credentials that predate it exactly as ``api.security.token_hash`` does.

    Each raw value mixes a backslash escape with non-ASCII text: a ``::bytea``
    cast would decode the escape, and any encoding other than UTF-8 would
    change the non-ASCII bytes, either way yielding a digest no request ever
    matches. Only 0053 is applied on top of 0052, so later migrations cannot
    change what this checks.
    """
    from api.security import token_hash
    from db.migrate import _run_up

    raw_sid = r"sid\101\\-セッション-é"
    raw_key = r"key\\-鍵-ü"
    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_down("0052", conn, force_destructive=True)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (email) VALUES ('backfill@example.com') RETURNING user_id")
            (user_id,) = cur.fetchone()
            cur.execute(
                "INSERT INTO sessions (sid, user_id, expires_at) VALUES (%s, %s, now() + interval '1 day')",
                (raw_sid, user_id),
            )
            cur.execute("INSERT INTO api_keys (key, owner_email) VALUES (%s, 'backfill@example.com')", (raw_key,))
        conn.commit()

        _run_up("0053", conn)

        with conn.cursor() as cur:
            cur.execute("SELECT max(version) FROM schema_migrations")
            assert cur.fetchone() == ("0053",)
            cur.execute("SELECT sid, sid_hash FROM sessions")
            assert cur.fetchall() == [(raw_sid, token_hash(raw_sid))]
            cur.execute("SELECT key, key_hash FROM api_keys")
            assert cur.fetchall() == [(raw_key, token_hash(raw_key))]
    finally:
        conn.rollback()
        migrate_up(conn)
        conn.close()


def test_migration_0015_down_dedups_signatures_shared_across_agencies():
    """The up migration scopes ask_intent_cache per agency because
    signature_hash is agency-agnostic, so two agencies can hold one signature.
    The down must collapse those to the single-column key instead of failing
    on a unique violation, and is marked destructive because it drops rows."""
    from db.migrate import DestructiveMigrationError

    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_down("0015", conn, force_destructive=True)  # leave 0015 itself applied
        with conn.cursor() as cur:
            agencies = []
            for name in ("down-dedup-a", "down-dedup-b"):
                cur.execute(
                    "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
                    (name, f"http://{name}.example.com"),
                )
                agencies.append(cur.fetchone()[0])
            for agency_id, hits in zip(agencies, (1, 9), strict=True):
                cur.execute(
                    "INSERT INTO ask_intent_cache "
                    "(signature_hash, tool, args, confidence, hit_count, last_question, agency_id) "
                    "VALUES ('sig0000000000001', 't', '{}', 0.9, %s, 'q', %s)",
                    (hits, agency_id),
                )
        conn.commit()
        with pytest.raises(DestructiveMigrationError):
            migrate_down("0014", conn)
        migrate_down("0014", conn, force_destructive=True)
        with conn.cursor() as cur:
            cur.execute("SELECT hit_count FROM ask_intent_cache WHERE signature_hash = 'sig0000000000001'")
            assert cur.fetchall() == [(9,)], "the most-used row survives the dedup"
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ask_intent_cache WHERE signature_hash = 'sig0000000000001'")
            cur.execute("DELETE FROM agencies WHERE agency_name LIKE 'down-dedup-%'")
        conn.commit()
        migrate_up(conn)
        conn.close()
