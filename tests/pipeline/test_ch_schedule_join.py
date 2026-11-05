"""The stop tables and the stop visits join each row to the timetable its version name archived."""

import dataclasses
import logging
from datetime import date

import pytest

from pipeline import agg_registry, ch_aggregates, static_archive
from pipeline.analyze import analyze
from pipeline.clickhouse import UPDATE_COLUMNS, insert_updates
from pipeline.db import build_stop_visits_ch_sql
from pipeline.static_archive import ScheduleJoin
from pipeline.static_loader import load_static
from tests.fixtures.synthetic_gtfs import build_static_zip

AGENCY = 7
A, B = "a" * 32, "b" * 32


def _row(captured_at: str, trip: str, seq: int, delay: int | None, stem: str | None, route: str = "R1") -> tuple:
    """One `updates` row in insert_updates' order (agency_id excluded)."""
    values = dict.fromkeys(UPDATE_COLUMNS[1:])
    values.update(
        file_name=f"f/{captured_at}/{trip}/{seq}",
        captured_at=captured_at,
        trip_id=trip,
        stop_sequence=seq,
        dep_delay=delay,
        route_code=route,
        static_version_id=stem,
    )
    return tuple(values[column] for column in UPDATE_COLUMNS[1:])


def test_the_dedup_keeps_one_event_with_its_latest_polls_version(ch_client):
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "gtfs_static_20260501"),
            _row("2026-06-01T00:05:00Z", "T1", 1, 60, "gtfs_static_20260601"),
            _row("2026-06-01T00:00:00Z", "T2", 1, 10, None),
        ],
    )
    where, params = ch_aggregates.month_where(202606)
    rows = ch_client.query(
        f"SELECT trip_id, dep_delay, static_version_id FROM ({ch_aggregates._dedup_select(where)}) ORDER BY trip_id",
        parameters={"agency_id": AGENCY, **params},
    ).result_rows
    assert rows == [("T1", 60, "gtfs_static_20260601"), ("T2", 10, "")]


def test_the_raw_source_keeps_every_rows_version(ch_client):
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "gtfs_static_20260501"),
            _row("2026-06-01T00:05:00Z", "T1", 1, None, None),
        ],
    )
    where, params = ch_aggregates.month_where(202606)
    rows = ch_client.query(
        f"SELECT static_version_id FROM ({ch_aggregates._raw_select(where)}) ORDER BY static_version_id",
        parameters={"agency_id": AGENCY, **params},
    ).result_rows
    assert rows == [("",), ("gtfs_static_20260501",)]


def _archive(
    ch_client, content: str, rows: list[tuple[str, int, str]], times: tuple[str | None, str | None] = (None, None)
) -> None:
    """(trip_id, stop_sequence, stop_id) rows archived for AGENCY under *content*, each at *times*
    (arrival, departure)."""
    ch_client.insert(
        static_archive.ARCHIVE,
        [(AGENCY, content, trip, seq, stop, *times) for trip, seq, stop in rows],
        column_names=[
            "agency_id",
            "content_hash",
            "trip_id",
            "stop_sequence",
            "stop_id",
            "arrival_time",
            "departure_time",
        ],
    )


def _stops_by_date(ch_client, join: ScheduleJoin) -> set[tuple]:
    sql, params = ch_aggregates.select_sql("agg_stop_daily", 202606, join=join)
    rows = ch_client.query(f"SELECT date, stop_id FROM ({sql})", parameters={"agency_id": AGENCY, **params})
    return set(rows.result_rows)


def test_rows_join_the_timetable_their_version_archived(ch_client):
    _archive(ch_client, A, [("T1", 1, "SA")])
    _archive(ch_client, B, [("T1", 1, "SB")])
    insert_updates(
        ch_client,
        AGENCY,
        [_row("2026-06-01T00:00:00Z", "T1", 1, 30, "v_a"), _row("2026-06-02T00:00:00Z", "T1", 1, 30, "v_b")],
    )
    join = ScheduleJoin(["v_a", "v_b"], [A, B], B)
    assert _stops_by_date(ch_client, join) == {(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB")}


def test_unmapped_and_unstamped_rows_join_the_loaded_timetable(ch_client):
    _archive(ch_client, B, [("T1", 1, "SB")])
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, None),
            _row("2026-06-02T00:00:00Z", "T1", 1, 30, "gtfs_static_19990101"),
        ],
    )
    assert _stops_by_date(ch_client, ScheduleJoin([], [], B)) == {(date(2026, 6, 1), "SB"), (date(2026, 6, 2), "SB")}


