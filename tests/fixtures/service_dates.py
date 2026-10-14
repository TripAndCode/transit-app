"""A resolved service filter built by hand, for tests of the query builders."""

from __future__ import annotations

from datetime import date, timedelta

from api.range import ServiceDates, ServiceType
from pipeline.day_types import THREE_WAY, DayType, DayTypes


def selecting(service: ServiceType, from_date: date, to_date: date, *dates: date) -> ServiceDates:
    """``service`` resolved over ``[from_date, to_date]`` so that it selects exactly ``dates``.

    Every other date is filed under a day type ``service`` does not select.
    """
    chosen: DayType = {"平日": "weekday", "土曜": "saturday", "日祝": "sunday_holiday", "土日祝": "saturday"}[service]
    other: DayType = "weekday" if chosen != "weekday" else "sunday_holiday"
    by_date: dict[date, DayType] = {}
    d = from_date
    while d <= to_date:
        by_date[d] = chosen if d in dates else other
        d += timedelta(days=1)
    return ServiceDates(service=service, day_types=DayTypes(buckets=THREE_WAY, by_date=by_date))
