"""api/range.py's default date-range window must anchor "today" to
Asia/Tokyo, matching the JST civil calendar every agg_*/analyze query is
bucketed against (api/main.py pins the DB session to the same zone). A
plain ``date.today()`` uses the server's local time, which was a real
prior bug for analyze() (~20% of rows mis-bucketed) - this is the same
class of bug for the request-side default window.
"""

from datetime import date, datetime, timezone

import api.range as range_mod


def test_jst_today_uses_tokyo_not_utc(monkeypatch):
    # 2026-01-01 20:00 UTC == 2026-01-02 05:00 JST - a UTC-local "today"
    # would be one calendar day behind the true JST date here.
    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)
    assert range_mod.jst_today() == date(2026, 1, 2)


def test_get_range_ctx_default_window_uses_jst_today(monkeypatch):
    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)
    ctx = range_mod.get_range_ctx(from_=None, to=None, dow="all", time_band="all", service="all", routes=None)
    assert ctx.to_date == date(2026, 1, 2)


def test_apply_date_overrides_default_window_uses_jst_today(monkeypatch):
    """pipeline/query/tools.py's _apply_date_overrides is a sibling of
    get_range_ctx's default-window logic (same today-29d..today pattern) and
    must anchor to the same JST civil calendar, not the server's local time."""
    from pipeline.query.tools import _apply_date_overrides

    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)
    ctx = range_mod.RangeCtx(from_date=date(2020, 1, 1), to_date=date(2020, 1, 31))
    derived = _apply_date_overrides(ctx, {"from_date": "2026-01-01"})  # from set, to omitted -> defaults to today
    assert derived.to_date == date(2026, 1, 2)


def test_get_range_ctx_clamps_future_to_date_to_jst_today(monkeypatch):
    """A caller-supplied ``to`` in the future (clock skew, or a deliberately
    crafted request) must never push the window past "today" -- every
    agg_*/analyze query is bucketed up to the current JST civil date, so a
    future ``to_date`` would just return empty rows for the tail while still
    reporting a misleading date range."""
    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)  # JST today = 2026-01-02

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)
    ctx = range_mod.get_range_ctx(from_=None, to="2099-12-31", dow="all", time_band="all", service="all", routes=None)
    assert ctx.to_date == date(2026, 1, 2)


def test_get_range_ctx_future_to_date_with_earlier_from_clamps_and_keeps_order(monkeypatch):
    """After clamping a future ``to_date`` to today, an explicit ``from``
    that is still before that clamped today must NOT be swapped -- only a
    ``from_date`` that ends up strictly after the clamped ``to_date``
    triggers the existing swap logic."""
    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)  # JST today = 2026-01-02

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)
    ctx = range_mod.get_range_ctx(
        from_="2025-12-01", to="2099-12-31", dow="all", time_band="all", service="all", routes=None
    )
    assert ctx.from_date == date(2025, 12, 1)
    assert ctx.to_date == date(2026, 1, 2)


def test_get_range_ctx_rejects_a_malformed_date_instead_of_defaulting():
    """A typo'd `from` used to fall through to the default 30-day window and
    return a confident answer for a period the caller never asked for."""
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        range_mod.get_range_ctx(from_="2026-99-99", to=None, dow="all", time_band="all", service="all", routes=None)
    assert exc.value.status_code == 422


def test_get_range_ctx_dedupes_and_caps_routes():
    ctx = range_mod.get_range_ctx(
        from_=None, to=None, dow="all", time_band="all", service="all", routes=" R2 ,R1,R2,,R3"
    )
    assert ctx.routes == ("R2", "R1", "R3")