def test_a_static_joined_build_without_a_join_is_refused(ch_client):
    with pytest.raises(ValueError, match="ScheduleJoin"):
        ch_aggregates.build(ch_client, AGENCY, {"agg_stop_daily": ch_aggregates.Rebuild([202606], None)})


def test_a_build_reports_the_rows_that_joined_the_loaded_timetable(ch_client, caplog):
    _archive(ch_client, A, [("T1", 1, "SA")])
    _archive(ch_client, B, [("T1", 1, "SB")])
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "v_a"),
            _row("2026-06-02T00:00:00Z", "T1", 1, 30, None),
            _row("2026-06-03T00:00:00Z", "T1", 1, 30, "gtfs_static_19990101"),
        ],
    )
    join = ScheduleJoin(["v_a"], [A], B)
    with caplog.at_level(logging.INFO, logger="pipeline.ch_aggregates"):
        ch_aggregates.build(ch_client, AGENCY, {"agg_stop_daily": ch_aggregates.Rebuild([202606], None)}, join=join)
    ch_aggregates.swap(ch_client, "agg_stop_daily", AGENCY, [202606])
    stops = ch_client.query(
        "SELECT date, stop_id FROM agg_stop_daily WHERE agency_id = {a:Int32} ORDER BY date", parameters={"a": AGENCY}
    ).result_rows
    assert stops == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB"), (date(2026, 6, 3), "SB")]
    assert "2 rows of versions not archived joined the loaded timetable" in caplog.text


def test_a_build_of_some_dates_reads_and_reports_only_their_rows(ch_client, caplog):
    _archive(ch_client, B, [("T1", 1, "SB")])
    insert_updates(
        ch_client,
        AGENCY,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, "gtfs_static_19990101"),
            _row("2026-06-02T00:00:00Z", "T1", 1, 30, "gtfs_static_19990102"),
        ],
    )
    rebuild = {"agg_stop_daily": ch_aggregates.Rebuild([202606], [date(2026, 6, 2)])}
    with caplog.at_level(logging.INFO, logger="pipeline.ch_aggregates"):
        ch_aggregates.build(ch_client, AGENCY, rebuild, join=ScheduleJoin([], [], B))
    assert "1 rows of versions not archived joined the loaded timetable (gtfs_static_19990102: 1)" in caplog.text


def _timetable(tmp_path, name: str, stop: str) -> str:
    """A timetable zip, version-named *name*, whose trip T1 makes its first stop at *stop*."""
    return build_static_zip(
        tmp_path,
        stops_rows=["SA,Stop A,34.39,132.45", "SB,Stop B,34.40,132.46"],
        routes_rows=["R1,1"],
        trips_rows=["T1,R1,,"],
        stop_times_rows=[f"T1,1,{stop},08:00:00,08:00:00"],
        filename=f"{name}.zip",
    )


def _stop_days(pg_conn, agency_id: int) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, stop_id FROM agg_stop_daily WHERE agency_id = %s ORDER BY date, stop_id", (agency_id,)
        )
        return cur.fetchall()


