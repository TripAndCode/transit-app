"""Each archived timetable keeps its per-route scheduled headway medians."""

import dataclasses

from pipeline import agg_registry, static_archive, static_headways
from pipeline.analyze import analyze
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
    key, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    analyze(agency_id, pg_conn, ch_client)
    assert static_headways.route_medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}
