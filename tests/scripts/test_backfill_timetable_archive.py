"""scripts/backfill_timetable_archive.py maps each pre-archive version name to its kept zip's own timetable,
and with --headways re-records each archived timetable's kept headways from its kept zip."""

import logging
import os
import zipfile
from datetime import date, datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from psycopg2 import sql

from db.migrate import migrate_up
from pipeline import agg_registry, static_archive, static_headways
from pipeline.agg_registry import Timetable
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.headways import ScheduledHeadways
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


def _timetable(folder, name: str, stop: str, first: str = "20260101", last: str = "20261231") -> str:
    """A timetable zip named *name* in *folder*, covering *first* to *last*, whose trip T1 makes its first
    stop at *stop*."""
    folder.mkdir(parents=True, exist_ok=True)
    return build_static_zip(
        folder,
        stops_rows=["SA,Stop A,34.39,132.45", "SB,Stop B,34.40,132.46"],
        routes_rows=["R1,1"],
        trips_rows=["T1,R1,,"],
        stop_times_rows=[f"T1,1,{stop},08:00:00,08:00:00"],
        calendar_rows=[f"WD,1,1,1,1,1,1,1,{first},{last}"],
        filename=f"{name}.zip",
    )


def _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client):
    """Rows of timetable_old, a name the archive never mapped, beside a loaded timetable_new whose T1 stops at
    SB, from the day after timetable_old's last. timetable_old's zip, whose T1 stops at SA, is kept in the
    returned static dir."""
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    static_dir = tmp_path / "static"
    _timetable(static_dir / str(agency_id), "timetable_old", "SA", last="20260601")
    load_static(
        _timetable(tmp_path / "loaded", "timetable_new", "SB", first="20260602"),
        agency_id,
        pg_conn,
        ch_client=ch_client,
    )
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


def _mirrored(url, agency_id) -> dict:
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT static_version_id, content_hash FROM {static_archive.MIRROR} WHERE agency_id = %s",
                (agency_id,),
            )
            return dict(cur.fetchall())
    finally:
        conn.close()


def test_each_name_the_backfill_maps_is_mirrored_in_the_target_not_the_scratch(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url
):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 0
    join = static_archive.schedule_join(ch_client, agency_id, None)
    old = dict(zip(join.stems, join.hashes, strict=True))["timetable_old"]
    assert _mirrored(os.environ["DATABASE_URL"], agency_id)["timetable_old"] == old
    assert _mirrored(scratch_url, agency_id) == {}


def test_a_target_without_the_mirror_is_refused_before_anything_is_mapped(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys, monkeypatch
):
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    monkeypatch.setattr(static_archive, "MIRROR", "static_version_contents_absent")
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 1
    assert "make migrate" in capsys.readouterr().err
    assert "timetable_old" not in static_archive.schedule_join(ch_client, agency_id, None).stems


def test_a_scratch_without_the_mirror_is_refused_before_anything_is_mapped(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    """load_static refuses such a scratch too, but only once a run is under way."""
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)
    scratch = psycopg2.connect(scratch_url)
    with scratch.cursor() as cur:
        cur.execute(f"DROP TABLE {static_archive.MIRROR}")
    scratch.commit()
    scratch.close()
    assert _run(agency_id, scratch_url, static_dir, "--apply") == 1
    assert "make migrate" in capsys.readouterr().err
    assert "timetable_old" not in static_archive.schedule_join(ch_client, agency_id, None).stems


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
        cur.execute(
            "INSERT INTO route_headway_by_content (agency_id, content_hash, route_code, logic_version, "
            "scheduled_headway_median_sec, scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2) "
            "VALUES (%s, 'k', 'R1', 1, 600, 1200, 720000)",
            (agency_id,),
        )
    scratch.commit()
    assert backfill.copy_medians(scratch, pg_conn, agency_id, "k") == 1
    assert backfill.copy_medians(scratch, pg_conn, agency_id, "k") == 0
    scratch.close()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_code, scheduled_headway_median_sec, scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2 "
            "FROM route_headway_by_content WHERE agency_id = %s AND content_hash = 'k'",
            (agency_id,),
        )
        assert cur.fetchall() == [("R1", 600.0, 1200.0, 720000.0)]


