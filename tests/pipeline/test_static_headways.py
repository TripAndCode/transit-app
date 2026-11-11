"""Each archived timetable keeps its per-route scheduled headways: their median, sum and sum of squares."""

import dataclasses
import logging
from datetime import date

import pytest

from pipeline import agg_registry, static_archive, static_headways
from pipeline.analyze import analyze
from pipeline.clickhouse import UPDATE_COLUMNS, insert_updates
from pipeline.headways import ScheduledHeadways
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


def _medians(pg_conn, agency_id, contents):
    return {key: kept.median_sec for key, kept in static_headways.route_headways(pg_conn, agency_id, contents).items()}


def _scheduled(pg_conn, agency_id):
    """Each daily row's scheduled figures, read from the timetable that judged its date."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, scheduled_headway_median_sec, is_high_frequency, scheduled_headway_sum_sec, "
            "scheduled_headway_sumsq_sec2 FROM agg_route_headway_daily WHERE agency_id = %s ORDER BY date",
            (agency_id,),
        )
        return cur.fetchall()


def test_recording_keeps_the_loaded_timetables_scheduled_sums(pg_conn, agency_id):
    """The scheduled wait of days pooled under several timetables needs each one's sums, not its median."""
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    static_headways.record_route_headways(pg_conn, agency_id, key, parts)
    assert static_headways.route_headways(pg_conn, agency_id, [key]) == {
        (key, "R1"): ScheduledHeadways(600.0, 1200.0, 720000.0)
    }


def test_a_content_kept_before_its_sums_were_reads_with_none(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO route_headway_by_content "
            "(agency_id, content_hash, route_code, logic_version, scheduled_headway_median_sec) "
            "VALUES (%s, 'old', 'R1', 1, 600.0)",
            (agency_id,),
        )
    pg_conn.commit()
    assert static_headways.route_headways(pg_conn, agency_id, ["old"]) == {
        ("old", "R1"): ScheduledHeadways(600.0, None, None)
    }


def test_recording_keeps_the_loaded_timetables_medians(pg_conn, agency_id):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 1
    assert _medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


def test_the_kept_median_of_an_even_count_of_gaps_is_the_mean_of_the_middle_two(pg_conn, agency_id):
    """The long-gap threshold's median is the actual side's: gaps of 480 and 1200 s give 840 s, not 480 s."""
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:08:00", "08:28:00"])
    static_headways.record_route_headways(pg_conn, agency_id, key, parts)
    assert _medians(pg_conn, agency_id, [key]) == {(key, "R1"): 840.0}


def test_recording_twice_adds_nothing(pg_conn, agency_id):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    static_headways.record_route_headways(pg_conn, agency_id, key, parts)
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 0
    assert len(_rows(pg_conn, agency_id)) == 1


def test_a_route_with_no_median_still_gets_a_row(pg_conn, agency_id):
    key, parts = _seed(pg_conn, agency_id, ["08:00:00"])
    assert static_headways.record_route_headways(pg_conn, agency_id, key, parts) == 1
    assert _medians(pg_conn, agency_id, [key]) == {(key, "R1"): None}
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
    assert _medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


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
    assert _medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


def test_analyze_records_the_loaded_timetables_medians(pg_conn, agency_id, ch_client):
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    key, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"])
    analyze(agency_id, pg_conn, ch_client)
    assert _medians(pg_conn, agency_id, [key]) == {(key, "R1"): 600.0}


def _departure(captured_at: str, trip: str, scheduled_sec: int, stem: str | None, route: str = "R1") -> tuple:
    """One departure poll at stop s1 of *route*, on time, in insert_updates' order."""
    values = dict.fromkeys(UPDATE_COLUMNS[1:])
    values.update(
        file_name=f"h/{captured_at}/{trip}/{route}",
        captured_at=captured_at,
        trip_id=trip,
        route_code=route,
        stop_sequence=1,
        stop_id="s1",
        dep_delay=0,
        scheduled_sec=scheduled_sec,
        static_version_id=stem,
    )
    return tuple(values[column] for column in UPDATE_COLUMNS[1:])


