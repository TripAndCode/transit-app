"""`scripts/repair_archive_rows.py` against throwaway ClickHouse and Postgres.

Archive rows are seeded the way the faulty backfill stored them: stamped from
a UTC file name read as JST, and with the timetable fields blank because no
timetable was loaded yet. Each test checks the repaired rows against what
ingest produces today (`archive_captured_at`, `static_join.parse_feed`).
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest

from pipeline.clickhouse import insert_updates
from pipeline.static_loader import load_static
from pipeline.strategies._pb import _ts, archive_captured_at
from pipeline.strategies.static_join import parse_feed
from scripts import repair_archive_rows as repair
from tests.fixtures.gtfs_rt import header_only_feed, stop_time_update, trip_update, trip_update_feed

ARCHIVE_FILE = "20260910/TripUpdate_000015.pb"
# Indexes of the timetable fields in a parse_feed row.
_SVC, _SCHED, _SEC, _VER = 3, 4, 13, 14


@pytest.fixture(autouse=True)
def _no_repair_tables(ch_client):
    """ch_client empties `updates` only; a repair's work table, backup and
    table comments would otherwise carry into the next test."""

    def clean():
        ch_client.command(f"DROP TABLE IF EXISTS {repair.WORK_TABLE}")
        ch_client.command("ALTER TABLE updates MODIFY COMMENT ''")

    clean()
    yield
    clean()


def _agency(pg_conn, strategy: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES (%s, %s, %s) RETURNING agency_id",
            (f"agency {strategy}", f"http://example.com/{strategy}.pb", strategy),
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()
    return aid


@pytest.fixture
def join_agency(pg_conn) -> int:
    return _agency(pg_conn, "static_join")


@pytest.fixture
def regex_agency(pg_conn) -> int:
    return _agency(pg_conn, "aomori_regex")


def _timetable(tmp_path, name: str, trips: list[str], stop_times: list[str]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,trip_headsign,shape_id,service_id\n" + "\n".join(trips))
        zf.writestr(
            "stop_times.txt", "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n" + "\n".join(stop_times)
        )
    path = tmp_path / f"{name}.zip"
    path.write_bytes(buf.getvalue())
    return str(path)


TODAYS_TRIPS = ["T1,R1,,,平日", "T2,R2,,,土日"]
TODAYS_STOP_TIMES = [
    "T1,1,S1,7:05:00,7:05:00",
    "T1,2,S2,,25:30:00",
    "T1,3,S3,,7:5",
    "T1,4,S4,,",
    "T2,1,S1,,08:00",
]


def _rt_feed() -> bytes:
    return trip_update_feed(
        trip_update(
            "T1",
            stop_time_update(1, departure_delay=30),
            stop_time_update(2, departure_delay=0),
            stop_time_update(3),
            stop_time_update(4),
            stop_time_update(9),
            route_id="R1",
        ),
        trip_update("T2", stop_time_update(1, departure_delay=-20), route_id="R2"),
        trip_update("OLD", stop_time_update(1, departure_delay=45), route_id="R9"),
    )


def _blank(row: tuple) -> tuple:
    out = list(row)
    for i in (_SVC, _SCHED, _SEC, _VER):
        out[i] = None
    return tuple(out)


def _timetable_fields(ch_client, table: str = "updates") -> dict[tuple[str, int], tuple]:
    rows = ch_client.query(
        f"SELECT trip_id, stop_sequence, service_type, scheduled_time, scheduled_sec, static_version_id FROM {table}"
    ).result_rows
    return {(t, s): (svc, sched, sec, ver) for t, s, svc, sched, sec, ver in rows}


def _all_rows(ch_client, table: str = "updates") -> list[tuple]:
    return sorted(ch_client.query(f"SELECT * FROM {table}").result_rows, key=repr)


def _comment(ch_client, table: str) -> str | None:
    rows = ch_client.query(
        "SELECT comment FROM system.tables WHERE database = currentDatabase() AND name = {t:String}",
        parameters={"t": table},
    ).result_rows
    return rows[0][0] if rows else None


def _seed_blank_archive_rows(pg_conn, ch_client, agency_id: int, tmp_path) -> dict[tuple[str, int], tuple]:
    """Load today's timetable, parse the RT feed as ingest does now, and seed
    those rows with the timetable fields blanked. Returns what parse_feed
    gave each (trip_id, stop_sequence)."""
    load_static(_timetable(tmp_path, "gtfs_static_20261014", TODAYS_TRIPS, TODAYS_STOP_TIMES), agency_id, pg_conn)
    reference = parse_feed(_rt_feed(), "2026-09-10T00:00:15+00:00", ARCHIVE_FILE, agency_id, pg_conn)
    insert_updates(ch_client, agency_id, [_blank(r) for r in reference])
    return {(r[2], r[6]): (r[_SVC], r[_SCHED], r[_SEC], r[_VER]) for r in reference}


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).replace(tzinfo=None)


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _stamp_row(file_name: str, captured_at: str, trip_id: str, header: int | None) -> tuple:
    return (file_name, captured_at, trip_id, None, None, "R1", 1, 60, None, None, None, None, header)


# (date dir, member name, header instant or None). Names are the UTC wall
# clock the collector wrote; the faulty backfill read them as JST.
STAMP_CASES = {
    "morning": ("20260905", "TripUpdate_054355.pb", "2026-09-05T05:43:50+00:00"),
    "next JST day": ("20260905", "TripUpdate_233000.pb", "2026-09-05T23:29:58+00:00"),
    "stale header": ("20260907", "TripUpdate_150000.pb", "2026-09-05T03:00:00+00:00"),
    "no header": ("20260908", "TripUpdate_101500.pb", None),
}


def _seed_stamps(ch_client, agency_id: int) -> dict[str, datetime]:
    """One row per case stamped the faulty way, one already right, one live.
    Returns the captured_at archive ingest assigns each one today."""
    expected: dict[str, datetime] = {}
    rows = []
    for trip, (d, pb, header_iso) in STAMP_CASES.items():
        header = _epoch(header_iso) if header_iso else None
        rows.append(_stamp_row(f"{d}/{pb}", _ts(d, pb), trip, header))
        expected[trip] = _utc(archive_captured_at(header_only_feed(header), d, pb))
    d, pb, header_iso = ("20260913", "TripUpdate_113312.pb", "2026-09-13T11:33:05+00:00")
    right = archive_captured_at(header_only_feed(_epoch(header_iso)), d, pb)
    rows.append(_stamp_row(f"{d}/{pb}", right, "already right", _epoch(header_iso)))
    expected["already right"] = _utc(right)
    live = ("live_20260912T074949Z", "2026-09-12T07:49:49+00:00", "live", _epoch("2026-09-12T07:49:20+00:00"))
    rows.append(_stamp_row(*live))
    expected["live"] = _utc(live[1])
    insert_updates(ch_client, agency_id, rows)
    return expected


def _stamps(ch_client) -> dict[str, datetime]:
    return dict(ch_client.query("SELECT trip_id, captured_at FROM updates").result_rows)


def _apply(*extra: str) -> int:
    return repair.main(["--apply", "--yes", *extra])


def test_archive_rows_take_the_captured_at_archive_ingest_assigns_now(ch_client, regex_agency):
    expected = _seed_stamps(ch_client, regex_agency)
    assert _stamps(ch_client) != expected
    assert _apply() == 0
    assert _stamps(ch_client) == expected


def test_live_rows_are_left_exactly_as_they_were(pg_conn, ch_client, join_agency, tmp_path):
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    live = ("live_20260912T074949Z", "2026-09-12T07:49:49+00:00", "T1", None, None, "R1", 1, 60)
    insert_updates(ch_client, join_agency, [(*live, None, None, None, None, _epoch("2026-09-12T07:40:00+00:00"))])
    before = [r for r in _all_rows(ch_client) if r[2].startswith("live_")]
    assert _apply() == 0
    assert [r for r in _all_rows(ch_client) if r[2].startswith("live_")] == before


def test_blank_timetable_fields_are_filled_as_parse_feed_fills_them(pg_conn, ch_client, join_agency, tmp_path):
    reference = _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    version = "gtfs_static_20261014"
    # The cases this has to cover, as parse_feed resolves them today.
    assert reference[("T1", 1)] == ("平日", "07:05:00", 25500, version)
    assert reference[("T1", 2)] == ("平日", None, 91800, version), "an hour past 24 keeps its seconds"
    assert reference[("T1", 3)] == ("平日", None, None, version), "a malformed time is NULL"
    assert reference[("T1", 9)] == (None, None, None, None), "a stop the timetable lacks"
    assert reference[("OLD", 1)] == (None, None, None, None), "a trip the timetable lacks"

    assert _apply() == 0
    assert _timetable_fields(ch_client) == reference


def test_rows_that_already_matched_and_other_strategies_keep_their_fields(
    pg_conn, ch_client, join_agency, regex_agency, tmp_path
):
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    earlier = "20260911/TripUpdate_000015.pb"
    matched = (earlier, "2026-09-11T00:00:15+00:00", "T2", "旧", "09:00", "R2", 1, 0)
    insert_updates(ch_client, join_agency, [(*matched, None, None, None, None, None, 32400, "gtfs_static_20260101")])
    load_static(_timetable(tmp_path, "gtfs_static_20261015", TODAYS_TRIPS, TODAYS_STOP_TIMES), regex_agency, pg_conn)
    insert_updates(ch_client, regex_agency, [(ARCHIVE_FILE, "2026-09-10T00:00:15+00:00", "T1", None, None, "R1", 1, 0)])

    assert _apply() == 0
    fields = ch_client.query(
        "SELECT agency_id, trip_id, service_type, scheduled_time, scheduled_sec, static_version_id FROM updates "
        "WHERE (agency_id = {j:UInt16} AND file_name = {f:String}) OR agency_id = {r:UInt16}",
        parameters={"j": join_agency, "f": earlier, "r": regex_agency},
    ).result_rows
    assert sorted(fields) == sorted(
        [
            (join_agency, "T2", "旧", "09:00", 32400, "gtfs_static_20260101"),
            (regex_agency, "T1", None, None, None, None),
        ]
    )


def test_an_older_timetable_fills_only_trips_todays_timetable_lacks(pg_conn, ch_client, join_agency, tmp_path):
    """parse_feed reads one timetable, so a trip's rows never mix versions: a
    trip today's timetable has takes nothing from an older one, even at a stop
    today's lacks."""
    reference = _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    older = _timetable(
        tmp_path,
        "gtfs_static_20260816",
        ["OLD,R9,,,旧平日", "T1,R1,,,旧"],
        ["OLD,1,S1,,6:40:00", "T1,1,S1,,06:00", "T1,9,S9,,24:10"],
    )
    assert _apply("--extra-static", f"{join_agency}={older}") == 0
    fields = _timetable_fields(ch_client)
    assert fields[("OLD", 1)] == ("旧平日", "06:40:00", 24000, "gtfs_static_20260816")
    assert fields[("T1", 1)] == reference[("T1", 1)]
    assert fields[("T1", 9)] == (None, None, None, None)


