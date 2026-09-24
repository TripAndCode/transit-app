#!/usr/bin/env python3
"""Thin CLI wrapper for the GTFS pipeline jobs."""

import argparse
import logging
import os
import sys
from urllib.parse import urlsplit

import psycopg2

from pipeline import runs as pipeline_runs
from pipeline.locks import try_lock_ingest_analyze_timed

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")

# sysexits.h EX_TEMPFAIL -- distinct from a genuine failure's exit(1), so a
# per-agency shell loop (scripts/fetch_and_ingest.sh, docs/deploy-railway.md's
# Railway sketch) can tell "lock busy, will resolve on the next scheduled
# run" apart from "broken" and `continue` to the next agency instead of
# aborting the whole remaining loop under `set -euo pipefail`.
EX_TEMPFAIL = 75

# Shared by every all-agencies loop below (analyze-all, check-aggs, ingest-live)
# so a test can assert against the query as actually executed, not a hand-typed
# copy that would stay green if the real filter were ever reverted.
ACTIVE_AGENCY_IDS_SQL = "SELECT agency_id FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"


def describe_target(url: str) -> str:
    """Render *url*'s host, port and database name, never its credentials.

    Safe to log and to put in an error message. A password can only appear in
    the userinfo half, before the ``@`` — libpq reads a netloc with no ``@`` as
    a hostspec, so ``postgresql://admin:54321/db`` connects to host ``admin``
    on port 54321 and holds no credential at all. Everything read here is taken
    from the hostspec alone; the userinfo half is dropped rather than masked,
    so neither the password nor its length leaks.

    Never raises. This runs before the connection is attempted, so anything it
    raised would pre-empt the driver's own error — and the driver's is the
    better one, because it names what is wrong with the URL.
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        # `urlsplit` itself rejects some malformed netlocs before we ever get
        # to parse anything — an unencoded literal `[` or `]`, which an
        # ordinary password can contain, trips its IPv6-bracket check. Strip
        # credentials by hand instead, using the same "everything up to the
        # last `@`" boundary rule as the parsed path below.
        _, _, rest = url.rpartition("@")
        return rest or "?"
    _, _, hostspec = parsed.netloc.rpartition("@")
    host = parsed.hostname or "?"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        # `.port` raises rather than returning None when the substring is not
        # an in-range integer. Echo it as written: it is part of the hostspec,
        # and the driver's own "invalid integer value" error quotes the same
        # text, so withholding it here would hide nothing and cost the one
        # detail that makes a typo'd port obvious in the log.
        _, _, raw = hostspec.rpartition(":")
        port = f":{raw}"
    return f"{host}{port}/{parsed.path.lstrip('/') or '?'}"


# Whether the target database holds this schema. Resolved through `search_path`
# like every other unqualified reference in this codebase, so it answers the
# question the commands themselves will ask a moment later.
SCHEMA_PROBE_SQL = "SELECT to_regclass('agencies') IS NOT NULL"


def _log_target() -> None:
    """Record which database this run connected to.

    Logged on success, not only on failure: these jobs write, and which
    database they wrote to is the first thing anyone reconstructing a run
    needs — and the hardest to recover afterwards.
    """
    logger.info(f"database: {describe_target(DATABASE_URL)}")


def _wrong_target_error() -> SystemExit:
    """The shared "this is not a transit database" failure.

    Built in one place because two connection stacks enforce it: the psycopg2
    commands through :func:`_get_conn` and the asyncpg ones through
    :func:`guard_async_conn`. One message with two bindings cannot drift the
    way two copies would.
    """
    return SystemExit(
        f"DATABASE_URL points at {describe_target(DATABASE_URL)}, which has no "
        "`agencies` table, so it is not a migrated transit database. Check the "
        "port and database name, then run `migrate up` if it really is meant to "
        "be a fresh one."
    )


async def guard_async_conn(conn) -> None:
    """Apply the wrong-target guard to an already-open asyncpg connection.

    The asyncpg-backed commands cannot borrow :func:`_get_conn`, so they share
    the probe and the message instead. Called after connecting rather than
    before, because the probe is a query.
    """
    if not await conn.fetchval(SCHEMA_PROBE_SQL):
        raise _wrong_target_error()


def _get_conn(require_schema: bool = True):
    _log_target()
    conn = psycopg2.connect(DATABASE_URL)
    # Pin JST so `captured_at::date` buckets every aggregate on the same civil
    # day the API reads it under (api/main pins Asia/Tokyo; tests too). The
    # server default is UTC, which mis-bucketed ~20% of observations by date —
    # silently diverging the agg fast paths from the live fallbacks they replace.
    # Committed up front (autocommit) so it survives analyze's txn rollback.
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Tokyo'")
    conn.autocommit = False
    # These commands write, and DATABASE_URL is a port on localhost in
    # development, so a stale or wrong value points at whatever else happens
    # to be listening — another project's database, or the throwaway test one.
    # Left unchecked the first symptom is an UndefinedTable traceback that
    # names the missing table but not the database it was missing from, which
    # is the one fact needed to see that the target is wrong.
    if require_schema:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PROBE_SQL)
            found = cur.fetchone()[0]
        if not found:
            conn.close()
            raise _wrong_target_error()
    return conn


def _record_displaced(conn, kind: str, agency_id: int | None, lock_wait_ms: int) -> None:
    """Leave a `skipped` pipeline_runs row for a job the lock turned away.

    The whole reason this table exists: a displaced job produces no data and
    therefore no other trace, so without this row a fleet losing every other
    scheduled run looks exactly like a healthy one on the control board.
    Best-effort like every other write in pipeline/runs.py -- it never changes
    the exit code the caller is about to take.
    """
    pipeline_runs.start_run(conn, kind, agency_id=agency_id, status="skipped", lock_wait_ms=lock_wait_ms)


def _lock_or_skip_agency(conn, cmd: str, kind: str, agency_id: int | None = None) -> None:
    """Exit(EX_TEMPFAIL) if another ingest/analyze process holds the lock.

    For `ingest` and `analyze` -- the single-agency commands
    scripts/fetch_and_ingest.sh and docs/deploy-railway.md's Railway sketch
    both invoke inside a per-agency `for` loop. EX_TEMPFAIL (not exit 1) lets
    that loop distinguish transient, self-healing contention from a genuine
    failure and skip just this agency this run, instead of the whole
    remaining loop aborting under `set -euo pipefail`.
    """
    got, lock_wait_ms = try_lock_ingest_analyze_timed(conn)
    if got:
        return
    logger.warning("%s: another ingest/analyze process is already running; skipping this agency this run.", cmd)
    _record_displaced(conn, kind, agency_id, lock_wait_ms)
    conn.close()
    sys.exit(EX_TEMPFAIL)


def _lock_or_exit(conn, cmd: str, kind: str) -> None:
    """Exit(1) if another ingest/analyze process holds the lock.

    For `analyze_all` and `ingest_live` -- the whole-fleet commands that no
    shell script loops over (docs/deploy-railway.md calls each exactly once,
    after -- not inside -- the per-agency `ingest` loop), so the abort-risk
    that justifies skipping in _lock_or_skip_agency doesn't apply here. Both
    are documented as fail-loud ("partial run can't pass silently" --
    CLAUDE.md); a silent no-op would violate that contract for no benefit.
    """
    got, lock_wait_ms = try_lock_ingest_analyze_timed(conn)
    if got:
        return
    logger.error("%s: another ingest/analyze process is already running; refusing to start.", cmd)
    _record_displaced(conn, kind, None, lock_wait_ms)
    conn.close()
    sys.exit(1)


def _args_agency_id(args) -> int | None:
    """The agency the command was asked for, before the DB is consulted.

    The lock is taken (and a displaced run recorded) before `_require_agency`
    can infer a sole agency from the database, so a skipped run knows only
    what the caller named. `None` means "not named", not "every agency".
    """
    raw = getattr(args, "agency_id", None)
    return int(raw) if raw else None


def _require_agency(args, conn) -> int:
    """Return agency_id from args or infer it when there is exactly one agency.

    Exits with a helpful message if no agencies exist or the choice is ambiguous.
    """
    if args.agency_id:
        return int(args.agency_id)
    with conn.cursor() as cur:
        cur.execute("SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id")
        agencies = cur.fetchall()
    if not agencies:
        logger.info("No agencies found. Add one first:")
        logger.info("  python gtfs_pipeline.py add_agency --name 'Agency Name' --feed-url 'http://...'")
        sys.exit(1)
    if len(agencies) == 1:
        return agencies[0][0]
    logger.info("Multiple agencies found. Specify --agency-id:")
    for aid, name in agencies:
        logger.info(f"  {aid}: {name}")
    sys.exit(1)


def cmd_add_agency(args):
    """Insert a new agency row and print the assigned agency_id."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, static_url) VALUES (%s, %s, %s) RETURNING agency_id",
            (args.name, args.feed_url, args.static_url),
        )
        aid = cur.fetchone()[0]
    conn.commit()
    logger.info(f"Added agency {aid}: {args.name}")
    conn.close()


