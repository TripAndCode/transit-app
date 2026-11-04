"""Each archived timetable keeps its per-route scheduled headway medians."""

import dataclasses
import logging
from datetime import date

import pytest

from pipeline import agg_registry, static_archive, static_headways
from pipeline.analyze import analyze
from pipeline.clickhouse import UPDATE_COLUMNS, insert_updates
from pipeline.static_content import static_parts
from pipeline.static_loader import load_static
from tests.fixtures.synthetic_gtfs import build_static_zip


def test_the_medians_table_is_keyed_by_content_route_and_version(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = 'route_headway_by_content'::regclass AND i.indisprimary "
            "ORDER BY a.attname"
        )
        assert [row[0] for row in cur.fetchall()] == ["agency_id", "content_hash", "logic_version", "route_code"]


def _seed(pg_conn, agency_id, departures, stem="timetable_a"):
    """Route R1 at stop s1, one trip per departure under service WD, its trips named *stem*."""
    from tests.pipeline.test_analyze import _seed_route_headway_schedule

    _seed_route_headway_schedule(pg_conn, agency_id, "R1", "WD", "s1", departures)
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_trips SET static_version_id = %s WHERE agency_id = %s", (stem, agency_id))
    pg_conn.commit()
    parts = static_parts(agency_id, pg_conn, True)
    return static_archive.content_hash(parts), parts


def _rows(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash, route_code, logic_version, scheduled_headway_median_sec "
            "FROM route_headway_by_content WHERE agency_id = %s ORDER BY logic_version",
            (agency_id,),
        )
        return cur.fetchall()


def test_recording_keeps_the_loaded_timetables_medians(pg_conn, agency_id):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 1
    assert static_headways.route_medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


def test_recording_twice_adds_nothing(pg_conn, agency_id):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    static_headways.record_route_headways(pg_conn, agency_id, key, parts)
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 0
    assert len(_rows(pg_conn, agency_id)) == 1


def test_a_route_with_no_median_still_gets_a_row(pg_conn, agency_id):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00"])
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 1
    assert static_headways.route_medians(pg_conn, agency_id, [key]) == {(key, "R1"): None}
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 0


def test_a_timetable_changed_while_recording_is_not_recorded(pg_conn, agency_id, monkeypatch):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    monkeypatch.setattr(static_headways, "static_parts", lambda *args: [*parts, "static_routes:9:9"])
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 0
    assert _rows(pg_conn, agency_id) == []


def test_a_bump_of_agg_route_headway_records_again(pg_conn, agency_id, monkeypatch):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    static_headways.record_route_headways(pg_conn, agency_id, key, parts)
    table = agg_registry.TABLES["agg_route_headway"]
    monkeypatch.setitem(agg_registry.TABLES, "agg_route_headway", dataclasses.replace(table, version=table.version + 1))
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 1
    assert [row[2] for row in _rows(pg_conn, agency_id)] == [table.version, table.version + 1]
    assert static_headways.route_medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


def test_load_static_records_the_outgoing_timetables_medians(tmp_path, pg_conn, agency_id, ch_client):
    key, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    incoming = build_static_zip(
        tmp_path,
        stops_rows=["s1,Stop 1,34.39,132.45"],
        routes_rows=["R1,1"],
        trips_rows=["T1,R1,,"],
        stop_times_rows=["T1,1,s1,09:00:00,09:00:00"],
        filename="timetable_b.zip",
    )
    load_static(incoming, agency_id, pg_conn, ch_client=ch_client)
    assert static_headways.route_medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


def test_analyze_records_the_loaded_timetables_medians(pg_conn, agency_id, ch_client):
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    key, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    analyze(agency_id, pg_conn, ch_client)
    assert static_headways.route_medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


def _departure(captured_at: str, trip: str, scheduled_sec: int, stem: str) -> tuple:
    """One departure poll at stop s1 of route R1, on time, in insert_updates' order."""
    values = dict.fromkeys(UPDATE_COLUMNS[1:])
    values.update(
        file_name=f"h/{captured_at}/{trip}",
        captured_at=captured_at,
        trip_id=trip,
        route_code="R1",
        stop_sequence=1,
        stop_id="s1",
        dep_delay=0,
        scheduled_sec=scheduled_sec,
        static_version_id=stem,
    )
    return tuple(values[column] for column in UPDATE_COLUMNS[1:])


def _a_long_gap(day: str, stem: str) -> list[tuple]:
    """Two departures 1500 s apart: long against a 600 s median (1.75 x 600 = 1050), not a 1200 s one."""
    return [_departure(f"{day}T00:00:00Z", "T0", 28800, stem), _departure(f"{day}T00:30:00Z", "T1", 30300, stem)]


def _replace_timetable(pg_conn, agency_id, departures, stem):
    with pg_conn.cursor() as cur:
        for table in ("static_stop_times", "static_trips", "static_routes", "static_stops"):
            cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    return _seed(pg_conn, agency_id, departures, stem)


