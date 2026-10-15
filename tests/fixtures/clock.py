"""Freezing a module's wall clock for one test."""

from __future__ import annotations

from datetime import datetime


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
