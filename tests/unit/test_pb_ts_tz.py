"""_ts() must derive a JST-anchored instant regardless of host timezone.

Regression for the archive-ingest Blocker: _ts() used to return a naive
ISO string built from the archive filename's date+time. clickhouse-connect
resolves naive datetimes via the *process-local* timezone when writing
DateTime64 columns, so on a UTC host (Railway/Docker/CI) every archive row
landed 9 hours late relative to the JST instant the filename actually
encodes. _ts() must instead return a timezone-aware ISO string pinned to
Asia/Tokyo, so the resulting instant is identical no matter what TZ the
host process happens to run under.
"""

import os
import time
from datetime import datetime, timezone

import pytest

from pipeline.strategies._pb import _ts


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
    assert dt.utcoffset().total_seconds() == 9 * 3600
    assert (dt.hour, dt.minute, dt.second) == (15, 0, 0)


def test_ts_date_only_fallback_is_jst_aware():
    iso = _ts("20260115", "no_time_in_name.pb")
    dt = datetime.fromisoformat(iso)
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 9 * 3600
