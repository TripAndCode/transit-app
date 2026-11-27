"""pipeline.ingest.IngestReport: which skipped files a rerun could store."""

import struct
import tarfile
import zlib
from datetime import date

import psycopg2
import pytest

from pipeline.ingest import IngestReport


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad"),
        IndexError("short"),
        struct.error("x"),
        tarfile.ReadError("corrupt"),
        EOFError(),
        zlib.error(),
        psycopg2.DataError("invalid input syntax for type integer"),
    ],
)
def test_bytes_that_never_decode_are_permanent(exc):
    report = IngestReport()
    report.record(exc, "20260401")
    assert (report.permanent, report.transient_days, report.transient_unplaced) == (1, set(), 0)


@pytest.mark.parametrize("exc", [OSError("disk"), ConnectionResetError("reset"), RuntimeError("server error")])
def test_a_store_or_io_failure_is_transient_on_its_utc_day(exc):
    report = IngestReport()
    report.record(exc, "20260401")
    report.record(exc, None)
    assert report.transient_days == {date(2026, 4, 1)}
    assert report.errors == {date(2026, 4, 1): f"{type(exc).__name__}: {exc}"}
    assert report.transient_unplaced == 1
    assert report.permanent == 0
