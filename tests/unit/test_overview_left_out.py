"""The overview's route lists leave out what every route list leaves out.

Retired codes and routes whose feed reports no delay at all
(:mod:`pipeline.route_catalog`) are neither listed nor counted in the
concentration card or the movers, and each payload reads one left-out set.
Fake connections answer the reads, so no database is involved.
"""

from datetime import date, timedelta

import pytest

from api.range import MAX_RANGE_DAYS, RangeCtx
from api.routers import overview as overview_router
from pipeline.reports import overview as ov
from pipeline.route_catalog import RETIRED, ZERO_DELAY

_LEFT_OUT = {RETIRED: frozenset({"OLD"}), ZERO_DELAY: frozenset({"ECHO"})}
_CTX = RangeCtx(date(2026, 9, 24), date(2026, 10, 23))


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *args):
        if "static_routes" in sql:
            return []
        return self.rows


def _late(code: str, minutes: float) -> dict:
    return {"route_code": code, "total_late_min": minutes}


async def test_concentration_shares_are_of_the_routes_it_can_list():
    rows = [_late("OLD", 50.0), _late("A", 30.0), _late("B", 20.0), _late("ECHO", 0.0)]
    out = await ov._concentration(1, _CTX, _Conn(rows), left_out=_LEFT_OUT)
    assert [(r["route_code"], r["share_pct"]) for r in out["top_routes"]] == [("A", 60.0), ("B", 40.0)]
    assert out["rest_share_pct"] == 0.0
    assert out["rest_route_count"] == 0


async def test_concentration_rest_counts_only_routes_it_could_list():
    rows = [_late("OLD", 100.0)] + [_late(f"C{i:02d}", 50.0 - i) for i in range(22)] + [_late("ECHO", 0.0)]
    out = await ov._concentration(1, _CTX, _Conn(rows), left_out=_LEFT_OUT)
    assert [r["route_code"] for r in out["top_routes"]] == [f"C{i:02d}" for i in range(20)]
    assert out["rest_route_count"] == 2


async def test_nothing_left_out_keeps_every_route():
    rows = [_late("OLD", 50.0), _late("A", 50.0)]
    out = await ov._concentration(1, _CTX, _Conn(rows), left_out={})
    assert [(r["route_code"], r["share_pct"]) for r in out["top_routes"]] == [("OLD", 50.0), ("A", 50.0)]


async def test_a_left_out_route_never_moves(monkeypatch):
    """A day-of-week filter can anchor the current window before a renumbering,
    so a retired code can reach both windows; it is still not a mover."""
    cur_ctx = RangeCtx(date(2026, 10, 10), date(2026, 10, 16))
    base_ctx = RangeCtx(date(2026, 10, 3), date(2026, 10, 9))
    averages = {
        cur_ctx: {"C1": (5.0, 50), "OLD": (9.0, 50), "C2": (1.0, 50)},
        base_ctx: {"C1": (3.0, 50), "OLD": (2.0, 50), "C2": (4.0, 50)},
    }

    async def per_route_avg(agency_id, ctx, conn, ch=None, grain=None):
        return averages[ctx]

    async def nothing(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(ov, "_per_route_avg", per_route_avg)
    monkeypatch.setattr(ov, "_route_short_names", nothing)
    monkeypatch.setattr(ov, "_route_weekly_history", nothing)
    out = await ov._movers(1, cur_ctx, base_ctx, None, left_out=_LEFT_OUT)
    assert [m["route_code"] for m in out["worse"]] == ["C1"]
    assert [m["route_code"] for m in out["better"]] == ["C2"]


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


@pytest.mark.parametrize("pool", [None, _Pool()], ids=["one connection", "pooled"])
async def test_every_route_list_reads_the_payloads_one_left_out_set(monkeypatch, pool):
    """The set is judged over the payload's period, whichever window a list
    ranks over, so the lists agree on which codes they leave out."""
    asked: list[RangeCtx] = []
    received: dict[str, object] = {}

    async def left_out(conn, agency_id, ctx):
        asked.append(ctx)
        return _LEFT_OUT

    def stage(name, value):
        async def fake(*_args, **kwargs):
            if "left_out" in kwargs:
                received[name] = kwargs["left_out"]
            return value

        return fake

    monkeypatch.setattr(ov, "left_out_unless_named", left_out)
    monkeypatch.setattr(ov, "_latest_data_date", stage("_latest_data_date", date(2026, 10, 23)))
    for name, value in {
        "_headline_stats": (2.5, 40),
        "_movers": {"worse": [], "better": []},
        "_concentration": {},
        "_top_delayed_routes": {},
        "_peak_hour_by_dow": None,
        "_service_day_split": ([], []),
        "_daily_sparkline": ([], None),
    }.items():
        monkeypatch.setattr(ov, name, stage(name, value))

    await ov.compute_overview_summary(1, _CTX, None, pool=pool)
    assert asked == [_CTX]
    assert received == {"_movers": _LEFT_OUT, "_concentration": _LEFT_OUT, "_top_delayed_routes": _LEFT_OUT}


class _SpanConn:
    def __init__(self, first, last):
        self.span = {"first": first, "last": last}

    async def fetchrow(self, sql, *args):
        return self.span


@pytest.mark.parametrize(
    ("first", "judged_from"),
    [
        (date(2026, 7, 12), date(2026, 7, 12)),
        (date(2023, 1, 1), date(2026, 10, 23) - timedelta(days=MAX_RANGE_DAYS - 1)),
    ],
    ids=["whole span", "clipped to the widest window"],
)
async def test_the_breakdown_judges_codes_over_at_most_the_widest_window(monkeypatch, first, judged_from):
    """The analysed history only grows, so the breakdown's left-out set is
    judged over at most the widest window a route list may be asked about,
    ending on the agency's last analysed day."""
    asked: list[RangeCtx] = []

    async def left_out(conn, agency_id, ctx):
        asked.append(ctx)
        return _LEFT_OUT

    monkeypatch.setattr(overview_router, "left_out_route_codes", left_out)
    got = await overview_router._all_time_left_out(_SpanConn(first, date(2026, 10, 23)), 1)
    assert got == _LEFT_OUT
    assert [(c.from_date, c.to_date) for c in asked] == [(judged_from, date(2026, 10, 23))]


async def test_the_breakdown_of_an_agency_without_analysed_days_leaves_out_nothing():
    assert await overview_router._all_time_left_out(_SpanConn(None, None), 1) == {}
