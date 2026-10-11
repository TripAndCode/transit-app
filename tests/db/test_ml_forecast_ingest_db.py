import io

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.ml_forecast_ingest import ingest_forecast


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects

    def list_objects_v2(self, Bucket, Prefix):
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


@pytest.mark.asyncio
async def test_ingest_forecast_upserts_rows_from_the_latest_manifest(aconn):
    await aconn.execute(
        "INSERT INTO agencies (agency_id, agency_name, feed_url) VALUES (8, 'Test Agency', 'https://example.test') "
        "ON CONFLICT DO NOTHING"
    )
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
    count = await ingest_forecast(aconn, s3, "transit-ml")
    assert count == 1
    row = await aconn.fetchrow("SELECT mean_min FROM agg_route_forecast_ml WHERE agency_id = 8")
    assert row["mean_min"] == 3.0


@pytest.mark.asyncio
async def test_ingest_forecast_returns_zero_without_raising_when_the_manifest_is_missing(aconn):
    count = await ingest_forecast(aconn, _FakeS3({}), "transit-ml")
    assert count == 0
