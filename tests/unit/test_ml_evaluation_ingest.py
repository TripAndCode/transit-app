"""Reading the weekly evaluation summaries from R2 into ml_evaluations: every
published week is upserted; a document that cannot be trusted is skipped by
name rather than aborting the rest."""

import json
import logging

import psycopg2
import pytest

from pipeline.ml_evaluation_ingest import (
    EvaluationIngest,
    EvaluationReadError,
    EvaluationWriteError,
    ingest_evaluations,
    load_evaluations,
)
from pipeline.ml_forecast_ingest import list_keys


def _summary(run_date, adopted=True, skill=0.063, schema=1):
    return {"schema": schema, "run_date": run_date, "adopted": adopted, "headline": {"skill": skill, "coverage": 0.8}}


class _FakeS3:
    def __init__(self, objects: dict[str, bytes], fail_listing=False):
        self._objects = objects
        self._fail_listing = fail_listing

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        if self._fail_listing:
            raise RuntimeError("AccessDenied")
        return {"Contents": [{"Key": k} for k in self._objects if k.startswith(Prefix)]}

    def get_object(self, Bucket, Key):
        data = self._objects[Key]

        class _Body:
            def read(self):
                return data

        return {"Body": _Body()}


def _published(*summaries, extra=None):
    objects = {f"evaluations/{s['run_date']}/summary.json": json.dumps(s).encode() for s in summaries}
    objects.update(extra or {})
    return _FakeS3(objects)


class _RecordingConn:
    def __init__(self):
        self.committed = False

    def cursor(self):
        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, *args):
                pass

        return _Cur()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    rows = []
    monkeypatch.setattr("psycopg2.extras.execute_values", lambda cur, sql, values, **kwargs: rows.extend(values))
    return rows


def test_list_keys_keeps_the_suffix_under_the_prefix():
    s3 = _FakeS3(
        {
            "evaluations/2026-10-18/summary.json": b"{}",
            "evaluations/2026-10-18/x.txt": b"",
            "forecasts/a/manifest.json": b"",
        }
    )
    assert list_keys(s3, "transit-ml", "evaluations/", "/summary.json") == ["evaluations/2026-10-18/summary.json"]


def test_every_published_week_is_upserted(captured):
    conn = _RecordingConn()
    store = _published(_summary("2026-10-18"), _summary("2026-10-25", skill=0.051))
    result = load_evaluations(conn, store, "transit-ml")
    assert result == EvaluationIngest(listed=2, upserted=2, skipped=())
    assert sorted((str(r[0]), r[1], r[2], r[3]) for r in captured) == [
        ("2026-10-18", 1, True, 0.063),
        ("2026-10-25", 1, True, 0.051),
    ]
    assert json.loads(captured[0][4])["schema"] == 1
    assert conn.committed


def test_nothing_published_is_zero_not_an_error(captured):
    conn = _RecordingConn()
    assert load_evaluations(conn, _FakeS3({}), "transit-ml") == EvaluationIngest(listed=0, upserted=0, skipped=())
    assert captured == []


_KEY = "evaluations/2026-11-01/summary.json"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"{not json", id="corrupt-json"),
        pytest.param(json.dumps(_summary("2026-11-01", schema=2)).encode(), id="newer-schema"),
        pytest.param(json.dumps(_summary("2026-10-25")).encode(), id="run-date-mismatch"),
        pytest.param(json.dumps({"schema": 1, "run_date": "2026-11-01"}).encode(), id="missing-field"),
        pytest.param(
            json.dumps({**_summary("2026-11-01"), "agencies": [{"lgbm_mae": float("nan")}]}).encode(),
            id="non-finite-number",
        ),
    ],
)
def test_one_untrustworthy_document_is_skipped_by_name_and_the_rest_ingested(captured, caplog, body):
    conn = _RecordingConn()
    with caplog.at_level(logging.WARNING, logger="pipeline.ml_evaluation_ingest"):
        result = load_evaluations(conn, _published(_summary("2026-10-18"), extra={_KEY: body}), "transit-ml")
    assert result == EvaluationIngest(listed=2, upserted=1, skipped=(_KEY,))
    assert [str(r[0]) for r in captured] == ["2026-10-18"]
    assert _KEY in caplog.text


def test_a_failed_listing_raises_a_read_error():
    with pytest.raises(EvaluationReadError, match="AccessDenied"):
        load_evaluations(_RecordingConn(), _FakeS3({}, fail_listing=True), "transit-ml")


class _DroppedConn:
    def cursor(self):
        raise psycopg2.OperationalError("server closed the connection unexpectedly")

    def commit(self):
        raise psycopg2.InterfaceError("connection already closed")

    def rollback(self):
        raise psycopg2.InterfaceError("connection already closed")


def test_a_failed_upsert_raises_a_write_error():
    with pytest.raises(EvaluationWriteError, match="server closed the connection"):
        load_evaluations(_DroppedConn(), _published(_summary("2026-10-18")), "transit-ml")


def test_the_cron_wrapper_never_raises(caplog):
    with caplog.at_level(logging.ERROR, logger="pipeline.ml_evaluation_ingest"):
        assert ingest_evaluations(_DroppedConn(), _published(_summary("2026-10-18")), "transit-ml") == 0
        assert ingest_evaluations(_RecordingConn(), _FakeS3({}, fail_listing=True), "transit-ml") == 0
    assert "could not upsert the evaluation summaries" in caplog.text
    assert "could not list the evaluation summaries" in caplog.text


class _FailingGetS3(_FakeS3):
    def get_object(self, Bucket, Key):
        raise RuntimeError("AccessDenied on GetObject")


def test_a_failed_fetch_is_a_read_error_not_a_skipped_document(captured):
    store = _FailingGetS3({"evaluations/2026-10-18/summary.json": json.dumps(_summary("2026-10-18")).encode()})
    with pytest.raises(EvaluationReadError, match="AccessDenied on GetObject"):
        load_evaluations(_RecordingConn(), store, "transit-ml")
    assert captured == []
