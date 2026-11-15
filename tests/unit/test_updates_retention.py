"""The `updates` partition prune: the retention floor a drop sets, which months
it may drop, and that it drops nothing unless its kill switch is on and the run
is not a dry run."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import flags, retention
from pipeline.analyze import Unapplied
from pipeline.retention import UpdatesPartition, floor_after, partition_span, plan_updates_prune
from pipeline.service_day import FILING_REACH_HOURS, service_day_start_utc

UTC = timezone.utc


def _partition(pid: str, *agencies: int) -> UpdatesPartition:
    return UpdatesPartition(pid, rows=10, bytes=100, agency_ids=tuple(agencies))


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _counts(first: date, last: date, rows: int = 5) -> dict[date, tuple[int, int]]:
    return dict.fromkeys(_days(first, last), (rows, rows))


def test_a_partition_spans_its_utc_month():
    assert partition_span("202402") == (datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC))
    assert partition_span("202412") == (datetime(2024, 12, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize("pid", ["20242", "2024022", "2024-2", "202413", "202400", "abcdef", "202402'; DROP"])
def test_a_partition_id_that_is_not_a_utc_month_is_refused(pid):
    with pytest.raises(ValueError):
        partition_span(pid)


@pytest.mark.parametrize("days", [0, -1])
def test_a_horizon_of_no_days_is_refused(days):
    with pytest.raises(ValueError):
        retention.updates_cutoff(datetime(2026, 11, 15, tzinfo=UTC), days)


def test_the_cutoff_is_the_horizon_before_now():
    now = datetime(2026, 11, 15, 6, tzinfo=UTC)
    assert retention.updates_cutoff(now, 400) == now - timedelta(days=400)


def test_the_floor_after_a_month_is_the_first_of_the_month_past_the_day_it_cut():
    """2025-02-01 00:00 UTC is 09:00 JST, inside service day 2025-02-01, which
    loses its first hours. Every row a stop event of 2025-02-02 is filed from
    is still held, and the floor rounds that day up to a month's first."""
    assert floor_after(datetime(2025, 2, 1, tzinfo=UTC)) == date(2025, 3, 1)
    assert floor_after(datetime(2025, 12, 1, tzinfo=UTC)) == date(2026, 1, 1)


def test_the_floor_follows_the_filing_reach_before_a_month_s_first_service_day():
    reach = timedelta(hours=FILING_REACH_HOURS)
    start = service_day_start_utc(date(2025, 3, 1))
    assert floor_after(start - reach) == date(2025, 3, 1)
    assert floor_after(start - reach + timedelta(microseconds=1)) == date(2025, 4, 1)
    assert floor_after(start) == date(2025, 4, 1)


def _plan(
    partitions,
    *,
    cutoff=datetime(2026, 1, 1, tzinfo=UTC),
    now=datetime(2026, 6, 1, tzinfo=UTC),
    soft_deleted=None,
    floors=None,
    ledgers=None,
    live=None,
    disagreeing=None,
    unapplied=None,
):
    partitions = list(partitions)
    every = {agency_id for partition in partitions for agency_id in partition.agency_ids}
    return plan_updates_prune(
        partitions,
        cutoff=cutoff,
        now=now,
        soft_deleted=dict.fromkeys(every, False) if soft_deleted is None else soft_deleted,
        floors=floors or {},
        ledgers=ledgers or {},
        live=live or {},
        disagreeing=disagreeing,
        unapplied=unapplied,
    )


def test_only_months_that_end_by_the_cutoff_are_candidates():
    held = _counts(date(2024, 12, 1), date(2025, 3, 31))
    verdicts = _plan(
        [_partition("202501", 1), _partition("202502", 1), _partition("202503", 1)],
        cutoff=datetime(2025, 3, 1, tzinfo=UTC),
        ledgers={1: held},
        live={1: held},
    )
    assert [v.partition.partition_id for v in verdicts] == ["202501", "202502"]
    assert all(v.held_by is None for v in verdicts)
    assert [v.floor for v in verdicts] == [date(2025, 3, 1), date(2025, 4, 1)]


def test_a_month_ending_a_second_past_the_cutoff_stays():
    held = _counts(date(2025, 1, 1), date(2025, 3, 31))
    cutoff = datetime(2025, 2, 28, 23, 59, 59, tzinfo=UTC)
    assert _plan([_partition("202502", 1)], cutoff=cutoff, ledgers={1: held}, live={1: held}) == []


