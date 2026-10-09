"""Reads from the replica's ClickHouse."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from clickhouse_connect.driver.client import Client

from ml.cells import Cell
from ml.sql import cells_sql


def _edge_day(client: Client, agency_id: int, order: Literal["ASC", "DESC"]) -> date | None:
    rows = client.query(
        "SELECT toDate(captured_at, 'Asia/Tokyo') FROM updates WHERE agency_id = {agency_id:UInt16} "
        f"ORDER BY captured_at {order} LIMIT 1",
        parameters={"agency_id": agency_id},
    ).result_rows
    return rows[0][0] if rows else None


def date_span(client: Client, agency_id: int) -> tuple[date, date] | None:
    """Each end via ORDER BY … LIMIT 1, not min()/max(): captured_at follows
    agency_id in updates' sort key, so the index serves it, where an aggregate
    would read every row the agency has."""
    first = _edge_day(client, agency_id, "ASC")
    last = _edge_day(client, agency_id, "DESC")
    return (first, last) if first is not None and last is not None else None


def fetch_cells(client: Client, agency_id: int, *, chunk_days: int = 7) -> list[Cell]:
    """A week at a time: the dedup holds one aggregate state per stop event,
    and over a long history those outgrow the replica's per-query memory cap.
    cells_sql orders by date first, so the chunks concatenate in order."""
    span = date_span(client, agency_id)
    if span is None:
        return []
    start, last = span
    cells: list[Cell] = []
    while start <= last:
        end = min(start + timedelta(days=chunk_days - 1), last)
        parameters = {"agency_id": agency_id, "start": start, "end": end}
        for r in client.query(cells_sql(), parameters=parameters).result_rows:
            cells.append(Cell(str(r[0]), r[1], int(r[2]), int(r[3]), float(r[4])))
        start = end + timedelta(days=1)
    return cells


def agencies_with_data(client: Client) -> list[int]:
    rows = client.query("SELECT DISTINCT agency_id FROM updates ORDER BY agency_id").result_rows
    return [int(r[0]) for r in rows]
