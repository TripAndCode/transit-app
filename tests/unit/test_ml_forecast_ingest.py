import io
import logging

import psycopg2
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.ml_forecast_ingest import (
    ForecastIngest,
    ForecastReadError,
    ForecastWriteError,
    StoreNotConfigured,
    fetch_latest_manifest,
    ingest_forecast,
    load_forecast,
    prune_forecast_ml_sql,
    s3_client_from_env,
)


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


class _DroppedConn:
    """A connection the server has dropped: every call on it raises, the
    rollback included, as psycopg2 does once the connection is closed."""

    def cursor(self):
        raise psycopg2.OperationalError("server closed the connection unexpectedly")

    def commit(self):
        raise psycopg2.InterfaceError("connection already closed")

    def rollback(self):
        raise psycopg2.InterfaceError("connection already closed")


def test_ingest_forecast_answers_a_dropped_connection_with_zero_and_logs_it(caplog):
    with caplog.at_level(logging.ERROR, logger="pipeline.ml_forecast_ingest"):
        assert ingest_forecast(_DroppedConn(), _published(_GOOD_ROW), "transit-ml") == 0
    assert "could not upsert the latest forecast" in caplog.text
    assert "server closed the connection unexpectedly" in caplog.text


_STORE_VARS = ("ML_STORE_ENDPOINT", "ML_STORE_BUCKET", "ML_STORE_ACCESS_KEY_ID", "ML_STORE_SECRET_ACCESS_KEY")


def test_load_forecast_reports_nothing_published_without_touching_the_db():
    conn = _UntouchedConn()
    assert load_forecast(conn, _FakeS3({}), "transit-ml") == ForecastIngest(prefix=None, rows=0)
    assert conn.touched is False


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({k: v for k, v in _GOOD_ROW.items() if k != "q90"}, id="missing-column"),
        pytest.param({**_GOOD_ROW, "origin_date": "11/10/2026"}, id="non-iso-origin-date"),
    ],
)
def test_load_forecast_raises_a_read_error_for_a_corrupt_publish(row):
    conn = _UntouchedConn()
    with pytest.raises(ForecastReadError):
        load_forecast(conn, _published(row), "transit-ml")
    assert conn.touched is False


def test_load_forecast_raises_a_write_error_carrying_the_db_failure():
    with pytest.raises(ForecastWriteError, match="server closed the connection unexpectedly"):
        load_forecast(_DroppedConn(), _published(_GOOD_ROW), "transit-ml")


class _RecordingConn:
    """Accepts every statement; records commit."""

    def __init__(self):
        self.committed = False
        self.statements: list[str] = []

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, *args):
                conn.statements.append(sql)

        return _Cur()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def test_load_forecast_returns_the_prefix_and_row_count(monkeypatch):
    # load_forecast imports execute_values at call time, so patching the
    # module attribute is what it picks up.
    calls = []
    monkeypatch.setattr("psycopg2.extras.execute_values", lambda cur, sql, rows: calls.append(len(rows)))
    conn = _RecordingConn()
    result = load_forecast(conn, _published(_GOOD_ROW), "transit-ml")
    assert result == ForecastIngest(prefix="forecasts/2026-10-11", rows=1)
    assert calls == [1]
    assert any("DELETE FROM agg_route_forecast_ml" in sql for sql in conn.statements)
    assert conn.committed is True


def test_s3_client_from_env_names_every_missing_variable(monkeypatch):
    for name in _STORE_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    with pytest.raises(StoreNotConfigured) as excinfo:
        s3_client_from_env()
    message = str(excinfo.value)
    assert "ML_STORE_ENDPOINT" in message
    assert "ML_STORE_ACCESS_KEY_ID" in message
    assert "ML_STORE_SECRET_ACCESS_KEY" in message
    assert "ML_STORE_BUCKET" not in message


def test_s3_client_from_env_returns_a_client_and_the_bucket(monkeypatch):
    for name in _STORE_VARS:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("ML_STORE_BUCKET", "transit-ml")
    monkeypatch.setenv("ML_STORE_ENDPOINT", "https://example.r2")
    client, bucket = s3_client_from_env()
    assert bucket == "transit-ml"
    assert hasattr(client, "list_objects_v2")
