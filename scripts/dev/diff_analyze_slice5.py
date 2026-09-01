"""Golden-fixture diff harness for the slice-5 `pipeline/analyze.py` refactor.

Seeds a fixed `updates` dataset into the throwaway test Postgres/ClickHouse
(:5544/:8124), runs `analyze()`, and dumps every `agg_*` table's contents for
that agency as sorted JSON. On first run (no golden file yet) it captures the
baseline. On later runs it diffs against that baseline and reports the exact
row(s) that changed, rather than just pass/fail.

Usage (from repo root, with the :5544/:8124 throwaway instances up):
    DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test \\
    CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8124 CLICKHOUSE_USER=transit \\
    CLICKHOUSE_PASSWORD=transit CLICKHOUSE_DATABASE=transit_test \\
    poetry run python scripts/dev/diff_analyze_slice5.py [--save]

Run once with --save against the pre-refactor code to capture the golden
fixture, then again (no flag) against the refactored code to diff.

NOTE: `tests/fixtures/baseline_outputs/analyze_slice5/golden.json` still
reflects `agg_route_stats`/`agg_route_hour`'s old `PERCENT_RANK()`-based
p50_min/p90_min formula. Since that formula was replaced with
`PERCENTILE_DISC`, a `--save` run against current `main` will change
`p90_min` for this harness's seeded rows (the median happens to land on the
same value under both formulas for this particular seed, but the 90th
percentile does not) -- expected, not a regression. This harness isn't
wired into CI/`make check`, so nothing enforces re-running `--save` after a
change like this; do so by hand next time this fixture is touched.
"""

import json
import os
import sys
import uuid
from datetime import time

import clickhouse_connect
import psycopg2

from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates

GOLDEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "tests", "fixtures", "baseline_outputs", "analyze_slice5", "golden.json"
)

_AGG_TABLES = (
    "agg_route_stats",
    "agg_route_hour",
    "agg_route_dow",
    "agg_route_hour_dow",
    "agg_daily_trend",
    "agg_route_daily",
    "agg_route_daily_dist",
    "agg_hour_daily",
    "agg_stop_seq",
    "agg_stop_daily",
    "agg_stop_routes",
    "agg_route_stop_daily",
    "agg_feed_health",
)


def _ch_client():
    return clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8124")),
        username=os.environ.get("CLICKHOUSE_USER", "transit"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", "transit"),
        database=os.environ.get("CLICKHOUSE_DATABASE", "transit_test"),
    )


def _mirror_updates_to_ch(ch_client, pg_conn, agency_id):
    """Mirror this agency's Postgres `updates` rows into ClickHouse — same
    approach as tests.conftest.mirror_updates_to_ch, reusing the same
    pipeline.clickhouse.insert_updates the real ingest path uses."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT file_name, captured_at, trip_id, service_type, scheduled_time, "
            "route_code, stop_sequence, dep_delay FROM updates WHERE agency_id = %s",
            (agency_id,),
        )
        pg_rows = cur.fetchall()
    if not pg_rows:
        return
    ch_rows = [
        (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4].strftime("%H:%M:%S") if row[4] is not None else None,
            row[5],
            row[6],
            row[7],
        )
        for row in pg_rows
    ]
    insert_updates(ch_client, agency_id, ch_rows)


def _seed(pg_conn, agency_id):
    """Same shape as tests/pipeline/test_analyze.py::_seed_updates, kept in
    lockstep on purpose so this harness's golden output stays comparable to
    that suite's characterization tests."""
    with pg_conn.cursor() as cur:
        for i in range(25):
            day = (i % 25) + 1
            seq = (i % 3) + 1
            cur.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    f"f{i}.pb",
                    f"2026-04-{day:02d}T11:37:00",
                    "平日_11時37分_系統44372",
                    "平日",
                    time(11, 37),
                    "44372",
                    seq,
                    (seq * 60) + i * 30,
                ),
            )
    pg_conn.commit()


def _snapshot(pg_conn, agency_id) -> dict:
    snapshot = {}
    with pg_conn.cursor() as cur:
        for table in _AGG_TABLES:
            cur.execute(f"SELECT * FROM {table} WHERE agency_id = %s", (agency_id,))
            col_names = [d.name for d in cur.description]
            # Drop agency_id: it's a freshly assigned SERIAL each run, not a
            # meaningful part of the behavior being compared.
            rows = [{k: v for k, v in zip(col_names, row, strict=True) if k != "agency_id"} for row in cur.fetchall()]
            rows.sort(key=lambda r: json.dumps(r, default=str, sort_keys=True))
            snapshot[table] = json.loads(json.dumps(rows, default=str, sort_keys=True))
    return snapshot


def main() -> int:
    database_url = os.environ["DATABASE_URL"]
    pg_conn = psycopg2.connect(database_url)
    with pg_conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        # Unique feed_url per run (this script is re-run pre- and post-refactor
        # against the same throwaway DB) so re-running never hits the
        # agencies.feed_url uniqueness constraint.
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("slice5-harness", f"http://example.com/feed-{uuid.uuid4()}.pb"),
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()

    ch_client = _ch_client()
    _seed(pg_conn, agency_id)
    _mirror_updates_to_ch(ch_client, pg_conn, agency_id)
    analyze(agency_id, pg_conn, ch_client)
    snapshot = _snapshot(pg_conn, agency_id)

    if "--save" in sys.argv:
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w") as f:
            json.dump(snapshot, f, indent=2, sort_keys=True, ensure_ascii=False)
        print(f"Saved golden fixture: {GOLDEN_PATH}")
        return 0

    with open(GOLDEN_PATH) as f:
        golden = json.load(f)

    divergences = []
    for table in _AGG_TABLES:
        if snapshot.get(table) != golden.get(table):
            divergences.append((table, golden.get(table), snapshot.get(table)))

    if divergences:
        print(f"{len(divergences)} table(s) diverged from golden:")
        for table, expected, actual in divergences:
            print(f"\n--- {table} ---")
            print(f"expected: {json.dumps(expected, ensure_ascii=False)}")
            print(f"actual:   {json.dumps(actual, ensure_ascii=False)}")
        return 1

    print(f"0 divergences across {len(_AGG_TABLES)} agg_* tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
