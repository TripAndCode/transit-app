"""Archive ingest replaces a day promotion stored from the live path, once the
folder holds the day's complete archive (pipeline.archive_replace)."""

import io
import tarfile
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from clickhouse_connect.driver.exceptions import OperationalError

from api.range import jst_today
from pipeline import archive_replace, refiled
from pipeline.analyze import _dates_needing_rebuild, analyze
from pipeline.archive_replace import DaySources, day_sources
from pipeline.clickhouse import LIVE_TABLE, insert_updates
from pipeline.ingest import IngestIncomplete, ingest
from pipeline.promote import promote_closed_days
from pipeline.static_archive import FILED_DATES
from tests.fixtures.gtfs_rt import header_only_feed

_JST = ZoneInfo("Asia/Tokyo")
_DAY = timedelta(days=1)
D = date(2026, 4, 2)


def _at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=_JST).astimezone(timezone.utc)


def _member_name(at: datetime) -> str:
    return f"{at:%Y%m%d}/TripUpdate_{at:%H%M%S}.pb"


def _tarball(folder, utc_day: date, instants: list[datetime]) -> None:
    """The collector's tarball of *utc_day*: a member per poll in *instants*,
    named by its UTC time and carrying it as its feed header."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for at in instants:
            assert at.date() == utc_day
            data = header_only_feed(int(at.timestamp()))
            info = tarfile.TarInfo(_member_name(at))
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    (folder / f"{utc_day:%Y%m%d}.tar.gz").write_bytes(buf.getvalue())


def _poll_rows(raw, ts, file_name, aid, conn):
    return [(file_name, ts, trip, "平日", "12:00", "R1", 1, 60) for trip in ("T1", "T2")]


def _promote(ch_client, agency_id: int, instants: list[datetime], *, table: str = "updates") -> None:
    """Store a poll per instant under the collector's push names, as promotion leaves them."""
    rows = [
        (f"oracle/{_member_name(at)}", at, trip, "平日", "12:00", "R1", 1, 60)
        for at in instants
        for trip in ("T1", "T2")
    ]
    insert_updates(ch_client, agency_id, rows, table=table)


def _ingest(folder, agency_id, pg_conn, ch_client, **kwargs) -> int:
    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=kwargs.pop("parse", _poll_rows)):
        return ingest(str(folder), agency_id, pg_conn, ch_client, **kwargs)


def _complete_archive(folder, day: date = D, polls_before_nine: tuple[int, ...] = (1, 3)) -> None:
    """Both tarballs of JST *day*: UTC day-1 holds its hours to 09:00, UTC day the rest."""
    _tarball(folder, day - _DAY, [_at(day, hh) for hh in polls_before_nine])
    _tarball(folder, day, [_at(day, hh) for hh in (10, 12, 20)])


_LIVE_POLLS = [_at(D, 3), _at(D, 12)]


def test_a_complete_archive_replaces_the_promoted_day(pg_conn, ch_client, agency_id, tmp_path):
    _promote(ch_client, agency_id, [*_LIVE_POLLS, _at(D - _DAY, 12)])
    _complete_archive(tmp_path)

    assert _ingest(tmp_path, agency_id, pg_conn, ch_client) == 10

    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)
    # The day before is not covered (its UTC day-1 tarball is absent): untouched.
    assert day_sources(ch_client, agency_id, D - _DAY) == DaySources(0, 0, 2, 1)


def test_a_second_run_over_a_replaced_day_changes_nothing(pg_conn, ch_client, agency_id, tmp_path):
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)
    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert _ingest(tmp_path, agency_id, pg_conn, ch_client) == 0

    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)


def test_a_day_missing_one_of_its_tarballs_keeps_its_live_copy(pg_conn, ch_client, agency_id, tmp_path):
    """Tarball D alone holds the day from 09:00 JST only, so its members of D are refused."""
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _tarball(tmp_path, D, [_at(D, hh) for hh in (10, 12, 20)])

    assert _ingest(tmp_path, agency_id, pg_conn, ch_client) == 0

    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 4, 2)


