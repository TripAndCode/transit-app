"""Reports whether the raw `sessions.sid` column is safe to drop.

`db/migrations/0053_hash_tokens.up.sql` moved sessions onto a `sid_hash`
primary key. The raw column survives, nullable, holding the session ids of
rows that predate it; the application never reads or writes it, and its new
rows leave it NULL. It exists only so that rolling 0053 back keeps those older
sessions working. Invariant for the follow-up migration that drops it: it may
run once no row holding a raw value is still resolvable, since until then a
rollback still has live sessions to preserve.

"Resolvable" mirrors the acceptance rule enforced for a session (a session
whose `expires_at` is in the future, `api/middleware/session.py`). A suspended
user does not exclude a row: suspension is reversible, and lifting it makes
that user's sessions work again.

"Due" answers only whether the column may go. The drop migration must also
remove what `0062_hash_tokens_legacy_compat` installed on it -- its insert
trigger reads it, so leaving that in place makes every insert into `sessions`
fail -- and a due report names whichever of those objects remain.

Read-only: issues SELECTs only, against the DATABASE_URL given via --database-url
or the environment. Never modifies the database. Exits non-zero when the raw
column still exists and no resolvable row holds a raw value (cleanup is due);
exits 0 otherwise (either already cleaned up, or not yet safe to clean up).

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
SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
     WHERE table_name = 'sessions' AND column_name = 'sid'
);
"""

_LIVE_RAW_SESSIONS_SQL = """
SELECT count(*) FROM sessions
 WHERE sid IS NOT NULL
   AND expires_at > now();
"""

# What 0062_hash_tokens_legacy_compat adds on top of the raw column.
_COMPAT_OBJECTS_SQL = """
SELECT 'trigger ' || t.tgname FROM pg_trigger t
 WHERE NOT t.tgisinternal AND t.tgname = 'sessions_fill_sid_hash'
UNION ALL
SELECT 'function ' || p.proname || '()' FROM pg_proc p
 WHERE p.proname = 'sessions_fill_sid_hash'
UNION ALL
SELECT 'index ' || c.relname FROM pg_class c
 WHERE c.relkind = 'i' AND c.relname = 'idx_sessions_sid'
ORDER BY 1;
"""


def cleanup_is_due(live_raw_sessions: int | None) -> bool:
    """True when the raw column remains and no resolvable row holds a raw value."""
    return live_raw_sessions == 0


def read_live_raw_sessions(cur) -> int | None:
    """Resolvable sessions still holding a raw id; ``None`` once the column is
    dropped, since counting would fail on it."""
    cur.execute(_COLUMN_EXISTS_SQL)
    if not cur.fetchone()[0]:
        return None
    cur.execute(_LIVE_RAW_SESSIONS_SQL)
    return cur.fetchone()[0]


def read_compat_objects(cur) -> list[str]:
    """The 0062 compatibility objects still present, which the drop migration must remove first."""
    cur.execute(_COMPAT_OBJECTS_SQL)
    return [row[0] for row in cur.fetchall()]


def due_message(compat_objects: list[str]) -> str:
    message = (
        "check_hash_token_cleanup: raw sessions.sid remains but no resolvable row "
        "holds a raw value -- safe to write the follow-up migration that drops it."
    )
    if compat_objects:
        message += (
            " That migration must first drop, or drop with it, what 0062_hash_tokens_legacy_compat "
            f"installed on that column: {', '.join(compat_objects)} "
            '(db/migrations/README.md, "Compatibility with releases that predate 0053").'
        )
    return message


def check(database_url: str) -> tuple[int | None, list[str]]:
    """Run the read-only checks against *database_url*."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            return read_live_raw_sessions(cur), read_compat_objects(cur)
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

    live_raw_sessions, compat_objects = check(database_url)
    if cleanup_is_due(live_raw_sessions):
        print(due_message(compat_objects))
        return 1
    if live_raw_sessions is None:
        print("check_hash_token_cleanup: not due (raw column already dropped).")
        return 0
    print(f"check_hash_token_cleanup: not due -- resolvable sessions still holding a raw id: {live_raw_sessions}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
