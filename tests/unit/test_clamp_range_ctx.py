"""`api.range.clamp_range_ctx` is the single clamp/validate path every
RangeCtx entry point goes through (the FastAPI query dependency, the Ask
request body, the Ask-dashboard query params, and a conversation's stored
``filter_ctx``).

Each rule pinned here used to exist in three hand-copied variants that had
already drifted apart: one silently swallowed malformed dates, one skipped
the route cap, one skipped enum validation entirely. Divergence is the bug
class these tests guard against, so they assert the rules on the shared
function rather than on any one caller.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import api.range as range_mod
from api.range import DEFAULT_RANGE_DAYS, MAX_RANGE_DAYS, clamp_range_ctx


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin ``jst_today()`` to 2026-01-02 (JST) for deterministic windows."""
    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)
    return date(2026, 1, 2)


def _call(**over):
    kwargs = dict(from_=None, to=None, dow="all", time_band="all", service="all", routes=())
    kwargs.update(over)
    return clamp_range_ctx(**kwargs)


def test_defaults_to_trailing_window_ending_today(frozen_today):
    ctx = _call()
    assert ctx.to_date == frozen_today
    assert ctx.from_date == frozen_today - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    assert ctx.days == DEFAULT_RANGE_DAYS


def test_empty_strings_are_treated_as_absent(frozen_today):
    ctx = _call(from_="", to="")
    assert ctx.to_date == frozen_today
    assert ctx.from_date == frozen_today - timedelta(days=DEFAULT_RANGE_DAYS - 1)


def test_accepts_date_objects_as_well_as_iso_strings(frozen_today):
    ctx = _call(from_=date(2025, 12, 1), to=date(2025, 12, 31))
    assert (ctx.from_date, ctx.to_date) == (date(2025, 12, 1), date(2025, 12, 31))


def test_reversed_range_is_swapped(frozen_today):
    ctx = _call(from_="2025-12-31", to="2025-12-01")
    assert (ctx.from_date, ctx.to_date) == (date(2025, 12, 1), date(2025, 12, 31))


def test_future_to_date_is_clamped_to_today(frozen_today):
    ctx = _call(from_="2025-12-01", to="2099-01-01")
    assert ctx.to_date == frozen_today


def test_future_from_date_is_clamped_to_today_and_not_swapped_back(frozen_today):
    """A wholly-future window collapses onto today rather than re-opening a
    future ``to_date`` via the reversed-range swap."""
    ctx = _call(from_="2099-01-01", to="2099-02-01")
    assert ctx.to_date == frozen_today
    assert ctx.from_date == frozen_today


def test_overwide_range_is_clamped_at_the_start(frozen_today):
    ctx = _call(from_="2000-01-01", to="2025-12-31")
    assert ctx.to_date == date(2025, 12, 31)
    assert ctx.from_date == date(2025, 12, 31) - timedelta(days=MAX_RANGE_DAYS - 1)
    assert ctx.days == MAX_RANGE_DAYS


@pytest.mark.parametrize("field", ["from_", "to"])
def test_malformed_date_is_rejected_not_silently_defaulted(frozen_today, field):
    """A non-empty but unparseable date used to fall through to the default
    window, so a typo silently returned data for a different period."""
    with pytest.raises(HTTPException) as exc:
        _call(**{field: "not-a-date"})
    assert exc.value.status_code == 422


def test_out_of_calendar_date_is_rejected(frozen_today):
    with pytest.raises(HTTPException) as exc:
        _call(from_="2026-02-30")
    assert exc.value.status_code == 422


@pytest.mark.parametrize("value", ["all", "weekday", "weekend"])
def test_valid_dow_passes_through(frozen_today, value):
    assert _call(dow=value).dow == value


@pytest.mark.parametrize("value", ["morning", "night", "late_night", "all"])
def test_valid_time_band_passes_through(frozen_today, value):
    assert _call(time_band=value).time_band == value


@pytest.mark.parametrize("value", ["all", "平日", "土日祝"])
def test_valid_service_passes_through(frozen_today, value):
    assert _call(service=value).service == value


@pytest.mark.parametrize(
    "field,value",
    [("dow", "tuesday"), ("time_band", "brunch"), ("service", "祝日")],
)
def test_unknown_enum_value_is_rejected(frozen_today, field, value):
    """Unknown enums used to be coerced to 'all', quietly answering a
    different question than the one asked."""
    with pytest.raises(HTTPException) as exc:
        _call(**{field: value})
    assert exc.value.status_code == 422


def test_routes_are_deduped_stripped_and_order_preserved(frozen_today):
    ctx = _call(routes=[" R2 ", "R1", "R2", "", "   ", "R3"])
    assert ctx.routes == ("R2", "R1", "R3")


def test_routes_are_capped_at_100(frozen_today):
    ctx = _call(routes=[f"R{i}" for i in range(500)])
    assert len(ctx.routes) == 100
    assert ctx.routes[0] == "R0"
    assert ctx.routes[-1] == "R99"


def test_routes_accepts_a_comma_free_iterable_of_any_kind(frozen_today):
    ctx = _call(routes=iter(["R1", "R1", "R2"]))
    assert ctx.routes == ("R1", "R2")
