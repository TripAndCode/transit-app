"""Pure-logic tests for the performance-standard bonus/malus formula -- no DB fixtures
(see pipeline.reports.performance_standard's module docstring for the
formula this exercises directly)."""

import pytest

from pipeline.reports.performance_standard import (
    _relative_deviation,
    delivered_reading,
    meets_standard,
    on_time_reading,
    simulation_disclaimer,
)
from pipeline.reports.rankings import RANKING_MIN_SAMPLES
from pipeline.reports.service_delivered import RouteDelivered


def test_relative_deviation_zero_exactly_at_threshold_lower_is_better():
    assert _relative_deviation("ewt_sec", threshold=50.0, actual=50.0) == 0.0


def test_relative_deviation_negative_when_worse_than_threshold_lower_is_better():
    # ewt_sec is lower-is-better: an actual value ABOVE threshold is worse.
    assert _relative_deviation("ewt_sec", threshold=100.0, actual=150.0) == -0.5


def test_relative_deviation_positive_when_better_than_threshold_lower_is_better():
    assert _relative_deviation("ewt_sec", threshold=100.0, actual=50.0) == 0.5


def test_relative_deviation_zero_exactly_at_threshold_higher_is_better():
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=80.0, actual=80.0) == 0.0


def test_relative_deviation_negative_when_worse_than_threshold_higher_is_better():
    # vehicle_km_delivered_pct is higher-is-better: an actual value BELOW
    # threshold is worse.
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=90.0, actual=80.0) == (80.0 - 90.0) / 90.0


def test_relative_deviation_positive_when_better_than_threshold_higher_is_better():
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=80.0, actual=90.0) == (90.0 - 80.0) / 80.0


def test_relative_deviation_none_for_zero_threshold():
    """A zero threshold has no meaningful percent-of-standard denominator --
    must surface as undefined (None), never raise or fabricate a number."""
    assert _relative_deviation("ewt_sec", threshold=0.0, actual=10.0) is None
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=0.0, actual=10.0) is None


def test_simulation_disclaimer_differs_by_locale_and_defaults_to_japanese():
    ja = simulation_disclaimer("ja")
    en = simulation_disclaimer("en")
    assert ja != en
    assert "シミュレーション" in ja
    assert "simulation" in en.lower()
    # An unsupported/missing locale falls back to Japanese rather than
    # raising a KeyError -- same "always assume a non-empty supported
    # value" convention as api.deps.get_locale.
    assert simulation_disclaimer("fr") == ja


def test_an_on_time_share_above_its_target_is_better():
    assert _relative_deviation("on_time_pct", threshold=80.0, actual=60.0) == -0.25
    assert _relative_deviation("on_time_pct", threshold=80.0, actual=100.0) == 0.25


@pytest.mark.parametrize(
    ("metric_type", "threshold", "actual", "meets"),
    [
        # A share at its target meets it; a tenth of a point under does not.
        ("on_time_pct", 85.0, 85.0, True),
        ("on_time_pct", 85.0, 84.9, False),
        ("on_time_pct", 85.0, 91.2, True),
        # A target of 0 is met by any share, though no achievement rate exists.
        ("on_time_pct", 0.0, 0.0, True),
        ("vehicle_km_delivered_pct", 90.0, 80.0, False),
        ("service_delivered_pct", 98.0, 98.0, True),
        ("service_delivered_pct", 98.0, 97.9, False),
        # Excess wait is lower-is-better.
        ("ewt_sec", 50.0, 50.0, True),
        ("ewt_sec", 50.0, 51.0, False),
        ("ewt_sec", 50.0, 20.0, True),
    ],
)
def test_meets_standard_reads_each_metrics_direction(metric_type, threshold, actual, meets):
    assert meets_standard(metric_type, threshold, actual) is meets


def test_meets_standard_is_unknown_without_a_figure():
    assert meets_standard("on_time_pct", 85.0, None) is None


