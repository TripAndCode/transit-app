"""Freezing a module's wall clock for one test, and anchoring a fixture to a JST day."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from api.range import jst_today

JST = timezone(timedelta(hours=9))


def freeze_now(monkeypatch, module, moment: datetime) -> None:
    """Make ``module.datetime.now()`` return ``moment`` for this test.

    The module must read the clock as ``datetime.now(...)`` through its own
    ``datetime`` name on every call, which is what gets replaced.
    """

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment

    monkeypatch.setattr(module, "datetime", FrozenDateTime)


def jst_midday(day: date | None = None) -> datetime:
    """A UTC instant at noon of the JST day ``day``, today by default.

    Queries bucket by the JST calendar (``api.range.jst_today``) and by service
    date, which turns over at 04:00 JST. Seeding at ``datetime.now()`` puts rows
    on whichever side of either boundary the clock happens to be. A fixture
    that inserts one row per minute then straddles two days when CI runs near
    one, and per-day assertions come apart: six trips become five and one.

    Midday leaves twelve hours of headroom to midnight either side and eight
    to the service-day cutover, which no fixture comes close to using. A test
    that also names the day in its request passes the same ``day`` here, so the
    seed and the request read the clock once.
    """
    return datetime.combine(day or jst_today(), time(12, 0), tzinfo=JST).astimezone(timezone.utc)
