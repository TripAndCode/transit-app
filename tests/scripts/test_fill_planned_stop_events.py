"""scripts/fill_planned_stop_events.py fills each saved day's unknown stop events
as the capture would have saved them, from the archived timetable in force that day.

April 2026 starts on a Wednesday: the 6th, 13th and 20th are Mondays, the 11th a
Saturday.
"""

import zipfile
from datetime import date, timedelta

import pytest

from pipeline import feed_quality, planned_trips, static_archive
from pipeline.planned_trips import capture_planned_trips
from pipeline.static_loader import load_static
from scripts import fill_planned_stop_events as fill
from tests.fixtures.clock import freeze_now, jst_midday

_A = "gtfs_static_20260401"
_B = "gtfs_static_20260415"


def _zip(folder, name: str, calendar: list[str], calendar_dates: list[str], trips: dict[str, tuple[int, int]]) -> str:
    """A timetable zip *name*: *trips* maps each service to (trips, stop times per trip)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.zip"
    trip_rows, stop_rows = [], []
    for service, (count, stops) in trips.items():
        for i in range(count):
            trip_id = f"{name[-4:]}-{service}-{i}"
            trip_rows.append(f"{trip_id},R1,,,{service}")
            stop_rows += [f"{trip_id},{seq},S{seq},08:0{seq}:00,08:0{seq}:00" for seq in range(1, stops + 1)]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n" + "\n".join(f"S{i},S{i},34,132" for i in range(1, 9))
        )
        zf.writestr("routes.txt", "route_id,route_short_name\nR1,1")
        zf.writestr("trips.txt", "trip_id,route_id,trip_headsign,shape_id,service_id\n" + "\n".join(trip_rows))
        zf.writestr(
            "stop_times.txt", "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n" + "\n".join(stop_rows)
        )
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            + "\n".join(calendar),
        )
        zf.writestr("calendar_dates.txt", "service_id,date,exception_type\n" + "\n".join(calendar_dates))
    return str(path)


def _timetable_a(folder) -> str:
    """Weekdays run WD (3 trips of 4 stops) through April but the 13th; the
    11th adds EX (1 trip of 5 stops); Saturdays run SA (1 trip of 2 stops)."""
    return _zip(
        folder,
        _A,
        ["WD,1,1,1,1,1,0,0,20260401,20260430", "SA,0,0,0,0,0,1,0,20260401,20260430"],
        ["WD,20260413,2", "EX,20260411,1", "EX,2026041,1"],
        {"WD": (3, 4), "SA": (1, 2), "EX": (1, 5)},
    )


def _timetable_b(folder) -> str:
    """Every day from the 20th runs ALL: 2 trips of 3 stops."""
    return _zip(folder, _B, ["ALL,1,1,1,1,1,1,1,20260420,20261014"], [], {"ALL": (2, 3)})


def _saved(pg_conn, agency_id) -> dict[date, int | None]:
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT service_date, planned_stop_events FROM planned_trips_daily WHERE agency_id = %s", (agency_id,)
        )
        rows = dict(cur.fetchall())
    pg_conn.rollback()
    return rows


def _forget_stop_events(pg_conn, agency_id) -> None:
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE planned_trips_daily SET planned_stop_events = NULL WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()


def _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch) -> dict[date, int]:
    """Timetable A loaded and captured through the 19th, then B loaded, which
    archives A, and captured through the 24th; every saved day's stop events
    then forgotten, as on a day saved before they were. The stop events the
    captures saved, by day."""
    freeze_now(monkeypatch, planned_trips, jst_midday(date(2026, 4, 20)))
    load_static(_timetable_a(tmp_path / "zips"), agency_id, pg_conn, ch_client=ch_client)
    capture_planned_trips(pg_conn, agency_id)
    load_static(_timetable_b(tmp_path / "zips"), agency_id, pg_conn, ch_client=ch_client)
    capture_planned_trips(pg_conn, agency_id, today=date(2026, 4, 25))
    captured = _saved(pg_conn, agency_id)
    _forget_stop_events(pg_conn, agency_id)
    return captured


def test_the_captures_saved_what_the_timetables_plan(tmp_path, pg_conn, agency_id, ch_client, monkeypatch):
    captured = _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    assert captured[date(2026, 4, 10)] == 12
    assert captured[date(2026, 4, 11)] == 2 + 5
    assert captured[date(2026, 4, 13)] == 0
    assert captured[date(2026, 4, 12)] == 0
    assert captured[date(2026, 4, 21)] == 6
    assert set(captured) == {date(2026, 4, 1) + timedelta(days=i) for i in range(24)}


def test_apply_fills_each_day_from_its_archived_timetable_as_the_capture_saved_it(
    tmp_path, pg_conn, agency_id, ch_client, monkeypatch, capsys
):
    """A is no longer loaded: its days are planned from its archived calendar
    and stop times. B is loaded but not yet archived, so A, the only version
    the archive knows, is in force on B's days too, and their saved trips are
    not A's: they wait until B is archived, as analyze does on its next run."""
    captured = _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    a_days = {day for day in captured if day < date(2026, 4, 20)}

    assert fill.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert f"agency {agency_id}: 24 day(s) without stop events: 19 to fill" in out
    assert "5 saved with other planned trips than the timetable in force plans" in out
    assert _saved(pg_conn, agency_id) == {day: captured[day] if day in a_days else None for day in captured}

    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert fill.main(["--apply"]) == 0
    assert f"agency {agency_id}: 5 day(s) without stop events: 5 to fill" in capsys.readouterr().out
    assert _saved(pg_conn, agency_id) == captured


