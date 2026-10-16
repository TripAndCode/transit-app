"""Rows and probes shared by the ClickHouse aggregate tests."""

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def update_row(
    captured_jst: datetime,
    trip_id: str,
    dep_delay: int | None,
    *,
    scheduled_time: str | None = "08:10:00",
    route_code: str | None = "R1",
    service_type: str | None = "平日",
    stop_sequence: int = 1,
    file_name: str | None = None,
) -> tuple:
    """One `updates` row for `pipeline.clickhouse.insert_updates`; *captured_jst* is a naive JST wall time.

    Rows sharing a capture time share a file, as one poll's rows do.
    """
    captured = captured_jst.replace(tzinfo=JST).astimezone(timezone.utc)
    name = file_name or f"{captured_jst:%Y%m%d%H%M%S}.pb"
    return (name, captured, trip_id, service_type, scheduled_time, route_code, stop_sequence, dep_delay)


def work_tables(ch_client) -> list[str]:
    """Every aggregate work table in the test database."""
    rows = ch_client.query(
        "SELECT name FROM system.tables WHERE database = currentDatabase() AND match(name, '__work_a[0-9]+$')"
    ).result_rows
    return sorted(row[0] for row in rows)
