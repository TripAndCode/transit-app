"""The definition stamp's feed-quality figures: an agency's realtime
completeness and clamp share over a request's range, shaped from the figures
the agencies board reads (pipeline.reports.network.feed_quality_of), so the
board, the scorecard and a footnote state the same figure and the same reason."""

from datetime import date

import pytest

from pipeline.reports import definition, network
from pipeline.reports.comparability import MAX_CLAMP_PCT, MIN_COVERAGE_PCT
from pipeline.reports.definition import FeedQuality, resolve_definition_meta
from pipeline.reports.network import feed_quality_of
from pipeline.reports.schedule_revision import ScheduleDays

# A board row's figures for an agency the gate passes.
_ROW = {
    "coverage_pct": 90.0,
    "observed_trips": 900,
    "coverage_planned_trips": 1000,
    "planned_trips": 1000,
    "delivered_missing": None,
    "clamp_pct": 0.42,
}


def _shaped(observed=True, **over) -> FeedQuality:
    return feed_quality_of(7, {**_ROW, **over}, observed=observed)


def test_an_agency_the_gate_passes_states_its_figures_unflagged():
    assert _shaped() == FeedQuality(
        agency_id=7,
        coverage_pct=90.0,
        observed_trips=900,
        coverage_planned_trips=1000,
        coverage_missing=None,
        coverage_low=False,
        clamp_pct=0.42,
        clamp_missing=None,
        clamp_high=False,
        min_coverage_pct=MIN_COVERAGE_PCT,
        max_clamp_pct=MAX_CLAMP_PCT,
    )


def test_no_observation_in_the_period_reads_no_data_never_0():
    """With a plan and nothing observed the ratio is 0.0, which the board's gate
    calls no data before it reads coverage at all."""
    quality = _shaped(observed=False, coverage_pct=0.0, observed_trips=0, clamp_pct=None)
    assert (quality.coverage_pct, quality.observed_trips, quality.coverage_planned_trips) == (None, None, None)
    assert (quality.coverage_missing, quality.coverage_low) == ("no_data", False)
    assert (quality.clamp_pct, quality.clamp_missing, quality.clamp_high) == (None, "no_data", False)


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        # A failed read nulls the planned count too, so it is told apart first.
        ({"delivered_missing": "unreadable", "coverage_planned_trips": None, "planned_trips": 0}, "unreadable"),
        ({"coverage_planned_trips": None, "observed_trips": None}, "unmatched"),
        ({"coverage_planned_trips": 0, "observed_trips": None, "planned_trips": 0}, "no_plan"),
        ({"coverage_planned_trips": 0, "observed_trips": None, "planned_trips": 400}, "no_plan_observed"),
    ],
)
def test_coverage_that_cannot_be_measured_says_why_as_the_board_does(over, reason):
    quality = _shaped(**over, coverage_pct=None)
    assert (quality.coverage_pct, quality.coverage_missing, quality.coverage_low) == (None, reason, False)
    assert (quality.observed_trips, quality.coverage_planned_trips) == (None, None)


@pytest.mark.parametrize(("coverage", "low"), [(69.9, True), (70.0, False)])
def test_low_coverage_is_flagged_by_the_gates_own_check(coverage, low):
    assert _shaped(coverage_pct=coverage).coverage_low is low


@pytest.mark.parametrize(("clamp", "high"), [(5.04, False), (5.05, True)])
def test_a_high_clamp_share_is_flagged_by_the_gates_own_check(clamp, high):
    assert _shaped(clamp_pct=clamp).clamp_high is high


def test_an_unmeasured_clamp_share_is_no_data_beside_measured_coverage():
    quality = _shaped(clamp_pct=None)
    assert (quality.coverage_pct, quality.clamp_pct, quality.clamp_missing) == (90.0, None, "no_data")


class _Conn:
    def is_in_transaction(self):
        return False

    async def fetch(self, _sql, *_args):
        return []

    async def fetchrow(self, _sql, *_args):
        return (0, None, None)


_SCHEDULE = ScheduleDays(date(2026, 5, 1), date(2026, 5, 2), [])


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_one_agencys_stamp_carries_its_figures_for_aggregates_and_live_scans_alike(monkeypatch, live):
    """The delay record is a live scan and states the figures too, read from
    the aggregates over the request's own range."""
    reads = []
    shaped = _shaped()

    async def fake_read(conn, agency_id, from_date, to_date):
        reads.append((agency_id, from_date, to_date))
        return shaped

    monkeypatch.setattr(definition, "clear_all", lambda: None)
    monkeypatch.setattr(network, "compute_agency_feed_quality", fake_read)
    meta = resolve_definition_meta("on_time", None, None, live=live)
    stamped = await definition.stamp_build(_Conn(), meta, 7, _SCHEDULE)
    assert stamped.feed_quality == [shaped]
    assert reads == [(7, date(2026, 5, 1), date(2026, 5, 2))]


@pytest.mark.asyncio
async def test_an_unreadable_quality_read_leaves_the_figures_out(monkeypatch):
    async def fake_read(*_args):
        return None

    monkeypatch.setattr(definition, "clear_all", lambda: None)
    monkeypatch.setattr(network, "compute_agency_feed_quality", fake_read)
    stamped = await definition.stamp_build(_Conn(), resolve_definition_meta("on_time", None, None), 7, _SCHEDULE)
    assert stamped.feed_quality is None


@pytest.mark.asyncio
async def test_the_boards_stamp_is_left_to_the_board_which_reads_every_agencys_figures_itself(monkeypatch):
    async def fail(*_args):
        raise AssertionError("the board's own rows carry its agencies' figures")

    monkeypatch.setattr(definition, "clear_all", lambda: None)
    monkeypatch.setattr(network, "compute_agency_feed_quality", fail)
    stamped = await definition.stamp_build(_Conn(), resolve_definition_meta("on_time", None, None), None, _SCHEDULE)
    assert stamped.feed_quality is None