def test_an_archive_with_fewer_polls_than_the_live_copy_is_taken_back(pg_conn, ch_client, agency_id, tmp_path):
    _promote(ch_client, agency_id, [_at(D, hh) for hh in (3, 5, 8, 12, 15, 18)])
    _complete_archive(tmp_path)

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 12, 6)


def test_a_failed_member_of_the_day_keeps_its_live_copy(pg_conn, ch_client, agency_id, tmp_path):
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)
    broken = _member_name(_at(D, 20))

    def parse(raw, ts, file_name, aid, conn):
        if file_name == broken:
            raise ValueError("corrupt member")
        return _poll_rows(raw, ts, file_name, aid, conn)

    with pytest.raises(IngestIncomplete):
        _ingest(tmp_path, agency_id, pg_conn, ch_client, parse=parse, strict=True)

    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 4, 2)


def test_a_failed_archive_insert_never_costs_the_live_copy(pg_conn, ch_client, agency_id, tmp_path):
    """The live rows go only after the archive's are in."""
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)

    with patch("pipeline.ingest.insert_updates", side_effect=OperationalError("connection dropped")):
        _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 4, 2)


def test_a_day_whose_rows_file_below_the_retention_floor_stays_live(pg_conn, ch_client, agency_id, tmp_path):
    """JST 2026-04-01's rows before 04:00 file under 2026-03-31, a final date
    once the floor is 2026-04-01, so that day stays live while the next one,
    in the same folder, is replaced."""
    floor = date(2026, 4, 1)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes) "
            "VALUES ('202603', %s, %s, 0, 0)",
            ([agency_id], floor),
        )
    pg_conn.commit()
    _promote(ch_client, agency_id, [_at(floor, 3), _at(floor, 12), *_LIVE_POLLS])
    _tarball(tmp_path, floor - _DAY, [_at(floor, 1), _at(floor, 3)])
    _tarball(tmp_path, floor, [_at(floor, hh) for hh in (10, 12, 20)] + [_at(D, hh) for hh in (1, 3)])
    _tarball(tmp_path, D, [_at(D, hh) for hh in (10, 12, 20)])

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, floor) == DaySources(0, 0, 4, 2)
    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)


def test_an_interrupted_replacement_is_finished_by_the_next_run(pg_conn, ch_client, agency_id, tmp_path):
    """A run that dies after the archive's rows are in leaves both sources; the
    next run over the same tarballs finds every member stored and deletes
    the live copy."""
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)
    with patch.object(archive_replace, "settle", return_value=0):
        _ingest(tmp_path, agency_id, pg_conn, ch_client)
    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 4, 2)

    assert _ingest(tmp_path, agency_id, pg_conn, ch_client) == 0

    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)


def test_promotion_never_brings_back_a_replaced_day(pg_conn, ch_client, agency_id, tmp_path):
    yday = jst_today() - _DAY
    polls = [_at(yday, 3), _at(yday, 12)]
    _promote(ch_client, agency_id, polls, table=LIVE_TABLE)
    assert promote_closed_days(agency_id, pg_conn, ch_client) == 4
    _complete_archive(tmp_path, yday)
    _ingest(tmp_path, agency_id, pg_conn, ch_client)
    assert day_sources(ch_client, agency_id, yday) == DaySources(10, 5, 0, 0)

    assert promote_closed_days(agency_id, pg_conn, ch_client) == 0

    assert day_sources(ch_client, agency_id, yday) == DaySources(10, 5, 0, 0)


def test_analyze_rebuilds_a_replaced_day_whose_counts_did_not_move(pg_conn, ch_client, agency_id, tmp_path):
    """An archive copy as dense as the live one it replaces keeps the date's
    row counts; the change of source still lists it for rebuild."""
    hours = (5, 8, 10, 12, 20)
    _promote(ch_client, agency_id, [_at(D, hh) for hh in hours])
    analyze(agency_id, pg_conn, ch_client)
    _complete_archive(tmp_path, polls_before_nine=(5, 8))

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)
    assert D in _dates_needing_rebuild(agency_id, pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT total_rows, archive_rows, live_rows FROM agg_feed_health WHERE agency_id = %s AND date = %s",
            (agency_id, D),
        )
        assert cur.fetchone() == (10, 10, 0)
    assert _dates_needing_rebuild(agency_id, pg_conn, ch_client) == []