@pytest.mark.parametrize(
    "now, today",
    [
        (datetime(2025, 2, 2, 12, tzinfo=UTC), date(2025, 2, 2)),
        (datetime(2025, 2, 28, 18, tzinfo=UTC), date(2025, 2, 28)),  # 03:00 JST, before the cutover
    ],
)
def test_a_month_whose_floor_would_be_after_today_is_held(now, today):
    """With a horizon of a day, dropping January would freeze February's dates
    before their rows arrive."""
    held = _counts(date(2024, 12, 31), date(2025, 3, 1))
    verdicts = _plan(
        [_partition("202501", 1)], cutoff=retention.updates_cutoff(now, 1), now=now, ledgers={1: held}, live={1: held}
    )
    assert [v.held_by for v in verdicts] == [
        f"its floor 2025-03-01 would be after today's service date {today.isoformat()}; "
        "analyze could never build the dates in between"
    ]


def test_a_month_whose_floor_is_today_is_dropped():
    now = datetime(2025, 3, 1, 12, tzinfo=UTC)  # 21:00 JST, service date 2025-03-01
    held = _counts(date(2024, 12, 31), date(2025, 3, 1))
    verdicts = _plan(
        [_partition("202501", 1)], cutoff=retention.updates_cutoff(now, 1), now=now, ledgers={1: held}, live={1: held}
    )
    assert [(v.floor, v.held_by) for v in verdicts] == [(date(2025, 3, 1), None)]


def test_a_month_whose_dates_are_all_aggregated_is_dropped_however_recent_the_ledger():
    """The floor, not the oldest ledger date, decides: aggregates reaching back
    past the month no longer hold it."""
    held = _counts(date(2024, 12, 31), date(2025, 12, 31))
    verdicts = _plan([_partition("202501", 1)], ledgers={1: held}, live={1: held})
    assert verdicts[0].held_by is None


def test_an_agency_with_no_aggregates_holds_every_month_it_has_rows_in():
    verdicts = _plan([_partition("202501", 3)], live={3: _counts(date(2025, 1, 1), date(2025, 2, 2))})
    assert (
        verdicts[0].held_by == "agency 3 has 32 service date(s) without aggregates, from 2025-01-01; analyze it first"
    )


def test_a_month_holding_service_dates_the_ledger_lacks_is_held():
    """A catch-up backfill older than the horizon lands rows no analyze has
    aggregated yet; dropping them would leave those dates with nothing."""
    live = _counts(date(2024, 12, 31), date(2025, 2, 2))
    ledger = {day: counts for day, counts in live.items() if day != date(2025, 1, 20)}
    verdicts = _plan([_partition("202501", 1)], ledgers={1: ledger}, live={1: live})
    assert verdicts[0].held_by is not None
    assert "agency 1" in verdicts[0].held_by and "2025-01-20" in verdicts[0].held_by


def test_a_lacking_date_below_the_agencys_floor_holds_its_month_for_an_operator():
    """Rows backfilled under an agency's floor are never aggregated, so they
    are not dropped unseen either, and no analyze will clear them."""
    live = {date(2024, 6, 10): (3, 3)}
    verdicts = _plan(
        [_partition("202406", 1)],
        floors={1: date(2025, 3, 1)},
        ledgers={1: _counts(date(2025, 1, 1), date(2025, 1, 2))},
        live={1: live},
    )
    assert "2024-06-10" in verdicts[0].held_by
    assert "analyze never builds them" in verdicts[0].held_by and "operator" in verdicts[0].held_by


@pytest.mark.parametrize(
    "stale_day",
    [
        date(2025, 1, 15),  # inside the month
        date(2025, 2, 1),  # past the month, below the floor the drop sets
        date(2025, 2, 20),  # past every date a dropped row reaches, below that floor all the same
    ],
)
def test_a_date_the_drop_freezes_whose_ledger_no_longer_matches_holds_the_month(stale_day):
    live = _counts(date(2024, 12, 31), date(2025, 3, 10))
    ledger = {**live, stale_day: (4, 4)}
    verdicts = _plan([_partition("202501", 1)], ledgers={1: ledger}, live={1: live})
    assert verdicts[0].held_by is not None
    assert stale_day.isoformat() in verdicts[0].held_by


@pytest.mark.parametrize("stale_day, holds", [(date(2025, 3, 1), True), (date(2025, 3, 2), False)])
def test_a_stale_ledger_on_the_new_floor_holds_the_month(stale_day, holds):
    """A reading captured early on the floor date files under the day before
    it, which the drop freezes; the day after the floor lends to no final date."""
    live = _counts(date(2024, 12, 31), date(2025, 3, 10))
    ledger = {**live, stale_day: (4, 4)}
    verdicts = _plan([_partition("202501", 1)], ledgers={1: ledger}, live={1: live})
    assert (verdicts[0].held_by is not None) is holds


