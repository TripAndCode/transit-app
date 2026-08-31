"""Regression coverage for the cross-table freshness invariant between
``pipeline.health.aggregate_freshness`` (reads ``agg_route_daily``) and
``pipeline.reports.network.compute_network_summary`` (reads
``agg_route_daily_dist``).

Both answer conceptually the same "is this agency's aggregate fresh" question
by comparing an agg table's latest date against the live feed's latest
completed day, but each reads a different agg table. They agree today only
because ``pipeline.analyze.analyze()`` writes every agg_* table -- including
both of these -- inside one transaction (a single ``conn.commit()``), so the
two tables' ``MAX(date)`` can never diverge for an agency that has been
analyzed. Nothing besides that single-transaction shape enforces the
invariant: a future refactor that splits the transaction, or lets one
builder fail independently of the other, could silently desync the two
tables and, with them, the admin health page from the network report.

This test seeds real per-day data, runs the actual ``analyze()`` pipeline,
and confirms the two tables' latest dates agree and the two independent
freshness computations built on top of them agree too. It then simulates
exactly the kind of non-atomic desync described above (deleting only the
newest ``agg_route_daily_dist`` row, as if that one builder had independently
failed to write the latest day) and confirms the two computations visibly
disagree in that case -- proving the earlier "they agree" assertion is not
vacuous, and that the single-transaction guarantee is the thing actually
holding them together.
"""

import os
from datetime import date, time

import asyncpg

from pipeline.analyze import analyze
from pipeline.health import aggregate_freshness
from pipeline.reports.network import compute_network_summary


def _seed_two_days(pg_conn, agency_id):
    """Insert mid-day rows across two completed civil days into Postgres
    `updates` (analyze()'s agg_feed_health/agg_stop_routes/agg_meta builders
    still read raw Postgres `updates` directly). Mid-day (11:37) keeps the
    JST/UTC civil date identical regardless of session timezone.
    """
    with pg_conn.cursor() as cur:
        for day in (1, 2):
            for seq in (1, 2, 3):
                cur.execute(
                    "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                    "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        agency_id,
                        f"f{day}_{seq}.pb",
                        f"2026-04-0{day}T11:37:00+09:00",
                        "平日_11時37分_系統44372",
                        "平日",
                        time(11, 37),
                        "44372",
                        seq,
                        seq * 60,
                    ),
                )
    pg_conn.commit()


def _seed_two_days_ch(ch_client, agency_id):
    """Mirror the same two completed civil days into ClickHouse `updates`,
    the source both freshness computations' live-side probe reads.
    """
    from pipeline.clickhouse import insert_updates

    rows = [
        (
            f"f{day}_{seq}.pb",
            f"2026-04-0{day}T02:37:00Z",
            "平日_11時37分_系統44372",
            "平日",
            "11:37",
            "44372",
            seq,
            seq * 60,
        )
        for day in (1, 2)
        for seq in (1, 2, 3)
    ]
    insert_updates(ch_client, agency_id, rows)


async def test_health_and_network_freshness_agree_after_atomic_analyze(pg_conn, ch_client, ch_async_client, agency_id):
    _seed_two_days(pg_conn, agency_id)
    _seed_two_days_ch(ch_client, agency_id)
    analyze(agency_id, pg_conn, ch_client)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM agg_route_daily WHERE agency_id = %s", (agency_id,))
        route_daily_max = cur.fetchone()[0]
        cur.execute("SELECT MAX(date) FROM agg_route_daily_dist WHERE agency_id = %s", (agency_id,))
        route_daily_dist_max = cur.fetchone()[0]

    # The invariant analyze()'s single transaction provides: both agg tables
    # land on the same latest date for an agency analyzed in one run.
    assert route_daily_max == route_daily_dist_max == date(2026, 4, 2)

    compute_network_summary.cache_clear()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        health_rows = await aggregate_freshness(conn, ch_async_client)
        health_row = next(r for r in health_rows if r.agency_id == agency_id)

        network_rows = await compute_network_summary(conn, ch_async_client, date(2026, 3, 1), date(2026, 12, 31))
        network_row = next(r for r in network_rows if r["agency_id"] == agency_id)

        # Both fresh: agg covers through the live feed's newest completed day.
        assert health_row.is_stale is False
        assert network_row["is_stale"] is False
        assert health_row.is_stale == network_row["is_stale"]

        # Simulate what a non-atomic analyze() could produce: one builder
        # (agg_route_daily_dist's) independently failing to write the
        # newest day while the other (agg_route_daily's) still has it. This
        # is not a hypothetical shape -- it's exactly what "split the
        # transaction" or "let one builder error out independently" would
        # cause. With the tables desynced, the two freshness computations
        # must now visibly disagree, proving they each depend entirely on
        # analyze()'s single-transaction guarantee for the agreement above.
        with pg_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agg_route_daily_dist WHERE agency_id = %s AND date = %s",
                (agency_id, date(2026, 4, 2)),
            )
        pg_conn.commit()
        compute_network_summary.cache_clear()

        health_rows_after = await aggregate_freshness(conn, ch_async_client)
        health_row_after = next(r for r in health_rows_after if r.agency_id == agency_id)
        network_rows_after = await compute_network_summary(
            conn, ch_async_client, date(2026, 3, 1), date(2026, 12, 31)
        )
        network_row_after = next(r for r in network_rows_after if r["agency_id"] == agency_id)

        assert health_row_after.is_stale is False  # agg_route_daily untouched, still covers 04-02
        assert network_row_after["is_stale"] is True  # agg_route_daily_dist now lags 04-02
        assert health_row_after.is_stale != network_row_after["is_stale"]
    finally:
        await conn.close()