def test_a_known_count_is_never_overwritten(tmp_path, pg_conn, agency_id, ch_client, monkeypatch):
    captured = _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE planned_trips_daily SET planned_stop_events = 999 WHERE agency_id = %s AND service_date = %s",
            (agency_id, date(2026, 4, 10)),
        )
    pg_conn.commit()
    assert fill.main(["--apply"]) == 0
    saved = _saved(pg_conn, agency_id)
    assert saved[date(2026, 4, 10)] == 999
    assert saved[date(2026, 4, 9)] == captured[date(2026, 4, 9)]


def test_the_dry_run_reports_and_writes_nothing(tmp_path, pg_conn, agency_id, ch_client, monkeypatch, capsys):
    captured = _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    assert fill.main([]) == 0
    out = capsys.readouterr().out
    assert f"agency {agency_id}: 24 day(s) without stop events: 19 to fill" in out
    assert "dry run: 19 day(s) would be filled; --apply fills them" in out
    assert _saved(pg_conn, agency_id) == dict.fromkeys(captured)


def test_a_day_whose_timetable_has_no_archived_calendar_stays_unknown_and_is_reported(
    tmp_path, pg_conn, agency_id, ch_client, monkeypatch, capsys
):
    captured = _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    ch_client.command(f"TRUNCATE TABLE {static_archive.CALENDAR}")
    assert fill.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert "24 whose timetable's calendar is not archived" in out
    assert _A in out
    assert "nothing to fill" in out
    assert _saved(pg_conn, agency_id) == dict.fromkeys(captured)


def test_a_day_no_archived_timetable_is_in_force_on_stays_unknown_and_is_reported(
    pg_conn, agency_id, ch_client, capsys
):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips) VALUES (%s, '2026-04-02', 3)",
            (agency_id,),
        )
    pg_conn.commit()
    assert fill.main(["--apply"]) == 0
    assert "1 with no archived timetable in force" in capsys.readouterr().out
    assert _saved(pg_conn, agency_id) == {date(2026, 4, 2): None}


def test_apply_clears_the_scorecards_state_so_the_next_analyze_rebuilds_it(
    tmp_path, pg_conn, agency_id, ch_client, monkeypatch
):
    _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_table_state (agency_id, table_name, fingerprint) VALUES (%s, %s, 'x'), (%s, 'other', 'y')",
            (agency_id, feed_quality.TABLE, agency_id),
        )
    pg_conn.commit()
    assert fill.main(["--apply"]) == 0
    with pg_conn.cursor() as cur:
        cur.execute("SELECT table_name FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        assert cur.fetchall() == [("other",)]
    pg_conn.rollback()


def test_a_fill_that_fails_still_leaves_the_scorecards_state_cleared(
    tmp_path, pg_conn, agency_id, ch_client, monkeypatch
):
    """Cleared before the write too: once a day is filled no rerun finds it
    pending, so a run stopped after the write could not clear it later."""
    _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_table_state (agency_id, table_name, fingerprint) VALUES (%s, %s, 'x')",
            (agency_id, feed_quality.TABLE),
        )
    pg_conn.commit()

    def stop(conn, agency, fills):
        with pg_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM agg_table_state WHERE agency_id = %s", (agency,))
            assert cur.fetchone()[0] == 0
        pg_conn.rollback()
        raise RuntimeError("stopped")

    monkeypatch.setattr(fill, "fill", stop)
    with pytest.raises(RuntimeError, match="stopped"):
        fill.main(["--apply"])


def test_a_calendar_gone_after_planning_leaves_its_days_unknown(
    tmp_path, pg_conn, agency_id, ch_client, monkeypatch, capsys
):
    captured = _two_timetables(tmp_path, pg_conn, agency_id, ch_client, monkeypatch)
    monkeypatch.setattr(static_archive, "archived_calendar", lambda *args: None)
    assert fill.main(["--apply"]) == 0
    assert "24 whose timetable's calendar is not archived" in capsys.readouterr().out
    assert _saved(pg_conn, agency_id) == dict.fromkeys(captured)
