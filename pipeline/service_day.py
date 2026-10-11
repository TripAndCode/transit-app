"""The service day: the calendar day an observation is filed under.

A trip that runs from 23:40 to 00:30 belongs to one service day, not two, and a
00:15 departure on a Friday night is Friday's service. Filing observations by
their capture date splits such a trip across two dates, so the post-midnight
stops are counted twice (once with the stale pre-midnight estimate) and land
under the wrong date and weekday.

The service day therefore turns over at ``SERVICE_DAY_CUTOVER_HOURS`` in Japan
time rather than at midnight: an observation captured before that hour belongs
to the previous calendar day. The hour sits between the last after-midnight
departure and the first morning one, so every trip falls inside one service
day. Every query that keys, filters or buckets by the date of an observation
uses this one definition so the dedup, the aggregates and the range filters
cannot disagree at the edge of a day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")

SERVICE_DAY_CUTOVER_HOURS = 4


def service_date_ch(column: str = "captured_at") -> str:
    """The ClickHouse expression for the service date of ``column``.

    Shifted back by the cutover before the Asia/Tokyo day is taken. Never a bare
    ``toDate(column)``: the stored instant is UTC, and a UTC day is the wrong
    civil day for every row captured between 00:00 and 09:00 in Japan.
    """
    return f"toDate({column} - INTERVAL {SERVICE_DAY_CUTOVER_HOURS} HOUR, 'Asia/Tokyo')"


def service_date_of(moment: datetime) -> date:
    """The service date a timestamp belongs to, matching :func:`service_date_ch`."""
    return (moment.astimezone(_JST) - timedelta(hours=SERVICE_DAY_CUTOVER_HOURS)).date()
