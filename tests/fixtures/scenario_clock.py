"""Where a scenario's service days sit, and whether its polls can reach the
pipeline, judged from one reading of the clock.

The days sit two days before the service day in progress. Analyze saves a
day's planned trips only once its service day is over, four hours after the
JST calendar day ends, so a second scenario day anchored on the calendar would
still be in progress during a run between midnight and 04:00 JST.

Promotion moves only closed JST days, so every poll must land on one. Every
poll must also outlast the run in ``updates_live``, whose TTL drops a row
``LIVE_TTL`` after its capture.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pipeline.service_day import service_date_of

JST = ZoneInfo("Asia/Tokyo")

# updates_live's TTL, as db/clickhouse/schema.sql declares it.
LIVE_TTL = timedelta(days=3)
# An upper bound on how long one scenario's run takes, from the clock reading
# to its last promotion.
RUN_BOUND = timedelta(minutes=15)


def anchor_day(now: datetime) -> date:
    """The first service day of a scenario run at *now*."""
    return service_date_of(now) - timedelta(days=2)


def check_closed_and_live(captured: list[datetime], now: datetime) -> None:
    """Raise unless every capture instant falls on a JST day closed at *now*
    and stays in updates_live for a run that starts at *now*."""
    if not captured:
        return
    today_start = datetime.combine(now.astimezone(JST).date(), time(0), tzinfo=JST)
    if max(captured) >= today_start:
        raise RuntimeError("a poll falls on today, which is not closed")
    if min(captured) <= now - LIVE_TTL + RUN_BOUND:
        raise RuntimeError("a poll would leave updates_live before the run promotes it")