def cmd_seed_agencies(args):
    """Idempotently upsert every row of a CSV into the agencies table.

    Columns: agency_id (optional), agency_name, feed_url, static_url, trip_id_pattern
    Empty strings become NULL for static_url and trip_id_pattern.
    Uniqueness is by feed_url; existing rows are updated, not duplicated.

    When ``agency_id`` is set in the CSV the INSERT uses it explicitly,
    so re-seeding after a TRUNCATE always produces the same id (avoids
    the sequence-drift bug where dev DBs accumulated agency_id=97 after
    repeated test truncations and broke fetch_and_ingest.sh's default
    ``AGENCY_ID=1``). The sequence is bumped to ``MAX(agency_id) + 1``
    afterwards so future inserts without an explicit id don't collide.
    """
    import csv

    path = args.csv
    conn = _get_conn()
    inserted = updated = 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn.cursor() as cur:
            for row in reader:
                name = row["agency_name"].strip()
                feed = row["feed_url"].strip()
                static = (row.get("static_url") or "").strip() or None
                pattern = (row.get("trip_id_pattern") or "").strip() or None
                ingest_strategy = (row.get("ingest_strategy") or "").strip() or None
                static_strategy = (row.get("static_strategy") or "").strip() or None
                if not name or not feed:
                    continue  # skip blank/comment lines
                aid_raw = (row.get("agency_id") or "").strip()
                explicit_id = int(aid_raw) if aid_raw.isdigit() else None
                if explicit_id is not None:
                    cur.execute(
                        """
                        INSERT INTO agencies (
                            agency_id, agency_name, feed_url, static_url,
                            trip_id_pattern, ingest_strategy, static_strategy
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (feed_url) DO UPDATE SET
                            agency_id = EXCLUDED.agency_id,
                            agency_name = EXCLUDED.agency_name,
                            static_url = EXCLUDED.static_url,
                            trip_id_pattern = EXCLUDED.trip_id_pattern,
                            ingest_strategy = EXCLUDED.ingest_strategy,
                            static_strategy = EXCLUDED.static_strategy
                        RETURNING agency_id, (xmax = 0) AS inserted
                        """,
                        (explicit_id, name, feed, static, pattern, ingest_strategy, static_strategy),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO agencies (
                            agency_name, feed_url, static_url,
                            trip_id_pattern, ingest_strategy, static_strategy
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (feed_url) DO UPDATE SET
                            agency_name = EXCLUDED.agency_name,
                            static_url = EXCLUDED.static_url,
                            trip_id_pattern = EXCLUDED.trip_id_pattern,
                            ingest_strategy = EXCLUDED.ingest_strategy,
                            static_strategy = EXCLUDED.static_strategy
                        RETURNING agency_id, (xmax = 0) AS inserted
                        """,
                        (name, feed, static, pattern, ingest_strategy, static_strategy),
                    )
                aid, was_inserted = cur.fetchone()
                if was_inserted:
                    inserted += 1
                    logger.info(f"  + agency {aid}: {name}")
                else:
                    updated += 1
                    logger.info(f"  ~ agency {aid}: {name} (updated)")
            # Realign the sequence so future inserts without an explicit
            # id don't collide with the explicit ones we just wrote.
            cur.execute(
                "SELECT setval('agencies_agency_id_seq', "
                "GREATEST((SELECT COALESCE(MAX(agency_id), 0) FROM agencies), 1))"
            )
    conn.commit()
    conn.close()
    logger.info(f"Seeded {inserted} new + {updated} updated from {path}")


def cmd_ingest(args):
    """Run the archive ingest pipeline for one agency."""
    from pipeline.clickhouse import get_client
    from pipeline.ingest import ingest

    conn = _get_conn()
    _lock_or_skip_agency(conn, "ingest", "ingest", _args_agency_id(args))
    agency_id = _require_agency(args, conn)
    ch_client = get_client()
    with pipeline_runs.record_run(conn, "ingest", agency_id=agency_id) as run:
        run.rows = ingest(args.folder, agency_id, conn, ch_client)
    conn.close()


def cmd_load_static(args):
    """Load a GTFS static zip into the database for one agency."""
    from pipeline.static_loader import load_static

    conn = _get_conn()
    agency_id = _require_agency(args, conn)
    with pipeline_runs.record_run(conn, "static", agency_id=agency_id):
        load_static(args.path, agency_id, conn)
    conn.close()


def cmd_refresh_static(args):
    """Conditionally fetch and load static GTFS via each agency's static_strategy."""
    import pathlib

    from pipeline.static_fetcher import refresh_all, refresh_static

    conn = _get_conn()
    dest = pathlib.Path(args.dest)
    if args.agency_id:
        result = refresh_static(int(args.agency_id), conn, dest)
        if result is None:
            logger.info("No change.")
    else:
        n, total, failed = refresh_all(conn, dest)
        conn.close()
        if failed:
            logger.error(f"refresh-static: {len(failed)} of {total} agencies failed: {failed}")
            sys.exit(1)
        logger.info(f"Refreshed {n} agencies.")
        return
    conn.close()


def cmd_analyze(args):
    """Run the analysis pass for one agency."""
    from pipeline.analyze import analyze
    from pipeline.clickhouse import get_client

    conn = _get_conn()
    _lock_or_skip_agency(conn, "analyze", "analyze", _args_agency_id(args))
    agency_id = _require_agency(args, conn)
    ch_client = get_client()
    with pipeline_runs.record_run(conn, "analyze", agency_id=agency_id):
        analyze(agency_id, conn, ch_client)
    conn.close()


def cmd_analyze_all(args):
    """Analyze every agency; report all failures and exit nonzero if any failed.

    Run-all-then-report: one agency raising does not abort the others, so a
    single run surfaces every failure at once. Replaces the silent per-agency
    loop as the canonical full rebuild.
    """
    from pipeline.analyze import analyze
    from pipeline.clickhouse import get_client

    conn = _get_conn()
    _lock_or_exit(conn, "analyze-all", "analyze")
    ch_client = get_client()
    with conn.cursor() as cur:
        cur.execute(ACTIVE_AGENCY_IDS_SQL)
        agency_ids = [r[0] for r in cur.fetchall()]
    if not agency_ids:
        logger.info("No agencies found.")
        conn.close()
        return
    failed = []
    for aid in agency_ids:
        try:
            logger.info(f"--- analyze agency_id={aid} ---")
            with pipeline_runs.record_run(conn, "analyze", agency_id=aid):
                analyze(aid, conn, ch_client)
        except Exception:
            logger.exception(f"analyze failed for agency {aid}")
            failed.append(aid)
    conn.close()
    if failed:
        logger.error(f"analyze-all: {len(failed)} of {len(agency_ids)} agencies failed: {failed}")
        sys.exit(1)
    logger.info(f"analyze-all: all {len(agency_ids)} agencies analyzed.")


def cmd_check_aggs(args):
    """Report agencies whose aggregates lag their newest completed day.

    Exits nonzero if any agency is stale — the post-merge / monitoring guard.
    """
    from pipeline.clickhouse import get_client
    from pipeline.freshness import check_agg_freshness

    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(ACTIVE_AGENCY_IDS_SQL)
        agency_ids = [r[0] for r in cur.fetchall()]
    ch_client = get_client()
    stale = check_agg_freshness(conn, ch_client, agency_ids)
    conn.close()
    if stale:
        for s in stale:
            logger.error(
                f"STALE agency {s.agency_id}: aggs cover {s.agg_max_day}, "
                f"live completed through {s.live_max_completed_day}"
            )
        sys.exit(1)
    logger.info(f"check-aggs: all {len(agency_ids)} agencies fresh.")


def cmd_check_migrations(args):
    """Report migrations on disk not applied to the DB; nonzero exit if any behind."""
    from db.migrate import pending_migrations

    # Reports "everything is pending" against a database that has no schema
    # yet, which is a legitimate thing to ask before migrating one.
    conn = _get_conn(require_schema=False)
    pending = pending_migrations(conn)
    conn.close()
    if pending:
        logger.error(f"schema behind: {len(pending)} migration(s) not applied: {pending}")
        sys.exit(1)
    logger.info("schema up to date.")


def cmd_digest(args):
    """Print the daily digest for one completed day (default: yesterday JST)."""
    from datetime import date

    from pipeline.digest.build import build_digest
    from pipeline.digest.render import render_digest

    conn = _get_conn()
    try:
        if args.day:
            target_day = date.fromisoformat(args.day)
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT (now() AT TIME ZONE 'Asia/Tokyo')::date - 1")
                target_day = cur.fetchone()[0]
        data = build_digest(conn, target_day)
    finally:
        conn.close()
    sys.stdout.write(render_digest(data, args.locale))


def cmd_ingest_live(args):
    """Fetch and ingest the live GTFS-RT feed for one or all agencies.

    All-agencies branch: run-all-then-report, matching cmd_analyze_all — one
    agency raising (network timeout, a rejected feed_url, a malformed
    trip_id_pattern regex) does not abort the others, so a single scheduled
    run surfaces every failure instead of silently skipping every agency
    after the first one that errors. ingest_live() has no internal
    rollback-on-error of its own (unlike analyze()), so the connection is
    rolled back here before continuing to the next agency.
    """
    from pipeline.clickhouse import get_client
    from pipeline.ingest import ingest_live

    conn = _get_conn()
    _lock_or_exit(conn, "ingest-live", "ingest")
    ch_client = get_client()
    if args.agency_id is not None:
        with pipeline_runs.record_run(conn, "ingest", agency_id=int(args.agency_id)) as run:
            run.rows = ingest_live(int(args.agency_id), conn, ch_client)
        conn.close()
        return

    with conn.cursor() as cur:
        cur.execute(ACTIVE_AGENCY_IDS_SQL)
        agency_ids = [r[0] for r in cur.fetchall()]
    if not agency_ids:
        logger.info("No agencies found.")
        conn.close()
        return
    failed = []
    for aid in agency_ids:
        try:
            logger.info(f"--- Ingesting agency_id={aid} ---")
            with pipeline_runs.record_run(conn, "ingest", agency_id=aid) as run:
                run.rows = ingest_live(aid, conn, ch_client)
        except Exception:
            logger.exception(f"ingest-live failed for agency {aid}")
            conn.rollback()
            failed.append(aid)
    conn.close()
    if failed:
        logger.error(f"ingest-live: {len(failed)} of {len(agency_ids)} agencies failed: {failed}")
        sys.exit(1)
    logger.info(f"ingest-live: all {len(agency_ids)} agencies ingested.")


def cmd_migrate(args):
    """Apply or roll back schema migrations."""
    from db.migrate import migrate_down, migrate_up

    # The one command whose whole purpose is a database that does not have the
    # schema yet, so it cannot require one.
    conn = _get_conn(require_schema=False)
    if args.direction == "up":
        migrate_up(conn)
    else:
        migrate_down(args.target, conn)
    conn.close()


def cmd_build_rag_index(args):
    """Embed every (id, question) line from tests/ask_eval/golden_set.jsonl
    into rag_chunks for the named agency (or every agency in `agencies` if
    --all-agencies is set). Idempotent via content_hash."""
    import asyncio
    from pathlib import Path

    import asyncpg

    from pipeline.query.rag_index import build_index

    golden = Path(__file__).resolve().parent / "tests" / "ask_eval" / "golden_set.jsonl"
    if not golden.exists():
        raise SystemExit(f"golden set not found: {golden}")

    async def run():
        """Async body executed via asyncio.run()."""
        _log_target()
        pool = await asyncpg.create_pool(DATABASE_URL)
        # try/finally so the pool closes on the early exits too — the guard
        # below and the missing-argument check both leave before the loop.
        try:
            async with pool.acquire() as conn:
                await guard_async_conn(conn)
            if args.all_agencies:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
                    )
                ids = [(r["agency_id"], r["agency_name"]) for r in rows]
            else:
                if args.agency_id is None:
                    raise SystemExit("--agency-id or --all-agencies required")
                ids = [(args.agency_id, f"agency {args.agency_id}")]

            for aid, name in ids:
                async with pool.acquire() as conn:
                    counts = await build_index(conn, aid, golden)
                logger.info(
                    f"  {aid:>3} {name}: "
                    f"inserted={counts['inserted']} updated={counts['updated']} skipped={counts['skipped']}"
                )
        finally:
            await pool.close()

    asyncio.run(run())


def cmd_prune_query_log(args):
    """Delete ask_query_log rows older than the retention window (default 90 days)."""
    import asyncio

    import asyncpg

    days = int(args.days)

    async def run():
        """Async body executed via asyncio.run()."""
        _log_target()
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await guard_async_conn(conn)
            result = await conn.execute(f"DELETE FROM ask_query_log WHERE created_at < now() - INTERVAL '{days} days'")
            logger.info(f"prune_query_log: {result}")
        finally:
            await conn.close()

    asyncio.run(run())


def cmd_ingest_weather(args):
    """Fetch observed daily weather for every configured representative station."""
    from pipeline.weather import PUBLICATION_WINDOW_DAYS, ingest_weather

    days = PUBLICATION_WINDOW_DAYS if args.days is None else int(args.days)
    conn = _get_conn()
    try:
        # Fleet-wide, not per-agency: the pass covers every configured
        # station, and several agencies may share one.
        with pipeline_runs.record_run(conn, "weather") as run:
            written, considered, failed = ingest_weather(conn, days=days)
            run.rows = written
    finally:
        conn.close()
    if failed:
        logger.error(f"ingest_weather: {len(failed)} station(s) failed: {failed}")
        sys.exit(1)
    logger.info(f"ingest_weather: wrote {written} station-day(s) of {considered} examined.")


def main():
    """Parse CLI arguments and dispatch to the appropriate command handler."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="GTFS pipeline CLI")
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add_agency")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--feed-url", required=True)
    p_add.add_argument("--static-url", default=None)

    p_seed = sub.add_parser("seed_agencies", help="Upsert agencies from a CSV (idempotent)")
    p_seed.add_argument("csv", help="Path to agencies CSV (default: ./agencies.csv)", nargs="?", default="agencies.csv")

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("folder")
    p_ingest.add_argument("--agency-id", default=None)

    p_static = sub.add_parser("load_static")
    p_static.add_argument("path")
    p_static.add_argument("--agency-id", default=None)

    p_refresh = sub.add_parser(
        "refresh-static",
        help="Conditionally fetch + load static GTFS via the agency's static_strategy",
    )
    p_refresh.add_argument("--agency-id", default=None, help="Specific agency (default: all configured)")
    p_refresh.add_argument("--dest", default="raw_archives_static", help="Local destination directory for fetched zips")

    p_analyze = sub.add_parser("analyze")
    p_analyze.add_argument("--agency-id", default=None)

    sub.add_parser("analyze_all", help="Analyze every agency; nonzero exit if any fails")
    sub.add_parser("check_aggs", help="Report agencies with stale aggregates; nonzero exit if any")
    sub.add_parser("check_migrations", help="Report unapplied migrations; nonzero exit if the DB schema is behind")

    p_digest = sub.add_parser("digest", help="Print the daily network-health digest (Markdown)")
    p_digest.add_argument("--day", default=None, help="Target day YYYY-MM-DD (default: yesterday JST)")
    p_digest.add_argument("--locale", default="ja", choices=["ja", "en"])

    p_live = sub.add_parser("ingest_live", help="Fetch and ingest live GTFS-RT from each agency's feed_url")
    p_live.add_argument("--agency-id", required=False, default=None, help="Agency ID to ingest (default: all agencies)")

    p_migrate = sub.add_parser("migrate", help="Apply or roll back schema migrations")
    p_migrate.add_argument("direction", choices=["up", "down"], nargs="?", default="up")
    p_migrate.add_argument(
        "--target",
        default=None,
        help="Roll back to (not including) this version, e.g. --target 0002",
    )

    p_rag = sub.add_parser("build_rag_index", help="Embed golden_set.jsonl into rag_chunks")
    p_rag.add_argument("--agency-id", type=int, default=None)
    p_rag.add_argument("--all-agencies", action="store_true")

    p_prune = sub.add_parser("prune_query_log", help="Delete ask_query_log rows older than N days")
    p_prune.add_argument("--days", type=int, default=90)

    p_weather = sub.add_parser(
        "ingest_weather",
        help="Fetch observed daily weather for each agency's representative station",
    )
    p_weather.add_argument(
        "--days",
        type=int,
        default=None,
        help=(
            "How many whole days back from yesterday to cover (default and maximum: the "
            "source's publication window, since a day past it is unfetchable). Today is never "
            "fetched -- a day in progress cannot be aggregated whole"
        ),
    )

    args = parser.parse_args()
    if args.command == "add_agency":
        cmd_add_agency(args)
    elif args.command == "seed_agencies":
        cmd_seed_agencies(args)
    elif args.command == "ingest":
        cmd_ingest(args)
    elif args.command == "load_static":
        cmd_load_static(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "analyze_all":
        cmd_analyze_all(args)
    elif args.command == "check_aggs":
        cmd_check_aggs(args)
    elif args.command == "check_migrations":
        cmd_check_migrations(args)
    elif args.command == "digest":
        cmd_digest(args)
    elif args.command == "ingest_live":
        cmd_ingest_live(args)
    elif args.command == "refresh-static":
        cmd_refresh_static(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "build_rag_index":
        cmd_build_rag_index(args)
    elif args.command == "prune_query_log":
        cmd_prune_query_log(args)
    elif args.command == "ingest_weather":
        cmd_ingest_weather(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
