"""The stop tables join each row to the timetable its version name archived."""

from pipeline import ch_aggregates
from pipeline.clickhouse import UPDATE_COLUMNS, insert_updates

AGENCY = 7


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