def test_an_ingest_live_sample_does_not_count_against_the_archive(pg_conn, ch_client, agency_id, tmp_path):
    """The collector pushed every poll it archived; the cron poke's own
    `ingest_live` sample is a fetch the archive never holds."""
    polls = [_at(D, hh) for hh in (1, 3, 10, 12, 20)]
    _promote(ch_client, agency_id, polls)
    sample = _at(D, 14)
    insert_updates(
        ch_client, agency_id, [(f"live_{sample:%Y%m%dT%H%M%SZ}", sample, "T1", "平日", "12:00", "R1", 1, 60)]
    )
    _complete_archive(tmp_path)

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)


def test_a_replaced_day_writes_off_its_refiled_rows(pg_conn, ch_client, agency_id, tmp_path):
    """A refiled build counts a date's rows, which an archive copy as dense as
    the live one keeps, so analyze would read the live copy's refiled rows."""
    _promote(ch_client, agency_id, _LIVE_POLLS)
    record = (agency_id, D, "b1", "gtfs_static_20260401", "c", {}, 4, refiled.RULES)
    ch_client.insert(FILED_DATES, [record], column_names=refiled._RECORD_COLUMNS)
    _complete_archive(tmp_path)

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert refiled.written(ch_client, agency_id, (D, D))[D].build == ""


def _rolling_pair(folder, day: date) -> None:
    """The daily job's folder for JST *day*: UTC day-1's tarball, holding *day*
    to 09:00, and UTC day's, holding the rest of it and the next day to 09:00."""
    _tarball(folder, day - _DAY, [_at(day, hh) for hh in (1, 3)])
    _tarball(folder, day, [_at(day, hh) for hh in (10, 12, 20)] + [_at(day + _DAY, hh) for hh in (1, 3)])


def test_the_next_rolling_pair_finishes_an_interrupted_replacement(pg_conn, ch_client, agency_id, tmp_path):
    """The next day's folder no longer covers D, and every member of D is
    already stored, yet D is settled from what `updates` holds."""
    _promote(ch_client, agency_id, [*_LIVE_POLLS, _at(D + _DAY, 3), _at(D + _DAY, 12)])
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _rolling_pair(first, D)
    with patch.object(archive_replace, "settle", return_value=0):
        _ingest(first, agency_id, pg_conn, ch_client)
    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 4, 2)

    _rolling_pair(second, D + _DAY)
    _tarball(second, D, [_at(D, hh) for hh in (10, 12, 20)] + [_at(D + _DAY, hh) for hh in (1, 3)])
    _ingest(second, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)
    assert day_sources(ch_client, agency_id, D + _DAY) == DaySources(10, 5, 0, 0)


def test_one_day_that_cannot_be_settled_leaves_the_others_settled(pg_conn, ch_client, agency_id, tmp_path):
    _promote(ch_client, agency_id, [*_LIVE_POLLS, _at(D + _DAY, 3), _at(D + _DAY, 12)])
    _rolling_pair(tmp_path, D)
    _tarball(tmp_path, D + _DAY, [_at(D + _DAY, hh) for hh in (10, 12, 20)])
    real_delete = archive_replace._delete

    def delete(ch, agency_id, day, *, live):
        if day == D:
            raise OperationalError("delete timed out")
        real_delete(ch, agency_id, day, live=live)

    with patch.object(archive_replace, "_delete", side_effect=delete):
        with pytest.raises(archive_replace.ReplacementIncomplete, match=str(D)):
            _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D + _DAY) == DaySources(10, 5, 0, 0)
    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 4, 2)