def test_every_row_is_kept_and_the_originals_stay_in_the_backup(pg_conn, ch_client, join_agency, tmp_path):
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    _seed_stamps(ch_client, join_agency)
    before = _all_rows(ch_client)
    ddl = ch_client.command("SHOW CREATE TABLE updates")

    assert _apply() == 0
    assert len(_all_rows(ch_client)) == len(before)
    assert _all_rows(ch_client) != before, "the repair swapped in the corrected rows"
    assert _all_rows(ch_client, repair.WORK_TABLE) == before
    assert _comment(ch_client, repair.WORK_TABLE).startswith(repair.BACKUP_MARK)
    assert _comment(ch_client, "updates") == ""
    assert ch_client.command("SHOW CREATE TABLE updates") == ddl


def test_a_dry_run_writes_nothing(pg_conn, ch_client, join_agency, tmp_path, capsys):
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    _seed_stamps(ch_client, join_agency)
    before = _all_rows(ch_client)
    tables = ch_client.query("SELECT name, comment FROM system.tables WHERE database = currentDatabase()").result_rows

    assert repair.main([]) == 0
    assert _all_rows(ch_client) == before
    assert (
        ch_client.query("SELECT name, comment FROM system.tables WHERE database = currentDatabase()").result_rows
        == tables
    )
    out = capsys.readouterr().out
    assert f"agency {join_agency}" in out
    assert "dry run" in out


