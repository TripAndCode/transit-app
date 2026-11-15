"""The `updates` partition prune: which months it may drop, and that it drops
nothing unless its kill switch is on and the run is not a dry run."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import flags, retention
from pipeline.retention import PartitionVerdict, UpdatesPartition, partition_span, plan_updates_prune
from pipeline.service_day import service_day_start_utc

UTC = timezone.utc


def _partition(pid: str, *agencies: int) -> UpdatesPartition:
    return UpdatesPartition(pid, rows=10, bytes=100, agency_ids=tuple(agencies))


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


def test_only_months_that_end_by_the_cutoff_are_candidates():
    cutoff = datetime(2025, 3, 1, tzinfo=UTC)
    ledger = {1: date(2026, 1, 1)}
    verdicts = plan_updates_prune(
        [_partition("202501", 1), _partition("202502", 1), _partition("202503", 1)],
        cutoff=cutoff,
        oldest_aggregated=ledger,
    )
    assert [v.partition.partition_id for v in verdicts] == ["202501", "202502"]
    assert all(v.held_by is None for v in verdicts)


def test_a_month_ending_a_second_past_the_cutoff_stays():
    cutoff = datetime(2025, 2, 28, 23, 59, 59, tzinfo=UTC)
    assert plan_updates_prune([_partition("202502", 1)], cutoff=cutoff, oldest_aggregated={1: date(2026, 1, 1)}) == []


def test_a_month_holding_the_day_before_an_agencys_oldest_aggregated_date_is_held():
    """The day before is held too: a row captured then can file a stop event
    under the oldest date itself."""
    oldest = date(2025, 3, 2)
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    verdicts = plan_updates_prune(
        [_partition("202501", 1), _partition("202502", 1)], cutoff=cutoff, oldest_aggregated={1: oldest}
    )
    by_id = {v.partition.partition_id: v for v in verdicts}
    assert by_id["202501"].held_by is None
    # 202502 runs to 2025-03-01 00:00 UTC, past service day 2025-03-01's start (2025-02-28 19:00 UTC).
    assert by_id["202502"].held_by is not None
    assert "2025-03-02" in by_id["202502"].held_by


def test_a_month_ending_before_the_floor_is_dropped():
    oldest = date(2025, 3, 3)
    assert service_day_start_utc(oldest - timedelta(days=1)) == datetime(2025, 3, 1, 19, tzinfo=UTC)
    verdicts = plan_updates_prune(
        [_partition("202502", 1)], cutoff=datetime(2026, 1, 1, tzinfo=UTC), oldest_aggregated={1: oldest}
    )
    assert verdicts[0].held_by is None


def test_every_agency_in_a_month_must_clear_its_floor():
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    verdicts = plan_updates_prune(
        [_partition("202501", 1, 2)],
        cutoff=cutoff,
        oldest_aggregated={1: date(2025, 6, 1), 2: date(2025, 1, 10)},
    )
    assert verdicts[0].held_by is not None
    assert "agency 2" in verdicts[0].held_by


def test_an_agency_with_no_aggregates_holds_every_month_it_has_rows_in():
    """Its first analyze rebuilds every date it has."""
    verdicts = plan_updates_prune(
        [_partition("202501", 3)], cutoff=datetime(2026, 1, 1, tzinfo=UTC), oldest_aggregated={}
    )
    assert verdicts[0].held_by is not None
    assert "agency 3" in verdicts[0].held_by


# The prune itself, with the stores replaced: what it reads, writes and drops.


class _Ch:
    def __init__(self, log: list):
        self.log = log

    def command(self, sql, parameters=None):
        self.log.append(("command", sql))


@pytest.fixture
def stores(monkeypatch):
    """A prune whose one candidate month, 202401, is droppable."""
    log: list = []
    monkeypatch.setattr(flags, "_load_overrides", lambda: {})
    monkeypatch.setattr(retention, "read_candidates", lambda ch, cutoff: [UpdatesPartition("202401", 7, 70, (1, 2))])
    monkeypatch.setattr(
        retention, "oldest_aggregated", lambda conn, agency_ids: {1: date(2026, 1, 1), 2: date(2026, 1, 1)}
    )
    monkeypatch.setattr(
        retention.refiled, "rows_rewritten", lambda ch, ids: log.append(("rows_rewritten", sorted(ids)))
    )

    @contextmanager
    def record_run(conn, kind, **kwargs):
        log.append(("run", kind))

        class _Run:
            rows = None
            status = "ok"

        run = _Run()
        yield run
        log.append(("run closed", run.status, run.rows))

    monkeypatch.setattr(retention.pipeline_runs, "record_run", record_run)
    return log


_NOW = datetime(2026, 11, 15, tzinfo=UTC)


def test_the_prune_writes_nothing_while_its_switch_is_off(stores, monkeypatch):
    monkeypatch.delenv("UPDATES_PRUNE_ENABLED", raising=False)
    result = retention.prune_updates(object(), _Ch(stores), now=_NOW)
    assert stores == []
    assert result.dropped == []
    assert result.enabled is False


def test_a_dry_run_lists_the_drops_and_writes_nothing(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    result = retention.prune_updates(object(), _Ch(stores), dry_run=True, now=_NOW)
    assert stores == []
    assert [v.partition.partition_id for v in result.verdicts if v.held_by is None] == ["202401"]
    assert result.dropped == []


def test_a_dry_run_works_with_the_switch_off(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "false")
    result = retention.prune_updates(object(), _Ch(stores), dry_run=True, now=_NOW)
    assert stores == []
    assert [v.partition.partition_id for v in result.verdicts] == ["202401"]


def test_the_refiled_rows_are_written_off_before_the_drop_inside_a_recorded_run(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    result = retention.prune_updates(object(), _Ch(stores), now=_NOW)
    assert stores == [
        ("run", "retention"),
        ("rows_rewritten", [1, 2]),
        ("command", "ALTER TABLE updates DROP PARTITION ID '202401'"),
        ("run closed", "ok", 7),
    ]
    assert result.dropped == ["202401"]


def test_a_prune_with_nothing_to_drop_records_an_unchanged_run(stores, monkeypatch):
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true")
    monkeypatch.setattr(retention, "oldest_aggregated", lambda conn, agency_ids: {})
    result = retention.prune_updates(object(), _Ch(stores), now=_NOW)
    assert stores == [("run", "retention"), ("run closed", "unchanged", 0)]
    assert result.dropped == []
    assert isinstance(result.verdicts[0], PartitionVerdict)
