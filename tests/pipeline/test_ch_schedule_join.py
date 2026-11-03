"""The stop tables join each row to the timetable its version name archived."""

import logging
from datetime import date

import pytest

from pipeline import ch_aggregates, static_archive
from pipeline.clickhouse import UPDATE_COLUMNS, insert_updates
from pipeline.static_archive import ScheduleJoin

AGENCY = 7
A, B = "a" * 32, "b" * 32


def _row(captured_at: str, trip: str, seq: int, delay: int | None, stem: str | None, route: str = "R1") -> tuple:
    """One `updates` row in insert_updates' order (agency_id excluded)."""
    values = dict.fromkeys(UPDATE_COLUMNS[1:])
    values.update(
        file_name=f"f/{captured_at}/{trip}/{seq}",
        captured_at=captured_at,
        trip_id=trip,
        stop_sequence=seq,
        dep_delay=delay,
        route_code=route,
        static_version_id=stem,
    )
    return tuple(values[column] for column in UPDATE_COLUMNS[1:])


def test_the_dedup_keeps_one_event_with_its_latest_polls_version(ch_client):
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "gtfs_static_20260501"),
            _row("2026-06-01T00:05:00Z", "T1", 1, 60, "gtfs_static_20260601"),
            _row("2026-06-01T00:00:00Z", "T2", 1, 10, None),
        ],
    )
    where, params = ch_aggregates.month_where(202606)
    rows = ch_client.query(
        f"SELECT trip_id, dep_delay, static_version_id FROM ({ch_aggregates._dedup_select(where)}) ORDER BY trip_id",
        parameters={"agency_id": AGENCY, **params},
    ).result_rows
    assert rows == [("T1", 60, "gtfs_static_20260601"), ("T2", 10, "")]


def test_the_raw_source_keeps_every_rows_version(ch_client):
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "gtfs_static_20260501"),
            _row("2026-06-01T00:05:00Z", "T1", 1, None, None),
        ],
    )
    where, params = ch_aggregates.month_where(202606)
    rows = ch_client.query(
        f"SELECT static_version_id FROM ({ch_aggregates._raw_select(where)}) ORDER BY static_version_id",
        parameters={"agency_id": AGENCY, **params},
    ).result_rows
    assert rows == [("",), ("gtfs_static_20260501",)]


def _archive(ch_client, content: str, rows: list[tuple[str, int, str]]) -> None:
    """(trip_id, stop_sequence, stop_id) rows archived for AGENCY under *content*."""
    ch_client.insert(
        static_archive.ARCHIVE,
        [(AGENCY, content, trip, seq, stop, None, None) for trip, seq, stop in rows],
        column_names=[
            "agency_id",
            "content_hash",
            "trip_id",
            "stop_sequence",
            "stop_id",
            "arrival_time",
            "departure_time",
        ],
    )


def _stops_by_date(ch_client, join: ScheduleJoin) -> set[tuple]:
    sql, params = ch_aggregates.select_sql("agg_stop_daily", 202606, join=join)
    rows = ch_client.query(f"SELECT date, stop_id FROM ({sql})", parameters={"agency_id": AGENCY, **params})
    return set(rows.result_rows)


def test_rows_join_the_timetable_their_version_archived(ch_client):
    _archive(ch_client, A, [("T1", 1, "SA")])
    _archive(ch_client, B, [("T1", 1, "SB")])
    insert_updates(
        ch_client,
        AGENCY,
        [_row("2026-06-01T00:00:00Z", "T1", 1, 30, "v_a"), _row("2026-06-02T00:00:00Z", "T1", 1, 30, "v_b")],
    )
    join = ScheduleJoin(["v_a", "v_b"], [A, B], B)
    assert _stops_by_date(ch_client, join) == {(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB")}


def test_unmapped_and_unstamped_rows_join_the_loaded_timetable(ch_client):
    _archive(ch_client, B, [("T1", 1, "SB")])
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, None),
            _row("2026-06-02T00:00:00Z", "T1", 1, 30, "gtfs_static_19990101"),
        ],
    )
    assert _stops_by_date(ch_client, ScheduleJoin([], [], B)) == {(date(2026, 6, 1), "SB"), (date(2026, 6, 2), "SB")}


def test_a_static_joined_build_without_a_join_is_refused(ch_client):
    with pytest.raises(ValueError, match="ScheduleJoin"):
        ch_aggregates.build(ch_client, AGENCY, {"agg_stop_daily": ch_aggregates.Rebuild([202606], None)})


def test_a_build_reports_the_rows_that_joined_the_loaded_timetable(ch_client, caplog):
    _archive(ch_client, A, [("T1", 1, "SA")])
    _archive(ch_client, B, [("T1", 1, "SB")])
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "v_a"),
            _row("2026-06-02T00:00:00Z", "T1", 1, 30, None),
            _row("2026-06-03T00:00:00Z", "T1", 1, 30, "gtfs_static_19990101"),
        ],
    )
    join = ScheduleJoin(["v_a"], [A], B)
    with caplog.at_level(logging.INFO, logger="pipeline.ch_aggregates"):
        ch_aggregates.build(ch_client, AGENCY, {"agg_stop_daily": ch_aggregates.Rebuild([202606], None)}, join=join)
    ch_aggregates.swap(ch_client, "agg_stop_daily", AGENCY, [202606])
    stops = ch_client.query(
        "SELECT date, stop_id FROM agg_stop_daily WHERE agency_id = {a:Int32} ORDER BY date", parameters={"a": AGENCY}
    ).result_rows
    assert stops == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB"), (date(2026, 6, 3), "SB")]
    assert "2 rows of versions not archived joined the loaded timetable" in caplog.text
