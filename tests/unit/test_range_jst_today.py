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
