import io
import json
from argparse import Namespace
from datetime import date, datetime, timedelta

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import gtfs_pipeline
from pipeline import ml_forecast_ingest
from pipeline.ml_forecast_ingest import ingest_forecast


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = [k for k in self._objects if k.startswith(Prefix)]
        return {"Contents": [{"Key": k} for k in keys]}

    def get_object(self, Bucket, Key):
        return {"Body": _Readable(self._objects[Key])}


class _Readable:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def _parquet_bytes(rows: list[dict]) -> bytes:
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _seed_agency(pg_conn) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_id, agency_name, feed_url) VALUES (8, 'Test Agency', 'https://example.test') "
            "ON CONFLICT DO NOTHING"
        )
    pg_conn.commit()


def test_ingest_forecast_upserts_rows_from_the_latest_manifest(pg_conn):
    _seed_agency(pg_conn)
    rows = [
        {
            "agency_id": 8,
            "route_code": "R1",
            "service_date": "2026-10-12",
            "hour": 8,
            "mean_min": 3.0,
            "q10": 1.0,
            "q50": 3.0,
            "q90": 5.0,
            "model_version": "2026-10-10",
            "origin_date": "2026-10-11",
            "generated_at": "2026-10-11T02:00:00",
        }
    ]
    s3 = _FakeS3(
        {
            "forecasts/2026-10-11/manifest.json": b'{"rows": 1, "origin_date": "2026-10-11"}',
            "forecasts/2026-10-11/forecast.parquet": _parquet_bytes(rows),
        }
    )
    count = ingest_forecast(pg_conn, s3, "transit-ml")
    assert count == 1
    with pg_conn.cursor() as cur:
        cur.execute("SELECT mean_min FROM agg_route_forecast_ml WHERE agency_id = 8")
        assert cur.fetchone()[0] == 3.0


def test_ingest_forecast_handles_a_production_shaped_parquet_frame(pg_conn):
    """ml.cli._predict writes service_date from a pandas datetime64 column
    (round-trips through parquet as a native datetime, not a date or a str)
    and origin_date/generated_at as plain ISO strings -- this fixture matches
    that shape instead of the all-string shape the other test here uses."""
    _seed_agency(pg_conn)
    frame = pd.DataFrame(
        [
            {
                "agency_id": 8,
                "route_code": "R1",
                "service_date": pd.Timestamp("2026-10-12"),
                "hour": 8,
                "mean_min": 3.0,
                "q10": 1.0,
                "q50": 3.0,
                "q90": 5.0,
                "model_version": "2026-10-10",
                "origin_date": "2026-10-11",
                "generated_at": datetime(2026, 10, 11, 2, 0, 0).isoformat(),
            }
        ]
    )
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), buf)
    s3 = _FakeS3(
        {
            "forecasts/2026-10-11/manifest.json": b'{"rows": 1, "origin_date": "2026-10-11"}',
            "forecasts/2026-10-11/forecast.parquet": buf.getvalue(),
        }
    )
    count = ingest_forecast(pg_conn, s3, "transit-ml")
    assert count == 1
    with pg_conn.cursor() as cur:
        cur.execute("SELECT service_date FROM agg_route_forecast_ml WHERE agency_id = 8")
        assert cur.fetchone()[0].isoformat() == "2026-10-12"


def test_ingest_forecast_upserts_rather_than_duplicates_the_same_slot(pg_conn):
    _seed_agency(pg_conn)

    def _rows(mean_min: float) -> list[dict]:
        return [
            {
                "agency_id": 8,
                "route_code": "R1",
                "service_date": "2026-10-12",
                "hour": 8,
                "mean_min": mean_min,
                "q10": 1.0,
                "q50": mean_min,
                "q90": 5.0,
                "model_version": "v",
                "origin_date": "2026-10-11",
                "generated_at": "2026-10-11T02:00:00",
            }
        ]

    s3_first = _FakeS3(
        {
            "forecasts/2026-10-11/manifest.json": b'{"rows": 1}',
            "forecasts/2026-10-11/forecast.parquet": _parquet_bytes(_rows(3.0)),
        }
    )
    assert ingest_forecast(pg_conn, s3_first, "transit-ml") == 1
    s3_second = _FakeS3(
        {
            "forecasts/2026-10-12/manifest.json": b'{"rows": 1}',
            "forecasts/2026-10-12/forecast.parquet": _parquet_bytes(_rows(4.0)),
        }
    )
    assert ingest_forecast(pg_conn, s3_second, "transit-ml") == 1
    with pg_conn.cursor() as cur:
        cur.execute("SELECT mean_min FROM agg_route_forecast_ml WHERE agency_id = 8")
        rows = cur.fetchall()
        assert rows == [(4.0,)]


def test_ingest_forecast_returns_zero_without_raising_when_the_manifest_is_missing(pg_conn):
    count = ingest_forecast(pg_conn, _FakeS3({}), "transit-ml")
    assert count == 0


def test_the_ingest_ml_command_upserts_the_latest_forecast_through_a_real_connection(pg_conn, monkeypatch):
    """Runs the operator command end to end on the connection `_get_conn()`
    really returns, which a mocked connection cannot stand in for: that one
    arrives mid-transaction from its schema probe."""
    _seed_agency(pg_conn)
    origin = date.today()
    rows = [
        {
            "agency_id": 8,
            "route_code": "R1",
            "service_date": (origin + timedelta(days=1)).isoformat(),
            "hour": 8,
            "mean_min": 4.5,
            "q10": 1.0,
            "q50": 4.0,
            "q90": 8.0,
            "model_version": "test",
            "origin_date": origin.isoformat(),
            "generated_at": f"{origin.isoformat()}T11:00:00",
        }
    ]
    prefix = f"forecasts/{origin.isoformat()}"
    s3 = _FakeS3(
        {
            f"{prefix}/manifest.json": b'{"rows": 1}',
            f"{prefix}/forecast.parquet": _parquet_bytes(rows),
            f"evaluations/{origin.isoformat()}/summary.json": json.dumps(
                {
                    "schema": 1,
                    "run_date": origin.isoformat(),
                    "adopted": True,
                    "headline": {"skill": 0.06, "coverage": 0.8},
                }
            ).encode(),
        }
    )
    monkeypatch.setattr(ml_forecast_ingest, "s3_client_from_env", lambda: (s3, "transit-ml"))

    gtfs_pipeline.cmd_ingest_ml(Namespace())

    with pg_conn.cursor() as cur:
        cur.execute("SELECT mean_min FROM agg_route_forecast_ml WHERE agency_id = 8 AND route_code = 'R1'")
        assert cur.fetchone()[0] == 4.5
        cur.execute("SELECT adopted FROM ml_evaluations WHERE run_date = %s", (origin,))
        assert cur.fetchone()[0] is True