def test_a_failed_median_copy_leaves_the_name_for_a_rerun(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, monkeypatch
):
    """The map row takes a name out of every later run's candidates, so it lands last."""
    static_dir = _a_pre_archive_name(tmp_path, pg_conn, agency_id, ch_client)

    def the_target_went_away(*args, **kwargs):
        raise RuntimeError("the target went away")

    monkeypatch.setattr(backfill, "copy_medians", the_target_went_away)
    # Twice on one scratch: the second run's load must not archive the first's leftover timetable.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            _run(agency_id, scratch_url, static_dir, "--apply")
        assert "timetable_old" not in static_archive.schedule_join(ch_client, agency_id, None).stems


def _headway_timetable(
    folder, name: str, departures: list[str], first: str, last: str, stop_sequence: str = "1"
) -> str:
    """A timetable zip named *name* in *folder*, covering *first* to *last*: route R1 at stop s1, trip Tn
    leaving at the nth of *departures*, all under service WD, each its stop *stop_sequence*."""
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\ns1,Stop 1,34.39,132.45\n",
        "routes.txt": "route_id,route_short_name\nR1,1\n",
        "trips.txt": "trip_id,route_id,service_id\n" + "".join(f"T{n},R1,WD\n" for n in range(len(departures))),
        "stop_times.txt": "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
        + "".join(f"T{n},{stop_sequence},s1,{at},{at}\n" for n, at in enumerate(departures)),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        f"WD,1,1,1,1,1,1,1,{first},{last}\n",
    }
    path = folder / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    return str(path)


# timetable_old's gaps are 600 s and 1200 s: its median is their mean.
_OLD_HEADWAYS = ScheduledHeadways(900.0, 1800.0, 600.0**2 + 1200.0**2)


def _old_timetable(folder, departures=("08:00:00", "08:10:00", "08:30:00"), stop_sequence: str = "1") -> str:
    return _headway_timetable(folder, "timetable_old", list(departures), "20260101", "20260601", stop_sequence)


def _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client):
    """timetable_old, replaced by the loaded timetable_new from 2026-06-02, kept as a timetable kept before
    the sums were: version 1 rows, a lower-middle median and no sums. Each has a long-gap day, and both
    zips are kept in the returned static dir, with old's content and new's."""
    from tests.pipeline.test_analyze import _set_ingest_strategy
    from tests.pipeline.test_static_headways import _a_long_gap

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    static_dir = tmp_path / "static"
    folder = static_dir / str(agency_id)
    load_static(_old_timetable(folder), agency_id, pg_conn, ch_client=ch_client)
    new = _headway_timetable(folder, "timetable_new", ["08:00:00", "08:20:00"], "20260602", "20261231")
    load_static(new, agency_id, pg_conn, ch_client=ch_client)
    old = _mapped_contents(ch_client, agency_id)["timetable_old"]
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM route_headway_by_content WHERE agency_id = %s AND content_hash = %s", (agency_id, old))
        cur.execute(
            "INSERT INTO route_headway_by_content "
            "(agency_id, content_hash, route_code, logic_version, scheduled_headway_median_sec) "
            "VALUES (%s, %s, 'R1', 1, 600.0)",
            (agency_id, old),
        )
    pg_conn.commit()
    insert_updates(
        ch_client, agency_id, [*_a_long_gap("2026-06-01", "timetable_old"), *_a_long_gap("2026-06-02", "timetable_new")]
    )
    analyze(agency_id, pg_conn, ch_client)
    new_content = _mapped_contents(ch_client, agency_id)["timetable_new"]
    assert _scheduled_on(pg_conn, agency_id, date(2026, 6, 1)) == (600.0, None, None)
    return static_dir, old, new_content


def _mapped_contents(ch_client, agency_id) -> dict:
    join = static_archive.schedule_join(ch_client, agency_id, None)
    return dict(zip(join.stems, join.hashes, strict=True))


