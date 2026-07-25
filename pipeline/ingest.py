"""GTFS-RT ingest router.

Looks up the ingest strategy for an agency and delegates pb decoding to it.
The router owns: file iteration (tarballs + loose .pb), captured_at
derivation, dedup against the updates table, and bulk INSERT.
"""

import logging
import pathlib
import re
import tarfile
from datetime import datetime, timezone

import psycopg2.extras

from pipeline.strategies import get_ingest_strategy

# ── Re-exports for back-compat (existing tests import these) ──────────────────
from pipeline.strategies._pb import UPDATE_INSERT_SQL, _dec, _fields, _read_ld, _read_varint, _ts  # noqa: F401
from pipeline.strategies.aomori_regex import (
    _TRIP_RE_DEFAULT,
    parse_trip_id,
)
from pipeline.url_guard import safe_urlopen

logger = logging.getLogger(__name__)

# YYYYMMDD path segment (e.g. tar member dir or .pb parent dir). Used to
# derive captured_at when the filename alone doesn't carry the date.
_DATE_DIR_RE = re.compile(r"\d{8}")


def _date_dir(name: str) -> str:
    """Return *name* if it is a YYYYMMDD token, otherwise ``""``."""
    return name if _DATE_DIR_RE.fullmatch(name) else ""


def _insert_updates(cur, agency_id: int, rows: list[tuple]) -> int:
    """Prepend ``agency_id`` to each row and bulk-INSERT into ``updates``."""
    pg_rows = [(agency_id, *r) for r in rows]
    psycopg2.extras.execute_batch(cur, UPDATE_INSERT_SQL, pg_rows)
    return len(pg_rows)


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


def ingest(folder: str, agency_id: int, conn) -> int:
    """Ingest all .pb files from tarballs and loose files in folder.

    Dispatches to the agency's ingest strategy. Returns total rows attempted.
    """
    root = pathlib.Path(folder)
    n_errors = 0
    n_inserted = 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT file_name FROM updates WHERE agency_id = %s",
            (agency_id,),
        )
        done = {r[0] for r in cur.fetchall()}

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
                        ts = _ts(d, pb_name)
                        fobj = tf.extractfile(member)
                        if fobj is None:  # non-file member (dir/special)
                            continue
                        raw = fobj.read()
                        rows = strategy.parse_feed(raw, ts, f"{d}/{pb_name}", agency_id, conn)
                        n_inserted += _insert_updates(cur, agency_id, rows)
                        done.add(f"{d}/{pb_name}")
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
                # SAVEPOINT, not conn.rollback(): this loop only commits every
                # 500 files, so a bare rollback on one bad file would also
                # discard every good file already inserted since the last
                # commit boundary in the same open transaction.
                cur.execute("SAVEPOINT pb_file")
                try:
                    rows = strategy.parse_feed(path.read_bytes(), ts, f"{d}/{path.name}", agency_id, conn)
                    n_inserted += _insert_updates(cur, agency_id, rows)
                    done.add(f"{d}/{path.name}")
                    cur.execute("RELEASE SAVEPOINT pb_file")
                except Exception as e:
                    logger.error(f"  [ERROR] {path.name}: {e}")
                    n_errors += 1
                    cur.execute("ROLLBACK TO SAVEPOINT pb_file")
                if j % 500 == 0:
                    conn.commit()
                    logger.info(f"  {j}/{len(new_pb)}")
        conn.commit()

    if n_errors:
        logger.warning(f"Skipped {n_errors} files with parse errors")
    logger.info(f"\nDone: {n_inserted} new rows inserted")
    return n_inserted


def ingest_live(agency_id: int, conn) -> int:
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

    logger.info(f"Fetching live feed from {feed_url} (strategy={strategy_name})")
    with safe_urlopen(feed_url, timeout=30) as resp:
        raw = resp.read()

    captured_at = datetime.now(timezone.utc).isoformat()
    file_name = f"live_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    rows = strategy.parse_feed(raw, captured_at, file_name, agency_id, conn)

    with conn.cursor() as cur:
        n_inserted = _insert_updates(cur, agency_id, rows)
    conn.commit()

    logger.info(f"Done: {n_inserted} rows inserted (live)")
    return n_inserted
