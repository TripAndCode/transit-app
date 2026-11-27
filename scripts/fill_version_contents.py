"""Fill the Postgres mirror of the timetable version map from ClickHouse.

The definition stamp names each timetable in force by a short hash of its
content, read from the mirror (pipeline.static_archive.MIRROR) rather than from
ClickHouse's static_version_map on every request. Each mapping is mirrored as
it is written; this copies the ones written before the mirror existed, each
version name at its latest content. Running it again changes nothing: a name
already mirrored at its latest mapping keeps its row, and no row ever moves
back to an earlier mapping.

The target is the environment's DATABASE_URL and CLICKHOUSE_*, as for every
pipeline command, its Postgres migrated to the mirror (make migrate); nothing
loads .env, so export it first (`set -a; . ./.env; set +a`). The dry run (the
default) counts, per agency, the names it would write and writes nothing;
--apply writes them. A name of an agency Postgres does not hold is reported
and skipped.
"""

from __future__ import annotations

import argparse
import os
import sys
from itertools import groupby

import clickhouse_connect
import psycopg2

from pipeline import static_archive
from pipeline.clickhouse import ch_conn_kwargs, get_client


def pending(mappings: list[static_archive.Mapping], mirrored: dict) -> list[static_archive.Mapping]:
    """The *mappings* the mirror (*mirrored*: (agency, name) to its row's
    archived_at) lacks, or holds at an earlier mapping: the rows
    static_archive.mirror would change."""
    return [
        m for m in mappings if (m.agency_id, m.stem) not in mirrored or mirrored[(m.agency_id, m.stem)] < m.archived_at
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the rows (default: count them)")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("refused: DATABASE_URL is not set", file=sys.stderr)
        return 1
    # The dry run only reads, and its sessions say so.
    ch = get_client() if args.apply else clickhouse_connect.get_client(**ch_conn_kwargs(), settings={"readonly": 1})
    conn = psycopg2.connect(url)
    try:
        if not args.apply:
            conn.set_session(readonly=True)
        try:
            static_archive.require_mirror(conn)
        except RuntimeError as why:
            print(f"refused: {why}", file=sys.stderr)
            return 1
        mappings = static_archive.latest_mappings(ch)
        with conn.cursor() as cur:
            cur.execute("SELECT agency_id FROM agencies")
            agencies = {row[0] for row in cur.fetchall()}
            cur.execute(f"SELECT agency_id, static_version_id, archived_at FROM {static_archive.MIRROR}")
            mirrored = {(agency, stem): at for agency, stem, at in cur.fetchall()}
        conn.rollback()
        chosen: list[static_archive.Mapping] = []
        for agency_id, own in groupby(mappings, key=lambda mapping: mapping.agency_id):
            names = list(own)
            if agency_id not in agencies:
                print(f"agency {agency_id}: {len(names)} name(s) mapped, not in Postgres; skipped")
                continue
            todo = pending(names, mirrored)
            print(
                f"agency {agency_id}: {len(names)} name(s) mapped, {len(names) - len(todo)} mirrored, "
                f"{len(todo)} to write"
            )
            chosen += todo
        if not chosen:
            print("nothing to write")
            return 0
        if not args.apply:
            print(f"dry run: {len(chosen)} row(s) would be written; --apply writes them")
            return 0
        print(f"wrote {static_archive.mirror(conn, chosen)} row(s)")
        return 0
    finally:
        conn.close()
        ch.close()


if __name__ == "__main__":
    sys.exit(main())
