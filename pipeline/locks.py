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

2. One agency's `updates` rows (``agency_ingest_lock`` for a holder that
   blocks for the duration of its work, and ``try_lock_agency_ingest`` for a
   caller that wants the same key with no wait at all -- a sibling, not a
   layer beneath it; each issues its own acquire).
   Held by promotion (pipeline/promote.py) around its copy from
   `updates_live` into `updates`, and by analyze() for the whole of its own
   run. The realtime writers (collector push, `ingest_live`) write
   `updates_live`, which analyze never reads, and take no per-agency key.
   Two *different* agencies never contend here: promotion's copy for one
   touches no row and no agg_* table the other reads, so a single global key
   serializing disjoint work would turn every concurrent fleet-wide
   promotion or analyze into needless blocking; the per-agency key keeps
   that contention scoped to genuine same-agency overlap.

Domain 2 covers analyze() as well as promotion's copy precisely BECAUSE
Postgres keeps single-argument and two-argument advisory locks in separate
spaces: holding domain 1 does not exclude a domain-2 write, so analyze() has
to take the agency's domain-2 key itself to get that exclusion back. It needs
it. analyze() reads `updates` from ClickHouse more than once per run at
different times -- the deduped fact slice is loaded into a TEMP TABLE early,
while agg_feed_health's raw per-date counts are a separate, later query -- and
promotion's copy landing between those two reads writes a ledger that is NEWER
than the aggregates it certifies. _dates_needing_rebuild compares that ledger
against the live count, finds them equal, and never re-lists the date, so the
other incremental tables stay permanently short those rows. Per-agency
exclusion, not the ledger, is what makes this safe; the ledger cannot detect
the one case that would need it to.

Do not narrow domain 2 back to promotion's copy alone, and do not move that
copy into domain 1: the first reopens the skew above, the second restores the
cross-agency serialization the split exists to remove.

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

import time
from collections.abc import Iterator
from contextlib import contextmanager

# Arbitrary, fixed -- only needs to be distinct from any other advisory
# lock this codebase takes, and there are none as of this writing. Reused as
# the first argument of the two-argument per-agency key so both domains stay
# traceable to one constant; the argument count, not the value, is what keeps
# them in separate lock spaces.
INGEST_ANALYZE_LOCK_KEY = 72710001


def try_lock_ingest_analyze_timed(conn) -> tuple[bool, int]:
    """:func:`try_lock_ingest_analyze`, plus what the attempt cost in ms.

    The acquire is non-blocking, so this is the round trip to Postgres and
    NOT time spent queueing behind the holder -- there is no queue. It is
    stored on the pipeline_runs row of a displaced job (pipeline/runs.py) so a
    run that did no work still records what finding that out cost; reading it
    as "how long this job waited for the lock" would overstate it.
    """
    started = time.monotonic()
    got = try_lock_ingest_analyze(conn)
    return got, round((time.monotonic() - started) * 1000)


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
    """Non-blocking acquire of one agency's `updates` lock.

    An instant answer with no wait at all, for a caller that can afford to
    walk away from the work outright rather than wait any bounded amount for
    it; the blocking form is `agency_ingest_lock`. No production caller
    takes this form today; `tests/pipeline/test_locks.py` uses it to pin the
    per-agency key's semantics, which is what keeps it honest.
    Same session-level semantics and release-by-closing-`conn` contract as
    try_lock_ingest_analyze; only the scope differs.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(%s, %s)",
            (INGEST_ANALYZE_LOCK_KEY, agency_id),
        )
        return cur.fetchone()[0]


@contextmanager
def agency_ingest_lock(conn, agency_id: int) -> Iterator[None]:
    """Hold one agency's `updates` lock for the duration of the block.

    Blocking, unlike try_lock_agency_ingest, and released on the way out
    rather than at connection close -- both because of who calls it.
    analyze() cannot skip an agency just because promotion is mid-flight (the
    result would be an agency that goes unanalyzed for a reason invisible in
    its output), and one long-lived connection analyzes every agency in turn,
    so a lock left to connection teardown would still be held for agency N
    while N+1..last are processed -- blocking that agency's promotion for the
    whole fleet's run, which is the contention this key exists to avoid.

    Waiting cannot deadlock: every holder (promotion, analyze) takes this key
    while holding at most INGEST_ANALYZE_LOCK_KEY's single-argument lock,
    which no holder of this key waits on.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s, %s)", (INGEST_ANALYZE_LOCK_KEY, agency_id))
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", (INGEST_ANALYZE_LOCK_KEY, agency_id))
