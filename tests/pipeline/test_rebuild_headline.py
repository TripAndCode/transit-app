"""A rebuild forced by a method version or fingerprint change returns the
agency's headline before and after it, for its pipeline_runs row."""

import asyncio
import dataclasses
import json
import logging
import os
from datetime import date, time

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline import agg_registry, rebuild_headline
from pipeline.analyze import analyze, mark_for_full_rebuild
from pipeline.reports.council import compute_council_summary
from pipeline.reports.rankings import DEFAULT_PRESET
from pipeline.runs import record_run
from tests.conftest import mirror_updates_to_ch

DAYS = (date(2026, 6, 8), date(2026, 6, 9))
DELAYS = (30, 90, 150, 240)


def _db_url() -> str:
    return os.environ["DATABASE_URL"]


def _seed(pg_conn, agency_id) -> None:
    """Four departures on each of DAYS, each its own trip so all of them count."""
    with pg_conn.cursor() as cur:
        for day in DAYS:
            for i, delay in enumerate(DELAYS):
                cur.execute(
                    "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                    "scheduled_time, route_code, stop_sequence, dep_delay) "
                    "VALUES (%s, %s, %s, %s, '平日', %s, 'R1', 1, %s)",
                    (agency_id, f"{day}-{i}.pb", f"{day}T08:1{i}:00", f"T{i}", time(8, 10 + i), delay),
                )
    pg_conn.commit()


def _mirror_and_analyze(agency_id, pg_conn, ch_client, **kwargs):
    """Mirror the Postgres `updates` seed into ClickHouse and analyze. A second
    mirror adds the rows again, which the ledger reads as changed days."""
    mirror_updates_to_ch(ch_client, agency_id)
    return analyze(agency_id, pg_conn, ch_client, **kwargs)


