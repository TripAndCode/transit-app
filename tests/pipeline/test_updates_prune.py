"""The `updates` partition prune against real stores: it drops a month past
the horizon whose dates are all aggregated, records the floor that sets, holds
a month with dates its agency's ledger lacks, and writes nothing on a dry run
or while its switch is off."""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline import flags, refiled
from pipeline.clickhouse import insert_updates
from pipeline.retention import prune_updates, retention_floor
from pipeline.static_archive import FILED_DATES

UTC = timezone.utc
_OLD = datetime(2023, 1, 15, 3, tzinfo=UTC)  # service date 2023-01-15
_OLD_DAY = date(2023, 1, 15)
_RECENT = datetime.now(UTC) - timedelta(days=2)


def _filed(ch_client, agency_id, day: date) -> None:
    ch_client.insert(
        FILED_DATES,
        [(agency_id, day, f"b-{day}", "v1", "c1", {}, 1, refiled.RULES)],
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


@pytest.fixture
def seeded(pg_conn, ch_client, agency_id):
    """One month past the horizon, one recent day, and a written refiled date in each."""
    insert_updates(
        ch_client,
        agency_id,
        [
            ("old.pb", _OLD, "T1", "weekday", "07:00:00", "R1", 1, 30),
            ("old.pb", _OLD, "T1", "weekday", "07:05:00", "R1", 2, 40),
            ("new.pb", _RECENT, "T1", "weekday", "07:00:00", "R1", 1, 50),
        ],
    )
    _filed(ch_client, agency_id, _OLD_DAY)
    _filed(ch_client, agency_id, _RECENT.date())
    return agency_id


def _ledger(pg_conn, agency_id, day: date, rows: int) -> None:
    """A ledger row for *day*, with every table recorded as built under the
    fingerprints a run would build under now, as after an analyze."""
    from pipeline.analyze import table_fingerprints

    with pg_conn.cursor() as cur:
        cur.execute("SELECT ingest_strategy FROM agencies WHERE agency_id = %s", (agency_id,))
        (strategy,) = cur.fetchone()
        for name, fingerprint in table_fingerprints(strategy, False, []).items():
            cur.execute(
                "INSERT INTO agg_table_state (agency_id, table_name, fingerprint) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (agency_id, name, fingerprint),
            )
        cur.execute(
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count, total_rows) "
            "VALUES (%s, %s, %s, 0, %s)",
            (agency_id, day, rows, rows),
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


def _recorded(pg_conn) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT partition_id, agency_ids, retention_floor, rows, dropped_at IS NOT NULL "
            "FROM updates_pruned_months ORDER BY id"
        )
        return cur.fetchall()


def _written_off(ch_client, agency_id) -> list[date]:
    return sorted(day for day, kept in refiled.written(ch_client, agency_id).items() if not kept.build)


def _prune(pg_conn, ch_client, **kwargs):
    return prune_updates(pg_conn, ch_client, acquire=lambda: None, sleep=lambda seconds: None, **kwargs)


def test_a_month_past_the_horizon_whose_dates_are_aggregated_is_dropped(pg_conn, ch_client, seeded, monkeypatch):
    _ledger(pg_conn, seeded, _OLD_DAY, 2)
    _switch(monkeypatch, True)
    before = _partitions(ch_client)
    assert "202301" in before

    result = _prune(pg_conn, ch_client)

    assert [v.partition.partition_id for v in result.verdicts] == ["202301"]
    assert result.dropped == ["202301"]
    assert _partitions(ch_client) == [p for p in before if p != "202301"]
    assert _runs(pg_conn) == [("retention", "ok", 2)]
    assert _recorded(pg_conn) == [("202301", [seeded], date(2023, 3, 1), 2, True)]
    assert retention_floor(pg_conn, seeded) == date(2023, 3, 1)
    # Only the dropped month's dates are written off, not the recent one.
    assert _written_off(ch_client, seeded) == [_OLD_DAY]


def test_a_month_with_dates_the_ledger_lacks_is_held(pg_conn, ch_client, seeded, monkeypatch):
    _ledger(pg_conn, seeded, _RECENT.date(), 1)
    _switch(monkeypatch, True)
    before = _partitions(ch_client)

    result = _prune(pg_conn, ch_client)

    assert result.dropped == []
    assert [(v.partition.partition_id, v.held_by) for v in result.verdicts] == [
        ("202301", f"agency {seeded} has 1 service date(s) without aggregates, from 2023-01-15; analyze it first")
    ]
    assert _partitions(ch_client) == before
    assert _runs(pg_conn) == [("retention", "unchanged", 0)]
    assert _recorded(pg_conn) == []
    assert _written_off(ch_client, seeded) == []


@pytest.mark.parametrize("dry_run, on", [(True, True), (True, False), (False, False)])
def test_a_dry_run_or_a_switched_off_prune_writes_nothing(pg_conn, ch_client, seeded, monkeypatch, dry_run, on):
    _ledger(pg_conn, seeded, _OLD_DAY, 2)
    _switch(monkeypatch, on)
    before = _partitions(ch_client)

    result = _prune(pg_conn, ch_client, dry_run=dry_run)

    assert result.dropped == []
    assert _partitions(ch_client) == before
    assert _runs(pg_conn) == []
    assert _recorded(pg_conn) == []
    assert retention_floor(pg_conn, seeded) is None
    assert _written_off(ch_client, seeded) == []
    if dry_run:
        assert [v.partition.partition_id for v in result.verdicts if v.held_by is None] == ["202301"]


def test_a_prune_with_no_month_past_the_horizon_records_an_unchanged_run(pg_conn, ch_client, monkeypatch):
    _switch(monkeypatch, True)
    result = _prune(pg_conn, ch_client)
    assert (result.verdicts, result.dropped) == ([], [])
    assert _runs(pg_conn) == [("retention", "unchanged", 0)]


def test_a_floor_recorded_mid_month_reads_as_the_first_of_the_next(pg_conn, seeded):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes) "
            "VALUES ('202301', %s, %s, 1, 1), ('202212', %s, %s, 1, 1)",
            ([seeded], date(2023, 2, 2), [seeded], date(2023, 1, 1)),
        )
    pg_conn.commit()
    assert retention_floor(pg_conn, seeded) == date(2023, 3, 1)


@pytest.mark.parametrize("reason", ["soft-deleted", "unknown agency, with no agencies row"])
def test_an_agency_analyze_all_never_visits_holds_its_month_for_an_operator(
    pg_conn, ch_client, seeded, monkeypatch, reason
):
    """Its rows share the month with an agency whose dates are all aggregated."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES ('gone', 'http://example.com/gone.pb', "
            "now()) RETURNING agency_id"
        )
        (other,) = cur.fetchone()
        if reason != "soft-deleted":
            cur.execute("DELETE FROM agencies WHERE agency_id = %s", (other,))
    pg_conn.commit()
    insert_updates(ch_client, other, [("gone.pb", _OLD, "T9", "weekday", "07:00:00", "R9", 1, 10)])
    _ledger(pg_conn, seeded, _OLD_DAY, 2)
    _switch(monkeypatch, True)

    result = _prune(pg_conn, ch_client)

    assert result.dropped == []
    assert [(v.partition.partition_id, v.held_by) for v in result.verdicts] == [
        (
            "202301",
            f"agency {other} is not analyzed by analyze_all: {reason}; "
            "an operator decides whether to keep or delete its rows",
        )
    ]
    assert retention_floor(pg_conn, seeded) is None