def _long_gaps(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, long_gap_count FROM agg_route_headway_daily WHERE agency_id = %s ORDER BY date", (agency_id,)
        )
        return cur.fetchall()


def _two_timetables(pg_conn, agency_id, ch_client):
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    key_a, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"], "timetable_a")
    insert_updates(ch_client, agency_id, _a_long_gap("2026-06-01", "timetable_a"))
    analyze(agency_id, pg_conn, ch_client)
    key_b, _ = _replace_timetable(pg_conn, agency_id, ["08:00:00", "08:20:00", "08:40:00"], "timetable_b")
    insert_updates(ch_client, agency_id, _a_long_gap("2026-06-02", "timetable_b"))
    return key_a, key_b


def test_each_dates_long_gaps_follow_its_own_timetable(pg_conn, agency_id, ch_client, caplog, monkeypatch):
    """The reload rebuilds only the new date; a bump of the medians' SQL rebuilds
    every date, each still against its own timetable."""
    key_a, key_b = _two_timetables(pg_conn, agency_id, ch_client)
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert static_headways.route_medians(pg_conn, agency_id, [key_a, key_b]) == {
        (key_a, "R1"): 600.0,
        (key_b, "R1"): 1200.0,
    }
    assert "scope agg_route_headway_daily: 1 date(s)" in caplog.text
    assert _long_gaps(pg_conn, agency_id) == [(date(2026, 6, 1), 1), (date(2026, 6, 2), 0)]

    table = agg_registry.TABLES["agg_route_headway"]
    monkeypatch.setitem(agg_registry.TABLES, table.name, dataclasses.replace(table, version=table.version + 1))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert "scope agg_route_headway_daily: every date" in caplog.text
    assert _long_gaps(pg_conn, agency_id) == [(date(2026, 6, 1), 1), (date(2026, 6, 2), 0)]


def test_a_timetable_without_medians_measures_against_the_loaded_one(pg_conn, agency_id, ch_client, monkeypatch):
    key_a, _ = _two_timetables(pg_conn, agency_id, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM route_headway_by_content WHERE agency_id = %s AND content_hash = %s", (agency_id, key_a)
        )
    pg_conn.commit()
    # A full rebuild, so day 1 is measured again.
    table = agg_registry.TABLES["agg_route_headway_daily"]
    monkeypatch.setitem(agg_registry.TABLES, table.name, dataclasses.replace(table, version=table.version + 1))
    analyze(agency_id, pg_conn, ch_client)
    assert _long_gaps(pg_conn, agency_id) == [(date(2026, 6, 1), 0), (date(2026, 6, 2), 0)]


def test_a_content_is_read_at_its_latest_version_only(pg_conn, agency_id):
    """A route the newer version no longer has is gone, not served at the older one's median."""
    with pg_conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO route_headway_by_content "
            "(agency_id, content_hash, route_code, logic_version, scheduled_headway_median_sec) "
            "VALUES (%s, 'c', %s, %s, %s)",
            [(agency_id, "R1", 1, 600.0), (agency_id, "R2", 1, 900.0), (agency_id, "R1", 2, 660.0)],
        )
    pg_conn.commit()
    assert static_headways.route_medians(pg_conn, agency_id, ["c"]) == {("c", "R1"): 660.0}


def test_analyze_keeps_no_medians_for_an_agency_that_builds_no_daily_headways(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    analyze(agency_id, pg_conn, ch_client)  # the agency_id fixture leaves ingest_strategy NULL
    assert _rows(pg_conn, agency_id) == []


def _rows_seen_afresh(pg_conn, agency_id):
    pg_conn.rollback()
    return _rows(pg_conn, agency_id)


def test_the_backstops_medians_outlive_a_failed_run(pg_conn, agency_id, ch_client, monkeypatch):
    """They belong to the timetable, so the run's rollback leaves them, and its long
    transaction never holds them against a concurrent load recording the same one."""
    from pipeline import analyze as analyze_mod
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    key, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure after the plan")

    monkeypatch.setattr(analyze_mod, "_rebuild_ch_aggregates", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        analyze(agency_id, pg_conn, ch_client)
    assert [row[0] for row in _rows_seen_afresh(pg_conn, agency_id)] == [key]


def test_the_outgoing_timetables_medians_outlive_a_failed_load(tmp_path, pg_conn, agency_id, ch_client, monkeypatch):
    key, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure mid-load")

    monkeypatch.setattr("pipeline.static_loader.rebuild_stop_clusters", fail)
    incoming = build_static_zip(
        tmp_path,
        stops_rows=["s1,Stop 1,34.39,132.45"],
        routes_rows=["R1,1"],
        trips_rows=["T1,R1,,"],
        stop_times_rows=["T1,1,s1,09:00:00,09:00:00"],
        filename="timetable_b.zip",
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        load_static(incoming, agency_id, pg_conn, ch_client=ch_client)
    assert [row[0] for row in _rows_seen_afresh(pg_conn, agency_id)] == [key]
