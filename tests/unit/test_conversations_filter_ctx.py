"""A conversation's stored ``filter_ctx`` is client-supplied state that was
persisted verbatim, so it gets the same validation as a live request.

It used to be fed straight into ``RangeCtx(...)``: ``date.fromisoformat``
raised an unhandled ValueError (500) on a malformed date, and an arbitrary
``dow``/``time_band``/``service`` string reached the SQL builders unchecked.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import api.range as range_mod
from api.range import DEFAULT_RANGE_DAYS
from api.routers.conversations import _ctx_from_stored_filters


@pytest.fixture
def frozen_today(monkeypatch):
    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)
    return date(2026, 1, 2)


def test_missing_filter_ctx_falls_back_to_the_default_window(frozen_today):
    ctx = _ctx_from_stored_filters(None)
    assert ctx.to_date == frozen_today
    assert ctx.from_date == frozen_today - timedelta(days=DEFAULT_RANGE_DAYS - 1)


def test_stored_filters_are_applied(frozen_today):
    ctx = _ctx_from_stored_filters(
        {
            "from_date": "2025-12-01",
            "to_date": "2025-12-31",
            "dow": "weekday",
            "time_band": "morning",
            "service": "平日",
            "routes": ["R1", "R1", "R2"],
        }
    )
    assert (ctx.from_date, ctx.to_date) == (date(2025, 12, 1), date(2025, 12, 31))
    assert (ctx.dow, ctx.time_band, ctx.service) == ("weekday", "morning", "平日")
    assert ctx.routes == ("R1", "R2")


def test_malformed_stored_date_raises_422_not_500(frozen_today):
    with pytest.raises(HTTPException) as exc:
        _ctx_from_stored_filters({"from_date": "2025-13-45", "to_date": "2025-12-31"})
    assert exc.value.status_code == 422


def test_unknown_stored_enum_raises_422(frozen_today):
    with pytest.raises(HTTPException) as exc:
        _ctx_from_stored_filters({"time_band": "brunch"})
    assert exc.value.status_code == 422


def test_stored_range_is_clamped_like_a_live_request(frozen_today):
    ctx = _ctx_from_stored_filters({"from_date": "2000-01-01", "to_date": "2099-01-01"})
    assert ctx.to_date == frozen_today
    assert ctx.days <= 365