def test_the_dry_run_reports_moves_and_matches(pg_conn, ch_client, join_agency, tmp_path):
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    _seed_stamps(ch_client, join_agency)
    with repair.pg_readonly() as conn:
        fill = repair.join_agencies(conn, None)
        keymap = repair.build_keymap(conn, fill, [])
    reports = repair.plan(repair.readonly_client(), repair.Scope(None, fill), keymap).agencies
    report = {r.agency_id: r for r in reports}[join_agency]
    assert report.archive_rows == 7 + 5
    # "no header" and "stale header" keep their stamp, as does the row already right.
    assert report.rows_moving == 2
    assert report.blank_rows == 7 + 5
    assert report.matched == {"gtfs_static_20261014": 5}


def test_a_second_run_refuses_while_a_backup_is_there(pg_conn, ch_client, join_agency, tmp_path, capsys):
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    assert _apply() == 0
    after = _all_rows(ch_client)
    capsys.readouterr()

    assert _apply() != 0
    assert _apply("--resume") != 0
    err = capsys.readouterr().err
    assert err.count(f"{repair.WORK_TABLE} holds the backup of an earlier repair") == 2
    assert _all_rows(ch_client) == after


def test_an_interrupted_copy_needs_resume_and_then_finishes(pg_conn, ch_client, join_agency, tmp_path, monkeypatch):
    reference = _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    original_verify = repair.verify

    def interrupted(*args, **kwargs):
        raise RuntimeError("killed")

    monkeypatch.setattr(repair, "verify", interrupted)
    with pytest.raises(RuntimeError, match="killed"):
        _apply()
    assert _comment(ch_client, repair.WORK_TABLE).startswith(repair.WORK_MARK)
    monkeypatch.setattr(repair, "verify", original_verify)

    assert _apply() != 0, "an earlier run's work table needs --resume"
    assert _apply("--resume") == 0
    assert _timetable_fields(ch_client) == reference
    assert _comment(ch_client, repair.WORK_TABLE).startswith(repair.BACKUP_MARK)


