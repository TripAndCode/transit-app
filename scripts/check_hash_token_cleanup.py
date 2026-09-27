"""Reports whether the raw `sessions.sid` / `api_keys.key` columns are safe to drop.

`db/migrations/0053_hash_tokens.up.sql` moved both tables onto `sid_hash` /
`key_hash` primary keys. The raw columns survive, nullable, holding the
credentials of rows that predate it; the application never reads or writes
them, and its new rows leave them NULL. They exist only so that rolling 0053
back keeps those older credentials working. Invariant for the follow-up
migration that drops them: it may run once no row holding a raw value is still
resolvable, since until then a rollback still has live credentials to preserve.

"Resolvable" mirrors what the auth middleware accepts: a session whose
`expires_at` is in the future (`api/middleware/session.py`), an API key that
is neither revoked nor past its expiry (`api/middleware/auth.py`). A
suspended user does not exclude a row: suspension is reversible, and lifting
it makes that user's credentials work again.

"Due" answers only whether the credentials may go. The drop migration must
also remove what `0062_hash_tokens_legacy_compat` installed on those columns --
its insert triggers read them, so leaving one in place makes every insert into
its table fail -- and a due report names whichever of those objects remain.

Read-only: issues SELECTs only, against the DATABASE_URL given via --database-url
or the environment. Never modifies the database. Exits non-zero when at least
one raw column still exists and no resolvable row holds a raw value (cleanup is
due); exits 0 otherwise (either already cleaned up, or not yet safe to clean up).

Usage:
    poetry run python scripts/check_hash_token_cleanup.py
    poetry run python scripts/check_hash_token_cleanup.py --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import NamedTuple

import psycopg2

_COLUMN_EXISTS_SQL = """
SELECT
    EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'sessions' AND column_name = 'sid'
    ) AS sessions_sid_exists,
    EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'api_keys' AND column_name = 'key'
    ) AS api_keys_key_exists;
"""

_LIVE_RAW_SESSIONS_SQL = """
SELECT count(*) FROM sessions
 WHERE sid IS NOT NULL
   AND expires_at > now();
"""

_LIVE_RAW_API_KEYS_SQL = """
SELECT count(*) FROM api_keys
 WHERE key IS NOT NULL
   AND revoked_at IS NULL
   AND (expires_at IS NULL OR expires_at > now());
"""


# What 0062_hash_tokens_legacy_compat adds on top of the raw columns.
_COMPAT_OBJECTS_SQL = """
SELECT 'trigger ' || t.tgname FROM pg_trigger t
 WHERE NOT t.tgisinternal AND t.tgname IN ('sessions_fill_sid_hash', 'api_keys_fill_key_hash')
UNION ALL
SELECT 'function ' || p.proname || '()' FROM pg_proc p
 WHERE p.proname IN ('sessions_fill_sid_hash', 'api_keys_fill_key_hash')
UNION ALL
SELECT 'index ' || c.relname FROM pg_class c
 WHERE c.relkind = 'i' AND c.relname IN ('idx_sessions_sid', 'idx_api_keys_key')
ORDER BY 1;
"""


class CleanupState(NamedTuple):
    """Resolvable rows still holding a raw credential; ``None`` once that raw column is dropped."""

    live_raw_sessions: int | None
    live_raw_api_keys: int | None


def cleanup_is_due(state: CleanupState) -> bool:
    """True when a raw column remains and no resolvable row holds a raw value in any remaining one."""
    remaining = [n for n in state if n is not None]
    return bool(remaining) and all(n == 0 for n in remaining)


def _count(cur, query: str) -> int:
    cur.execute(query)
    return cur.fetchone()[0]


def read_state(cur) -> CleanupState:
    """A dropped raw column reads as ``None``; it is never counted, since the query would fail on it."""
    cur.execute(_COLUMN_EXISTS_SQL)
    sessions_sid_exists, api_keys_key_exists = cur.fetchone()
    return CleanupState(
        live_raw_sessions=_count(cur, _LIVE_RAW_SESSIONS_SQL) if sessions_sid_exists else None,
        live_raw_api_keys=_count(cur, _LIVE_RAW_API_KEYS_SQL) if api_keys_key_exists else None,
    )


def read_compat_objects(cur) -> list[str]:
    """The 0062 compatibility objects still present, which the drop migration must remove first."""
    cur.execute(_COMPAT_OBJECTS_SQL)
    return [row[0] for row in cur.fetchall()]


def due_message(compat_objects: list[str]) -> str:
    message = (
        "check_hash_token_cleanup: raw sessions.sid/api_keys.key remain but no resolvable row "
        "holds a raw value -- safe to write the follow-up migration that drops them."
    )
    if compat_objects:
        message += (
            " That migration must first drop, or drop with them, what 0062_hash_tokens_legacy_compat "
            f"installed on those columns: {', '.join(compat_objects)} "
            '(db/migrations/README.md, "Compatibility with releases that predate 0053").'
        )
    return message


def check(database_url: str) -> tuple[CleanupState, list[str]]:
    """Run the read-only checks against *database_url*."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            return read_state(cur), read_compat_objects(cur)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="Defaults to $DATABASE_URL")
    args = parser.parse_args()

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print("check_hash_token_cleanup: DATABASE_URL is not set; refusing to run.", file=sys.stderr)
        return 2

    state, compat_objects = check(database_url)
    if cleanup_is_due(state):
        print(due_message(compat_objects))
        return 1
    if state.live_raw_sessions is None and state.live_raw_api_keys is None:
        print("check_hash_token_cleanup: not due (raw columns already dropped).")
        return 0
    sessions, api_keys = ("column dropped" if n is None else n for n in state)
    print(
        "check_hash_token_cleanup: not due -- resolvable rows still holding a raw credential: "
        f"sessions={sessions}, api_keys={api_keys}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
