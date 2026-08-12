"""Cross-process coordination via a Postgres advisory lock.

One lock domain today: ingest + analyze must not run twice concurrently
for the same agency's Postgres agg_* tables (each analyze() run does
DELETE FROM agg_* WHERE agency_id=... then re-INSERTs the same PKs in its
own transaction -- two concurrent runs for the same agency hit a
unique-violation once the winner commits, not a harmless no-op) or
double-insert the same ClickHouse poll (insert_updates' intra-batch dedup
and ingest_live's recent_file_name_exists guard only protect within one
process's own batch, not across two processes racing the same feed).

Deliberately ONE global key, not per-agency: api/routers/internal.py's
cron endpoint already treats "ingest+analyze every agency" as one
all-or-nothing job, and this module's callers (that endpoint, plus
gtfs_pipeline.py's ingest/ingest_live/analyze/analyze_all commands) are
the production Railway job and its documented fallback -- a per-agency
lock would let those two interleave through their own agency loops,
which is a more complex primitive for a collision (two whole-fleet jobs
running at once) that a single lock already prevents correctly.
"""

# Arbitrary, fixed -- only needs to be distinct from any other advisory
# lock this codebase takes, and there are none as of this writing.
INGEST_ANALYZE_LOCK_KEY = 72710001


def try_lock_ingest_analyze(conn) -> bool:
    """Non-blocking acquire. Returns False if another process already holds it.

    Session-level (pg_try_advisory_lock, not the transaction-scoped
    pg_advisory_xact_lock): the lock must hold across every transaction the
    caller runs while holding it, not just one, and it must release on its
    own if the holding process dies without a chance to clean up (SIGKILL,
    OOM, a killed cron pod) -- Postgres releases every session-level
    advisory lock a session holds when that session's connection ends, so
    there is nothing for a caller to explicitly unlock; closing `conn` is
    sufficient and is the only supported way to release it here.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        return cur.fetchone()[0]