def test_dates_below_the_agencys_current_floor_are_final_and_not_compared():
    """The day an earlier drop cut short reads fewer rows than its ledger,
    by design; it must not hold every later month."""
    floor = date(2025, 3, 1)
    live = {**_counts(date(2025, 2, 1), date(2025, 4, 30)), date(2025, 2, 1): (1, 1)}
    ledger = _counts(date(2024, 12, 1), date(2025, 4, 30))
    verdicts = _plan([_partition("202502", 1)], floors={1: floor}, ledgers={1: ledger}, live={1: live})
    assert verdicts[0].held_by is None


@pytest.mark.parametrize(
    "soft_deleted, reason",
    [
        ({1: False, 2: True}, "soft-deleted"),
        ({1: False}, "unknown agency, with no agencies row"),
    ],
)
def test_an_agency_analyze_all_never_visits_holds_its_months_for_an_operator(soft_deleted, reason):
    """Whatever else it lacks, no analyze would clear an "analyze it first" hold on it."""
    good = _counts(date(2024, 12, 31), date(2025, 3, 1))
    verdicts = _plan(
        [_partition("202501", 1, 2)],
        soft_deleted=soft_deleted,
        ledgers={1: good},
        live={1: good, 2: good},
    )
    assert verdicts[0].held_by == (
        f"agency 2 is not analyzed by analyze_all: {reason}; an operator decides whether to keep or delete its rows"
    )


def test_every_agency_in_a_month_must_clear():
    good = _counts(date(2024, 12, 31), date(2025, 2, 2))
    verdicts = _plan(
        [_partition("202501", 1, 2)],
        ledgers={1: good, 2: {**good, date(2025, 1, 5): (1, 1)}},
        live={1: good, 2: good},
    )
    assert "agency 2" in verdicts[0].held_by


def test_a_month_held_for_a_lacking_date_holds_every_later_month_of_that_agency():
    """Dropping a later month would raise the floor past the unaggregated
    dates, freezing them with nothing."""
    live = _counts(date(2024, 12, 31), date(2025, 3, 2))
    ledger = {day: counts for day, counts in live.items() if day != date(2025, 1, 20)}
    verdicts = _plan([_partition("202501", 1), _partition("202502", 1)], ledgers={1: ledger}, live={1: live})
    assert [v.held_by is not None for v in verdicts] == [True, True]


def test_an_agency_whose_stores_disagree_holds_its_months():
    """analyze repairs a disagreement only from the floor on, so a month it
    would freeze waits until the stores agree."""
    held = _counts(date(2024, 12, 31), date(2025, 3, 1))
    verdicts = _plan([_partition("202501", 1)], ledgers={1: held}, live={1: held}, disagreeing={1: ["agg_route_daily"]})
    assert verdicts[0].held_by is not None and "agg_route_daily" in verdicts[0].held_by


def test_an_agency_with_a_table_awaiting_a_new_fingerprint_holds_its_months():
    held = _counts(date(2024, 12, 31), date(2025, 3, 1))
    verdicts = _plan(
        [_partition("202501", 1)],
        ledgers={1: held},
        live={1: held},
        unapplied={1: Unapplied(["agg_stop_daily"], [])},
    )
    assert "agg_stop_daily" in verdicts[0].held_by


@pytest.mark.parametrize(
    "moved, holds",
    [
        (date(2025, 2, 20), True),  # below the floor the drop sets
        (date(2025, 1, 5), True),
        (date(2025, 3, 1), True),  # that floor, whose rows can file under the day before
        (date(2025, 3, 2), False),  # past it, still rebuildable after the drop
        (date(2024, 12, 31), False),  # below the current floor, final already
    ],
)
def test_a_date_whose_version_in_force_moved_holds_the_months_that_would_freeze_it(moved, holds):
    held = _counts(date(2024, 12, 31), date(2025, 3, 2))
    verdicts = _plan(
        [_partition("202501", 1)],
        floors={1: date(2025, 1, 1)},
        ledgers={1: held},
        live={1: held},
        unapplied={1: Unapplied([], [moved])},
    )
    assert (verdicts[0].held_by is not None) is holds


# The prune itself, with the stores replaced: what it reads, writes and drops.


class _Ch:
    def __init__(self, log: list):
        self.log = log

    def command(self, sql, parameters=None):
        self.log.append(("command", sql))


_NOW = datetime(2026, 11, 15, tzinfo=UTC)


