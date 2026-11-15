"""analyze leaves every aggregate before an agency's retention floor as it
stands, once the prune has dropped the `updates` months beneath it, and
behaves as before while no month has been dropped."""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import agg_registry, ch_aggregates, feed_quality, flags, refiled
from pipeline.analyze import _AGG_TABLES_ORDERED, _INCREMENTAL_AGG_TABLES, analyze, mark_for_full_rebuild
from pipeline.clickhouse import insert_updates
from pipeline.retention import prune_updates, retention_floor
from pipeline.service_day import filed_date_of
from tests.pipeline.ch_agg_helpers import JST, ch_rows, seed_schedule, update_row

# The prune runs as of mid-March 2025 with a 30-day horizon, so only the
# January partition is past it. Dropping it sets the floor at 2025-02-02.
_NOW = datetime(2025, 3, 15, tzinfo=timezone.utc)
_FLOOR = date(2025, 2, 2)
_DATED = sorted(_INCREMENTAL_AGG_TABLES)
_UNSEEDED = {"agg_route_headway_daily", "agg_schedule_revision_daily", "agg_service_delivered_daily"}


@pytest.fixture(params=[None, "static_join"])
def pruned(request, pg_conn, ch_client, agency_id, monkeypatch):
    """An agency analyzed over January, the start of February and March,
    and the agency's every aggregate row as that analyze left them. Under
    static_join, the builders only that ingest gets run too, and its rows
    are read filed under the timetable in force."""
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE agencies SET ingest_strategy = %s WHERE agency_id = %s", (request.param, agency_id))
    pg_conn.commit()
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
            update_row(datetime(2025, 2, 2, 9, 0), "c", 50, scheduled_time="09:00:00"),
            update_row(datetime(2025, 2, 5, 8, 0), "d", 15),
            update_row(datetime(2025, 3, 10, 8, 0), "a", 75),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)
    before = _snapshot(pg_conn, ch_client, agency_id)
    empty = {table for table, rows in _below_floor(pg_conn, ch_client, agency_id).items() if not rows}
    # The rest would pass vacuously: these rows need a richer timetable than
    # seed_schedule's, or static_join for the dwell runs.
    assert empty == _UNSEEDED | ({"agg_route_daily_dwell_run"} if request.param is None else set())
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    flags.reset_cache()
    return before


def _below_floor(pg_conn, ch_client, agency_id) -> dict[str, int]:
    """How many rows each dated aggregate holds before the floor, the scorecard by month."""
    counts = {}
    with pg_conn.cursor() as cur:
        for table in (*_DATED, feed_quality.TABLE):
            column = "month" if table == feed_quality.TABLE else "date"
            cur.execute(f"SELECT count(*) FROM {table} WHERE agency_id = %s AND {column} < %s", (agency_id, _FLOOR))
            counts[table] = cur.fetchone()[0]
    for table in ch_aggregates.MONTHLY_TABLES:
        counts[f"clickhouse {table}"] = ch_client.query(
            f"SELECT count() FROM {table} WHERE agency_id = {{a:Int32}} AND date < {{floor:Date}}",
            parameters={"a": agency_id, "floor": _FLOOR},
        ).result_rows[0][0]
    return counts


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
        assert cur.fetchall() == [(_FLOOR, 1), (date(2025, 2, 5), 2), (date(2025, 3, 10), 1)]


def test_without_a_recorded_drop_analyze_purges_dates_whose_rows_are_gone(pg_conn, ch_client, agency_id, pruned):
    """No floor, no change: a month removed by anything but the prune still
    takes its aggregates with it at the next full rebuild."""
    ch_client.command("ALTER TABLE updates DROP PARTITION ID '202501'")
    mark_for_full_rebuild(pg_conn, [agency_id])
    analyze(agency_id, pg_conn, ch_client)
    assert retention_floor(pg_conn, agency_id) is None
    assert all(day >= date(2025, 2, 1) for day in _dates(pg_conn, agency_id, "agg_route_daily"))


def _built_at(pg_conn, agency_id) -> dict:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT table_name, built_at FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        return dict(cur.fetchall())