def _scheduled_on(pg_conn, agency_id, day) -> tuple:
    """*day*'s daily headway row's scheduled median and sums."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT scheduled_headway_median_sec, scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2 "
            "FROM agg_route_headway_daily WHERE agency_id = %s AND date = %s",
            (agency_id, day),
        )
        return cur.fetchone()


def _kept_rows(pg_conn, agency_id) -> list:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash, route_code, logic_version, scheduled_headway_median_sec, scheduled_headway_sum_sec, "
            "scheduled_headway_sumsq_sec2 FROM route_headway_by_content WHERE agency_id = %s ORDER BY 1, 2, 3",
            (agency_id,),
        )
        rows = cur.fetchall()
    pg_conn.rollback()
    return rows


def _archive_rows(ch_client, agency_id) -> dict:
    """How many rows each archive table holds of the agency, and its mirror."""
    tables = (static_archive.ARCHIVE, static_archive.SCHEDULE, static_archive.VERSION_MAP, static_archive.SPANS)
    counts = {
        table: ch_client.query(
            f"SELECT count() FROM {table} WHERE agency_id = {{agency:Int32}}", parameters={"agency": agency_id}
        ).result_rows[0][0]
        for table in tables
    }
    return {**counts, static_archive.MIRROR: _mirrored(os.environ["DATABASE_URL"], agency_id)}


def _run_headways(scratch, static_dir, *extra, agency=None) -> int:
    chosen = [] if agency is None else ["--agency", str(agency)]
    return backfill.main(
        ["--headways", *chosen, "--scratch-database-url", scratch, "--static-dir", str(static_dir), *extra]
    )


_CURRENT_VERSION = agg_registry.TABLES["agg_route_headway"].version
_KEPT_HEADWAY_READERS = set(static_headways.kept_headway_readers())


def test_the_headways_dry_run_lists_every_agencys_timetables_and_writes_nothing(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir, old, new = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    kept, states, archive = (
        _kept_rows(pg_conn, agency_id),
        _states(pg_conn, agency_id),
        _archive_rows(ch_client, agency_id),
    )
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir) == 0
    out = capsys.readouterr().out
    assert (
        f"agency {agency_id}: 2 archived timetable(s): 1 to re-record, 1 current, 0 without a kept zip, "
        "0 whose kept zips cannot be loaded"
    ) in out
    assert f"re-record {old}: timetable_old" in out
    assert f"current {new}: timetable_new" in out
    assert "dry run: 1 timetable(s) would be re-recorded" in out
    assert _kept_rows(pg_conn, agency_id) == kept
    assert _states(pg_conn, agency_id) == states
    assert _archive_rows(ch_client, agency_id) == archive


def test_the_headways_apply_re_records_the_timetable_so_its_days_are_judged_by_its_sums(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir, old, new = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 0
    assert f"re-recorded {old} from timetable_old: 1 route(s)" in capsys.readouterr().out
    assert static_headways.current_contents(pg_conn, agency_id) == {old, new}
    assert static_headways.route_headways(pg_conn, agency_id, [old]) == {(old, "R1"): _OLD_HEADWAYS}
    assert (old, "R1", _CURRENT_VERSION, *_OLD_HEADWAYS) in _kept_rows(pg_conn, agency_id)
    analyze(agency_id, pg_conn, ch_client)
    assert _scheduled_on(pg_conn, agency_id, date(2026, 6, 1)) == tuple(_OLD_HEADWAYS)


def test_the_headways_apply_clears_only_the_states_of_the_tables_built_from_the_kept_headways(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url
):
    """The other archive-joined tables read nothing the re-record changed, so they stand."""
    static_dir, _, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    states = _states(pg_conn, agency_id)
    assert "agg_route_headway_daily" in _KEPT_HEADWAY_READERS
    assert _KEPT_HEADWAY_READERS < set(static_archive.archive_joined_tables()) & set(states)
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 0
    assert _states(pg_conn, agency_id) == {
        name: state for name, state in states.items() if name not in _KEPT_HEADWAY_READERS
    }


def test_the_headways_apply_leaves_the_archive_the_map_and_its_mirror_as_they_were(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url
):
    """It reads ClickHouse through a read-only session: it archives, maps and spans nothing."""
    static_dir, old, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    archive, mapped = _archive_rows(ch_client, agency_id), _mapped_contents(ch_client, agency_id)
    assert _run_headways(scratch_url, static_dir, "--apply") == 0
    assert old in static_headways.current_contents(pg_conn, agency_id)
    assert _archive_rows(ch_client, agency_id) == archive
    assert _mapped_contents(ch_client, agency_id) == mapped
    assert _mirrored(scratch_url, agency_id) == {}


def test_a_second_headways_run_finds_nothing_to_do(tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys):
    static_dir, _, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 0
    analyze(agency_id, pg_conn, ch_client)
    kept, states = _kept_rows(pg_conn, agency_id), _states(pg_conn, agency_id)
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 0
    out = capsys.readouterr().out
    assert "0 to re-record, 2 current" in out and "nothing to re-record" in out
    assert _kept_rows(pg_conn, agency_id) == kept
    assert _states(pg_conn, agency_id) == states


def test_a_timetable_without_a_kept_zip_is_reported_and_keeps_its_headways(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir, old, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    (static_dir / str(agency_id) / "timetable_old.zip").unlink()
    kept, states = _kept_rows(pg_conn, agency_id), _states(pg_conn, agency_id)
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir, agency=agency_id) == 0
    assert f"no kept zip {old}: timetable_old; not re-recorded" in capsys.readouterr().out
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 1
    captured = capsys.readouterr()
    assert (
        f"no kept zip {old}: timetable_old; not re-recorded" in captured.out and "nothing to re-record" in captured.out
    )
    assert "1 timetable(s) not re-recorded" in captured.err
    assert _kept_rows(pg_conn, agency_id) == kept
    assert _states(pg_conn, agency_id) == states


def test_a_zip_that_does_not_reproduce_its_timetable_is_reported_and_nothing_is_written(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    """A name refetched under the same file name holds another timetable: it is never remapped."""
    static_dir, old, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    _old_timetable(static_dir / str(agency_id), ("08:00:00", "08:15:00"))
    kept, states, mapped = (
        _kept_rows(pg_conn, agency_id),
        _states(pg_conn, agency_id),
        _mapped_contents(ch_client, agency_id),
    )
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 1
    out = capsys.readouterr().out
    assert f"does not reproduce {old}" in out and f"{old}: not re-recorded" in out
    assert _kept_rows(pg_conn, agency_id) == kept
    assert _states(pg_conn, agency_id) == states
    assert _mapped_contents(ch_client, agency_id) == mapped


def test_a_zip_whose_span_is_not_the_recorded_one_is_not_loaded(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    """load_static would record the zip's span again, moving the days its name is in force."""
    static_dir, _, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    static_archive.record_span(ch_client, agency_id, "timetable_old", (date(2026, 1, 1), date(2026, 5, 31)))
    kept, archive = _kept_rows(pg_conn, agency_id), _archive_rows(ch_client, agency_id)
    capsys.readouterr()
    # The dry run lists the zip as the apply treats it.
    assert _run_headways(scratch_url, static_dir, agency=agency_id) == 0
    out = capsys.readouterr().out
    assert "its span is not the one recorded for timetable_old; not loaded" in out
    assert "0 to re-record" in out and "1 whose kept zips cannot be loaded" in out and "re-record " not in out
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 1
    assert "its span is not the one recorded for timetable_old; not loaded" in capsys.readouterr().out
    assert _kept_rows(pg_conn, agency_id) == kept
    assert _archive_rows(ch_client, agency_id) == archive


