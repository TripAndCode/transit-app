"""Each service day's planned trips are saved while its timetable is loaded.

The capture writes the days up to today that the loaded timetable covers and
that have no saved count yet. A timetable reload captures first, for the days
before the incoming timetable starts, so those keep the outgoing counts after
it is replaced, and analyze captures on every run. Neither lets a failed
capture stop it.

April 2026 starts on a Wednesday: the 6th and 13th are Mondays, the 11th a
Saturday.
"""

import zipfile
from datetime import date, timedelta

import pytest

from pipeline import planned_trips
from pipeline.analyze import analyze
from pipeline.planned_trips import capture_planned_trips
from pipeline.static_loader import load_static
from tests.fixtures.clock import freeze_now, jst_midday

_FAILING_SQL = "SELECT 1 / 0"


def _weekly(pg_conn, agency_id: int, service_id: str, days: str, start: date, end: date) -> None:
    """One ``calendar.txt`` row; ``days`` is its Monday-first ``"1111100"`` flags."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
            "saturday, sunday, start_date, end_date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (agency_id, service_id, *(flag == "1" for flag in days), start, end),
        )
    pg_conn.commit()


def _addition(pg_conn, agency_id: int, service_id: str, gtfs_date: str) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES (%s, %s, %s, 1)",
            (agency_id, service_id, gtfs_date),
        )
    pg_conn.commit()


def _trips(pg_conn, agency_id: int, service_id: str, count: int) -> None:
    with pg_conn.cursor() as cur:
        for i in range(count):
            cur.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, 'R1', %s)",
                (agency_id, f"{service_id}-{i}", service_id),
            )
    pg_conn.commit()


def _saved(pg_conn, agency_id: int) -> dict[date, int]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT service_date, planned_trips FROM planned_trips_daily WHERE agency_id = %s ORDER BY 1",
            (agency_id,),
        )
        return dict(cur.fetchall())


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _timetable_zip(tmp_path, name: str, start: str, end: str, trips: int) -> str:
    """A zip with one every-day service running ``trips`` trips from ``start`` to ``end``."""
    path = tmp_path / f"{name}_static.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            f"ALL,1,1,1,1,1,1,1,{start},{end}\n",
        )
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,trip_headsign,shape_id,service_id\n"
            + "\n".join(f"{name}-{i},R1,,,ALL" for i in range(trips)),
        )
    return str(path)


def test_capture_saves_each_covered_day_up_to_today(pg_conn, agency_id):
    _weekly(pg_conn, agency_id, "WD", "1111100", date(2026, 4, 6), date(2026, 4, 30))
    _trips(pg_conn, agency_id, "WD", 3)
    _addition(pg_conn, agency_id, "EX", "20260402")
    _addition(pg_conn, agency_id, "EX", "20260420")  # after today
    _trips(pg_conn, agency_id, "EX", 2)

    inserted = capture_planned_trips(pg_conn, agency_id, today=date(2026, 4, 15))
    pg_conn.commit()

    weekend = {date(2026, 4, 11), date(2026, 4, 12)}
    expected = {date(2026, 4, 2): 2} | {d: 0 if d in weekend else 3 for d in _days(date(2026, 4, 6), date(2026, 4, 15))}
    # The weekend is covered with nothing running: saved as 0, not left out,
    # so a reload cannot turn it into a day no plan covers.
    assert _saved(pg_conn, agency_id) == expected
    assert inserted == len(expected)


def test_capture_never_overwrites_a_saved_day(pg_conn, agency_id):
    _weekly(pg_conn, agency_id, "ALL", "1111111", date(2026, 4, 1), date(2026, 4, 30))
    _trips(pg_conn, agency_id, "ALL", 3)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips, recorded_at) "
            "VALUES (%s, '2026-04-14', 99, '2026-04-14T12:00:00+09:00')",
            (agency_id,),
        )
    pg_conn.commit()

    capture_planned_trips(pg_conn, agency_id, today=date(2026, 4, 15))
    pg_conn.commit()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT planned_trips, recorded_at::date FROM planned_trips_daily "
            "WHERE agency_id = %s AND service_date = '2026-04-14'",
            (agency_id,),
        )
        assert cur.fetchone() == (99, date(2026, 4, 14))
    assert _saved(pg_conn, agency_id)[date(2026, 4, 15)] == 3
    assert capture_planned_trips(pg_conn, agency_id, today=date(2026, 4, 15)) == 0


def test_another_agencys_timetable_is_not_captured(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO agencies (agency_name, feed_url) VALUES ('b', 'http://b') RETURNING agency_id")
        other = cur.fetchone()[0]
    pg_conn.commit()
    _weekly(pg_conn, other, "ALL", "1111111", date(2026, 4, 1), date(2026, 4, 30))
    _trips(pg_conn, other, "ALL", 3)

    assert capture_planned_trips(pg_conn, agency_id, today=date(2026, 4, 15)) == 0
    pg_conn.commit()
    assert _saved(pg_conn, agency_id) == {}


@pytest.mark.parametrize(
    ("incoming_start", "outgoing_through"),
    [
        # The incoming timetable starts on the day it is loaded: that day is its own.
        (date(2026, 4, 15), date(2026, 4, 14)),
        # It starts tomorrow: today stays with the outgoing timetable.
        (date(2026, 4, 16), date(2026, 4, 15)),
        # It reaches back before today: the days it covers are its own.
        (date(2026, 4, 12), date(2026, 4, 11)),
    ],
)
def test_a_reload_saves_the_outgoing_days_before_the_incoming_timetable_starts(
    pg_conn, agency_id, tmp_path, monkeypatch, incoming_start, outgoing_through
):
    """Timetable A plans 3 trips a day through April and is captured on the
    10th. On the 15th timetable B, planning 5 a day, replaces it. The reload
    saves A's days up to the one before B starts, and the next capture saves
    B's days from there."""
    freeze_now(monkeypatch, planned_trips, jst_midday(date(2026, 4, 10)))
    load_static(_timetable_zip(tmp_path, "a", "20260401", "20260430", 3), agency_id, pg_conn)
    capture_planned_trips(pg_conn, agency_id)
    assert _saved(pg_conn, agency_id) == {d: 3 for d in _days(date(2026, 4, 1), date(2026, 4, 10))}

    freeze_now(monkeypatch, planned_trips, jst_midday(date(2026, 4, 15)))
    load_static(_timetable_zip(tmp_path, "b", incoming_start.strftime("%Y%m%d"), "20261014", 5), agency_id, pg_conn)
    assert _saved(pg_conn, agency_id) == {d: 3 for d in _days(date(2026, 4, 1), outgoing_through)}

    capture_planned_trips(pg_conn, agency_id, today=date(2026, 4, 16))
    expected = {d: 3 for d in _days(date(2026, 4, 1), outgoing_through)}
    expected |= {d: 5 for d in _days(outgoing_through + timedelta(days=1), date(2026, 4, 16))}
    assert _saved(pg_conn, agency_id) == expected


