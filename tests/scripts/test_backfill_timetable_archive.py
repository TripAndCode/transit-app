"""scripts/backfill_timetable_archive.py maps each pre-archive version name to its kept zip's own timetable."""

import logging
import os
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from psycopg2 import sql

from db.migrate import migrate_up
from pipeline import agg_registry, static_archive
from pipeline.agg_registry import Timetable
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.static_loader import load_static
from scripts import backfill_timetable_archive as backfill
from tests.fixtures.synthetic_gtfs import build_static_zip
from tests.pipeline.test_ch_schedule_join import _row, _stop_days


@pytest.fixture
def scratch_url():
    """A second, migrated database on the test Postgres server, dropped afterwards."""
    parts = urlsplit(os.environ["DATABASE_URL"])
    name = f"backfill_scratch_{os.getpid()}"
    admin = psycopg2.connect(urlunsplit(parts._replace(path="/postgres")))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = urlunsplit(parts._replace(path=f"/{name}"))
    try:
        conn = psycopg2.connect(url)
        try:
            migrate_up(conn)
        finally:
            conn.close()
        yield url
    finally:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


def _timetable(folder, name: str, stop: str) -> str:
    """A timetable zip named *name* in *folder*, whose trip T1 makes its first stop at *stop*."""
    folder.mkdir(parents=True, exist_ok=True)
    return build_static_zip(
        folder,
        stops_rows=["SA,Stop A,34.39,132.45", "SB,Stop B,34.40,132.46"],
        routes_rows=["R1,1"],
        trips_rows=["T1,R1,,"],
        stop_times_rows=[f"T1,1,{stop},08:00:00,08:00:00"],
        filename=f"{name}.zip",
    )


def _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client):
    """Rows of timetable_old, a name the archive never mapped, beside a loaded timetable_new whose T1 stops at
    SB. timetable_old's zip, whose T1 stops at SA, is kept in the returned static dir."""
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    static_dir = tmp_path / "static"
    _timetable(static_dir / str(agency_id), "timetable_old", "SA")
    load_static(_timetable(tmp_path / "loaded", "timetable_new", "SB"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(
        ch_client,
        agency_id,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "timetable_old"),
            _row("2026-06-02T00:00:00Z", "T1", 1, 30, "timetable_new"),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SB"), (date(2026, 6, 2), "SB")]
    return static_dir


def _run(agency_id, scratch, static_dir, *extra) -> int:
    return backfill.main(
        ["--agency", str(agency_id), "--scratch-database-url", scratch, "--static-dir", str(static_dir), *extra]
    )


def _states(pg_conn, agency_id) -> dict:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT table_name, fingerprint FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        return dict(cur.fetchall())


_ARCHIVED = {name for name, table in agg_registry.TABLES.items() if table.timetable is Timetable.ARCHIVED}


def test_a_pre_archive_name_joins_its_own_timetable_after_the_backfill(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, caplog
):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 0
    join = static_archive.schedule_join(ch_client, agency_id, None)
    old = dict(zip(join.stems, join.hashes, strict=True))["timetable_old"]
    assert static_archive.is_archived(ch_client, agency_id, old)
    assert not set(_states(pg_conn, agency_id)) & _ARCHIVED
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert "scope agg_stop_daily: every date" in caplog.text
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB")]


def test_the_dry_run_lists_the_names_and_writes_nothing(tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    join, states = static_archive.schedule_join(ch_client, agency_id, None), _states(pg_conn, agency_id)
    assert _run(agency_id, scratch_url, static_dir) == 0
    assert "timetable_old" in capsys.readouterr().out
    assert static_archive.schedule_join(ch_client, agency_id, None) == join
    assert _states(pg_conn, agency_id) == states
    scratch = psycopg2.connect(scratch_url)
    with scratch.cursor() as cur:
        cur.execute("SELECT count(*) FROM agencies")
        assert cur.fetchone()[0] == 0
    scratch.close()


def test_a_name_without_a_kept_zip_is_reported_and_skipped(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    insert_updates(ch_client, agency_id, [_row("2026-06-03T00:00:00Z", "T1", 1, 30, "gtfs_static_19990101")])
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 0
    out = capsys.readouterr().out
    assert "gtfs_static_19990101" in out and "no zip" in out
    assert "gtfs_static_19990101" not in static_archive.schedule_join(ch_client, agency_id, None).stems


def test_a_second_run_finds_nothing_to_do(tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 0
    analyze(agency_id, pg_conn, ch_client)
    states = _states(pg_conn, agency_id)
    capsys.readouterr()
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 0
    assert "nothing to archive" in capsys.readouterr().out
    assert _states(pg_conn, agency_id) == states


def test_a_run_stopped_partway_still_clears_the_states(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, monkeypatch
):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    _timetable(static_dir / str(agency_id), "timetable_older", "SB")
    insert_updates(ch_client, agency_id, [_row("2026-06-03T00:00:00Z", "T1", 1, 30, "timetable_older")])
    real = backfill.load_static
    calls = []

    def fail_on_the_second(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise RuntimeError("a zip that does not load")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(backfill, "load_static", fail_on_the_second)
    with pytest.raises(RuntimeError):
        _run(agency_id, scratch_url, static_dir, "--apply")
    assert "timetable_old" in static_archive.schedule_join(ch_client, agency_id, None).stems
    assert not set(_states(pg_conn, agency_id)) & _ARCHIVED


def test_a_scratch_that_is_the_target_is_refused(tmp_path, pg_conn, agency_id, ch_client, capsys):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    assert _run(agency_id, os.environ["DATABASE_URL"], static_dir, "--apply") == 1
    assert "refused" in capsys.readouterr().err
    assert "timetable_old" not in static_archive.schedule_join(ch_client, agency_id, None).stems


def test_a_scratch_holding_another_agency_under_the_id_is_refused(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    scratch = psycopg2.connect(scratch_url)
    with scratch.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_id, agency_name, feed_url) VALUES (%s, 'someone else', 'http://elsewhere')",
            (agency_id,),
        )
    scratch.commit()
    scratch.close()
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 1
    assert "refused" in capsys.readouterr().err


def test_the_medians_kept_in_the_scratch_reach_the_target(pg_conn, agency_id, scratch_url):
    scratch = psycopg2.connect(scratch_url)
    with scratch.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_id, agency_name, feed_url) VALUES (%s, 'x', 'http://x')", (agency_id,)
        )
        cur.execute("INSERT INTO route_headway_by_content VALUES (%s, 'k', 'R1', 1, 600)", (agency_id,))
    scratch.commit()
    assert backfill.copy_medians(scratch, pg_conn, agency_id, "k") == 1
    assert backfill.copy_medians(scratch, pg_conn, agency_id, "k") == 0
    scratch.close()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_code, scheduled_headway_median_sec FROM route_headway_by_content "
            "WHERE agency_id = %s AND content_hash = 'k'",
            (agency_id,),
        )
        assert cur.fetchall() == [("R1", 600.0)]


def test_a_failed_median_copy_leaves_the_name_for_a_rerun(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, monkeypatch
):
    """The map row takes a name out of every later run's candidates, so it lands last."""
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)

    def the_target_went_away(*args, **kwargs):
        raise RuntimeError("the target went away")

    monkeypatch.setattr(backfill, "copy_medians", the_target_went_away)
    with pytest.raises(RuntimeError):
        _run(agency_id, scratch_url, static_dir, "--apply")
    assert "timetable_old" not in static_archive.schedule_join(ch_client, agency_id, None).stems
