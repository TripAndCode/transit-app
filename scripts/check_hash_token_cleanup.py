"""Reports whether the raw `sessions.sid` / `api_keys.key` columns are safe to drop.

`db/migrations/0053_hash_tokens.up.sql` added `sid_hash`/`key_hash` primary keys
but left the raw columns in place and nullable, so a writer not yet updated to
the hash-only path can keep working during a rolling deploy. Those columns stop
protecting anything once every row already carries its hash -- once no writer
is still inserting a hash-less row -- at which point a follow-up migration can
drop them (see `db/migrations/README.md`).

Read-only: issues SELECTs only, against the DATABASE_URL given via --database-url
or the environment. Never modifies the database. Exits non-zero when the raw
columns still exist and no hash-less row remains (cleanup is due); exits 0
otherwise (either already cleaned up, or not yet safe to clean up).

Usage:
    poetry run python scripts/check_hash_token_cleanup.py
    poetry run python scripts/check_hash_token_cleanup.py --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import os
import sys

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

_HASHLESS_ROW_COUNTS_SQL = """
SELECT
    (SELECT count(*) FROM sessions WHERE sid_hash IS NULL) AS hashless_sessions,
    (SELECT count(*) FROM api_keys WHERE key_hash IS NULL) AS hashless_api_keys;
"""


def cleanup_is_due(
    *,
    sessions_sid_exists: bool,
    api_keys_key_exists: bool,
    hashless_sessions: int,
    hashless_api_keys: int,
) -> bool:
    """True once the raw columns can be dropped.

    Pure decision logic, kept apart from the SQL above so it is testable
    without a database: due when at least one raw column still exists and no
    row anywhere lacks its hash (every writer is already hash-aware).
    """
    columns_exist = sessions_sid_exists or api_keys_key_exists
    no_hashless_rows = hashless_sessions == 0 and hashless_api_keys == 0
    return columns_exist and no_hashless_rows


def check(database_url: str) -> bool:
    """Run the read-only checks against *database_url*. Returns cleanup_is_due()."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_COLUMN_EXISTS_SQL)
            sessions_sid_exists, api_keys_key_exists = cur.fetchone()
            cur.execute(_HASHLESS_ROW_COUNTS_SQL)
            hashless_sessions, hashless_api_keys = cur.fetchone()
    finally:
        conn.close()
    return cleanup_is_due(
        sessions_sid_exists=sessions_sid_exists,
        api_keys_key_exists=api_keys_key_exists,
        hashless_sessions=hashless_sessions,
        hashless_api_keys=hashless_api_keys,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="Defaults to $DATABASE_URL")
    args = parser.parse_args()

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print("check_hash_token_cleanup: DATABASE_URL is not set; refusing to run.", file=sys.stderr)
        return 2

    if check(database_url):
        print(
            "check_hash_token_cleanup: raw sessions.sid/api_keys.key still exist and every row "
            "already has a hash -- safe to write the follow-up migration that drops them."
        )
        return 1
    print("check_hash_token_cleanup: not due (columns already dropped, or hash-less rows remain).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
