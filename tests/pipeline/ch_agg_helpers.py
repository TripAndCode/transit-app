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


def edge_rows() -> list[tuple]:
    """Observations at the edges a ported builder must match Postgres on, all on service day 2026-09-14."""
    day = datetime(2026, 9, 14, 8, 0)
    return [
        # Mean ties of 1.5 s and -1.5 s, which Postgres rounds away from zero.
        update_row(day, "t1", 1, scheduled_time="07:10:00", route_code="TIE+"),
        update_row(day, "t2", 2, scheduled_time="07:20", route_code="TIE+"),
        update_row(day, "t3", -1, scheduled_time="09:00:00", route_code="TIE-"),
        update_row(day, "t4", -2, scheduled_time="09:59:59", route_code="TIE-"),
        # The first and the last hour, one written without a leading zero.
        update_row(day, "t5", 30, scheduled_time="0:05"),
        update_row(day, "t6", -30, scheduled_time="23:59:00"),
        # No scheduled time: in the route tables, not in agg_hour_daily.
        update_row(day, "t7", 600, scheduled_time=None),
        # Polled twice: only the later estimate counts.
        update_row(day, "t8", 999, scheduled_time="10:00:00"),
        update_row(day + timedelta(minutes=1), "t8", 60, scheduled_time="10:00:00"),
        # Beyond the plausibility clamp: the dedup drops it.
        update_row(day, "t9", 10**6, scheduled_time="10:00:00"),
        # 03:30 on the 15th is still service day 2026-09-14.
        update_row(datetime(2026, 9, 15, 3, 30), "t10", 45, scheduled_time="03:30:00"),
        # Keys the route tables keep apart: no service type, a lower-case route, no route.
        update_row(day, "t11", 90, scheduled_time=None, service_type=None),
        update_row(day, "t12", 120, scheduled_time=None, route_code="r1"),
        update_row(day, "t13", 50, scheduled_time=None, route_code=None),
    ]
