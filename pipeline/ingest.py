"""GTFS-RT ingest router.

Looks up the ingest strategy for an agency and delegates pb decoding to it.
The router owns: file iteration (tarballs + loose .pb), captured_at
derivation, dedup against the updates table, and bulk INSERT.
"""

import logging
import pathlib
import re
import tarfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from clickhouse_connect.driver.exceptions import DataError

from pipeline.clickhouse import distinct_file_names, insert_updates, recent_file_name_exists
from pipeline.strategies import get_ingest_strategy

# ── Re-exports for back-compat (existing tests import these) ──────────────────
from pipeline.strategies._pb import _dec, _fields, _read_ld, _read_varint, _ts  # noqa: F401
from pipeline.strategies.aomori_regex import (
    _TRIP_RE_DEFAULT,
    parse_trip_id,
)
from pipeline.url_guard import _redact_url, safe_urlopen

logger = logging.getLogger(__name__)

# Flush a ClickHouse insert after accumulating this many rows across files —
# large enough that per-insert overhead (~100ms fixed cost, confirmed
# empirically: 50x20-row inserts = 4.98s total vs. 1x1000-row insert = 0.10s)
# is amortized across thousands of rows instead of dozens, small enough to
# keep a single flush's memory footprint and latency modest even for a dense
# agency's file. See Task 8.9 — one-insert-per-source-file previously made a
# real agency-1 backfill run 7h36m wall-clock for 14m52s of actual CPU work.
_BATCH_ROWS = 20_000

# YYYYMMDD path segment (e.g. tar member dir or .pb parent dir). Used to
# derive captured_at when the filename alone doesn't carry the date.
_DATE_DIR_RE = re.compile(r"\d{8}")


def _date_dir(name: str) -> str:
    """Return *name* if it is a YYYYMMDD token, otherwise ``""``."""
    return name if _DATE_DIR_RE.fullmatch(name) else ""


@contextmanager
def _savepoint(cur, name: str) -> Iterator[None]:
    """Run a block as a Postgres SAVEPOINT, isolating its failure from the
    surrounding (uncommitted) transaction: on success the savepoint is
    released; on an exception its writes are rolled back, then it's
    released too, and the exception re-raised for the caller's own
    try/except to log and count.

    Used to isolate one bad tarball member / loose ``.pb`` file from every
    other member/file already inserted since the last periodic
    ``conn.commit()`` — a bare ``conn.rollback()`` on one bad item would
    discard ALL of them, not just the failing one.

    RELEASE is issued on both paths because ``ROLLBACK TO SAVEPOINT``
    undoes the writes but does not destroy the savepoint itself; without
    it, the next iteration's same-named ``SAVEPOINT`` would stack on top
    of the still-live one instead of replacing it. That stacking is what
    RELEASE actually prevents — it does *not*, by itself, keep Postgres's
    64-entry per-backend subtransaction cache from filling up on a long
    run of *successful* items in one commit window (each successful
    SAVEPOINT still holds a slot until the next top-level commit). This
    pipeline's read endpoints serve from precomputed ``agg_*`` tables, not
    live scans of ``updates`` (see CLAUDE.md), so the resulting
    pg_subtrans-lookup overhead on concurrent readers is low-impact here;
    lowering the commit cadence below 64 items would close that specific
    gap but cost more frequent fsyncs, so it's accepted as-is rather than
    tuned for a cost with no observed impact.
    """
    cur.execute(f"SAVEPOINT {name}")
    try:
        yield
    except Exception:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        raise
    finally:
        cur.execute(f"RELEASE SAVEPOINT {name}")


