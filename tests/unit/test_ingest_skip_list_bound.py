"""Bounding the archive ingest's already-ingested file-name skip list.

`distinct_file_names` is a full-partition scan of `updates` per agency, paid
once per archive ingest to answer a question about a folder covering a handful
of days. These cover the bound the caller may put on it, and the rule that
decides when it may put one at all — a bound that excludes a file which IS
already ingested makes that file read as new and duplicates its rows, so the
caller errs toward no bound whenever a source's date is unknowable.
"""

import pathlib
from datetime import date, datetime, timezone

from pipeline.clickhouse import distinct_file_names
from pipeline.ingest import _archive_since


class _FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClickHouse:
    def __init__(self):
        self.sql = ""
        self.parameters: dict = {}

    def query(self, sql, parameters=None):
        self.sql = sql
        self.parameters = parameters or {}
        return _FakeResult([("20260401/a.pb",)])


# ── the bounded scan ──────────────────────────────────────────────────────


def test_distinct_file_names_is_unbounded_without_a_since():
    client = FakeClickHouse()

    distinct_file_names(client, agency_id=7)

    assert "captured_at" not in client.sql
    assert "since" not in client.parameters


def test_distinct_file_names_carries_the_bound_when_given_one():
    client = FakeClickHouse()

    distinct_file_names(client, agency_id=7, since=date(2026, 4, 1))

    assert "captured_at >= {since:DateTime64}" in client.sql
    assert client.parameters["since"] == datetime(2026, 3, 31, 15, 0, tzinfo=timezone.utc)


def test_the_bound_is_jst_midnight_not_utc_midnight():
    """Archive file keys name a JST calendar day. A UTC-midnight bound would
    sit nine hours late and hide every file captured in that day's first nine
    JST hours, re-ingesting each one."""
    client = FakeClickHouse()

    distinct_file_names(client, agency_id=7, since=date(2026, 4, 1))

    assert client.parameters["since"] < datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_distinct_file_names_still_returns_the_names():
    client = FakeClickHouse()

    assert distinct_file_names(client, agency_id=7, since=date(2026, 4, 1)) == {"20260401/a.pb"}


# ── when the caller may bound at all ──────────────────────────────────────


def _p(name: str) -> pathlib.Path:
    return pathlib.Path(name)


def test_archive_since_is_the_earliest_date_the_folder_names():
    assert _archive_since(
        [_p("/a/20260403.tar.gz"), _p("/a/20260401.tar.gz")],
        [_p("/a/20260405/x.pb")],
    ) == date(2026, 4, 1)


def test_archive_since_reads_a_loose_pb_parent_directory():
    assert _archive_since([], [_p("/a/20260405/x.pb"), _p("/a/20260402/y.pb")]) == date(2026, 4, 2)


def test_archive_since_declines_when_a_tarball_carries_no_date():
    """Such a tarball's members fall back to their own inner directory, which
    cannot be read without opening the archive."""
    assert _archive_since([_p("/a/backfill.tar.gz"), _p("/a/20260401.tar.gz")], []) is None


def test_archive_since_declines_when_a_loose_pb_has_no_date_directory():
    """Its captured_at falls back to `now()`, which names no archive day."""
    assert _archive_since([], [_p("/a/loose/x.pb")]) is None


def test_archive_since_declines_on_an_empty_folder():
    assert _archive_since([], []) is None


def test_archive_since_declines_on_an_unparseable_date_token():
    assert _archive_since([_p("/a/20261301.tar.gz")], []) is None