def _a_long_gap(day: str, stem: str | None, route: str = "R1") -> list[tuple]:
    """Two departures 1500 s apart: long against a 600 s median (1.75 x 600 = 1050), not a 1200 s one."""
    return [
        _departure(f"{day}T00:00:00Z", "T0", 28800, stem, route),
        _departure(f"{day}T00:30:00Z", "T1", 30300, stem, route),
    ]


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
    assert _medians(pg_conn, agency_id, [key_a, key_b]) == {
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


def test_each_dates_high_frequency_test_and_scheduled_sums_follow_its_own_timetable(pg_conn, agency_id, ch_client):
    """6/1 ran timetable A (every 10 min) and 6/2 timetable B (every 20 min),
    loaded now: 6/1 is high-frequency by A, not by the loaded B."""
    _two_timetables(pg_conn, agency_id, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    assert _scheduled(pg_conn, agency_id) == [
        (date(2026, 6, 1), 600.0, True, 1200.0, 720000.0),
        (date(2026, 6, 2), 1200.0, False, 2400.0, 2880000.0),
    ]


def test_a_timetable_without_medians_measures_against_the_loaded_one(pg_conn, agency_id, ch_client, monkeypatch):
    """Its long gaps take the loaded timetable's median, but its high-frequency
    test and scheduled wait take nothing: the day is left unjudged."""
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
    assert _scheduled(pg_conn, agency_id)[0] == (date(2026, 6, 1), None, None, None, None)


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
    assert _medians(pg_conn, agency_id, ["c"]) == {("c", "R1"): 660.0}


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


def _following_on_two_routes(pg_conn, agency_id, ch_client):
    """Rows following the loaded timetable: on R1 (median 600 s under A) on 6/3,
    on R9 (no median kept anywhere) on 6/4. Returns A's key."""
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    key_a, _ = _seed(pg_conn, agency_id, ["08:00:00", "08:10:00", "08:20:00"], "timetable_a")
    insert_updates(ch_client, agency_id, [*_a_long_gap("2026-06-03", None), *_a_long_gap("2026-06-04", None, "R9")])
    analyze(agency_id, pg_conn, ch_client)
    assert (date(2026, 6, 3), 1) in _long_gaps(pg_conn, agency_id)
    return key_a


def _reload_to_a_20_minute_timetable(pg_conn, agency_id, ch_client, caplog):
    _replace_timetable(pg_conn, agency_id, ["08:00:00", "08:20:00", "08:40:00"], "timetable_b")
    insert_updates(ch_client, agency_id, _a_long_gap("2026-06-02", "timetable_b"))
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)


def test_a_reload_rebuilds_the_following_dates_of_routes_whose_median_changed(pg_conn, agency_id, ch_client, caplog):
    _following_on_two_routes(pg_conn, agency_id, ch_client)
    _reload_to_a_20_minute_timetable(pg_conn, agency_id, ch_client, caplog)
    # The new 6/2 and R1's 6/3; R9's 6/4 reads no median that changed.
    assert "scope agg_route_headway_daily: 2 date(s)" in caplog.text
    assert (date(2026, 6, 3), 0) in _long_gaps(pg_conn, agency_id)


def test_a_reload_from_a_timetable_without_medians_rebuilds_every_following_date(pg_conn, agency_id, ch_client, caplog):
    key_a = _following_on_two_routes(pg_conn, agency_id, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM route_headway_by_content WHERE agency_id = %s AND content_hash = %s", (agency_id, key_a)
        )
    pg_conn.commit()
    _reload_to_a_20_minute_timetable(pg_conn, agency_id, ch_client, caplog)
    assert "keeps no medians" in caplog.text
    assert "scope agg_route_headway_daily: 3 date(s)" in caplog.text