def _record_whole_builds(monkeypatch) -> list:
    calls: list = []
    real = ch_aggregates.build_whole_history

    def build_whole_history(ch, agency_id, tables, *args, **kwargs):
        calls.append(sorted(tables))
        return real(ch, agency_id, tables, *args, **kwargs)

    monkeypatch.setattr(ch_aggregates, "build_whole_history", build_whole_history)
    return calls


def test_a_run_after_a_prune_with_nothing_new_rebuilds_nothing(pg_conn, ch_client, agency_id, pruned, monkeypatch):
    """The dropped dates' ledger rows no longer match `updates`, which must
    not read as a change: report caches key on built_at."""
    _prune(pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    before = _built_at(pg_conn, agency_id)
    whole_builds = _record_whole_builds(monkeypatch)
    analyze(agency_id, pg_conn, ch_client)
    assert whole_builds == []
    assert _built_at(pg_conn, agency_id) == before
    whole = [name for name, table in agg_registry.TABLES.items() if table.kind is agg_registry.Kind.WHOLE]
    assert whole and all(name in before for name in whole)


def _delete_route_daily_row(pg_conn, agency_id, *, below_floor: bool) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM agg_route_daily WHERE ctid = (SELECT ctid FROM agg_route_daily WHERE agency_id = %s "
            f"AND date {'<' if below_floor else '>='} %s ORDER BY date LIMIT 1)",
            (agency_id, _FLOOR.replace(day=1) if below_floor else _FLOOR),
        )
        assert cur.rowcount == 1
    pg_conn.commit()


def test_a_store_disagreement_below_the_floors_month_is_final(pg_conn, ch_client, agency_id, pruned):
    """A rebuild repairs only from the floor on, so the comparison that
    decides one starts at the floor's month: a gap below it is not chased on
    every run."""
    _prune(pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    _delete_route_daily_row(pg_conn, agency_id, below_floor=True)
    before = _built_at(pg_conn, agency_id)
    analyze(agency_id, pg_conn, ch_client)
    assert _built_at(pg_conn, agency_id) == before


def test_a_store_disagreement_from_the_floor_on_is_repaired(pg_conn, ch_client, agency_id, pruned):
    _prune(pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    _delete_route_daily_row(pg_conn, agency_id, below_floor=False)
    analyze(agency_id, pg_conn, ch_client)
    assert _changed(pruned, _snapshot(pg_conn, ch_client, agency_id)) == []


def test_the_prune_holds_an_agency_whose_stores_disagree(pg_conn, ch_client, agency_id, pruned):
    _delete_route_daily_row(pg_conn, agency_id, below_floor=True)
    result = prune_updates(pg_conn, ch_client, acquire=lambda: None, days=30, now=_NOW)
    assert result.dropped == []
    assert "agg_route_daily" in result.verdicts[0].held_by
    assert retention_floor(pg_conn, agency_id) is None


def test_the_prune_holds_a_stale_date_in_the_scorecard_month_the_floor_freezes(pg_conn, ch_client, agency_id, pruned):
    """The floor lands on 2025-02-02, which freezes February's scorecard row,
    so a February date analyze has not caught up with holds the drop."""
    insert_updates(ch_client, agency_id, [update_row(datetime(2025, 2, 20, 8, 0), "d", 30)])
    result = prune_updates(pg_conn, ch_client, acquire=lambda: None, days=30, now=_NOW)
    assert result.dropped == []
    assert "2025-02-20" in result.verdicts[0].held_by


def test_dates_another_version_moved_to_below_the_floor_stay_final(pg_conn, ch_client, agency_id, pruned, monkeypatch):
    """A date whose version in force moved is rebuilt, but not below the floor."""
    from pipeline import analyze as analyze_module

    with pg_conn.cursor() as cur:
        cur.execute("SELECT ingest_strategy FROM agencies WHERE agency_id = %s", (agency_id,))
        if cur.fetchone()[0] != "static_join":
            pytest.skip("only a version-stamping ingest reads the versions in force")
    _prune(pg_conn, ch_client)
    monkeypatch.setattr(analyze_module, "_dates_in_force_moved", lambda *args: [date(2025, 1, 10)])
    analyze(agency_id, pg_conn, ch_client)
    assert _changed(pruned, _snapshot(pg_conn, ch_client, agency_id)) == []


def _final_rows(pg_conn, ch_client, agency_id) -> dict[str, list]:
    """Every row of the agency dated before the floor, in both stores, with
    its refiled writes and the scorecard months that begin before it."""
    rows: dict[str, list] = {}
    with pg_conn.cursor() as cur:
        for table in (*_DATED, feed_quality.TABLE):
            column = "month" if table == feed_quality.TABLE else "date"
            cur.execute(f"SELECT * FROM {table} WHERE agency_id = %s AND {column} < %s", (agency_id, _FLOOR))
            rows[table] = sorted(map(repr, cur.fetchall()))
    for table in ch_aggregates.MONTHLY_TABLES:
        sql, params = ch_aggregates.rows_sql(table, agency_id, None)
        result = ch_client.query(f"{sql} AND date < {{floor:Date}}", parameters={**params, "floor": _FLOOR})
        rows[f"clickhouse {table}"] = sorted(map(repr, result.result_rows))
    written = refiled.written(ch_client, agency_id)
    rows["refiled writes"] = sorted(repr((day, kept)) for day, kept in written.items() if day < _FLOOR)
    return rows


def test_a_reading_on_the_floor_date_filed_under_the_day_before_leaves_that_day_final(
    pg_conn, ch_client, agency_id, pruned
):
    """A stop event the floor date's rows file under the day before marks that
    day changed; it is final all the same."""
    _prune(pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    before = _final_rows(pg_conn, ch_client, agency_id)
    captured = datetime(2025, 2, 2, 7, 30)
    scheduled_sec = 3 * 3600 + 50 * 60
    assert filed_date_of(captured.replace(tzinfo=JST), scheduled_sec) == _FLOOR - timedelta(days=1)
    insert_updates(
        ch_client,
        agency_id,
        [update_row(captured, "c", 40, scheduled_time="03:50:00", scheduled_sec=scheduled_sec)],
    )
    analyze(agency_id, pg_conn, ch_client)
    assert _changed(before, _final_rows(pg_conn, ch_client, agency_id)) == []


def test_a_floor_date_that_lost_rows_leaves_the_day_before_final(pg_conn, ch_client, agency_id, pruned):
    """A date that lost rows marks both its neighbours changed."""
    _prune(pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    before = _final_rows(pg_conn, ch_client, agency_id)
    ch_client.command(
        "DELETE FROM updates WHERE agency_id = {agency_id:UInt16} AND trip_id = 'c' "
        "AND captured_at >= {start:DateTime64} AND captured_at < {end:DateTime64}",
        parameters={
            "agency_id": agency_id,
            "start": datetime(2025, 2, 2, 4, 0, tzinfo=JST).astimezone(timezone.utc),
            "end": datetime(2025, 2, 3, 4, 0, tzinfo=JST).astimezone(timezone.utc),
        },
    )
    remaining = ch_client.query(
        "SELECT count() FROM updates WHERE agency_id = {agency_id:UInt16} AND trip_id = 'c'",
        parameters={"agency_id": agency_id},
    ).result_rows[0][0]
    assert remaining == 1  # 2025-02-01's later reading; its earlier one went with January
    analyze(agency_id, pg_conn, ch_client)
    assert _changed(before, _final_rows(pg_conn, ch_client, agency_id)) == []
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agg_feed_health WHERE agency_id = %s AND date = %s", (agency_id, _FLOOR))
        assert cur.fetchone()[0] == 0


def test_the_prune_holds_an_agency_with_a_table_awaiting_a_new_fingerprint(pg_conn, ch_client, agency_id, pruned):
    """A timetable loaded since the last analyze rebuilds only from the floor
    on, so the drop waits for that analyze."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE agg_table_state SET fingerprint = 'before' WHERE agency_id = %s AND table_name = 'agg_stop_daily'",
            (agency_id,),
        )
    pg_conn.commit()
    result = prune_updates(pg_conn, ch_client, acquire=lambda: None, days=30, now=_NOW)
    assert result.dropped == []
    assert "agg_stop_daily" in result.verdicts[0].held_by
