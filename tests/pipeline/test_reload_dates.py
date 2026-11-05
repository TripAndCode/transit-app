"""A reload rebuilds a date holding rows that follow the loaded timetable only
where what those rows read of it changed."""

import dataclasses
import logging
from datetime import date

import pytest

from pipeline import agg_registry
from pipeline.analyze import analyze, table_fingerprints
from pipeline.clickhouse import insert_updates
from pipeline.static_loader import load_static
from tests.fixtures.synthetic_gtfs import build_static_zip
from tests.pipeline.test_ch_schedule_join import _row, _stop_days


def _two_trips(tmp_path, name: str, t1_stop: str, *, t2_route: str = "R1") -> str:
    """A timetable zip, version-named *name*: trip T1 of R1 stops first at *t1_stop*, trip T2
    of *t2_route* at SA. Another *t2_route* is other content (static_content.STATIC_DEPENDENCY_COLUMNS)
    with the same stop times; the version name alone is not content."""
    return build_static_zip(
        tmp_path,
        stops_rows=["SA,Stop A,34.39,132.45", "SB,Stop B,34.40,132.46"],
        routes_rows=["R1,1", "R2,2"],
        trips_rows=["T1,R1,,", f"T2,{t2_route},,"],
        stop_times_rows=[f"T1,1,{t1_stop},08:00:00,08:00:00", "T2,1,SA,09:00:00,09:00:00"],
        filename=f"{name}.zip",
    )


def _following_on_two_trips(tmp_path, pg_conn, agency_id, ch_client, stem=None) -> None:
    """A stamping agency on timetable_a, with rows following it: T1 on 6/1, T2 on 6/2."""
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    load_static(_two_trips(tmp_path, "timetable_a", "SA"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(
        ch_client,
        agency_id,
        [_row("2026-06-01T00:00:00Z", "T1", 1, 30, stem), _row("2026-06-02T00:00:00Z", "T2", 1, 30, stem)],
    )
    analyze(agency_id, pg_conn, ch_client)


def _reanalyze(agency_id, pg_conn, ch_client, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)


@pytest.mark.parametrize("stem", [None, "gtfs_static_19990101"])
def test_a_reload_rebuilds_only_the_following_dates_whose_trip_changed(
    tmp_path, pg_conn, agency_id, ch_client, caplog, stem
):
    _following_on_two_trips(tmp_path, pg_conn, agency_id, ch_client, stem)
    load_static(_two_trips(tmp_path, "timetable_b", "SB"), agency_id, pg_conn, ch_client=ch_client)
    _reanalyze(agency_id, pg_conn, ch_client, caplog)
    assert "scope agg_route_headway: every date" in caplog.text
    assert "scope agg_stop_daily: 1 date(s)" in caplog.text
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SB"), (date(2026, 6, 2), "SA")]


def test_a_reload_with_the_same_stop_times_rebuilds_no_following_date(tmp_path, pg_conn, agency_id, ch_client, caplog):
    _following_on_two_trips(tmp_path, pg_conn, agency_id, ch_client)
    load_static(_two_trips(tmp_path, "timetable_b", "SA", t2_route="R2"), agency_id, pg_conn, ch_client=ch_client)
    _reanalyze(agency_id, pg_conn, ch_client, caplog)
    assert "scope agg_route_headway: every date" in caplog.text
    assert "scope agg_stop_daily: unchanged" in caplog.text


def test_a_bump_of_the_loaded_table_rebuilds_no_following_date(
    tmp_path, pg_conn, agency_id, ch_client, caplog, monkeypatch
):
    _following_on_two_trips(tmp_path, pg_conn, agency_id, ch_client)
    table = agg_registry.TABLES["agg_route_headway"]
    monkeypatch.setitem(agg_registry.TABLES, table.name, dataclasses.replace(table, version=table.version + 1))
    _reanalyze(agency_id, pg_conn, ch_client, caplog)
    assert "scope agg_route_headway: every date" in caplog.text
    assert "scope agg_stop_daily: unchanged" in caplog.text


def test_two_reloads_between_runs_compare_the_timetable_last_built_against(
    tmp_path, pg_conn, agency_id, ch_client, caplog
):
    _following_on_two_trips(tmp_path, pg_conn, agency_id, ch_client)
    load_static(_two_trips(tmp_path, "timetable_b", "SB"), agency_id, pg_conn, ch_client=ch_client)
    load_static(_two_trips(tmp_path, "timetable_c", "SA", t2_route="R2"), agency_id, pg_conn, ch_client=ch_client)
    _reanalyze(agency_id, pg_conn, ch_client, caplog)
    assert "scope agg_route_headway: every date" in caplog.text
    assert "scope agg_stop_daily: unchanged" in caplog.text
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SA")]


def test_a_reload_from_an_unarchived_timetable_rebuilds_every_following_date(
    tmp_path, pg_conn, agency_id, ch_client, caplog
):
    _following_on_two_trips(tmp_path, pg_conn, agency_id, ch_client)
    # The stored state names a timetable the archive does not keep.
    unknown = table_fingerprints("static_join", True, ["static_trips:0:0"])["agg_route_headway"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE agg_table_state SET fingerprint = %s WHERE agency_id = %s AND table_name = 'agg_route_headway'",
            (unknown, agency_id),
        )
    pg_conn.commit()
    load_static(_two_trips(tmp_path, "timetable_b", "SB"), agency_id, pg_conn, ch_client=ch_client)
    _reanalyze(agency_id, pg_conn, ch_client, caplog)
    assert "is not archived" in caplog.text
    assert "scope agg_stop_daily: 2 date(s)" in caplog.text