def _inflate_delays(pg_conn, agency_id, minutes: int) -> None:
    """Move the stored average delay without touching what a rebuild computes,
    so only rows read before the rebuild show it."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE agg_route_daily_dist SET sum_delay_sec = sum_delay_sec + %s * samples WHERE agency_id = %s",
            (minutes * 60, agency_id),
        )
        assert cur.rowcount > 0
    pg_conn.commit()


def _stored_avg_delay_min(pg_conn, agency_id) -> float:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT SUM(sum_delay_sec)::float / SUM(samples) / 60 FROM agg_route_daily_dist WHERE agency_id = %s",
            (agency_id,),
        )
        return cur.fetchone()[0]


def _bump(monkeypatch, table: str) -> None:
    monkeypatch.setitem(agg_registry.TABLES, table, dataclasses.replace(agg_registry.TABLES[table], version=99))


def test_a_method_bump_records_the_headline_from_before_and_after_its_rebuild(
    pg_conn, agency_id, ch_client, monkeypatch
):
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    _inflate_delays(pg_conn, agency_id, minutes=10)
    _bump(monkeypatch, "agg_route_daily_dist")

    headline = analyze(agency_id, pg_conn, ch_client, db_url=_db_url())

    assert headline is not None
    assert (headline["first_day"], headline["last_day"]) == ("2026-06-08", "2026-06-09")
    before, after = headline["before"], headline["after"]
    assert before["avg_delay_min"] == pytest.approx(after["avg_delay_min"] + 10)
    assert before["on_time_pct"] == after["on_time_pct"]
    assert before["samples"] == after["samples"] == len(DAYS) * len(DELAYS)
    assert after["avg_delay_min"] == pytest.approx(_stored_avg_delay_min(pg_conn, agency_id), abs=0.005)
    assert headline["method_version"] == agg_registry.METHOD_VERSION


async def _council_summary(agency_id: int, first: date, last: date) -> dict:
    conn = await asyncpg.connect(_db_url())
    try:
        return await compute_council_summary(agency_id, RangeCtx(from_date=first, to_date=last), conn, None)
    finally:
        await conn.close()


def test_the_headline_is_the_council_summarys_under_the_default_definition(pg_conn, agency_id, ch_client, monkeypatch):
    """Whatever the default on-time window is, the headline is the figure a
    report that names no window prints."""
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    _bump(monkeypatch, "agg_route_daily_dist")

    headline = analyze(agency_id, pg_conn, ch_client, db_url=_db_url())

    assert headline is not None
    summary = asyncio.run(_council_summary(agency_id, *DAYS))
    expected = {k: summary[k] for k in ("on_time_pct", "avg_delay_min", "samples")}
    assert headline["before"] == headline["after"] == expected
    assert headline["preset"] == DEFAULT_PRESET


def test_a_full_rebuild_records_the_headline(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    mark_for_full_rebuild(pg_conn, [agency_id])

    headline = analyze(agency_id, pg_conn, ch_client, db_url=_db_url())

    assert headline is not None
    assert headline["before"] == headline["after"]


def test_a_run_with_no_ledger_to_scope_it_records_the_headline(pg_conn, agency_id, ch_client):
    """With no ledger every table is rebuilt in full, over history that stood."""
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM agg_feed_health WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()

    assert analyze(agency_id, pg_conn, ch_client, db_url=_db_url()) is not None


def test_an_agencys_first_run_has_nothing_to_compare(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    assert _mirror_and_analyze(agency_id, pg_conn, ch_client, db_url=_db_url()) is None


def test_a_run_over_changed_days_records_nothing(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    assert _mirror_and_analyze(agency_id, pg_conn, ch_client, db_url=_db_url()) is None


def test_a_run_with_nothing_new_records_nothing(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    assert analyze(agency_id, pg_conn, ch_client, db_url=_db_url()) is None


def test_a_rebuild_repairing_stores_that_disagree_records_nothing(pg_conn, agency_id, ch_client):
    """Its rows are put back as they were built, under the same method."""
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    built = _stored_avg_delay_min(pg_conn, agency_id)
    _inflate_delays(pg_conn, agency_id, minutes=10)

    assert analyze(agency_id, pg_conn, ch_client, db_url=_db_url()) is None
    assert _stored_avg_delay_min(pg_conn, agency_id) == pytest.approx(built)


@pytest.mark.parametrize("failing_read", [1, 2], ids=["before", "after"])
def test_a_headline_that_cannot_be_read_leaves_the_rebuild_standing(
    pg_conn, agency_id, ch_client, monkeypatch, caplog, failing_read
):
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    built = _stored_avg_delay_min(pg_conn, agency_id)
    _inflate_delays(pg_conn, agency_id, minutes=10)
    _bump(monkeypatch, "agg_route_daily_dist")
    reads = []
    pooled = rebuild_headline._agency_on_time_pooled

    async def _fail_one(*args, **kwargs):
        reads.append(args)
        if len(reads) == failing_read:
            raise RuntimeError("connection reset")
        return await pooled(*args, **kwargs)

    monkeypatch.setattr(rebuild_headline, "_agency_on_time_pooled", _fail_one)

    with caplog.at_level(logging.WARNING, logger="pipeline.rebuild_headline"):
        assert analyze(agency_id, pg_conn, ch_client, db_url=_db_url()) is None

    assert len(reads) == failing_read
    assert any(r.name == "pipeline.rebuild_headline" and r.levelno == logging.WARNING for r in caplog.records)
    assert _stored_avg_delay_min(pg_conn, agency_id) == pytest.approx(built)


def test_the_headline_lands_on_the_analyze_runs_row(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    _mirror_and_analyze(agency_id, pg_conn, ch_client)
    mark_for_full_rebuild(pg_conn, [agency_id])

    with record_run(pg_conn, "analyze", agency_id=agency_id) as run:
        run.rebuild_headline = analyze(agency_id, pg_conn, ch_client, db_url=_db_url())

    with pg_conn.cursor() as cur:
        cur.execute("SELECT status, rebuild_headline::text FROM pipeline_runs WHERE run_id = %s", (run.run_id,))
        status, stored = cur.fetchone()
    assert status == "ok"
    assert run.rebuild_headline is not None and json.loads(stored) == run.rebuild_headline
