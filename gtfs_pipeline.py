#!/usr/bin/env python3
"""Thin CLI wrapper for the GTFS pipeline jobs."""
import argparse
import os
import sys
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def _require_agency(args, conn) -> int:
    if args.agency_id:
        return int(args.agency_id)
    with conn.cursor() as cur:
        cur.execute("SELECT agency_id, agency_name FROM agencies ORDER BY agency_id")
        agencies = cur.fetchall()
    if not agencies:
        print("No agencies found. Add one first:")
        print("  python gtfs_pipeline.py add_agency --name 'Agency Name' --feed-url 'http://...'")
        sys.exit(1)
    if len(agencies) == 1:
        return agencies[0][0]
    print("Multiple agencies found. Specify --agency-id:")
    for aid, name in agencies:
        print(f"  {aid}: {name}")
    sys.exit(1)


def cmd_add_agency(args):
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, static_url) "
            "VALUES (%s, %s, %s) RETURNING agency_id",
            (args.name, args.feed_url, args.static_url),
        )
        aid = cur.fetchone()[0]
    conn.commit()
    print(f"Added agency {aid}: {args.name}")
    conn.close()


def cmd_ingest(args):
    from pipeline.ingest import ingest
    conn = _get_conn()
    agency_id = _require_agency(args, conn)
    ingest(args.folder, agency_id, conn)
    conn.close()


def cmd_load_static(args):
    from pipeline.static_loader import load_static
    conn = _get_conn()
    agency_id = _require_agency(args, conn)
    load_static(args.path, agency_id, conn)
    conn.close()


def cmd_analyze(args):
    from pipeline.analyze import analyze
    conn = _get_conn()
    agency_id = _require_agency(args, conn)
    analyze(agency_id, conn)
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="GTFS pipeline CLI")
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add_agency")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--feed-url", required=True)
    p_add.add_argument("--static-url", default=None)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("folder")
    p_ingest.add_argument("--agency-id", default=None)

    p_static = sub.add_parser("load_static")
    p_static.add_argument("path")
    p_static.add_argument("--agency-id", default=None)

    p_analyze = sub.add_parser("analyze")
    p_analyze.add_argument("--agency-id", default=None)

    args = parser.parse_args()
    if args.command == "add_agency":
        cmd_add_agency(args)
    elif args.command == "ingest":
        cmd_ingest(args)
    elif args.command == "load_static":
        cmd_load_static(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