def _resolve_strategy_name(agency_id: int, conn) -> str:
    """Return the ingest strategy name for an agency, falling back to aomori_regex."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ingest_strategy FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return "aomori_regex"  # back-compat for un-migrated agencies


def parse_pb(
    raw: bytes,
    captured_at: str,
    file_name: str,
    pattern: re.Pattern | None = None,
) -> list:
    """Back-compat wrapper used by the regression test and a few unit tests.

    Returns the legacy 12-tuple shape:
      (file_name, captured_at, trip_id, service, sched, route,
       stop_seq, stop_id, arr_delay, arr_time, dep_delay, dep_time)

    Note: the live ingest path no longer calls this function. New code should
    call the strategy module directly.
    """
    from pipeline.strategies._pb import _dec as _d
    from pipeline.strategies._pb import _fields as _f

    pat = pattern or _TRIP_RE_DEFAULT
    rows: list[tuple] = []
    try:
        top = _f(raw)
    except Exception:
        return rows
    for ent_bytes in top.get(2, []):
        ent = _f(ent_bytes)
        if 3 not in ent:
            continue
        tu = _f(ent[3][0])
        trip_id = None
        if 1 in tu:
            trip = _f(tu[1][0])
            if 1 in trip:
                trip_id = _d(trip[1][0])
        if not trip_id:
            continue
        parsed = parse_trip_id(trip_id, pattern=pat)
        if parsed is None:
            continue
        service = parsed.get("service")
        hour = parsed.get("hour", "")
        minute = parsed.get("minute", "")
        sched = f"{hour.zfill(2)}:{minute.zfill(2)}" if hour and minute else None
        route = parsed.get("route")
        for stu_bytes in tu.get(2, []):
            stu = _f(stu_bytes)
            stop_seq = stu.get(1, [None])[0]
            stop_id = None
            if 4 in stu:
                stop_id = _d(stu[4][0])
            arr_delay = arr_time = dep_delay = dep_time = None
            if 2 in stu:
                arr = _f(stu[2][0])
                arr_delay = arr.get(1, [None])[0]
                arr_time = arr.get(2, [None])[0]
            if 3 in stu:
                dep = _f(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
                dep_time = dep.get(2, [None])[0]
            rows.append(
                (
                    file_name,
                    captured_at,
                    trip_id,
                    service,
                    sched,
                    route,
                    stop_seq,
                    stop_id,
                    arr_delay,
                    arr_time,
                    dep_delay,
                    dep_time,
                )
            )
    return rows


def ingest(folder: str, agency_id: int, conn, ch_client) -> int:
    """Ingest all .pb files from tarballs and loose files in folder.

    Dispatches to the agency's ingest strategy. Returns total rows attempted.
    """
    root = pathlib.Path(folder)
    n_errors = 0
    n_inserted = 0

    done = distinct_file_names(ch_client, agency_id)

    # `done` is only updated by _flush() (every _BATCH_ROWS rows, Task 8.9),
    # so a file buffered but not yet flushed is invisible to any dedup check
    # against `done` alone. `seen` closes that gap: every file key is added
    # to it the instant it's buffered (added to pending_files), not once it's
    # flushed, so the same run can never buffer the same key twice - even
    # across two tarballs, or between the tarball loop and the loose-.pb
    # loop. Seeded from `done` so files already ingested in a PRIOR run are
    # still skipped from the very first check.
    #
    # `seen` and `done` deliberately diverge on a failed flush: a failing
    # file stays in `seen` (so it isn't re-buffered later in THIS run -
    # re-parsing it again would just duplicate the failure accounting, not
    # help) but is absent from `done` (so the NEXT ingest() run, which
    # recomputes `done` fresh from ClickHouse, retries it - see _flush()'s
    # docstring for that crash-safety invariant). _flush() retries a failed
    # whole-batch insert file-by-file, so this divergence is scoped to just
    # the file(s) that actually failed on retry, not every file that shared
    # the batch with them.
    seen = set(done)

    strategy_name = _resolve_strategy_name(agency_id, conn)
    strategy = get_ingest_strategy(strategy_name)

    # Rows accumulate here across BOTH the tarball loop and the loose-.pb
    # loop below (shared, not reset between them) and are flushed to
    # ClickHouse in one INSERT per _BATCH_ROWS-sized batch instead of one
    # per source file (Task 8.9 — see _BATCH_ROWS docstring above for why).
    #
    # Crash-safety invariant, preserved from the old per-file code just at
    # batch grain: a file's rows are only ever marked `done` in the exact
    # same operation that successfully inserted them. If the whole-batch
    # insert_updates call raises a clickhouse_connect DataError — a
    # client-side columnar serialization failure (e.g. a None in a
    # non-Nullable column, see db/clickhouse/schema.sql's
    # route_code/stop_sequence) that provably never reached the server, so
    # retrying is safe — _flush() retries file-by-file (see below) so only
    # the actually-offending file(s), not every file that happened to share
    # a batch with them, end up uncounted/undone.
    #
    # Any OTHER exception (connection drop, timeout, server-side error) is
    # NOT retried per-file and falls back to the old whole-batch-discard
    # behavior instead: such a failure is either systemic (in which case
    # ~600 doomed per-file HTTP inserts would just hammer an already-struggling
    # server) or, worse, may have actually committed server-side despite the
    # client raising (e.g. a read timeout after the request body was fully
    # sent) — in which case a per-file retry would silently re-insert and
    # permanently duplicate every "good" file's rows. Whole-batch discard is
    # safe here because the next run's distinct_file_names() re-check would
    # skip any file that DID actually land, and re-buffer any that didn't.
    pending_rows: list[tuple] = []
    pending_files: list[str] = []
    # Parallel to pending_files: how many of pending_rows' trailing rows
    # belong to the file at the same index. Lets a failed whole-batch insert
    # be retried file-by-file by slicing pending_rows back into the pieces
    # each file contributed, without re-parsing anything.
    pending_counts: list[int] = []

    def _flush() -> None:
        nonlocal n_inserted, n_errors
        # Guard on pending_FILES, not pending_rows: a source file can
        # legitimately parse to zero rows (e.g. no trip_id in this feed
        # matches the agency's pattern), so an all-zero-row leftover batch
        # must still run through insert_updates ([] is a harmless no-op
        # returning 0 — see pipeline/clickhouse.py) and get its files
        # marked `done`, not be silently abandoned because pending_rows
        # alone happened to be empty.
        if not pending_files:
            return
        try:
            try:
                n_inserted += insert_updates(ch_client, agency_id, pending_rows)
                done.update(pending_files)
            except DataError as e:
                # Client-side columnar serialization failure — the insert
                # provably never reached the server (0 rows land in every
                # case, per empirical driver testing up to 40k rows / 2
                # driver blocks), so it's safe to narrow the retry to just
                # the file(s) actually carrying the bad row.
                logger.error(
                    f"  [ERROR] batch insert of {len(pending_files)} files failed with a "
                    f"DataError, retrying file-by-file: {e}"
                )
                offset = 0
                for file_name, n_rows in zip(pending_files, pending_counts, strict=True):
                    file_rows = pending_rows[offset : offset + n_rows]
                    offset += n_rows
                    if not file_rows:
                        # Zero-row file — nothing to insert, so nothing can
                        # fail; mark done same as the happy path would have.
                        done.add(file_name)
                        continue
                    try:
                        n_inserted += insert_updates(ch_client, agency_id, file_rows)
                        done.add(file_name)
                    except Exception as e2:
                        # Broad catch is safe here (unlike the top-level gate
                        # above): this is a single one-shot attempt per file,
                        # never itself retried, so there's no risk of
                        # duplicating an already-committed insert — whatever
                        # exception type, the file just stays un-done and is
                        # picked up again (idempotently) next run.
                        logger.error(f"  [ERROR] inserting {file_name}: {e2}")
                        n_errors += 1
            except Exception as e:
                # NOT a DataError — connection drop, timeout, server-side
                # failure, etc. Do not retry per-file: the failure may be
                # systemic (retrying ~hundreds of files individually would
                # just hammer an already-struggling server) or the batch may
                # have actually committed server-side despite the client
                # raising, in which case a per-file retry would duplicate
                # every "good" file's rows. Fall back to the old
                # whole-batch-discard behavior; the next run's
                # distinct_file_names() re-check safely skips whatever
                # actually landed and re-buffers whatever didn't.
                logger.error(f"  [ERROR] inserting batch of {len(pending_files)} files: {e}")
                n_errors += len(pending_files)
        finally:
            pending_rows.clear()
            pending_files.clear()
            pending_counts.clear()

    tarballs = sorted(root.glob("*.tar.gz")) + sorted(root.glob("*.tgz"))
    pb_loose = sorted(root.rglob("*.pb"))
    logger.info(f"Found {len(tarballs)} tar.gz, {len(pb_loose)} loose .pb (strategy={strategy_name})")

    with conn.cursor() as cur:
        for i, tgz in enumerate(tarballs, 1):
            date_m = re.search(r"(\d{8})", tgz.stem)
            date_dir = date_m.group(1) if date_m else ""
            logger.info(f"[{i}/{len(tarballs)}] {tgz.name}")
            try:
                with tarfile.open(tgz, "r:gz") as tf:
                    members = []
                    for m in tf.getmembers():
                        if not m.name.endswith(".pb"):
                            continue
                        pb_name = pathlib.Path(m.name).name
                        inner_dir = pathlib.Path(m.name).parent.name
                        d = _date_dir(inner_dir) or date_dir
                        members.append((m, pb_name, d))
                    new = [(m, pb, d) for m, pb, d in members if f"{d}/{pb}" not in seen]
                    logger.info(f"  {len(members)} pb files, {len(new)} new")
                    for j, (member, pb_name, d) in enumerate(new):
                        # _savepoint isolates one bad member's Postgres-side work
                        # (parse_feed's static_join JOIN, if any) from every good
                        # member already inserted since the last commit boundary
                        # in this tarball — extractfile() itself can raise on a
                        # corrupt member header/index, not just parse_feed, so it
                        # must be inside the savepoint too. The outer try/except
                        # still covers genuinely tar-wide failures (a corrupt
                        # archive tarfile.open can't even read).
                        #
                        # The ClickHouse insert is deliberately OUTSIDE the
                        # savepoint: it no longer touches Postgres, so a Postgres
                        # SAVEPOINT protects nothing for that step. The parsed
                        # rows are buffered into pending_rows/pending_files and
                        # only actually inserted (and marked `done`) by _flush(),
                        # in batches, per Task 8.9 — see _flush()'s docstring
                        # above for the crash-safety invariant this preserves.
                        try:
                            with _savepoint(cur, "tar_member"):
                                ts = _ts(d, pb_name)
                                fobj = tf.extractfile(member)
                                if fobj is None:  # non-file member (dir/special)
                                    continue
                                raw = fobj.read()
                                rows = strategy.parse_feed(raw, ts, f"{d}/{pb_name}", agency_id, conn)
                        except Exception as e:
                            logger.error(f"  [ERROR] {pb_name}: {e}")
                            n_errors += 1
                            continue
                        pending_rows.extend(rows)
                        pending_files.append(f"{d}/{pb_name}")
                        pending_counts.append(len(rows))
                        seen.add(f"{d}/{pb_name}")
                        if len(pending_rows) >= _BATCH_ROWS:
                            _flush()
                        if j % 300 == 0 and j > 0:
                            conn.commit()
                            logger.info(f"    {j}/{len(new)}...")
            except Exception as e:
                logger.error(f"  [ERROR] {e}")
                n_errors += 1
                conn.rollback()
            conn.commit()

        # Flush whatever the tarball loop above buffered (even under
        # _BATCH_ROWS) so `done` is accurate before the loose-.pb loop below
        # computes its own dedup skip-list. `new_pb`'s check against `seen`
        # (not `done`) already prevents double-processing on its own even
        # without this flush, but leaving files unflushed here would let
        # `done` silently drift out of sync with what this run has actually
        # persisted — this flush keeps the two in step at the loop boundary.
        _flush()

        new_pb = [p for p in pb_loose if f"{_date_dir(p.parent.name)}/{p.name}" not in seen]
        if new_pb:
            logger.info(f"\n{len(new_pb)} loose .pb files")
            for j, path in enumerate(new_pb, 1):
                d = _date_dir(path.parent.name)
                ts = _ts(d, path.name)
                # _savepoint isolates one bad file's Postgres-side work from
                # every good file already inserted since the last commit
                # boundary in this batch (see _savepoint's docstring for why
                # RELEASE matters). The ClickHouse insert is outside the
                # savepoint (see the tarball loop above for why); rows are
                # buffered into pending_rows/pending_files and only actually
                # inserted (and marked `done`) by _flush(), in batches shared
                # with the tarball loop above (Task 8.9).
                try:
                    with _savepoint(cur, "pb_file"):
                        rows = strategy.parse_feed(path.read_bytes(), ts, f"{d}/{path.name}", agency_id, conn)
                except Exception as e:
                    logger.error(f"  [ERROR] {path.name}: {e}")
                    n_errors += 1
                    continue
                pending_rows.extend(rows)
                pending_files.append(f"{d}/{path.name}")
                pending_counts.append(len(rows))
                seen.add(f"{d}/{path.name}")
                if len(pending_rows) >= _BATCH_ROWS:
                    _flush()
                if j % 500 == 0:
                    conn.commit()
                    logger.info(f"  {j}/{len(new_pb)}")
        conn.commit()

    # Flush whatever's left in the buffer — without this, up to
    # _BATCH_ROWS - 1 rows from the tail of a run would never be persisted
    # or marked `done` at all.
    _flush()

    if n_errors:
        logger.warning(f"Skipped {n_errors} files due to parse or insert errors — see log above for detail")
    logger.info(f"\nDone: {n_inserted} new rows inserted")
    return n_inserted


def ingest_live(agency_id: int, conn, ch_client) -> int:
    """Fetch the agency's GTFS-RT feed_url and ingest it live."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT feed_url FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row is None or not row[0]:
        raise ValueError(f"No feed_url configured for agency_id={agency_id!r}")
    feed_url = row[0]

    strategy_name = _resolve_strategy_name(agency_id, conn)
    strategy = get_ingest_strategy(strategy_name)

    logger.info(f"Fetching live feed from {_redact_url(feed_url)} (strategy={strategy_name})")
    with safe_urlopen(feed_url, timeout=30) as resp:
        raw = resp.read()

    captured_at = datetime.now(timezone.utc).isoformat()
    file_name = f"live_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    # `file_name` is second-granularity, so two invocations within the same
    # second — a double cron poke, or a retried BackgroundTask (the cron
    # endpoint's worker, api/routers/internal.py) — would otherwise insert
    # the same poll twice with no guard: unlike ingest(), this path has no
    # done/seen check at all. Postgres's UNIQUE(agency_id, file_name,
    # trip_id, stop_sequence) + ON CONFLICT DO NOTHING used to absorb this
    # for free; ClickHouse has no equivalent. A bounded single-file check
    # (not distinct_file_names' unbounded full-partition scan, which would
    # be wasteful to pay on every ~30s poll) mirrors ingest()'s file-level
    # idempotency at this path's much smaller grain.
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    if recent_file_name_exists(ch_client, agency_id, file_name, since):
        logger.info(f"Skipping duplicate live poll: {file_name} already ingested")
        return 0

    rows = strategy.parse_feed(raw, captured_at, file_name, agency_id, conn)

    n_inserted = insert_updates(ch_client, agency_id, rows)
    conn.commit()

    logger.info(f"Done: {n_inserted} rows inserted (live)")
    return n_inserted