def test_a_resume_with_other_inputs_than_the_interrupted_run_refuses(
    pg_conn, ch_client, join_agency, tmp_path, monkeypatch, capsys
):
    """A resume keeps the partitions already copied, so it must fill the rest
    from the same timetables and scope the interrupted run used."""
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    original_verify = repair.verify

    def interrupted(*args, **kwargs):
        raise RuntimeError("killed")

    monkeypatch.setattr(repair, "verify", interrupted)
    with pytest.raises(RuntimeError, match="killed"):
        _apply()
    monkeypatch.setattr(repair, "verify", original_verify)
    older = _timetable(tmp_path, "gtfs_static_20260816", ["OLD,R9,,,旧平日"], ["OLD,1,S1,,6:40:00"])
    capsys.readouterr()

    assert _apply("--resume", "--extra-static", f"{join_agency}={older}") != 0
    assert "different timetables or scope" in capsys.readouterr().err
    assert _apply("--resume") == 0


def test_a_run_killed_after_the_swap_resumes_without_copying_again(
    pg_conn, ch_client, join_agency, tmp_path, monkeypatch
):
    reference = _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    original = _all_rows(ch_client)
    original_finalize = repair.finalize

    def killed(*args, **kwargs):
        raise RuntimeError("killed")

    monkeypatch.setattr(repair, "finalize", killed)
    with pytest.raises(RuntimeError, match="killed"):
        _apply()
    monkeypatch.setattr(repair, "finalize", original_finalize)

    assert _apply("--resume") == 0
    assert _timetable_fields(ch_client) == reference
    assert _all_rows(ch_client, repair.WORK_TABLE) == original
    assert _comment(ch_client, "updates") == ""


