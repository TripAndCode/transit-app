"""The standalone poller's step schedule (pipeline.poller_schedule), as a
table of clock readings, outcomes and the step each one starts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.poller_schedule import MAX_RETRIES, RETRY_AFTER, State, decide, exhausted, settle

T0 = datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc)  # 19:00 JST
UTC_PLAN = {(a, s) for a in (1, 2) for s in ("pack", "promote", "ingest", "analyze")}
JST_PLAN = {(a, s) for a in (1, 2) for s in ("promote", "analyze")}


def _pairs(keys) -> set[tuple[int, str]]:
    return {(aid, stage) for _, aid, stage in keys}


class _Run:
    """Drives decide/settle the way the poller does: decide, then settle with
    the stages that failed."""

    def __init__(self) -> None:
        self.state = State()
        self.now = T0

    def step(self, failed=(), *, at: datetime | None = None):
        self.now = at or self.now
        kind, keys, self.state = decide(self.now, [1, 2], self.state)
        if keys:
            self.state = settle(self.now, self.state, keys, failed)
        return kind, _pairs(keys)

    def later(self, delta: timedelta, failed=()):
        return self.step(failed, at=self.now + delta)


@pytest.mark.parametrize(
    ("first_at", "second_at", "second_kind", "second_plan"),
    [
        # The same UTC and JST day: nothing.
        (T0, T0 + timedelta(hours=4, minutes=59), None, set()),
        # JST midnight (15:00 UTC): promote and analyze only.
        (T0, datetime(2026, 4, 2, 15, 0, 1, tzinfo=timezone.utc), "jst", JST_PLAN),
        # UTC midnight: the whole step.
        (T0, datetime(2026, 4, 3, 0, 0, 1, tzinfo=timezone.utc), "utc", UTC_PLAN),
    ],
)
def test_day_rules(first_at, second_at, second_kind, second_plan):
    run = _Run()
    assert run.step(at=first_at) == ("utc", UTC_PLAN)
    assert run.step(at=second_at) == (second_kind, second_plan)


def test_a_utc_step_on_a_new_jst_day_also_counts_as_that_days_jst_step():
    run = _Run()
    run.step(at=datetime(2026, 4, 2, 23, 0, tzinfo=timezone.utc))
    assert run.step(at=datetime(2026, 4, 3, 0, 30, tzinfo=timezone.utc))[0] == "utc"
    assert run.later(timedelta(hours=1)) == (None, set())


def test_a_failed_stage_is_retried_alone_with_what_follows_it_after_its_wait():
    run = _Run()
    run.step(failed={(2, "ingest")})
    assert run.later(RETRY_AFTER - timedelta(seconds=1)) == (None, set())
    assert run.later(timedelta(seconds=1)) == ("retry", {(2, "ingest"), (2, "analyze")})
    assert run.later(RETRY_AFTER) == (None, set())


def test_a_pack_failure_retries_pack_ingest_and_analyze():
    run = _Run()
    run.step(failed={(1, "pack")})
    assert run.later(RETRY_AFTER) == ("retry", {(1, "pack"), (1, "ingest"), (1, "analyze")})


def test_retries_stop_once_a_stage_spends_its_budget():
    run = _Run()
    run.step(failed={(1, "promote")})
    kinds = [run.later(RETRY_AFTER, failed={(1, "promote")})[0] for _ in range(MAX_RETRIES + 2)]
    assert kinds == ["retry"] * MAX_RETRIES + [None, None]
    assert exhausted(run.state) == [("utc", 1, "promote")]


def test_one_agencys_exhausted_budget_leaves_anothers_whole():
    run = _Run()
    run.step(failed={(1, "promote")})
    for _ in range(MAX_RETRIES):
        run.later(RETRY_AFTER, failed={(1, "promote")})
    run.later(timedelta(seconds=1))
    run.state = settle(run.now, run.state, [("utc", 2, "ingest")], {(2, "ingest")})
    assert run.later(RETRY_AFTER) == ("retry", {(2, "ingest"), (2, "analyze")})


def test_a_jst_step_failing_after_the_utc_step_spent_its_retries_is_still_retried():
    run = _Run()
    run.step(failed={(1, "promote")})
    for _ in range(MAX_RETRIES):
        run.later(RETRY_AFTER, failed={(1, "promote")})
    assert exhausted(run.state) == [("utc", 1, "promote")]
    kind, _ = run.step(failed={(1, "promote")}, at=datetime(2026, 4, 2, 15, 0, 1, tzinfo=timezone.utc))
    assert kind == "jst"
    assert run.later(RETRY_AFTER) == ("retry", {(1, "promote"), (1, "analyze")})


def test_a_new_utc_day_resets_the_utc_budgets_but_not_the_jst_ones():
    run = _Run()
    run.step(failed={(1, "ingest")})
    run.step(failed={(2, "promote")}, at=datetime(2026, 4, 2, 15, 0, 1, tzinfo=timezone.utc))
    run.step(at=datetime(2026, 4, 3, 0, 0, 1, tzinfo=timezone.utc))
    assert set(run.state.pending) == {("jst", 2, "promote")}


def test_a_new_jst_day_resets_the_jst_budgets():
    run = _Run()
    run.step(at=datetime(2026, 4, 2, 15, 0, 1, tzinfo=timezone.utc), failed={(2, "promote")})
    run.step(at=datetime(2026, 4, 3, 0, 0, 1, tzinfo=timezone.utc))
    run.step(at=datetime(2026, 4, 3, 15, 0, 1, tzinfo=timezone.utc))
    assert ("jst", 2, "promote") not in run.state.pending


def test_a_retry_that_succeeds_clears_the_stage():
    run = _Run()
    run.step(failed={(1, "promote")})
    run.later(RETRY_AFTER)
    assert run.state.pending == {}


def test_no_agency_starts_nothing_but_still_marks_the_day():
    kind, keys, state = decide(T0, [], State())
    assert (kind, keys) == ("utc", frozenset())
    assert decide(T0, [], state)[:2] == (None, frozenset())
