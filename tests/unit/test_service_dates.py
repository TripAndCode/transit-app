"""The service filter selects the dates each agency's own timetable files under it.

``resolve_service_dates`` turns a 平日 / 土曜 / 日祝 / 土日祝 filter into dates
once per request; every SQL builder then filters on those dates instead of the
raw ``service_type`` column, and refuses a filter that was never resolved for
its window.
"""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from api.range import (
    RangeCtx,
    ServiceDates,
    UnresolvedServiceFilter,
    build_agg_stop_filter,
    build_updates_filter_ch,
    resolve_service_dates,
    selected_service_dates,
)
from pipeline.day_types import public_holidays_between
from pipeline.reports.filters import _agg_filter, _dist_filter
from pipeline.reports.weather import _daily_filter
from pipeline.service_day import service_date_ch

WEEKDAYS = (True, True, True, True, True, False, False)
SATURDAY = (False, False, False, False, False, True, False)
SUNDAY = (False, False, False, False, False, False, True)
WEEKEND = (False, False, False, False, False, True, True)

CAL_START, CAL_END = date(2025, 4, 1), date(2025, 12, 31)
# Monday 4/28 to Sunday 5/11. Golden Week puts public holidays on Tuesday 4/29,
# Saturday 5/3, Sunday 5/4, Monday 5/5 and the substitute holiday Tuesday 5/6.
FROM, TO = date(2025, 4, 28), date(2025, 5, 11)
HOLIDAYS = frozenset(public_holidays_between(FROM, TO))


def _days(lo: date, hi: date) -> list[date]:
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


class _CalendarConn:
    """Answers :func:`pipeline.day_types.load_day_types`' two reads from memory."""

    def __init__(self, weekly=(), exceptions=()):
        self.weekly = list(weekly)
        self.exceptions = list(exceptions)
        self.reads = 0

    async def fetch(self, sql, *args):
        self.reads += 1
        if "static_calendar_dates" in sql:
            lo, hi = args[1], args[2]
            rows = [(s, d.strftime("%Y%m%d"), t) for s, d, t in self.exceptions]
            return [{"service_id": s, "date": d, "exception_type": t} for s, d, t in rows if lo <= d <= hi]
        names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        return [
            {"service_id": s, **dict(zip(names, flags, strict=True)), "start_date": a, "end_date": b, "trips": n}
            for s, flags, a, b, n in self.weekly
        ]


def _holiday_swaps(regular_by_weekday, holiday_service):
    """The calendar_dates.txt a Japanese feed publishes: each public holiday
    runs the holiday timetable in place of the weekday's own."""
    out = []
    for d in HOLIDAYS:
        out.append((holiday_service, d, 1))
        out.append((regular_by_weekday(d), d, 2))
    return out


def _three_way_conn():
    weekly = [
        ("11_平日(共通)", WEEKDAYS, CAL_START, CAL_END, 300),
        ("11_土曜(共通)", SATURDAY, CAL_START, CAL_END, 200),
        ("11_日祝(共通)", SUNDAY, CAL_START, CAL_END, 150),
    ]

    def regular(d):
        return {6: "11_土曜(共通)", 7: "11_日祝(共通)"}.get(d.isoweekday(), "11_平日(共通)")

    return _CalendarConn(weekly, _holiday_swaps(regular, "11_日祝(共通)"))


def _two_way_conn():
    weekly = [("平日", WEEKDAYS, CAL_START, CAL_END, 834), ("土日祝", WEEKEND, CAL_START, CAL_END, 710)]
    return _CalendarConn(weekly, _holiday_swaps(lambda d: "土日祝" if d.isoweekday() >= 6 else "平日", "土日祝"))


WEEKDAY_DATES = [d for d in _days(FROM, TO) if d.isoweekday() <= 5 and d not in HOLIDAYS]
SATURDAY_DATES = [d for d in _days(FROM, TO) if d.isoweekday() == 6 and d not in HOLIDAYS]
SUNDAY_HOLIDAY_DATES = [d for d in _days(FROM, TO) if d.isoweekday() == 7 or d in HOLIDAYS]
WEEKEND_HOLIDAY_DATES = sorted(SATURDAY_DATES + SUNDAY_HOLIDAY_DATES)


async def _resolve(conn, service):
    ctx = await resolve_service_dates(RangeCtx(from_date=FROM, to_date=TO, service=service), conn, 7)
    assert ctx.service_dates is not None
    return list(ctx.service_dates.dates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("平日", WEEKDAY_DATES),
        ("土曜", SATURDAY_DATES),
        ("日祝", SUNDAY_HOLIDAY_DATES),
        ("土日祝", WEEKEND_HOLIDAY_DATES),
    ],
)
async def test_each_value_on_a_three_way_feed(service, expected):
    assert await _resolve(_three_way_conn(), service) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("平日", WEEKDAY_DATES),
        # The feed has one timetable for Saturday and Sunday alike, so 土曜 and
        # 日祝 each resolve as its 土日祝 rather than coming back empty.
        ("土曜", WEEKEND_HOLIDAY_DATES),
        ("日祝", WEEKEND_HOLIDAY_DATES),
        ("土日祝", WEEKEND_HOLIDAY_DATES),
    ],
)
async def test_each_value_on_a_two_way_feed(service, expected):
    assert await _resolve(_two_way_conn(), service) == expected


