"""`db.migrate.data_losing_tables` reads which tables a migration loses data
from. Every down migration it flags must carry the `-- DESTRUCTIVE` line
`db.migrate` gates on, or `migrate down` runs it without being asked twice;
every up migration it flags stops scripts/dev_upgrade.py until the owner
names it."""

from __future__ import annotations

import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from db.migrate import _MIGRATIONS_DIR, data_losing_tables, is_destructive_down, rebuildable_agg_tables


def test_classifier_counts_tables_columns_and_rows_but_not_indexes_or_rebuildable_aggs():
    assert data_losing_tables("DROP TABLE IF EXISTS users;\n") == ["users"]
    assert data_losing_tables("DROP TABLE IF EXISTS agg_static_version_summary;") == ["agg_static_version_summary"]
    assert data_losing_tables("ALTER TABLE static_trips\n    DROP COLUMN IF EXISTS service_id;\n") == ["static_trips"]
    assert data_losing_tables(
        "DELETE FROM login_events WHERE kind = 'x';\nALTER TABLE login_events DROP CONSTRAINT c;"
    ) == ["login_events"]
    assert (
        data_losing_tables(
            "DROP INDEX IF EXISTS idx_x;\nDROP TRIGGER IF EXISTS t ON api_keys;\nDROP FUNCTION IF EXISTS f();"
        )
        == []
    )
    assert (
        data_losing_tables("DROP TABLE IF EXISTS agg_route_daily;\nALTER TABLE agg_meta DROP COLUMN IF EXISTS x;") == []
    )
    assert data_losing_tables("ALTER TABLE agg_route_dow DROP CONSTRAINT agg_route_dow_pkey;") == []
    assert data_losing_tables("-- also mentions DROP TABLE users in prose\nSELECT 1;") == []


def test_classifier_reads_only_comma_lists_and_quoted_or_qualified_names():
    assert data_losing_tables("ALTER TABLE ONLY users DROP COLUMN email;") == ["users"]
    assert data_losing_tables("ALTER TABLE only_users DROP COLUMN email;") == ["only_users"]
    assert data_losing_tables("DROP TABLE agg_route_daily, users;") == ["users"]
    assert data_losing_tables("TRUNCATE sessions, agg_meta, api_keys;") == ["sessions", "api_keys"]
    assert data_losing_tables('DROP TABLE IF EXISTS "Users";') == ["Users"]
    assert data_losing_tables("DELETE FROM ONLY public.login_events WHERE true;") == ["login_events"]
    assert data_losing_tables('ALTER TABLE public."agg_route_daily" DROP COLUMN y;') == []


@pytest.mark.parametrize(
    ("sql", "lost"),
    [
        ("SET DEFAULT 'a -- b'; DROP TABLE t;", ["t"]),
        ("ALTER TABLE t ALTER COLUMN c SET DEFAULT 'a -- b'; DROP TABLE users;", ["users"]),
        ("ALTER TABLE t ALTER COLUMN c SET DEFAULT 'a /* b'; DROP TABLE users; -- */", ["users"]),
        ("/* header */ DROP TABLE users;", ["users"]),
        ("DROP/**/TABLE users;", ["users"]),
        ("/* outer /* nested */ still the comment; DROP TABLE users; */ SELECT 1;", []),
        ("SELECT 'x; DROP TABLE users';", []),
        ("ALTER TABLE users ALTER COLUMN c SET DEFAULT 'drop column';", []),
        ("SELECT E'it\\'s -- not a comment; '; DROP TABLE users;", ["users"]),
        ("E'\\' ; DROP TABLE users; '", []),
        (
            "CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$\n"
            "BEGIN PERFORM 1; DELETE FROM users; RETURN NULL; END\n$$;",
            [],
        ),
        (
            "CREATE FUNCTION f() RETURNS text LANGUAGE sql AS $body$ SELECT 'it''s -- ;' $body$; DROP TABLE users;",
            ["users"],
        ),
        ("DO $$ BEGIN RAISE NOTICE 'don''t'; END $$; DELETE FROM sessions;", ["sessions"]),
        ("CREATE TABLE s (id TEXT CHECK (id ~ '^[a-z]{2,64}$')); TRUNCATE users;", ["users"]),
        ("CREATE TABLE t$a$ (x INT); DROP TABLE users;", ["users"]),
        ("PREPARE q(int) AS SELECT $1; DROP TABLE users;", ["users"]),
        ('DROP TABLE "semi;colon", users;', ["semi;colon", "users"]),
    ],
)
def test_classifier_reads_past_quotes_comments_and_dollar_quoted_bodies(sql: str, lost: list[str]):
    assert data_losing_tables(sql) == lost


