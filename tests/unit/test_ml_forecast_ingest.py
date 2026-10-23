import io
import logging

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.ml_forecast_ingest import fetch_latest_manifest, ingest_forecast, prune_forecast_ml_sql


def test_prune_sql_deletes_rows_for_a_service_date_in_the_past():
    sql = prune_forecast_ml_sql(14)
    assert "DELETE FROM agg_route_forecast_ml" in sql
    assert "service_date <" in sql
    assert "14 days" in sql


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


def test_fetch_latest_manifest_picks_the_most_recent_date_prefix():
    manifest = b'{"rows": 2, "origin_date": "2026-10-12"}'
    s3 = _FakeS3(
        {
            "forecasts/2026-10-10/manifest.json": b'{"rows": 1, "origin_date": "2026-10-10"}',
            "forecasts/2026-10-12/manifest.json": manifest,
            "forecasts/2026-10-11/manifest.json": b'{"rows": 1, "origin_date": "2026-10-11"}',
        }
    )
    result = fetch_latest_manifest(s3, "transit-ml")
    assert result == {"rows": 2, "origin_date": "2026-10-12", "prefix": "forecasts/2026-10-12"}


def test_fetch_latest_manifest_returns_none_when_the_bucket_is_empty():
    assert fetch_latest_manifest(_FakeS3({}), "transit-ml") is None


class _PagedFakeS3:
    """Splits `pages` across successive list_objects_v2 calls, the way a
    real S3-compatible bucket paginates once a prefix holds more keys than
    one page -- fetch_latest_manifest must follow IsTruncated/
    NextContinuationToken rather than trusting a single response."""

    def __init__(self, pages: list[list[str]], objects: dict[str, bytes]):
        self._pages = pages
        self._objects = objects

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        index = int(ContinuationToken) if ContinuationToken else 0
        page = self._pages[index]
        truncated = index + 1 < len(self._pages)
        return {
            "Contents": [{"Key": k} for k in page],
            "IsTruncated": truncated,
            "NextContinuationToken": str(index + 1) if truncated else None,
        }

    def get_object(self, Bucket, Key):
        return {"Body": _Readable(self._objects[Key])}


def test_fetch_latest_manifest_follows_pagination_across_multiple_pages():
    # The newest manifest sorts last and sits on the second (later) page --
    # a caller that reads only the first page would miss it.
    manifest = b'{"rows": 1, "origin_date": "2026-10-12"}'
    objects = {
        "forecasts/2026-10-01/manifest.json": b'{"rows": 1, "origin_date": "2026-10-01"}',
        "forecasts/2026-10-12/manifest.json": manifest,
    }
    s3 = _PagedFakeS3(
        pages=[
            ["forecasts/2026-10-01/manifest.json"],
            ["forecasts/2026-10-12/manifest.json"],
        ],
        objects=objects,
    )
    result = fetch_latest_manifest(s3, "transit-ml")
    assert result == {"rows": 1, "origin_date": "2026-10-12", "prefix": "forecasts/2026-10-12"}


_GOOD_ROW = {
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


def _published(row: dict) -> _FakeS3:
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist([row]), buf)
    return _FakeS3(
        {
            "forecasts/2026-10-11/manifest.json": b'{"rows": 1, "origin_date": "2026-10-11"}',
            "forecasts/2026-10-11/forecast.parquet": buf.getvalue(),
        }
    )


class _UntouchedConn:
    """A connection the ingest must never reach for a publish it cannot read."""

    touched = False

    def cursor(self):
        self.touched = True
        raise AssertionError("cursor() on a corrupt publish")

    def commit(self):
        self.touched = True

    def rollback(self):
        self.touched = True


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({k: v for k, v in _GOOD_ROW.items() if k != "q90"}, id="missing-column"),
        pytest.param({**_GOOD_ROW, "origin_date": "11/10/2026"}, id="non-iso-origin-date"),
        pytest.param({**_GOOD_ROW, "generated_at": "yesterday"}, id="non-iso-generated-at"),
    ],
)
def test_ingest_forecast_answers_a_corrupt_publish_with_zero_and_logs_it(row, caplog):
    conn = _UntouchedConn()
    with caplog.at_level(logging.ERROR, logger="pipeline.ml_forecast_ingest"):
        assert ingest_forecast(conn, _published(row), "transit-ml") == 0
    assert "could not read the latest forecast" in caplog.text
    assert conn.touched is False