def test_an_archive_with_a_gap_keeps_the_live_day_whatever_its_poll_count(pg_conn, ch_client, agency_id, tmp_path):
    """Ten archive polls outnumber three pushes, but the archive has nothing
    after 14:00, where the collector pushed one."""
    _promote(ch_client, agency_id, [_at(D, 3), _at(D, 12), _at(D, 22)])
    _tarball(tmp_path, D - _DAY, [_at(D, hh) for hh in (1, 2, 3, 4, 5)])
    _tarball(tmp_path, D, [_at(D, hh) for hh in (10, 11, 12, 13, 14)])

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 6, 3)


def test_a_push_stamped_after_midnight_matches_its_member_stamped_before(pg_conn, ch_client, agency_id, tmp_path):
    """The collector stamps a push when it sends it, the archive by the feed's
    header, so one poll can fall either side of JST midnight."""
    header = _at(D - _DAY, 23, 59)
    sent = header + timedelta(minutes=2)
    rows = [(f"oracle/{_member_name(header)}", sent, trip, "平日", "12:00", "R1", 1, 60) for trip in ("T1", "T2")]
    insert_updates(ch_client, agency_id, rows)
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _tarball(tmp_path, D - _DAY, [header, *(_at(D, hh) for hh in (1, 3))])
    _tarball(tmp_path, D, [_at(D, hh) for hh in (10, 12, 20)])

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D).live_rows == 0


def test_an_insert_that_lands_before_it_fails_is_taken_back(pg_conn, ch_client, agency_id, tmp_path):
    """A batch the server committed though the client raised is not in the
    run's skip-list, so its day is not intact; its rows are deleted."""
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)
    real_insert = insert_updates

    def insert(*args, **kwargs):
        real_insert(*args, **kwargs)
        raise OperationalError("read timed out after the insert was sent")

    with patch("pipeline.ingest.insert_updates", side_effect=insert):
        assert _ingest(tmp_path, agency_id, pg_conn, ch_client) == 0

    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 4, 2)


def test_a_day_rolled_back_to_live_is_replaced_by_a_later_clean_run(pg_conn, ch_client, agency_id, tmp_path):
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)
    broken = _member_name(_at(D, 20))

    def parse(raw, ts, file_name, aid, conn):
        if file_name == broken:
            raise ValueError("corrupt member")
        return _poll_rows(raw, ts, file_name, aid, conn)

    assert _ingest(tmp_path, agency_id, pg_conn, ch_client, parse=parse) == 0
    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 4, 2)

    assert _ingest(tmp_path, agency_id, pg_conn, ch_client) == 10

    assert day_sources(ch_client, agency_id, D) == DaySources(10, 5, 0, 0)


def test_an_unreadable_tarball_keeps_both_of_its_days_live(pg_conn, ch_client, agency_id, tmp_path):
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)
    (tmp_path / f"{D:%Y%m%d}.tar.gz").write_bytes(b"not a tarball")

    _ingest(tmp_path, agency_id, pg_conn, ch_client)

    assert day_sources(ch_client, agency_id, D) == DaySources(0, 0, 4, 2)


def test_check_aggs_fails_on_a_day_holding_both_sources(pg_conn, ch_client, agency_id, tmp_path, monkeypatch, caplog):
    import gtfs_pipeline
    from pipeline import freshness

    monkeypatch.setattr(freshness, "check_agg_freshness", lambda conn, ch, ids: [])
    _promote(ch_client, agency_id, _LIVE_POLLS)
    _complete_archive(tmp_path)
    with patch.object(archive_replace, "settle", return_value=0):
        _ingest(tmp_path, agency_id, pg_conn, ch_client)
    assert freshness.two_source_days(pg_conn, ch_client, [agency_id]) == {agency_id: [D]}

    with pytest.raises(SystemExit) as exc:
        gtfs_pipeline.cmd_check_aggs(None)
    assert exc.value.code == 1
    assert f"TWO SOURCES agency {agency_id}: {D}" in caplog.text

    _ingest(tmp_path, agency_id, pg_conn, ch_client)
    assert freshness.two_source_days(pg_conn, ch_client, [agency_id]) == {}
    gtfs_pipeline.cmd_check_aggs(None)