def test_a_reload_rebuilds_each_date_against_its_own_timetable(
    tmp_path, pg_conn, agency_id, ch_client, monkeypatch, caplog
):
    # The agency_id fixture's ingest stamps no version, so its reload still
    # rebuilds every date of the archive-joined tables.
    load_static(_timetable(tmp_path, "timetable_a", "SA"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, [_row("2026-06-01T00:00:00Z", "T1", 1, 30, "timetable_a")])
    analyze(agency_id, pg_conn, ch_client)
    load_static(_timetable(tmp_path, "timetable_b", "SB"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, [_row("2026-06-02T00:00:00Z", "T1", 1, 30, "timetable_b")])
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    # The reload rebuilt 2026-06-01 too, and it joined the timetable its rows were ingested against.
    assert "scope agg_stop_daily: every date" in caplog.text
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB")]

    table = agg_registry.TABLES["agg_stop_daily"]
    monkeypatch.setitem(agg_registry.TABLES, "agg_stop_daily", dataclasses.replace(table, version=table.version + 1))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert "scope agg_stop_daily: every date" in caplog.text
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB")]


def test_a_stamping_agencys_reload_rebuilds_only_the_changed_date(tmp_path, pg_conn, agency_id, ch_client, caplog):
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    load_static(_timetable(tmp_path, "timetable_a", "SA"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, [_row("2026-06-01T00:00:00Z", "T1", 1, 30, "timetable_a")])
    analyze(agency_id, pg_conn, ch_client)
    load_static(_timetable(tmp_path, "timetable_b", "SB"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, [_row("2026-06-02T00:00:00Z", "T1", 1, 30, "timetable_b")])
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert "scope agg_route_headway: every date" in caplog.text
    assert "scope agg_stop_daily: 1 date(s)" in caplog.text
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SB")]


def test_rows_with_no_or_an_unknown_version_join_the_loaded_timetable(tmp_path, pg_conn, agency_id, ch_client, caplog):
    load_static(_timetable(tmp_path, "timetable_a", "SA"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(
        ch_client,
        agency_id,
        [
            _row("2026-06-01T00:00:00Z", "T1", 1, 30, None),
            _row("2026-06-02T00:00:00Z", "T1", 1, 30, "gtfs_static_19990101"),
        ],
    )
    with caplog.at_level(logging.INFO, logger="pipeline.ch_aggregates"):
        analyze(agency_id, pg_conn, ch_client)
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SA")]
    assert "2 rows of versions not archived joined the loaded timetable" in caplog.text


def test_rows_of_an_archived_version_join_it_with_no_timetable_loaded(tmp_path, pg_conn, agency_id, ch_client):
    load_static(_timetable(tmp_path, "timetable_a", "SA"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, [_row("2026-06-01T00:00:00Z", "T1", 1, 30, "timetable_a")])
    analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        for table in ("static_stop_times", "static_trips", "static_routes", "static_stops"):
            cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    analyze(agency_id, pg_conn, ch_client)
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA")]


def _visit_poll(
    captured_at: str, seq: int, *, dep: int | None, arr: int | None, stem: str | None, scheduled_sec: int | None = None
) -> tuple:
    """One poll of trip T1's stop *seq*, carrying a departure or an arrival, in insert_updates' order."""
    values = dict.fromkeys(UPDATE_COLUMNS[1:])
    values.update(
        file_name=f"v/{captured_at}/{seq}/{dep}/{arr}",
        captured_at=captured_at,
        trip_id="T1",
        route_code="R1",
        stop_sequence=seq,
        dep_delay=dep,
        arr_delay=arr,
        scheduled_sec=scheduled_sec,
        static_version_id=stem,
    )
    return tuple(values[column] for column in UPDATE_COLUMNS[1:])


def _visit(day: str, seq: int, arrival_stem: str | None, departure_stem: str | None) -> list[tuple]:
    """A visit's arrival poll, then a later departure poll, on *day*."""
    return [
        _visit_poll(f"{day}T00:00:00Z", seq, dep=None, arr=50, stem=arrival_stem),
        _visit_poll(f"{day}T00:01:00Z", seq, dep=20, arr=None, stem=departure_stem, scheduled_sec=36420),
    ]


def _visits(ch_client, join: ScheduleJoin, columns: str = "sched_arrival_time, sched_departure_time") -> list[tuple]:
    sql = build_stop_visits_ch_sql(include_scheduled_sec="scheduled_sec" in columns, join_schedule=True)
    rows = ch_client.query(
        f"SELECT date, stop_sequence, {columns} FROM ({sql}) ORDER BY date, stop_sequence",
        parameters={"agency_id": AGENCY, **join.params()},
    )
    return rows.result_rows


def _two_timetables(ch_client) -> None:
    _archive(ch_client, A, [("T1", 2, "S2")], ("10:05:00", "10:06:00"))
    _archive(ch_client, B, [("T1", 2, "S2")], ("10:05:00", "10:07:00"))


def test_a_visit_takes_its_scheduled_times_from_its_own_timetable(ch_client):
    _two_timetables(ch_client)
    insert_updates(ch_client, AGENCY, [*_visit("2026-06-01", 2, "v_a", "v_a"), *_visit("2026-06-02", 2, "v_b", "v_b")])
    assert _visits(ch_client, ScheduleJoin(["v_a", "v_b"], [A, B], B)) == [
        (date(2026, 6, 1), 2, "10:05:00", "10:06:00"),
        (date(2026, 6, 2), 2, "10:05:00", "10:07:00"),
    ]


def test_a_visit_polled_under_two_versions_takes_its_departures(ch_client):
    _two_timetables(ch_client)
    insert_updates(ch_client, AGENCY, _visit("2026-06-01", 2, "v_a", "v_b"))
    assert _visits(ch_client, ScheduleJoin(["v_a", "v_b"], [A, B], A)) == [
        (date(2026, 6, 1), 2, "10:05:00", "10:07:00")
    ]


def test_an_unversioned_visit_takes_the_loaded_timetables_times(ch_client):
    _two_timetables(ch_client)
    insert_updates(ch_client, AGENCY, _visit("2026-06-01", 2, None, None))
    assert _visits(ch_client, ScheduleJoin([], [], A)) == [(date(2026, 6, 1), 2, "10:05:00", "10:06:00")]


def test_a_visit_with_no_archived_stop_time_keeps_its_row(ch_client):
    _two_timetables(ch_client)
    insert_updates(ch_client, AGENCY, _visit("2026-06-01", 9, "v_a", "v_a"))
    assert _visits(ch_client, ScheduleJoin(["v_a"], [A], A)) == [(date(2026, 6, 1), 9, None, None)]


def test_the_join_composes_with_the_scheduled_seconds(ch_client):
    _two_timetables(ch_client)
    insert_updates(ch_client, AGENCY, _visit("2026-06-01", 2, "v_b", "v_b"))
    rows = _visits(ch_client, ScheduleJoin(["v_b"], [B], A), "scheduled_sec, sched_departure_time")
    assert rows == [(date(2026, 6, 1), 2, 36420, "10:07:00")]


def test_the_visits_without_the_join_keep_their_columns():
    sql = build_stop_visits_ch_sql(include_scheduled_sec=True)
    assert "sched_arrival_time" not in sql
    assert "join_" not in sql


def _dwell_timetable(tmp_path, name: str, stop_2_departure: str) -> str:
    """Trip T1 leaves stop 1 at 10:00 and calls at stop 2 from 10:05 until *stop_2_departure*."""
    return build_static_zip(
        tmp_path,
        stops_rows=["S1,Stop 1,34.39,132.45", "S2,Stop 2,34.40,132.46"],
        routes_rows=["R1,1"],
        trips_rows=["T1,R1,,"],
        stop_times_rows=["T1,1,S1,10:00:00,10:00:00", f"T1,2,S2,10:05:00,{stop_2_departure}"],
        filename=f"{name}.zip",
    )


def _dwell_polls(day: str, stem: str | None) -> list[tuple]:
    """Stop 1's departure, then stop 2's arrival (50 s late) and later departure (20 s late)."""
    return [_visit_poll(f"{day}T00:00:00Z", 1, dep=30, arr=None, stem=stem), *_visit(day, 2, stem, stem)]


def _dwell_sums(pg_conn, agency_id: int) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, dwell_sum_sec FROM agg_route_daily_dwell_run "
            "WHERE agency_id = %s AND route_code = 'R1' ORDER BY date",
            (agency_id,),
        )
        return cur.fetchall()


def test_dwell_follows_each_dates_timetable_across_a_reload(
    tmp_path, pg_conn, agency_id, ch_client, caplog, monkeypatch
):
    """Stop 2's dwell is (scheduled departure + 20) - (10:05 + 50): 30 s under A's 10:06, 90 s under B's 10:07.
    The reload rebuilds only the new date; a full rebuild keeps each date on its own timetable."""
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    load_static(_dwell_timetable(tmp_path, "dwell_a", "10:06:00"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, _dwell_polls("2026-06-01", "dwell_a"))
    analyze(agency_id, pg_conn, ch_client)
    load_static(_dwell_timetable(tmp_path, "dwell_b", "10:07:00"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, _dwell_polls("2026-06-02", "dwell_b"))
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert "scope agg_route_daily_dwell_run: 1 date(s)" in caplog.text
    assert _dwell_sums(pg_conn, agency_id) == [(date(2026, 6, 1), 30), (date(2026, 6, 2), 90)]

    table = agg_registry.TABLES["agg_route_daily_dwell_run"]
    monkeypatch.setitem(agg_registry.TABLES, table.name, dataclasses.replace(table, version=table.version + 1))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert "scope agg_route_daily_dwell_run: every date" in caplog.text
    assert _dwell_sums(pg_conn, agency_id) == [(date(2026, 6, 1), 30), (date(2026, 6, 2), 90)]


def test_dwell_of_an_unversioned_visit_uses_the_loaded_timetable(tmp_path, pg_conn, agency_id, ch_client):
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    load_static(_dwell_timetable(tmp_path, "dwell_b", "10:07:00"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, _dwell_polls("2026-06-01", None))
    analyze(agency_id, pg_conn, ch_client)
    assert _dwell_sums(pg_conn, agency_id) == [(date(2026, 6, 1), 90)]


@pytest.mark.parametrize("stem", [None, "gtfs_static_19990101"])
def test_a_reload_rebuilds_the_dates_of_rows_of_no_archived_version(
    tmp_path, pg_conn, agency_id, ch_client, caplog, stem
):
    """A row ingested with no version (its trip was not in the timetable loaded
    then), or with one whose timetable the archive never kept, joins the loaded
    timetable, so a new one rebuilds its date."""
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    load_static(_timetable(tmp_path, "timetable_a", "SA"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(
        ch_client,
        agency_id,
        [_row("2026-06-01T00:00:00Z", "T1", 1, 30, stem), _row("2026-06-02T00:00:00Z", "T1", 1, 30, "timetable_a")],
    )
    analyze(agency_id, pg_conn, ch_client)
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA"), (date(2026, 6, 2), "SA")]
    load_static(_timetable(tmp_path, "timetable_b", "SB"), agency_id, pg_conn, ch_client=ch_client)
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert "scope agg_stop_daily: 1 date(s)" in caplog.text
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SB"), (date(2026, 6, 2), "SA")]


def test_a_full_rebuild_reads_no_dates_following_the_loaded_timetable(
    tmp_path, pg_conn, agency_id, ch_client, monkeypatch
):
    """With every archive-joined table rebuilt in full, those dates add nothing,
    so their scan of the agency's whole history is skipped."""
    from pipeline import analyze as analyze_module
    from tests.pipeline.test_analyze import _set_ingest_strategy

    def _unexpected(*_args):
        raise AssertionError("read the dates following the loaded timetable")

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    load_static(_timetable(tmp_path, "timetable_a", "SA"), agency_id, pg_conn, ch_client=ch_client)
    insert_updates(ch_client, agency_id, [_row("2026-06-01T00:00:00Z", "T1", 1, 30, None)])
    monkeypatch.setattr(analyze_module, "_dates_the_move_reaches", _unexpected)
    analyze(agency_id, pg_conn, ch_client)
    assert _stop_days(pg_conn, agency_id) == [(date(2026, 6, 1), "SA")]


def _changed_trips(ch_client, before: str, after: str) -> list[str]:
    return [
        row[0]
        for row in ch_client.query(
            f"SELECT trip_id FROM {static_archive.changed_trips_sql()} ORDER BY trip_id",
            parameters={"agency_id": AGENCY, "before": before, "after": after},
        ).result_rows
    ]


def test_a_trip_changes_when_its_stop_times_differ_or_only_one_timetable_has_it(ch_client):
    at_8 = ("08:00:00", "08:00:00")
    _archive(ch_client, A, [("T1", 1, "SA"), ("T1", 2, "SB"), ("T2", 1, "SA"), ("T3", 1, "SA")], at_8)
    _archive(ch_client, B, [("T1", 2, "SB"), ("T1", 1, "SA"), ("T4", 1, "SA")], at_8)
    _archive(ch_client, B, [("T2", 1, "SA")], ("08:01:00", "08:01:00"))
    assert _changed_trips(ch_client, A, B) == ["T2", "T3", "T4"]


def test_against_a_timetable_with_no_stop_times_every_trip_changes(ch_client):
    _archive(ch_client, A, [("T1", 1, "SA"), ("T2", 1, "SA")])
    assert _changed_trips(ch_client, A, "") == ["T1", "T2"]


def test_is_archived_reads_the_agencys_own_contents(ch_client):
    _archive(ch_client, A, [("T1", 1, "SA")])
    assert static_archive.is_archived(ch_client, AGENCY, A)
    assert not static_archive.is_archived(ch_client, AGENCY, B)
    assert not static_archive.is_archived(ch_client, AGENCY + 1, A)
