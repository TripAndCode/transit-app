"""Cross-process coordination via a Postgres advisory lock.

One lock domain today: ingest + analyze must not run twice concurrently
for the same agency's Postgres agg_* tables (each analyze() run does
DELETE FROM agg_* WHERE agency_id=... then re-INSERTs the same PKs in its
own transaction -- two concurrent runs for the same agency hit a
unique-violation once the winner commits, not a harmless no-op) or
double-insert the same ClickHouse poll (insert_updates' intra-batch dedup
and ingest_live's recent_file_name_exists guard only protect within one
process's own batch, not across two processes racing the same feed).

Deliberately ONE global key, not per-agency: a per-agency lock would let
two whole-fleet jobs (the cron endpoint and a scheduled CLI run)
interleave through their own agency loops, which is a more complex
primitive for a collision this single lock already prevents correctly.

Best-effort, not job-level atomicity: production (scripts/fetch_and_ingest.sh)
invokes ingest/load_static/analyze as separate per-agency CLI processes, each
independently acquiring and releasing this lock -- so a cron poke can still
land *between* two of those per-agency commands and run its own full
ingest+analyze in the gap. Callers (this module's docstring above notwithstanding
in older revisions) must treat a lock miss as ordinary, self-healing contention:
log and skip, never abort a larger loop over it -- see gtfs_pipeline.py's
_acquire_ingest_analyze_lock. Closing the interleave gap fully would need a
lock held for an entire multi-command job, not per CLI invocation; accepted
as a known trade-off rather than solved here.
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
