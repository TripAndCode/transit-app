"""analyze leaves every aggregate before an agency's retention floor as it
stands, once the prune has dropped the `updates` months beneath it, and
behaves as before while no month has been dropped."""

from datetime import date, datetime, timezone

import pytest

from pipeline import ch_aggregates, feed_quality, flags
from pipeline.analyze import _AGG_TABLES_ORDERED, analyze, mark_for_full_rebuild
from pipeline.clickhouse import insert_updates
from pipeline.retention import prune_updates, retention_floor
from tests.pipeline.ch_agg_helpers import ch_rows, seed_schedule, update_row

# The prune runs as of mid-March 2025 with a 30-day horizon, so only the
# January partition is past it. Dropping it sets the floor at 2025-02-03.
_NOW = datetime(2025, 3, 15, tzinfo=timezone.utc)
_FLOOR = date(2025, 2, 3)


@pytest.fixture
def pruned(pg_conn, ch_client, agency_id, monkeypatch):
    """An agency analyzed over January, the start of February and March,
    and the agency's every aggregate row as that analyze left them."""
    seed_schedule(pg_conn, agency_id, ["a", "b", "c", "d"])
    insert_updates(
        ch_client,
        agency_id,
        [
            update_row(datetime(2025, 1, 10, 8, 0), "a", 60),
            update_row(datetime(2025, 1, 11, 8, 0), "b", 120, route_code="R2"),
            update_row(datetime(2025, 1, 12, 8, 0), "a", 30),
            # Service day 2025-02-01 straddles the partition boundary
            # (09:00 JST): its first reading goes with January.
            update_row(datetime(2025, 2, 1, 8, 0), "c", 90),
            update_row(datetime(2025, 2, 1, 12, 0), "c", 45, scheduled_time="12:00:00"),
            update_row(datetime(2025, 2, 5, 8, 0), "d", 15),
            update_row(datetime(2025, 3, 10, 8, 0), "a", 75),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)
    before = _snapshot(pg_conn, ch_client, agency_id)
    assert any(day < _FLOOR for day in _dates(pg_conn, agency_id, "agg_route_daily")), "nothing below the floor"
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    flags.reset_cache()
    return before


def _dates(pg_conn, agency_id, table) -> list[date]:
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT date FROM {table} WHERE agency_id = %s ORDER BY date", (agency_id,))
        return [row[0] for row in cur.fetchall()]


def _snapshot(pg_conn, ch_client, agency_id) -> dict[str, list]:
    """Every aggregate row of the agency in both stores, the scorecard included."""
    snapshot: dict[str, list] = {}
    with pg_conn.cursor() as cur:
        for table in (*_AGG_TABLES_ORDERED, feed_quality.TABLE):
            cur.execute(f"SELECT * FROM {table} WHERE agency_id = %s", (agency_id,))
            snapshot[table] = sorted(map(repr, cur.fetchall()))
    for table in ch_aggregates.ALL_TABLES:
        snapshot[f"clickhouse {table}"] = ch_rows(ch_client, agency_id, table)
    return snapshot


def _changed(before: dict, after: dict) -> list[str]:
    return [table for table in before if before[table] != after[table]]


def _prune(pg_conn, ch_client) -> list[str]:
    return prune_updates(pg_conn, ch_client, acquire=lambda: None, days=30, now=_NOW).dropped


def test_the_prune_sets_the_floor_past_the_month_it_drops(pg_conn, ch_client, agency_id, pruned):
    assert retention_floor(pg_conn, agency_id) is None
    assert _prune(pg_conn, ch_client) == ["202501"]
    assert retention_floor(pg_conn, agency_id) == _FLOOR


def test_a_dropped_months_aggregates_survive_the_next_analyze(pg_conn, ch_client, agency_id, pruned):
    """Its ledger dates have lost their rows, which reads as changed dates."""
    _prune(pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    assert _changed(pruned, _snapshot(pg_conn, ch_client, agency_id)) == []


def test_a_dropped_months_aggregates_survive_a_full_rebuild(pg_conn, ch_client, agency_id, pruned):
    _prune(pg_conn, ch_client)
    mark_for_full_rebuild(pg_conn, [agency_id])
    analyze(agency_id, pg_conn, ch_client)
    assert _changed(pruned, _snapshot(pg_conn, ch_client, agency_id)) == []


def test_a_full_rebuild_still_rebuilds_the_dates_from_the_floor_on(pg_conn, ch_client, agency_id, pruned):
    _prune(pg_conn, ch_client)
    insert_updates(ch_client, agency_id, [update_row(datetime(2025, 2, 5, 8, 30), "d", 600)])
    mark_for_full_rebuild(pg_conn, [agency_id])
    analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, total_rows FROM agg_feed_health WHERE agency_id = %s AND date >= %s ORDER BY date",
            (agency_id, _FLOOR),
        )
        assert cur.fetchall() == [(date(2025, 2, 5), 2), (date(2025, 3, 10), 1)]


def test_without_a_recorded_drop_analyze_purges_dates_whose_rows_are_gone(pg_conn, ch_client, agency_id, pruned):
    """No floor, no change: a month removed by anything but the prune still
    takes its aggregates with it at the next full rebuild."""
    ch_client.command("ALTER TABLE updates DROP PARTITION ID '202501'")
    mark_for_full_rebuild(pg_conn, [agency_id])
    analyze(agency_id, pg_conn, ch_client)
    assert retention_floor(pg_conn, agency_id) is None
    assert all(day >= date(2025, 2, 1) for day in _dates(pg_conn, agency_id, "agg_route_daily"))