def test_a_reload_cuts_over_on_the_incoming_feeds_first_calendar_dates_day(pg_conn, agency_id, tmp_path, monkeypatch):
    """A calendar_dates.txt day before the incoming calendar starts is the
    incoming timetable's first day; a malformed date there covers nothing."""
    freeze_now(monkeypatch, planned_trips, jst_midday(date(2026, 4, 15)))
    load_static(_timetable_zip(tmp_path, "a", "20260401", "20260430", 3), agency_id, pg_conn)
    incoming = _timetable_zip(tmp_path, "b", "20260420", "20261014", 5)
    with zipfile.ZipFile(incoming, "a") as zf:
        zf.writestr("calendar_dates.txt", "service_id,date,exception_type\nALL,20260413,1\nALL,2026041,1\n")

    load_static(incoming, agency_id, pg_conn)
    assert _saved(pg_conn, agency_id) == {d: 3 for d in _days(date(2026, 4, 1), date(2026, 4, 12))}


def test_the_reload_commits_its_capture_before_replacing_the_timetable(pg_conn, agency_id, tmp_path, monkeypatch):
    """While the load is still open, another connection already sees the saved
    days and still sees the outgoing calendar. A load that then fails keeps
    them, which stays right: the outgoing timetable is still the one loaded."""
    import os

    import psycopg2

    from pipeline import static_loader

    freeze_now(monkeypatch, planned_trips, jst_midday(date(2026, 4, 15)))
    load_static(_timetable_zip(tmp_path, "a", "20260401", "20260430", 3), agency_id, pg_conn)
    seen = {}

    def _fail_mid_load(*_args, **_kwargs):
        other = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            seen["saved"] = _saved(other, agency_id)
            with other.cursor() as cur:
                cur.execute("SELECT start_date FROM static_calendar WHERE agency_id = %s", (agency_id,))
                seen["calendar"] = cur.fetchall()
        finally:
            other.close()
        raise RuntimeError("load failed")

    monkeypatch.setattr(static_loader, "load_route_terminals", _fail_mid_load)
    with pytest.raises(RuntimeError):
        load_static(_timetable_zip(tmp_path, "b", "20260420", "20261014", 5), agency_id, pg_conn)
    pg_conn.rollback()

    assert seen == {
        "saved": {d: 3 for d in _days(date(2026, 4, 1), date(2026, 4, 15))},
        "calendar": [(date(2026, 4, 1),)],
    }
    assert _saved(pg_conn, agency_id) == seen["saved"]
    with pg_conn.cursor() as cur:
        cur.execute("SELECT start_date FROM static_calendar WHERE agency_id = %s", (agency_id,))
        assert cur.fetchall() == [(date(2026, 4, 1),)]