@pytest.mark.parametrize(
    ("sql", "lost"),
    [
        ("ALTER TABLE users DROP email;", ["users"]),
        ("ALTER TABLE users DROP IF EXISTS email;", ["users"]),
        ("ALTER TABLE users ALTER COLUMN a DROP DEFAULT, DROP COLUMN b;", ["users"]),
        ("ALTER TABLE users ALTER COLUMN a DROP DEFAULT, ALTER COLUMN b DROP NOT NULL, DROP CONSTRAINT k;", []),
        ("ALTER TABLE users ALTER COLUMN a DROP EXPRESSION, ALTER COLUMN b DROP IDENTITY IF EXISTS;", []),
        ("ALTER TABLE users ADD COLUMN drop_count INT;", []),
        ("CREATE TABLE t (a INT REFERENCES agencies(agency_id) ON DELETE CASCADE ON UPDATE CASCADE);", []),
        ("UPDATE users SET email = lower(email);", []),
    ],
)
def test_classifier_counts_column_drops_but_not_other_alters_fk_actions_or_updates(sql: str, lost: list[str]):
    assert data_losing_tables(sql) == lost


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM agg_route_daily WHERE date < '2025-01-01';",
        "TRUNCATE agg_route_daily;",
        "ALTER TABLE agg_route_daily DROP COLUMN late_samples;",
        "ALTER TABLE agg_route_daily DROP late_samples;",
        "DROP TABLE agg_route_daily;",
        "ALTER TABLE agg_route_daily ADD COLUMN early_samples INT NOT NULL DEFAULT 0;",
        "ALTER TABLE IF EXISTS ONLY public.agg_route_daily ADD early_samples INT;",
        "ALTER TABLE agg_route_daily  DROP  late_samples;",
        "ALTER TABLE agg_route_daily\n    DROP COLUMN late_samples;",
        "ALTER TABLE agg_route_daily  ADD  early_samples INT;",
        "ALTER TABLE agg_route_daily\n    ADD COLUMN early_samples INT;",
        "ALTER TABLE agg_route_daily ALTER COLUMN samples SET NOT NULL, ADD COLUMN early_samples INT;",
        "ALTER TABLE agg_route_daily ALTER COLUMN samples TYPE numeric(10, 2), DROP late_samples;",
        "CREATE TABLE agg_route_daily (agency_id INT);",
        "CREATE TABLE IF NOT EXISTS public.agg_route_daily (agency_id INT);",
    ],
)
def test_a_retention_floor_makes_a_rebuildable_aggregate_lose_data(sql):
    """Below the floor analyze rebuilds nothing, so a rebuildable aggregate's
    rows there, or a column or a table analyze would fill, are restored by
    nothing."""
    assert data_losing_tables(sql) == []
    assert data_losing_tables(sql, retention_floor=True) == ["agg_route_daily"]


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE agg_route_daily ADD CONSTRAINT c CHECK (samples >= 0);",
        "ALTER TABLE agg_route_daily ADD PRIMARY KEY (agency_id, date);",
        "ALTER TABLE agg_route_daily ADD UNIQUE (agency_id, date);",
        "ALTER TABLE agg_route_daily ADD FOREIGN KEY (agency_id) REFERENCES agencies;",
        "ALTER TABLE agg_route_daily ALTER COLUMN samples SET DEFAULT 0;",
        "CREATE INDEX idx_agg_route_daily_x ON agg_route_daily (date);",
        "ALTER TABLE users ADD COLUMN nickname TEXT;",
        "UPDATE agg_route_daily SET samples = 0 WHERE samples IS NULL;",
        "ALTER TABLE agg_route_daily ADD  CONSTRAINT c CHECK (samples >= 0);",
        "ALTER TABLE agg_route_daily ADD\n    CONSTRAINT c CHECK (samples >= 0);",
        "ALTER TABLE agg_route_daily DROP  CONSTRAINT c;",
        "ALTER TABLE agg_route_daily DROP\n    CONSTRAINT c;",
        "ALTER TABLE agg_route_daily ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY;",
        "ALTER TABLE agg_route_daily ALTER COLUMN samples DROP DEFAULT;",
        "ALTER TABLE users DROP  CONSTRAINT users_pkey;",
        "ALTER TABLE users\n    DROP CONSTRAINT users_pkey;",
        "ALTER TABLE users ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY;",
        "ALTER TABLE users ALTER COLUMN email DROP NOT NULL;",
        "CREATE TABLE users_archive (user_id INT);",
    ],
)
def test_a_retention_floor_leaves_constraints_indexes_and_other_tables_columns_alone(sql):
    assert data_losing_tables(sql) == []
    assert data_losing_tables(sql, retention_floor=True) == []


