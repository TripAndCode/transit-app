"""The `updates` partition prune against real stores: it drops a month past
the horizon that no aggregate covers, holds one an agency's aggregates still
reach, and writes nothing on a dry run or while its switch is off."""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import flags, refiled
from pipeline.clickhouse import insert_updates
from pipeline.retention import prune_updates
from pipeline.static_archive import FILED_DATES

UTC = timezone.utc
_OLD = datetime(2023, 1, 15, 3, tzinfo=UTC)


@pytest.fixture
def seeded(pg_conn, ch_client, agency_id):
    """One month past the horizon, one recent day, and a written refiled date."""
    recent = datetime.now(UTC) - timedelta(days=2)
    insert_updates(
        ch_client,
        agency_id,
        [
            ("old.pb", _OLD, "T1", "weekday", "07:00:00", "R1", 1, 30),
            ("old.pb", _OLD, "T1", "weekday", "07:05:00", "R1", 2, 40),
            ("new.pb", recent, "T1", "weekday", "07:00:00", "R1", 1, 50),
        ],
    )
    ch_client.insert(
        FILED_DATES,
        [(agency_id, recent.date(), "b1", "v1", "c1", {}, 1, refiled.RULES)],
        column_names=[
            "agency_id",
            "filed_date",
            "build",
            "static_version_id",
            "content_hash",
            "stamped",
            "source_rows",
            "rules",
        ],
    )
    return agency_id


def _ledger(pg_conn, agency_id, day: date) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count, total_rows) "
            "VALUES (%s, %s, 1, 0, 1)",
            (agency_id, day),
        )
    pg_conn.commit()


def _switch(monkeypatch, on: bool) -> None:
    monkeypatch.setenv("UPDATES_PRUNE_ENABLED", "true" if on else "false")
    flags.reset_cache()


def _partitions(ch_client) -> list[str]:
    rows = ch_client.query(
        "SELECT DISTINCT partition_id FROM system.parts "
        "WHERE database = currentDatabase() AND table = 'updates' AND active ORDER BY partition_id"
    ).result_rows
    return [row[0] for row in rows]


def _runs(pg_conn) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT kind, status, rows FROM pipeline_runs ORDER BY run_id")
        return cur.fetchall()


def _write_offs(ch_client, agency_id) -> int:
    return ch_client.query(
        f"SELECT count() FROM {FILED_DATES} WHERE agency_id = {{a:UInt16}} AND build = ''", parameters={"a": agency_id}
    ).result_rows[0][0]


def test_a_month_past_the_horizon_that_no_aggregate_covers_is_dropped(pg_conn, ch_client, seeded, monkeypatch):
    _ledger(pg_conn, seeded, datetime.now(UTC).date() - timedelta(days=2))
    _switch(monkeypatch, True)
    before = _partitions(ch_client)
    assert "202301" in before

    result = prune_updates(pg_conn, ch_client)

    assert result.dropped == ["202301"]
    assert _partitions(ch_client) == [p for p in before if p != "202301"]
    assert _runs(pg_conn) == [("retention", "ok", 2)]
    assert _write_offs(ch_client, seeded) == 1


def test_a_month_an_agencys_aggregates_still_reach_is_held(pg_conn, ch_client, seeded, monkeypatch):
    _ledger(pg_conn, seeded, _OLD.date())
    _switch(monkeypatch, True)
    before = _partitions(ch_client)

    result = prune_updates(pg_conn, ch_client)

    assert result.dropped == []
    assert [v.partition.partition_id for v in result.verdicts if v.held_by] == ["202301"]
    assert _partitions(ch_client) == before
    assert _runs(pg_conn) == [("retention", "unchanged", 0)]
    assert _write_offs(ch_client, seeded) == 0


@pytest.mark.parametrize("dry_run, on", [(True, True), (True, False), (False, False)])
def test_a_dry_run_or_a_switched_off_prune_writes_nothing(pg_conn, ch_client, seeded, monkeypatch, dry_run, on):
    _ledger(pg_conn, seeded, datetime.now(UTC).date() - timedelta(days=2))
    _switch(monkeypatch, on)
    before = _partitions(ch_client)

    result = prune_updates(pg_conn, ch_client, dry_run=dry_run)

    assert result.dropped == []
    assert _partitions(ch_client) == before
    assert _runs(pg_conn) == []
    assert _write_offs(ch_client, seeded) == 0
    if dry_run:
        assert [v.partition.partition_id for v in result.verdicts if v.held_by is None] == ["202301"]