def test_the_headways_apply_refuses_a_database_without_the_sums(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys, monkeypatch
):
    static_dir, _, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    kept = _kept_rows(pg_conn, agency_id)
    monkeypatch.setattr(backfill, "_SUM_COLUMNS", (*backfill._SUM_COLUMNS, "scheduled_headway_absent"))
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 1
    assert "make migrate" in capsys.readouterr().err
    assert _kept_rows(pg_conn, agency_id) == kept


def _another_agency(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("別の交通", "http://example.com/other.pb"),
        )
        other = cur.fetchone()[0]
    pg_conn.commit()
    return other


def _two_agencies_kept_before_their_sums(tmp_path, pg_conn, agency_id, ch_client):
    """The agency_id fixture's agency and a later one, each with a timetable kept before its sums, in one
    static dir."""
    static_dir, old, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    other = _another_agency(pg_conn)
    _a_timetable_kept_before_its_sums(tmp_path, pg_conn, other, ch_client)
    return static_dir, old, other


def _readers_cleared(pg_conn, agency_id, states) -> bool:
    return _states(pg_conn, agency_id) == {
        name: state for name, state in states.items() if name not in _KEPT_HEADWAY_READERS
    }


def test_a_zip_that_will_not_load_is_reported_and_the_next_agency_is_still_re_recorded(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir, old, other = _two_agencies_kept_before_their_sums(tmp_path, pg_conn, agency_id, ch_client)
    # It reads, but its stop sequences are not numbers, so load_static fails on it.
    _old_timetable(static_dir / str(agency_id), stop_sequence="x")
    states, other_states = _states(pg_conn, agency_id), _states(pg_conn, other)
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir, "--apply") == 1
    captured = capsys.readouterr()
    assert f"could not load {static_dir / str(agency_id) / 'timetable_old.zip'}:" in captured.out
    assert f"{old}: not re-recorded" in captured.out
    assert f"next: analyze agencies {other};" in captured.out
    assert "1 timetable(s) not re-recorded" in captured.err
    assert old not in static_headways.current_contents(pg_conn, agency_id)
    assert _states(pg_conn, agency_id) == states
    assert old in static_headways.current_contents(pg_conn, other)
    assert _readers_cleared(pg_conn, other, other_states)