def test_a_partial_partition_in_the_work_table_is_left_to_the_operator(
    pg_conn, ch_client, join_agency, tmp_path, monkeypatch, capsys
):
    _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    original_verify = repair.verify

    def interrupted(*args, **kwargs):
        raise RuntimeError("killed")

    monkeypatch.setattr(repair, "verify", interrupted)
    with pytest.raises(RuntimeError, match="killed"):
        _apply()
    monkeypatch.setattr(repair, "verify", original_verify)
    ch_client.command(f"INSERT INTO {repair.WORK_TABLE} SELECT * FROM updates WHERE trip_id = 'T2'")
    held = _all_rows(ch_client, repair.WORK_TABLE)
    capsys.readouterr()

    assert _apply("--resume") != 0
    err = capsys.readouterr().err
    assert f"{repair.WORK_TABLE} DROP PARTITION ID '202609'" in err
    assert _all_rows(ch_client, repair.WORK_TABLE) == held, "the tool clears nothing itself"


def test_a_row_that_would_land_on_a_day_holding_live_rows_stops_the_run(ch_client, regex_agency, capsys):
    pb, header = "TripUpdate_233000.pb", _epoch("2026-09-05T23:29:58+00:00")
    insert_updates(ch_client, regex_agency, [_stamp_row(f"20260905/{pb}", _ts("20260905", pb), "moves", header)])
    live = _stamp_row("live_20260906T080000Z", "2026-09-06T08:00:00+00:00", "live", None)
    insert_updates(ch_client, regex_agency, [live])
    before = _all_rows(ch_client)

    assert _apply() != 0
    assert "live" in capsys.readouterr().err
    assert _all_rows(ch_client) == before
    assert _comment(ch_client, repair.WORK_TABLE) is None


def test_apply_without_yes_needs_a_terminal(ch_client, regex_agency, monkeypatch):
    _seed_stamps(ch_client, regex_agency)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    before = _all_rows(ch_client)
    assert repair.main(["--apply"]) != 0
    assert _all_rows(ch_client) == before
    assert _comment(ch_client, repair.WORK_TABLE) is None


def test_agency_limits_what_is_repaired(pg_conn, ch_client, join_agency, regex_agency, tmp_path):
    reference = _seed_blank_archive_rows(pg_conn, ch_client, join_agency, tmp_path)
    _seed_stamps(ch_client, regex_agency)
    stamps_before = _stamps(ch_client)

    assert _apply("--agency", str(join_agency)) == 0
    fields = _timetable_fields(ch_client)
    assert {key: fields[key] for key in reference} == reference
    after = ch_client.query(
        "SELECT trip_id, captured_at FROM updates WHERE agency_id = {a:UInt16}", parameters={"a": regex_agency}
    ).result_rows
    assert len(after) == len(STAMP_CASES) + 2
    assert dict(after) == {t: stamps_before[t] for t, _ in after}, "the agency left out keeps its stamps"


def test_the_key_map_reaches_clickhouse_whatever_text_a_timetable_holds(ch_client):
    keymap = repair.KeyMap()
    odd = ["tab\there", "line\nbreak", "back\\slash", "\\N", "cr\rlf", "nul\0", "", "平日"]
    for i, text in enumerate(odd):
        keymap.add((8, text, i), (text, None, i, f"v{text}"))
    rows = ch_client.query(
        f"SELECT trip_id, stop_sequence, service_type, scheduled_time, scheduled_sec, static_version_id, hit "
        f"FROM {repair._KEYMAP} ORDER BY stop_sequence",
        external_data=keymap.external(),
    ).result_rows
    assert rows == [(text, i, text, None, i, f"v{text}", 1) for i, text in enumerate(odd)]