def test_a_failed_capture_does_not_stop_the_reload(pg_conn, agency_id, tmp_path, monkeypatch, caplog):
    """The capture fails inside Postgres, which aborts the transaction it runs
    in; the load's own work still has to land."""
    freeze_now(monkeypatch, planned_trips, jst_midday(date(2026, 4, 10)))
    load_static(_timetable_zip(tmp_path, "a", "20260401", "20260430", 3), agency_id, pg_conn)
    monkeypatch.setattr(planned_trips, "_CAPTURE_SQL", _FAILING_SQL)

    with caplog.at_level("WARNING", logger="pipeline.planned_trips"):
        load_static(_timetable_zip(tmp_path, "b", "20260415", "20261014", 5), agency_id, pg_conn)

    assert "could not save planned trips" in caplog.text
    assert _saved(pg_conn, agency_id) == {}
    with pg_conn.cursor() as cur:
        cur.execute("SELECT start_date FROM static_calendar WHERE agency_id = %s", (agency_id,))
        assert cur.fetchall() == [(date(2026, 4, 15),)]
        cur.execute("SELECT count(*) FROM static_trips WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 5


def test_analyze_captures_the_loaded_timetable(pg_conn, agency_id, ch_client, monkeypatch):
    _weekly(pg_conn, agency_id, "ALL", "1111111", date(2026, 4, 1), date(2026, 4, 30))
    _trips(pg_conn, agency_id, "ALL", 3)
    freeze_now(monkeypatch, planned_trips, jst_midday(date(2026, 4, 15)))

    analyze(agency_id, pg_conn, ch_client)

    assert _saved(pg_conn, agency_id) == {d: 3 for d in _days(date(2026, 4, 1), date(2026, 4, 15))}


def test_a_failed_capture_does_not_stop_analyze(pg_conn, agency_id, ch_client, monkeypatch, caplog):
    _weekly(pg_conn, agency_id, "ALL", "1111111", date(2026, 4, 1), date(2026, 4, 30))
    _trips(pg_conn, agency_id, "ALL", 3)
    monkeypatch.setattr(planned_trips, "_CAPTURE_SQL", _FAILING_SQL)

    with caplog.at_level("WARNING", logger="pipeline.planned_trips"):
        analyze(agency_id, pg_conn, ch_client)

    assert "could not save planned trips" in caplog.text
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agg_meta WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 1


@pytest.mark.parametrize("today", [date(2026, 3, 31), date(2026, 4, 1)])
def test_a_timetable_that_starts_after_today_saves_nothing_before_it(pg_conn, agency_id, today):
    _weekly(pg_conn, agency_id, "ALL", "1111111", date(2026, 4, 1), date(2026, 4, 30))
    _trips(pg_conn, agency_id, "ALL", 3)

    capture_planned_trips(pg_conn, agency_id, today=today)
    pg_conn.commit()
    assert _saved(pg_conn, agency_id) == {d: 3 for d in _days(date(2026, 4, 1), today)}
