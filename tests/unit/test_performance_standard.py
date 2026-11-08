"""Pure-logic tests for the performance-standard bonus/malus formula -- no DB fixtures
(see pipeline.reports.performance_standard's module docstring for the
formula this exercises directly)."""

import pytest

from pipeline.reports.performance_standard import (
    _relative_deviation,
    meets_standard,
    on_time_reading,
    simulation_disclaimer,
)
from pipeline.reports.rankings import RANKING_MIN_SAMPLES


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


def test_an_on_time_share_over_the_floor_is_judged():
    assert on_time_reading(85.0, RANKING_MIN_SAMPLES, zero_delay=False) == (85.0, None)


@pytest.mark.parametrize(("share", "observations"), [(None, 0), (60.0, 42), (60.0, RANKING_MIN_SAMPLES - 1)])
def test_an_on_time_share_under_the_floor_says_how_many_observations_it_had(share, observations):
    """The route page shows a share from as few as 21 observations; the
    verdict needs the table's floor, and says so with the count."""
    assert on_time_reading(share, observations, zero_delay=False) == (
        None,
        {"reason": "too_few_observations", "observations": observations, "floor": RANKING_MIN_SAMPLES},
    )


def test_a_feed_that_reports_zero_delay_only_is_not_judged_however_often_observed():
    assert on_time_reading(100.0, 750, zero_delay=True) == (
        None,
        {"reason": "zero_delay_feed", "observations": None, "floor": None},
    )


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
        "R": (None, {"reason": "too_few_observations", "observations": 42, "floor": RANKING_MIN_SAMPLES})
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
        "THIN": (None, {"reason": "too_few_observations", "observations": 30, "floor": RANKING_MIN_SAMPLES}),
    }
