"""Cross-process coordination via a Postgres advisory lock.

Two lock domains, distinguished by what the caller is about to write:

1. Whole-job ingest + analyze (``try_lock_ingest_analyze``). analyze() must
   not run twice concurrently for the same agency's Postgres agg_* tables
   (each run does DELETE FROM agg_* WHERE agency_id=... then re-INSERTs the
   same PKs in its own transaction -- two concurrent runs for the same agency
   hit a unique-violation once the winner commits, not a harmless no-op), and
   ingest must not double-insert the same ClickHouse poll (insert_updates'
   intra-batch dedup and ingest_live's recent_file_name_exists guard only
   protect within one process's own batch, not across two processes racing
   the same feed).

2. One agency's raw-poll append (``try_lock_agency_ingest``), used by the
   collector push endpoint. That path only appends one already-fetched
   protobuf to ClickHouse for a single agency; it touches no agg_* table and
   shares no feed with any other agency, so two agencies pushing at once is
   disjoint work that a single global key would serialize for nothing. The
   collector polls every agency on its own dense interval, so the global key
   turned every concurrent arrival into a 409 and dropped that poll -- the
   two keys exist to keep same-agency retries mutually exclusive without
   paying that cross-agency cost.

The two domains are deliberately NOT mutually exclusive: Postgres keeps
single-argument and two-argument advisory locks in separate spaces, so a
collector append can land while analyze() runs for that same agency. That is
survivable precisely because analyze() decides what to rebuild from a
per-date row-count ledger (agg_feed_health) rather than a high-water mark --
a date whose ClickHouse count moved after analyze() read it is named by the
next run's _dates_needing_rebuild and rebuilt then. The residual is one
analyze cycle of staleness for that date, self-healing, not a lost or
corrupted aggregate. Do not add a raw append to domain 1 to "fix" this: that
reintroduces the cross-agency serialization the split exists to remove.

Best-effort, not job-level atomicity: production (scripts/fetch_and_ingest.sh,
docs/deploy-railway.md's Railway sketch) invokes ingest/load_static/analyze
as separate per-agency CLI processes, each independently acquiring and
releasing this lock -- so a cron poke can still land *between* two of those
per-agency commands and run its own full ingest+analyze in the gap. Callers
must treat a lock miss on a single-agency command as ordinary, self-healing
contention (see gtfs_pipeline.py's _lock_or_skip_agency, EX_TEMPFAIL) rather
than a fatal error, since aborting the shell loop over it would skip every
agency after the collision -- worse than the race this lock prevents. The
whole-fleet commands (analyze_all, ingest_live) that nothing shell-loops over
still fail loudly on a miss (_lock_or_exit) per their own documented
contract. Closing the interleave gap fully would need a lock held for an
entire multi-command job, not per CLI invocation; accepted as a known
trade-off rather than solved here.

Also does not cover pipeline/static_loader.py's load_static(): its
DELETE+re-INSERT of static_stop_times etc. is one committed transaction, so
Postgres's READ COMMITTED isolation already keeps a concurrent analyze()
from seeing a half-replaced table -- the accepted residual is that analyze()
may read a static schedule version that is about to be superseded mid-run,
not table corruption.
"""

# Arbitrary, fixed -- only needs to be distinct from any other advisory
# lock this codebase takes, and there are none as of this writing. Reused as
# the first argument of the two-argument per-agency key so both domains stay
# traceable to one constant; the argument count, not the value, is what keeps
# them in separate lock spaces.
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
    sufficient and is the only supported way to release it here. `conn`'s
    autocommit setting doesn't matter -- callers use both (api/routers/
    internal.py acquires under autocommit=True; gtfs_pipeline.py's
    commands under the default autocommit=False) and the lock is
    session-, not transaction-, scoped either way.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        return cur.fetchone()[0]


def try_lock_agency_ingest(conn, agency_id: int) -> bool:
    """Non-blocking acquire of one agency's raw-append lock.

    Same session-level semantics and release-by-closing-`conn` contract as
    try_lock_ingest_analyze; only the scope differs. Two agencies never
    contend, and the caller must be appending raw polls only -- see this
    module's docstring for why that restriction is what makes the narrower
    scope sound, and why widening it would undo the point.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(%s, %s)",
            (INGEST_ANALYZE_LOCK_KEY, agency_id),
        )
        return cur.fetchone()[0]
