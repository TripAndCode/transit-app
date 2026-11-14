"""Reading the daily GTFS-RT validator summaries from R2 into rt_validation_daily:
each document is the one scripts/rt_validate_sample.py writes, a document that
cannot be trusted is skipped by name, and a key already stored under the same
ETag is not fetched again."""

import json
import logging
from datetime import date

import pytest

from pipeline import rt_validation_ingest as ingest
from pipeline.rt_validation_ingest import ValidationReadError, ingest_validation, load_validation
from scripts import rt_validate_sample as rv

_DAY = date(2026, 11, 10)
_E022 = {
    "rule_id": "E022",
    "severity": "ERROR",
    "title": "Sequential stop_time_update times are not increasing",
    "occurrences": 120,
    "files_with_rule": 58,
}


def _summary(agency_id=8, day=_DAY, status="ok", **over):
    args = {"files_given": 60, "files_validated": 58, "rules": [_E022]} if status == "ok" else {"reason": "no archive"}
    return rv.summary(
        agency_id=agency_id,
        service_date=day,
        validator=rv.validator_identity("docker"),
        status=status,
        **{**args, **over},
    )


def _key(agency_id=8, day=_DAY):
    return rv.summary_key(agency_id, day)


def test_the_consumer_reads_the_schema_and_statuses_the_runner_writes():
    assert ingest.SCHEMA == rv.SCHEMA
    assert ingest.STATUSES == rv.STATUSES
    assert ingest.key_of(8, _DAY) == rv.summary_key(8, _DAY)


def test_an_ok_summary_becomes_its_row():
    row = ingest.parse_summary(_key(), json.dumps(_summary()).encode())
    assert row.agency_id == 8
    assert row.service_date == _DAY
    assert row.status == "ok"
    assert row.reason is None
    assert (row.files_given, row.files_validated) == (60, 58)
    assert json.loads(row.summary)["rules"] == [_E022]


def test_a_failed_summary_keeps_its_reason_and_carries_no_rule():
    row = ingest.parse_summary(_key(), json.dumps(_summary(status="no_archive")).encode())
    assert (row.status, row.reason, row.files_validated) == ("no_archive", "no archive", 0)
    assert json.loads(row.summary)["rules"] == []


def _broken(**over):
    document = _summary()
    document.update(over)
    return json.dumps(document).encode()


@pytest.mark.parametrize(
    ("key", "body"),
    [
        pytest.param(_key(), b"{not json", id="not-json"),
        pytest.param(_key(), _broken(schema=2), id="other-schema"),
        pytest.param(_key(agency_id=9), _broken(), id="agency-not-its-key"),
        pytest.param(_key(day=date(2026, 11, 11)), _broken(), id="date-not-its-key"),
        pytest.param(_key(), _broken(status="graded"), id="unknown-status"),
        pytest.param(_key(), _broken(reason="stray"), id="ok-with-a-reason"),
        pytest.param(_key(), _broken(status="no_archive", reason="gone"), id="failed-with-rules"),
        pytest.param(_key(), _broken(files_validated=61), id="more-validated-than-given"),
        pytest.param(_key(), _broken(files_given=-1), id="negative-count"),
        pytest.param(_key(), _broken(files_given=True), id="bool-count"),
        pytest.param(_key(), _broken(rules=[{**_E022, "occurrences": "120"}]), id="rule-count-not-a-number"),
        pytest.param(_key(), _broken(rules=[{**_E022, "files_with_rule": 59}]), id="rule-in-more-polls-than-validated"),
        pytest.param(_key(), _broken(rules=[_E022, _E022]), id="rule-listed-twice"),
        pytest.param(_key(), _broken(rules=[{"rule_id": "E022"}]), id="rule-missing-fields"),
        pytest.param(_key(), _broken(runtime_sec=float("nan")), id="non-finite-number"),
    ],
)
def test_a_summary_that_cannot_be_trusted_is_refused(key, body):
    with pytest.raises((ValueError, KeyError, TypeError)):
        ingest.parse_summary(key, body)


class _FakeS3:
    def __init__(self, objects: dict[str, bytes], etags: dict[str, str] | None = None, fail_listing=False):
        self.objects = objects
        self.etags = etags or {}
        self.fail_listing = fail_listing
        self.fetched: list[str] = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        if self.fail_listing:
            raise RuntimeError("AccessDenied")
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        # Two pages, so a lister that reads only the first misses keys.
        half = len(keys) // 2
        page = keys[half:] if ContinuationToken else keys[:half]
        response = {"Contents": [{"Key": k, "ETag": self.etags.get(k, f'"{k}"')} for k in page]}
        if not ContinuationToken and half:
            response.update(IsTruncated=True, NextContinuationToken="page-2")
        return response

    def get_object(self, Bucket, Key):
        self.fetched.append(Key)
        data = self.objects[Key]

        class _Body:
            def read(self):
                return data

        return {"Body": _Body()}


def test_every_page_of_the_prefix_is_listed_with_its_etag():
    objects = {_key(day=date(2026, 11, d)): b"{}" for d in range(1, 6)}
    listed = ingest.list_summaries(_FakeS3(objects), "transit-ml")
    assert sorted(listed) == sorted(objects)
    assert listed[_key(day=date(2026, 11, 1))] == f'"{_key(day=date(2026, 11, 1))}"'


def test_a_listing_failure_is_a_read_error():
    with pytest.raises(ValidationReadError):
        load_validation(object(), _FakeS3({}, fail_listing=True), "transit-ml")


def test_the_cron_sweeps_copy_never_raises(caplog):
    with caplog.at_level(logging.ERROR):
        assert ingest_validation(object(), _FakeS3({}, fail_listing=True), "transit-ml") == 0
    assert "validator summaries" in caplog.text