@pytest.fixture
def stores(monkeypatch):
    """A prune whose two candidate months, 202401 and 202402, are droppable."""
    log: list = []
    monkeypatch.setattr(flags, "_load_overrides", lambda: {})
    monkeypatch.setattr(
        retention,
        "read_candidates",
        lambda ch, cutoff: [UpdatesPartition("202401", 7, 70, (1, 2)), UpdatesPartition("202402", 5, 50, (2,))],
    )
    held = _counts(date(2023, 12, 1), date(2024, 3, 31))
    monkeypatch.setattr(retention, "retention_floors", lambda conn, ids: {})
    monkeypatch.setattr(retention, "read_soft_deleted", lambda conn, ids: dict.fromkeys(ids, False))
    monkeypatch.setattr(retention, "read_ledgers", lambda conn, bounds: {1: held, 2: held})
    monkeypatch.setattr(retention, "read_held_counts", lambda ch, agency_id, before, after: held)
    monkeypatch.setattr(retention, "read_disagreements", lambda conn, ch, agency_id, floor: [])
    monkeypatch.setattr(retention, "read_unapplied", lambda conn, ch, agency_id: Unapplied([], []))
    monkeypatch.setattr(
        retention.refiled,
        "rows_rewritten",
        lambda ch, ids, dates=None: log.append(("rows_rewritten", sorted(ids), min(dates), max(dates))),
    )

    @contextmanager
    def agency_lock(conn, agency_id):
        log.append(("agency lock", agency_id))
        yield

    monkeypatch.setattr(retention, "agency_ingest_lock", agency_lock)
    monkeypatch.setattr(
        retention, "_record_drops", lambda conn, run_id, verdicts: log.append(("record", [v.floor for v in verdicts]))
    )
    monkeypatch.setattr(retention, "_mark_dropped", lambda conn, run_id, pid: log.append(("dropped", pid)))

    @contextmanager
    def record_run(conn, kind, **kwargs):
        log.append(("run", kind))

        class _Run:
            run_id = 9
            rows = None
            status = "ok"

        run = _Run()
        try:
            yield run
        finally:
            log.append(("run closed", run.status, run.rows))

    monkeypatch.setattr(retention.pipeline_runs, "record_run", record_run)
    return log


def _prune(stores, **kwargs):
    def acquire():
        stores.append(("lock",))

    return retention.prune_updates(object(), _Ch(stores), acquire=acquire, now=_NOW, **kwargs)


def test_the_prune_reads_and_writes_nothing_while_its_switch_is_off(stores, monkeypatch):
    monkeypatch.delenv("UPDATES_PRUNE_ENABLED", raising=False)
    result = _prune(stores)
    assert stores == []
    assert result.dropped == []
    assert result.enabled is False


@pytest.mark.parametrize("switch", ["true", "false"])
def test_a_dry_run_lists_the_drops_and_writes_nothing_whatever_the_switch(stores, monkeypatch, switch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", switch)
    result = _prune(stores, dry_run=True)
    assert stores == []
    assert [v.partition.partition_id for v in result.verdicts if v.held_by is None] == ["202401", "202402"]
    assert result.dropped == []


def test_the_switch_is_read_once(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    reads = []
    real = retention.updates_prune_enabled

    def counted():
        reads.append(1)
        return real()

    monkeypatch.setattr(retention, "updates_prune_enabled", counted)
    _prune(stores)
    assert reads == [1]


def test_a_prune_locks_records_every_floor_then_writes_off_and_drops_each_month_in_turn(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    result = _prune(stores)
    assert stores == [
        ("lock",),
        ("agency lock", 1),
        ("agency lock", 2),
        ("run", "retention"),
        ("record", [date(2024, 3, 1), date(2024, 4, 1)]),
        # Only the service dates the month's rows can be filed under.
        ("rows_rewritten", [1, 2], date(2024, 1, 1), date(2024, 2, 1)),
        ("command", "ALTER TABLE updates DROP PARTITION ID '202401'"),
        ("dropped", "202401"),
        ("rows_rewritten", [2], date(2024, 2, 1), date(2024, 3, 1)),
        ("command", "ALTER TABLE updates DROP PARTITION ID '202402'"),
        ("dropped", "202402"),
        ("run closed", "ok", 12),
    ]
    assert result.dropped == ["202401", "202402"]


def test_a_drop_that_fails_leaves_the_rows_dropped_so_far_on_the_run(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")

    class _Failing(_Ch):
        def command(self, sql, parameters=None):
            super().command(sql, parameters)
            if "202402" in sql:
                raise RuntimeError("gone")

    with pytest.raises(RuntimeError):
        retention.prune_updates(object(), _Failing(stores), acquire=lambda: None, now=_NOW)
    assert stores[-1] == ("run closed", "ok", 7)
    assert ("dropped", "202401") in stores and ("dropped", "202402") not in stores


def test_a_prune_with_nothing_to_drop_records_an_unchanged_run(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    monkeypatch.setattr(retention, "read_ledgers", lambda conn, bounds: {})
    result = _prune(stores)
    assert stores[-2:] == [("run", "retention"), ("run closed", "unchanged", 0)]
    assert result.dropped == []
    assert all(v.held_by for v in result.verdicts)