def test_a_retention_floor_keeps_the_tables_already_counted():
    assert data_losing_tables("DROP TABLE users, agg_meta;", retention_floor=True) == ["users", "agg_meta"]


UP_FILES = sorted(_MIGRATIONS_DIR.glob("*.up.sql"))


def test_the_up_migrations_that_lose_data_are_pinned():
    """scripts/dev_upgrade.py stops on each of these until the owner names it,
    so a classifier change, or a new up migration that loses data, shows up
    here for review. 0045 and 0047 delete stored LLM keys of a provider the app
    stopped supporting, and 0108 removes the reader accounts and everything
    stored for them: user data no rebuild restores."""
    flagged = {path.name: data_losing_tables(path.read_text()) for path in UP_FILES}
    assert {name: lost for name, lost in flagged.items() if lost} == {
        "0045_widen_user_llm_keys_providers.up.sql": ["user_llm_keys"],
        "0047_narrow_user_llm_keys_providers.up.sql": ["user_llm_keys"],
        "0108_drop_reader_tables.up.sql": [
            "filter_presets",
            "user_llm_keys",
            "user_invites",
            "api_keys",
            "login_events",
            "users",
            "user_activity_daily",
            "user_activity_daily",
            "feature_flags",
        ],
    }


DOWN_FILES = sorted(_MIGRATIONS_DIR.glob("*.down.sql"))


def _stems(suffix: str) -> set[str]:
    return {p.name.removesuffix(suffix) for p in _MIGRATIONS_DIR.glob(f"*{suffix}")}


def test_every_up_migration_has_a_down_file():
    assert DOWN_FILES
    assert _stems(".up.sql") == _stems(".down.sql")


@pytest.mark.parametrize("path", DOWN_FILES, ids=lambda p: p.name)
def test_a_data_losing_down_migration_carries_the_marker(path: Path):
    sql = path.read_text()
    lost = data_losing_tables(sql)
    if lost:
        assert is_destructive_down(sql), f"{path.name} loses data in {lost} but has no `-- DESTRUCTIVE` line"


# Classifying one statement is linear in its length: migrate up classifies
# every pending migration on each deploy. A pattern that backtracks
# exponentially on a statement it does not match runs for hours instead.
_CLASSIFY_SECONDS = 0.5


class _TooSlow(Exception):
    pass


@contextmanager
def _within(seconds: float) -> Iterator[None]:
    """Fail, rather than hang, once *seconds* pass."""

    def _expired(signum, frame):
        raise _TooSlow(f"still classifying after {seconds}s")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(scope="module")
def _rebuildable_loaded():
    """The rebuildable set imports analyze once; that is not classification."""
    rebuildable_agg_tables()


@pytest.mark.parametrize("path", UP_FILES + DOWN_FILES, ids=lambda p: p.name)
def test_every_migration_classifies_in_linear_time(path: Path, _rebuildable_loaded):
    sql = path.read_text()
    start = time.monotonic()
    with _within(_CLASSIFY_SECONDS * 4):
        data_losing_tables(sql)
        data_losing_tables(sql, retention_floor=True)
    assert time.monotonic() - start < _CLASSIFY_SECONDS


def test_an_alter_of_many_subcommands_that_lose_nothing_classifies_in_linear_time(_rebuildable_loaded):
    sql = (
        "ALTER TABLE agg_route_daily\n"
        + ",\n".join(f"    ADD CONSTRAINT c{i} CHECK (samples >= {i})" for i in range(30))
        + ";"
    )
    with _within(_CLASSIFY_SECONDS):
        assert data_losing_tables(sql) == []
        assert data_losing_tables(sql, retention_floor=True) == []
