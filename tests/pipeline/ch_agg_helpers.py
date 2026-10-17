"""Rows and probes shared by the ClickHouse aggregate tests."""

from datetime import datetime, time, timedelta, timezone

from pipeline import ch_aggregates

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
        "SELECT name FROM system.tables WHERE database = currentDatabase() AND match(name, {pattern:String})",
        parameters={"pattern": ch_aggregates.WORK_TABLE_PATTERN},
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


def normalized(value):
    """*value* as both stores can be compared on: floats at float32 precision, instants in UTC."""
    if isinstance(value, float):
        return float(f"{value:.7g}")
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return value


def ch_rows(ch_client, agency_id: int, table: str) -> list[str]:
    """The agency's rows of *table* in ClickHouse, normalized, sorted and rendered with str()."""
    sql, params = ch_aggregates.rows_sql(table, agency_id, None)
    return sorted(str(tuple(map(normalized, row))) for row in ch_client.query(sql, parameters=params).result_rows)


def pg_rows(conn, agency_id: int, table: str) -> list[str]:
    """The agency's rows of *table* in Postgres, in ch_rows's form."""
    columns = ", ".join(ch_aggregates.COLUMNS[table])
    with conn.cursor() as cur:
        cur.execute(f"SELECT {columns} FROM {table} WHERE agency_id = %s", (agency_id,))
        return sorted(str(tuple(map(normalized, row))) for row in cur.fetchall())


def record_builds(monkeypatch) -> list[tuple[str, list[int]]]:
    """Record each table's months from every ch_aggregates.build call, while still building."""
    calls: list[tuple[str, list[int]]] = []
    real = ch_aggregates.build

    def build(ch, agency_id, rebuilds, *args, **kwargs):
        calls.extend((table, rebuild.months) for table, rebuild in rebuilds.items())
        real(ch, agency_id, rebuilds, *args, **kwargs)

    monkeypatch.setattr(ch_aggregates, "build", build)
    return calls


def histogram_edge_rows() -> list[tuple]:
    """One observation at each histogram and threshold edge on route EDGE, service day 2026-09-14."""
    day = datetime(2026, 9, 14, 8, 0)
    delays = [-301, -300, -1, 0, 60, 61, 299, 300, 301, 1799, 1800, 7000]
    return [update_row(day, f"e{i}", delay, route_code="EDGE") for i, delay in enumerate(delays)]


def whole_history_rows() -> list[tuple]:
    """Typed observations over two months for the whole-history tables, on route WH.

    2026-09-13 is a Sunday and 2026-09-14 a Monday. The 07:05 departure is written
    both with and without seconds, and its 15:00 sibling only on Sunday, so the
    hour and the day of week both split.
    """
    sun, mon, oct_fri = datetime(2026, 9, 13, 8, 0), datetime(2026, 9, 14, 8, 0), datetime(2026, 10, 2, 8, 0)
    return [
        update_row(sun, "w1", 60, scheduled_time="7:05", route_code="WH"),
        update_row(sun, "w2", 90, scheduled_time="07:05:00", route_code="WH"),
        update_row(sun, "w3", -30, scheduled_time="15:00:00", route_code="WH"),
        update_row(mon, "w4", 60, scheduled_time="07:05", route_code="WH"),
        update_row(mon, "w5", 61, scheduled_time="07:05:00", route_code="WH"),
        update_row(oct_fri, "w6", 1, scheduled_time="07:05:00", route_code="WH"),
        update_row(oct_fri, "w7", 2, scheduled_time="07:05:00", route_code="WH"),
        # Excluded from every whole-history table: no service type, no route.
        update_row(mon, "w8", 500, scheduled_time="07:05:00", route_code="WH", service_type=None),
        update_row(mon, "w9", 500, scheduled_time="07:05:00", route_code=None),
        # In the counts with no scheduled time, so in agg_route_stats only.
        update_row(mon, "w10", 120, scheduled_time=None, route_code="WH"),
    ]
