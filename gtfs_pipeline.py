#!/usr/bin/env python3
"""Thin CLI wrapper for the GTFS pipeline jobs."""

import argparse
import logging
import os
import sys

import psycopg2

from pipeline.locks import try_lock_ingest_analyze

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")

# Shared by every all-agencies loop below (analyze-all, check-aggs, ingest-live)
# so a test can assert against the query as actually executed, not a hand-typed
# copy that would stay green if the real filter were ever reverted.
ACTIVE_AGENCY_IDS_SQL = "SELECT agency_id FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"


def _get_conn():
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
    return conn


def _require_ingest_analyze_lock(conn) -> None:
    """Exit(1) if another ingest/analyze process already holds the lock.

    ingest/ingest_live/analyze/analyze_all all take this (pipeline/locks.py) --
    the same lock api/routers/internal.py's cron fallback endpoint takes, so
    a scheduled CLI run and a cron poke can't collide either. Unlike that
    endpoint's BackgroundTask (no caller to report failure to, so it logs
    and skips silently), a CLI invocation has a real exit code its caller --
    a systemd timer, the Railway job runner -- can alert on, so this fails
    loudly instead of silently no-op-ing.
    """
    if not try_lock_ingest_analyze(conn):
        logger.error("Another ingest/analyze process is already running; refusing to start.")
        conn.close()
        sys.exit(1)


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
    _require_ingest_analyze_lock(conn)
    agency_id = _require_agency(args, conn)
    ch_client = get_client()
    ingest(args.folder, agency_id, conn, ch_client)
    conn.close()


def cmd_load_static(args):
    """Load a GTFS static zip into the database for one agency."""
    from pipeline.static_loader import load_static

    conn = _get_conn()
    agency_id = _require_agency(args, conn)
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
    _require_ingest_analyze_lock(conn)
    agency_id = _require_agency(args, conn)
    ch_client = get_client()
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
    _require_ingest_analyze_lock(conn)
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

    conn = _get_conn()
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
    _require_ingest_analyze_lock(conn)
    ch_client = get_client()
    if args.agency_id is not None:
        ingest_live(int(args.agency_id), conn, ch_client)
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
            ingest_live(aid, conn, ch_client)
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

    conn = _get_conn()
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
        pool = await asyncpg.create_pool(DATABASE_URL)
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

        await pool.close()

    asyncio.run(run())


def cmd_prune_query_log(args):
    """Delete ask_query_log rows older than the retention window (default 90 days)."""
    import asyncio

    import asyncpg

    days = int(args.days)

    async def run():
        """Async body executed via asyncio.run()."""
        conn = await asyncpg.connect(DATABASE_URL)
        result = await conn.execute(f"DELETE FROM ask_query_log WHERE created_at < now() - INTERVAL '{days} days'")
        logger.info(f"prune_query_log: {result}")
        await conn.close()

    asyncio.run(run())


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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