def _not_judged(reason, *, observations=None, floor=None, planned_trips=None):
    """The one shape every row's not-judged reason takes."""
    return {"reason": reason, "observations": observations, "floor": floor, "planned_trips": planned_trips}


def test_an_on_time_share_over_the_floor_is_judged():
    assert on_time_reading(85.0, RANKING_MIN_SAMPLES, zero_delay=False) == (85.0, None)


@pytest.mark.parametrize(("share", "observations"), [(None, 0), (60.0, 42), (60.0, RANKING_MIN_SAMPLES - 1)])
def test_an_on_time_share_under_the_floor_says_how_many_observations_it_had(share, observations):
    """The route page shows a share from as few as 21 observations; the
    verdict needs the table's floor, and says so with the count."""
    assert on_time_reading(share, observations, zero_delay=False) == (
        None,
        _not_judged("too_few_observations", observations=observations, floor=RANKING_MIN_SAMPLES),
    )


def test_a_feed_that_reports_zero_delay_only_is_not_judged_however_often_observed():
    assert on_time_reading(100.0, 750, zero_delay=True) == (None, _not_judged("zero_delay_feed"))


def _route(planned: int, missed: int, observations: int) -> RouteDelivered:
    executed = max(planned - missed, 0)
    return RouteDelivered(
        planned_trips=planned,
        service_delivered_pct=round(executed / planned * 100, 1) if planned else None,
        observations=observations,
    )


def test_a_routes_delivered_share_over_both_floors_is_judged():
    route = _route(RANKING_MIN_SAMPLES, 3, RANKING_MIN_SAMPLES)
    assert delivered_reading(None, True, route, zero_delay=False) == (97.0, None)


@pytest.mark.parametrize(
    "missing", ["no_plan", "feed_cannot_report", "feed_unverified", "feed_reports_no_cancellations"]
)
def test_the_agencys_reason_for_no_rate_stands_for_the_route(missing):
    """A route's share needs what the agency's needs, first of all: a feed
    that reports trips not run, or every route would read 100%."""
    route = _route(500, 0, 500)
    assert delivered_reading(missing, True, route, zero_delay=False) == (None, _not_judged(missing))


def test_a_period_whose_plan_is_not_split_by_route_on_every_day_is_not_judged():
    route = _route(500, 0, 500)
    assert delivered_reading(None, False, route, zero_delay=False) == (None, _not_judged("route_plan_unknown"))


@pytest.mark.parametrize("planned", [0, 42, RANKING_MIN_SAMPLES - 1])
def test_a_route_planning_fewer_trips_than_the_floor_says_how_many(planned):
    route = _route(planned, 0, 500) if planned else None
    assert delivered_reading(None, True, route, zero_delay=False) == (
        None,
        _not_judged("too_few_planned_trips", floor=RANKING_MIN_SAMPLES, planned_trips=planned),
    )


def test_a_route_whose_feed_reports_zero_delay_only_is_not_judged_on_delivery_either():
    """A feed echoing the timetable for a route reports none of its trips not
    run, so the route would read 100% delivered."""
    route = _route(500, 0, 500)
    assert delivered_reading(None, True, route, zero_delay=True) == (None, _not_judged("zero_delay_feed"))


@pytest.mark.parametrize("observations", [0, RANKING_MIN_SAMPLES - 1])
def test_a_route_the_feed_rarely_reports_is_not_judged(observations):
    """A route the feed never reports has no trip reported not run either."""
    route = _route(500, 0, observations)
    assert delivered_reading(None, True, route, zero_delay=False) == (
        None,
        _not_judged("too_few_observations", observations=observations, floor=RANKING_MIN_SAMPLES),
    )


