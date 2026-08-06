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
from datetime import datetime, timezone
from typing import Iterator

from pipeline.clickhouse import distinct_file_names, insert_updates
from pipeline.strategies import get_ingest_strategy

# ── Re-exports for back-compat (existing tests import these) ──────────────────
from pipeline.strategies._pb import UPDATE_INSERT_SQL, _dec, _fields, _read_ld, _read_varint, _ts  # noqa: F401
from pipeline.strategies.aomori_regex import (
    _TRIP_RE_DEFAULT,
    parse_trip_id,
)
from pipeline.url_guard import _redact_url, safe_urlopen

logger = logging.getLogger(__name__)

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

    strategy_name = _resolve_strategy_name(agency_id, conn)
    strategy = get_ingest_strategy(strategy_name)

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
                    new = [(m, pb, d) for m, pb, d in members if f"{d}/{pb}" not in done]
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
                        # SAVEPOINT protects nothing for that step. It gets its
                        # own try/except below — on failure the file is NOT added
                        # to `done`, so it's retried on the next ingest() run
                        # (mirrors this loop's pre-existing behavior: a file only
                        # ever joined `done` after a successful insert).
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
                        try:
                            n_inserted += insert_updates(ch_client, agency_id, rows)
                            done.add(f"{d}/{pb_name}")
                        except Exception as e:
                            logger.error(f"  [ERROR] inserting {pb_name}: {e}")
                            n_errors += 1
                        if j % 300 == 0 and j > 0:
                            conn.commit()
                            logger.info(f"    {j}/{len(new)}...")
            except Exception as e:
                logger.error(f"  [ERROR] {e}")
                n_errors += 1
                conn.rollback()
            conn.commit()

        new_pb = [p for p in pb_loose if f"{_date_dir(p.parent.name)}/{p.name}" not in done]
        if new_pb:
            logger.info(f"\n{len(new_pb)} loose .pb files")
            for j, path in enumerate(new_pb, 1):
                d = _date_dir(path.parent.name)
                ts = _ts(d, path.name)
                # _savepoint isolates one bad file's Postgres-side work from
                # every good file already inserted since the last commit
                # boundary in this batch (see _savepoint's docstring for why
                # RELEASE matters). The ClickHouse insert is outside the
                # savepoint (see the tarball loop above for why) with its own
                # try/except: on failure the file is NOT added to `done`, so
                # it's retried on the next ingest() run.
                try:
                    with _savepoint(cur, "pb_file"):
                        rows = strategy.parse_feed(path.read_bytes(), ts, f"{d}/{path.name}", agency_id, conn)
                except Exception as e:
                    logger.error(f"  [ERROR] {path.name}: {e}")
                    n_errors += 1
                    continue
                try:
                    n_inserted += insert_updates(ch_client, agency_id, rows)
                    done.add(f"{d}/{path.name}")
                except Exception as e:
                    logger.error(f"  [ERROR] inserting {path.name}: {e}")
                    n_errors += 1
                if j % 500 == 0:
                    conn.commit()
                    logger.info(f"  {j}/{len(new_pb)}")
        conn.commit()

    if n_errors:
        logger.warning(f"Skipped {n_errors} files with parse errors")
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

    rows = strategy.parse_feed(raw, captured_at, file_name, agency_id, conn)

    n_inserted = insert_updates(ch_client, agency_id, rows)
    conn.commit()

    logger.info(f"Done: {n_inserted} rows inserted (live)")
    return n_inserted