@pytest.mark.asyncio
async def test_a_holiday_saturday_is_a_sunday_holiday_on_a_three_way_feed():
    holiday_saturday = date(2025, 5, 3)
    assert holiday_saturday.isoweekday() == 6 and holiday_saturday in HOLIDAYS
    assert holiday_saturday in await _resolve(_three_way_conn(), "日祝")
    assert holiday_saturday not in await _resolve(_three_way_conn(), "土曜")
    # Where no calendar is loaded the weekday rule decides, and it agrees.
    assert holiday_saturday in await _resolve(_CalendarConn(), "日祝")
    assert holiday_saturday not in await _resolve(_CalendarConn(), "土曜")


@pytest.mark.asyncio
async def test_all_returns_the_ctx_unchanged_without_reading_the_calendar():
    conn = _three_way_conn()
    ctx = RangeCtx(from_date=FROM, to_date=TO)
    assert await resolve_service_dates(ctx, conn, 7) is ctx
    assert conn.reads == 0
    assert selected_service_dates(ctx) is None


@pytest.mark.asyncio
async def test_a_ctx_already_resolved_for_a_covering_window_is_not_read_again():
    conn = _three_way_conn()
    ctx = await resolve_service_dates(RangeCtx(from_date=FROM, to_date=TO, service="平日"), conn, 7)
    reads = conn.reads
    narrower = replace(ctx, from_date=date(2025, 5, 1))
    assert await resolve_service_dates(narrower, conn, 7) is narrower
    assert conn.reads == reads


@pytest.mark.asyncio
async def test_an_explicit_span_widens_the_resolution_to_cover_it():
    conn = _three_way_conn()
    ctx = await resolve_service_dates(
        RangeCtx(from_date=date(2025, 5, 7), to_date=TO, service="平日"), conn, 7, from_date=FROM
    )
    assert (ctx.from_date, ctx.to_date) == (date(2025, 5, 7), TO)
    assert ctx.service_dates is not None
    assert (ctx.service_dates.from_date, ctx.service_dates.to_date) == (FROM, TO)
    assert list(ctx.service_dates.dates) == WEEKDAY_DATES
    # A window the span covers needs no second resolution.
    assert selected_service_dates(replace(ctx, from_date=FROM)) == WEEKDAY_DATES


def _resolved(service="平日", dates=(date(2025, 4, 28), date(2025, 4, 30))) -> RangeCtx:
    return RangeCtx(
        from_date=FROM,
        to_date=TO,
        service=service,
        service_dates=ServiceDates(service=service, from_date=FROM, to_date=TO, dates=tuple(dates)),
    )


def test_selected_dates_refuse_a_filter_that_was_never_resolved():
    with pytest.raises(UnresolvedServiceFilter):
        selected_service_dates(RangeCtx(from_date=FROM, to_date=TO, service="平日"))


def test_selected_dates_refuse_a_window_the_resolution_does_not_cover():
    with pytest.raises(UnresolvedServiceFilter):
        selected_service_dates(replace(_resolved(), from_date=FROM - timedelta(days=7)))
    with pytest.raises(UnresolvedServiceFilter):
        selected_service_dates(replace(_resolved(), to_date=TO + timedelta(days=1)))


def test_selected_dates_refuse_dates_resolved_for_another_service():
    with pytest.raises(UnresolvedServiceFilter):
        selected_service_dates(replace(_resolved(), service="土日祝"))


def test_selected_dates_keep_only_the_ctx_window():
    ctx = replace(_resolved(), from_date=date(2025, 4, 29))
    assert selected_service_dates(ctx) == [date(2025, 4, 30)]


# Every builder filters by date, never by the raw service_type column.


def test_clickhouse_filter_selects_the_service_dates():
    frag, params = build_updates_filter_ch(replace(_resolved(), routes=("R1",)))
    assert "service_type" not in frag
    assert f"{service_date_ch()} IN {{ch_service_dates:Array(Date)}}" in frag
    assert params["ch_service_dates"] == [date(2025, 4, 28), date(2025, 4, 30)]
    # The date range stays beside it, for the sort key and partition pruning.
    assert "{ch_from_date:Date}" in frag and "{ch_to_date:Date}" in frag


@pytest.mark.parametrize(
    ("build", "column"),
    [
        (lambda ctx, n: build_agg_stop_filter(ctx, next_param=n), "date"),
        (lambda ctx, n: _agg_filter(ctx, n), "date"),
        (lambda ctx, n: _dist_filter(ctx, n), "date"),
        (lambda ctx, n: _daily_filter(ctx, n), "d.date"),
    ],
)
def test_postgres_filters_select_the_service_dates(build, column):
    frag, params, n = build(_resolved(), 2)
    assert "service_type" not in frag
    assert f"{column} = ANY(${n - 1}::date[])" in frag
    assert params[-1] == [date(2025, 4, 28), date(2025, 4, 30)]


@pytest.mark.parametrize(
    "build",
    [
        lambda ctx: build_updates_filter_ch(ctx),
        lambda ctx: build_agg_stop_filter(ctx, next_param=2),
        lambda ctx: _agg_filter(ctx, 2),
        lambda ctx: _dist_filter(ctx, 2),
        lambda ctx: _daily_filter(ctx, 2),
    ],
)
def test_every_builder_raises_on_an_unresolved_service_filter(build):
    with pytest.raises(UnresolvedServiceFilter):
        build(RangeCtx(from_date=FROM, to_date=TO, service="土日祝"))


def test_a_service_with_no_dates_in_the_window_matches_nothing():
    empty = _resolved(dates=())
    frag, params = build_updates_filter_ch(empty)
    assert "0" in frag.split(" AND ") and "ch_service_dates" not in params
    pg_frag, pg_params, _ = _agg_filter(empty, 2)
    assert "FALSE" in pg_frag.split(" AND ") and pg_params == [str(FROM), str(TO)]