async def test_a_vehicle_km_target_without_route_shapes_reads_no_route_split(monkeypatch):
    """Without route shapes every vehicle-km row is `no_shapes`, so an agency
    whose only delivered-type targets are vehicle-km reads nothing of its
    routes' service delivered."""
    from datetime import date

    from api.range import RangeCtx
    from pipeline.reports import performance_standard

    async def no_shapes(conn, agency_id):
        return False

    split_reads: list[int] = []

    async def spy(conn, agency_id, from_date, to_date):
        split_reads.append(agency_id)
        raise AssertionError("the route split was read")

    monkeypatch.setattr(performance_standard, "_has_route_shapes", no_shapes)
    monkeypatch.setattr(performance_standard, "compute_service_delivered_by_route", spy)
    standards = [
        {
            "route_code": "R1",
            "metric_type": "vehicle_km_delivered_pct",
            "threshold_value": 80.0,
            "bonus_malus_rate": 0.0,
        }
    ]

    rows = await performance_standard.compute_performance_standards(
        1, RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 30)), None, standards
    )

    assert split_reads == []
    assert [(r["actual_value"], r["not_judged"]) for r in rows] == [(None, _not_judged("no_shapes"))]


class _FakeTable:
    """compute_route_table standing in for the Routes table: each call returns
    the next of *answers*, at or over its floor, recording the routes and floor
    it was asked for. A route's tuple is (code, avg, share, late5 share, late5
    count, observations, early share)."""

    def __init__(self, *answers: dict[str, tuple[float, int]]):
        self.answers = list(answers)
        self.calls: list[tuple[tuple[str, ...], int]] = []

    async def __call__(self, agency_id, ctx, conn, *, min_samples, **_window):
        self.calls.append((ctx.routes, min_samples))
        answer = self.answers.pop(0)
        return [
            (code, None, share, None, None, observations, None)
            for code, (share, observations) in answer.items()
            if observations >= min_samples and (not ctx.routes or code in ctx.routes)
        ]


async def _readings(monkeypatch, table, ctx, codes, *, one_route):
    from pipeline.reports import performance_standard

    async def no_zero_delay(conn, agency_id, ctx):
        return frozenset()

    monkeypatch.setattr(performance_standard, "compute_route_table", table)
    monkeypatch.setattr(performance_standard, "zero_delay_route_codes", no_zero_delay)
    return await performance_standard._on_time_by_route(1, ctx, None, None, None, None, codes, one_route=one_route)


@pytest.mark.asyncio
async def test_a_route_page_reads_its_route_once_and_judges_the_count_itself(monkeypatch):
    from api.range import clamp_range_ctx

    ctx = clamp_range_ctx(from_="2026-05-14", to="2026-05-14", routes=["R"])
    thin = _FakeTable({"R": (60.0, 42)})
    assert await _readings(monkeypatch, thin, ctx, {"R"}, one_route=True) == {
        "R": (None, _not_judged("too_few_observations", observations=42, floor=RANKING_MIN_SAMPLES))
    }
    busy = _FakeTable({"R": (80.0, RANKING_MIN_SAMPLES)})
    assert await _readings(monkeypatch, busy, ctx, {"R"}, one_route=True) == {"R": (80.0, None)}
    assert thin.calls == busy.calls == [(("R",), 1)]


@pytest.mark.asyncio
async def test_a_thin_route_counted_at_the_floor_by_the_second_read_is_judged(monkeypatch):
    """The Routes table's floor read and the count of the routes under it run
    apart, and the data can grow between them. A count at the floor is judged,
    never reported as under it."""
    from api.range import clamp_range_ctx

    ctx = clamp_range_ctx(from_="2026-05-14", to="2026-05-14")
    table = _FakeTable(
        {"BUSY": (90.0, 500), "GROWN": (70.0, RANKING_MIN_SAMPLES - 1)},
        {"GROWN": (75.0, RANKING_MIN_SAMPLES), "THIN": (50.0, 30)},
    )
    readings = await _readings(monkeypatch, table, ctx, {"BUSY", "GROWN", "THIN"}, one_route=False)

    assert table.calls == [((), RANKING_MIN_SAMPLES), (("GROWN", "THIN"), 1)]
    assert readings == {
        "BUSY": (90.0, None),
        "GROWN": (75.0, None),
        "THIN": (None, _not_judged("too_few_observations", observations=30, floor=RANKING_MIN_SAMPLES)),
    }