def test_an_unreadable_zip_is_listed_as_not_loaded_and_left(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir, old, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    (static_dir / str(agency_id) / "timetable_old.zip").write_bytes(b"not a zip")
    kept, states = _kept_rows(pg_conn, agency_id), _states(pg_conn, agency_id)
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir, agency=agency_id) == 0
    out = capsys.readouterr().out
    assert "timetable_old.zip: could not read it:" in out and f"no loadable zip {old}" in out
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 1
    assert _kept_rows(pg_conn, agency_id) == kept
    assert _states(pg_conn, agency_id) == states


def test_an_agency_that_fails_outside_its_zips_is_reported_and_the_next_still_re_recorded(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys, monkeypatch
):
    static_dir, _, other = _two_agencies_kept_before_their_sums(tmp_path, pg_conn, agency_id, ch_client)
    real = backfill.copy_medians

    def the_first_agency_cannot_be_written(scratch_conn, target_conn, agency, key):
        if agency == agency_id:
            raise RuntimeError("the target refused the write")
        return real(scratch_conn, target_conn, agency, key)

    monkeypatch.setattr(backfill, "copy_medians", the_first_agency_cannot_be_written)
    other_states = _states(pg_conn, other)
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir, "--apply") == 1
    captured = capsys.readouterr()
    assert f"agency {agency_id}: stopped, the target refused the write" in captured.err
    assert f"next: analyze agencies {other};" in captured.out
    assert _readers_cleared(pg_conn, other, other_states)


def test_an_agency_the_target_does_not_hold_is_reported_and_skipped(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys
):
    static_dir, old, _ = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT max(agency_id) + 1000 FROM agencies")
        absent = cur.fetchone()[0]
    pg_conn.rollback()
    ch_client.insert(
        static_archive.VERSION_MAP,
        [(absent, "timetable_elsewhere", "c0ffee", datetime.now(timezone.utc))],
        column_names=["agency_id", "static_version_id", "content_hash", "archived_at"],
    )
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir) == 0
    out = capsys.readouterr().out
    assert f"agency {absent}: not in the target database; skipped" in out
    assert f"re-record {old}: timetable_old" in out
    # Named on its own, it is refused, as the name backfill refuses it.
    assert _run_headways(scratch_url, static_dir, agency=absent) == 1
    assert f"refused: agency {absent} is not in the target database" in capsys.readouterr().err


def test_a_copy_failing_in_the_target_reports_its_own_error_and_the_states_are_still_cleared(
    tmp_path, pg_conn, agency_id, ch_client, scratch_url, capsys, monkeypatch
):
    """The failed statement leaves the target's transaction aborted; the clear at the end still runs."""
    static_dir, old, new = _a_timetable_kept_before_its_sums(tmp_path, pg_conn, agency_id, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM route_headway_by_content WHERE agency_id = %s AND content_hash = %s", (agency_id, new))
    pg_conn.commit()
    states = _states(pg_conn, agency_id)
    real = backfill.copy_medians
    copies = []

    def the_second_copy_fails(scratch_conn, target_conn, agency, key):
        copies.append(key)
        if len(copies) == 2:
            with target_conn.cursor() as cur:
                cur.execute("SELECT 1/0")
        return real(scratch_conn, target_conn, agency, key)

    monkeypatch.setattr(backfill, "copy_medians", the_second_copy_fails)
    capsys.readouterr()
    assert _run_headways(scratch_url, static_dir, "--apply", agency=agency_id) == 1
    assert copies == [new, old]
    assert f"agency {agency_id}: stopped, division by zero" in capsys.readouterr().err
    assert static_headways.current_contents(pg_conn, agency_id) == {new}
    assert _readers_cleared(pg_conn, agency_id, states)
