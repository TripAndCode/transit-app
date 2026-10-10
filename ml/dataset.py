"""Trip runs as a pandas frame: what the models learn from and are scored on.
Needs the optional `ml` dependency group."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

import pandas as pd
from clickhouse_connect.driver.client import Client

from ml.data import date_span
from ml.sql import runs_sql

RUN_COLUMNS = [
    "agency_id",
    "route_code",
    "trip_id",
    "service_date",
    "hour",
    "service",
    "delay_min",
    "stops",
    "span_min",
]


def fetch_runs(client: Client, agency_id: int, *, chunk_days: int = 7) -> pd.DataFrame:
    """An agency's runs a week at a time, the same way fetch_cells reads its cells."""
    span = date_span(client, agency_id)
    chunks: list[pd.DataFrame] = []
    if span is not None:
        start, last = span
        while start <= last:
            end = min(start + timedelta(days=chunk_days - 1), last)
            parameters = {"agency_id": agency_id, "start": start, "end": end}
            chunks.append(client.query_df(runs_sql(), parameters=parameters))
            start = end + timedelta(days=1)
    if not chunks:
        return _typed(pd.DataFrame({column: [] for column in RUN_COLUMNS}))
    frame = pd.concat(chunks, ignore_index=True).rename(columns={"mean_delay_min": "delay_min"})
    frame["agency_id"] = agency_id
    return _typed(frame)


def load_runs(client: Client, agency_ids: Iterable[int]) -> pd.DataFrame:
    agency_ids = list(agency_ids)
    if not agency_ids:
        return _typed(pd.DataFrame({column: [] for column in RUN_COLUMNS}))
    return pd.concat([fetch_runs(client, agency_id) for agency_id in agency_ids], ignore_index=True)


def _typed(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "agency_id": frame["agency_id"].astype("int16"),
            "route_code": frame["route_code"].astype("string"),
            "trip_id": frame["trip_id"].astype("string"),
            "service_date": pd.to_datetime(frame["service_date"]),
            "hour": frame["hour"].astype("int16"),
            "service": frame["service"].astype("string"),
            "delay_min": pd.to_numeric(frame["delay_min"]).astype("float32"),
            "stops": frame["stops"].astype("int16"),
            "span_min": pd.to_numeric(frame["span_min"], errors="coerce").astype("float32"),
        }
    )
