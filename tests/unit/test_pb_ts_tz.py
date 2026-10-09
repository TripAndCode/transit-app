"""Archive captured_at derivation: the feed header's instant when plausible
for the name, else _ts(), which reads the name as a JST wall-clock time and
must derive the same instant regardless of host timezone.

clickhouse-connect resolves naive datetimes via the *process-local* timezone
when writing DateTime64 columns, so a naive ISO string would land every
archive row 9 hours late on a UTC host (Railway/Docker/CI). _ts() must
return a timezone-aware ISO string pinned to Asia/Tokyo, so the resulting
instant is identical no matter what TZ the host process happens to run
under.
"""

import os
import time
from datetime import datetime, timezone

import pytest

from pipeline.strategies._pb import _ts, archive_captured_at
from tests.fixtures.gtfs_rt import header_only_feed


def _instant(iso_str: str) -> datetime:
    """Parse _ts()'s output and return the absolute instant it represents."""
    dt = datetime.fromisoformat(iso_str)
    assert dt.tzinfo is not None, f"_ts() returned a naive datetime: {iso_str!r}"
    return dt.astimezone(timezone.utc)


@pytest.mark.parametrize("tz", ["UTC", "Asia/Tokyo"])
def test_ts_instant_is_independent_of_host_timezone(tz, monkeypatch):
    """Same filename+date must yield the same absolute instant under any host TZ."""
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        iso = _ts("20260115", "some_feed_150000.pb")
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    instant = _instant(iso)
    # 2026-01-15 15:00:00 JST == 2026-01-15 06:00:00 UTC, independent of host TZ.
    assert instant == datetime(2026, 1, 15, 6, 0, 0, tzinfo=timezone.utc)


def test_ts_returns_jst_offset_for_full_timestamp():
    iso = _ts("20260115", "some_feed_150000.pb")
    dt = datetime.fromisoformat(iso)
    assert dt.tzinfo is not None
    offset = dt.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 3600
    assert (dt.hour, dt.minute, dt.second) == (15, 0, 0)


def test_ts_date_only_fallback_is_jst_aware():
    iso = _ts("20260115", "no_time_in_name.pb")
    dt = datetime.fromisoformat(iso)
    assert dt.tzinfo is not None
    offset = dt.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 3600


def test_archive_captured_at_is_the_feeds_own_instant_whatever_clock_named_the_file():
    """rt-poller.sh names archive members by UTC wall clock; read as JST that
    name lands 9h early. The header timestamp is an absolute instant, so it
    wins over the name."""
    instant = datetime(2026, 9, 15, 0, 0, 11, tzinfo=timezone.utc)
    iso = archive_captured_at(header_only_feed(int(instant.timestamp())), "20260915", "TripUpdate_000011.pb")
    assert _instant(iso) == instant


def _epoch(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize(
    "raw",
    [
        header_only_feed(None),
        header_only_feed(0),
        b"\x00",
        # A frozen feed's header from two days before the name's date: taking
        # it would stamp the rows below the skip-list bound _archive_since
        # derives from the names, so a re-run would ingest the file twice.
        header_only_feed(_epoch(2026, 9, 13, 0, 0, 11)),
        # Further ahead than any zone could put the name.
        header_only_feed(_epoch(2026, 9, 20, 0, 0, 11)),
        # Decodable, but past any representable date.
        header_only_feed(99_999_999_999_999),
        header_only_feed(2**63 - 1),
    ],
    ids=[
        "no-timestamp",
        "zero",
        "undecodable",
        "before-the-names-day",
        "days-after-the-names-day",
        "past-year-9999",
        "int64-max",
    ],
)
def test_archive_captured_at_falls_back_to_the_name_without_a_usable_header_timestamp(raw):
    iso = archive_captured_at(raw, "20260915", "TripUpdate_000011.pb")
    assert _instant(iso) == _instant(_ts("20260915", "TripUpdate_000011.pb"))


def test_archive_captured_at_takes_a_header_from_the_jst_day_after_the_names_day():
    """A UTC name from late in its day is already the next JST day."""
    instant = datetime(2026, 9, 15, 22, 30, 0, tzinfo=timezone.utc)
    iso = archive_captured_at(header_only_feed(int(instant.timestamp())), "20260915", "TripUpdate_223000.pb")
    assert _instant(iso) == instant


def test_archive_captured_at_reads_the_name_when_its_date_is_not_a_date(monkeypatch):
    """Eight digits that are not a calendar date give no day to bound the
    header by, so the header cannot be checked against the skip-list bound
    and the name reading stands."""
    from pipeline.strategies import _pb

    monkeypatch.setattr(_pb, "_ts", lambda date_str, pb_name: f"name:{date_str}/{pb_name}")
    instant = datetime(2026, 9, 15, 0, 0, 11, tzinfo=timezone.utc)
    raw = header_only_feed(int(instant.timestamp()))
    assert archive_captured_at(raw, "99999913", "TripUpdate_000011.pb") == "name:99999913/TripUpdate_000011.pb"


def test_archive_captured_at_takes_the_header_when_there_is_no_date_to_bound_it():
    """A loose file with no date directory has no skip-list bound at all
    (_archive_since returns None), so the header is safe to use."""
    instant = datetime(2026, 9, 15, 0, 0, 11, tzinfo=timezone.utc)
    iso = archive_captured_at(header_only_feed(int(instant.timestamp())), "", "TripUpdate_000011.pb")
    assert _instant(iso) == instant
