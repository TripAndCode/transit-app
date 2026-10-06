"""Restamping archive-ingested `updates` rows with the captured_at archive
ingest assigns today (pipeline.strategies._pb.archive_captured_at)."""

from datetime import datetime, timezone

import pytest

from pipeline.clickhouse import insert_updates
from pipeline.restamp_archive import BACKUP_TABLE, LEFTOVER_TABLE, plan_restamp, restamp_archive_rows
from pipeline.strategies._pb import _ts, archive_captured_at
from tests.fixtures.gtfs_rt import header_only_feed


@pytest.fixture(autouse=True)
def _no_restamp_tables(ch_client):
    """The shared ch_client fixture empties `updates` only; the tables and
    the mark a restamp leaves behind would otherwise carry into the next test."""
    for table in ("updates_restamp_staging", BACKUP_TABLE, LEFTOVER_TABLE):
        ch_client.command(f"DROP TABLE IF EXISTS {table}")
    ch_client.command("ALTER TABLE updates MODIFY COMMENT ''")


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).replace(tzinfo=None)


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _row(file_name: str, captured_at: str, trip_id: str, header: int | None) -> tuple:
    # (file_name, captured_at, trip_id, service_type, scheduled_time, route_code,
    #  stop_sequence, dep_delay, stop_id, arr_delay, sr_trip, sr_stop, feed_timestamp)
    return (file_name, captured_at, trip_id, "平日", "08:00:00", "R1", 1, 60, None, None, None, None, header)


# (date dir, member name, header instant or None). Names are the UTC wall
# clock rt-poller.sh writes; the old reading took them as JST.
ARCHIVE_CASES = {
    "morning": ("20260905", "TripUpdate_054355.pb", "2026-09-05T05:43:50+00:00"),
    "next JST day": ("20260905", "TripUpdate_233000.pb", "2026-09-05T23:29:58+00:00"),
    "stale header": ("20260907", "TripUpdate_150000.pb", "2026-09-05T03:00:00+00:00"),
    "no header": ("20260908", "TripUpdate_101500.pb", None),
}


def _seed(ch_client) -> dict[str, datetime]:
    """Seed one row per case, stamped the old way, and return what archive
    ingest stamps each one with today."""
    expected: dict[str, datetime] = {}
    rows = []
    for trip, (d, pb, header_iso) in ARCHIVE_CASES.items():
        header = _epoch(header_iso) if header_iso else None
        rows.append(_row(f"{d}/{pb}", _ts(d, pb), trip, header))
        expected[trip] = _utc(archive_captured_at(header_only_feed(header), d, pb))
    already = ("20260913", "TripUpdate_113312.pb", "2026-09-13T11:33:05+00:00")
    stamp = archive_captured_at(header_only_feed(_epoch(already[2])), already[0], already[1])
    rows.append(_row(f"{already[0]}/{already[1]}", stamp, "already restamped", _epoch(already[2])))
    expected["already restamped"] = _utc(stamp)
    rows.append(_row("live_20260912T074949Z", "2026-09-12T07:49:49+00:00", "live", _epoch("2026-09-12T07:49:20+00:00")))
    expected["live"] = _utc("2026-09-12T07:49:49+00:00")
    insert_updates(ch_client, 1, rows)
    return expected


def _stamps(ch_client) -> dict[str, datetime]:
    return dict(ch_client.query("SELECT trip_id, captured_at FROM updates").result_rows)


def test_restamps_archive_rows_to_what_archive_ingest_assigns_now(ch_client):
    expected = _seed(ch_client)
    restamp_archive_rows(ch_client)
    assert _stamps(ch_client) == expected


def test_moves_only_the_rows_stamped_the_old_way(ch_client):
    expected = _seed(ch_client)
    before = _stamps(ch_client)
    plan = plan_restamp(ch_client)
    moved = {trip for trip in expected if expected[trip] != before[trip]}
    assert moved == {"morning", "next JST day"}
    assert sum(p.rows_to_move for p in plan) == len(moved)
    assert _stamps(ch_client) == before, "planning must not write"


def test_a_second_run_changes_nothing(ch_client):
    _seed(ch_client)
    restamp_archive_rows(ch_client)
    once = _stamps(ch_client)
    assert sum(p.rows_to_move for p in plan_restamp(ch_client)) == 0
    restamp_archive_rows(ch_client)
    assert _stamps(ch_client) == once


def test_keeps_the_old_rows_aside_and_every_row(ch_client):
    _seed(ch_client)
    total = ch_client.query("SELECT count() FROM updates").result_rows[0][0]
    restamp_archive_rows(ch_client)
    assert ch_client.query("SELECT count() FROM updates").result_rows[0][0] == total
    assert ch_client.query(f"SELECT count() FROM {BACKUP_TABLE}").result_rows[0][0] == total


def test_marks_an_agency_for_a_rebuild_of_every_date(pg_conn, ch_client, agency_id):
    """A restamp moves rows between service dates, and the per-date count
    ledger misses a date whose moves in and out cancel; marking the agency
    makes its next analyze rebuild every date instead of trusting the ledger."""
    from pipeline.analyze import _dates_needing_rebuild, mark_for_full_rebuild

    row = _row("20260905/TripUpdate_054355.pb", "2026-09-05T05:43:55+00:00", "t", None)
    insert_updates(ch_client, agency_id, [row])
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_meta (agency_id, analyzed_at, static_fingerprint) VALUES (%s, now(), 'fp')", (agency_id,)
        )
        cur.execute(
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) VALUES (%s, '2026-09-05', 1, 0)",
            (agency_id,),
        )
    pg_conn.commit()
    assert _dates_needing_rebuild(agency_id, pg_conn, ch_client, "fp") == []

    mark_for_full_rebuild(pg_conn, [agency_id])
    assert _dates_needing_rebuild(agency_id, pg_conn, ch_client, "fp") is None


class _WritesDuringSwap:
    """A client whose `updates` gains a row just before the swap, the instant
    the copy check can no longer see."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def command(self, sql, *args, **kwargs):
        if sql.startswith("EXCHANGE TABLES"):
            insert_updates(
                self._client,
                2,
                [_row("20260909/TripUpdate_010000.pb", _ts("20260909", "TripUpdate_010000.pb"), "late", None)],
            )
        return self._client.command(sql, *args, **kwargs)


def test_never_drops_a_row_written_during_the_swap(ch_client):
    _seed(ch_client)
    restamp_archive_rows(ch_client)
    with pytest.raises(RuntimeError, match=LEFTOVER_TABLE):
        restamp_archive_rows(_WritesDuringSwap(ch_client))
    leftover = ch_client.query(f"SELECT trip_id FROM {LEFTOVER_TABLE} WHERE trip_id = 'late'").result_rows
    assert leftover == [("late",)]


class _FullDisk:
    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def query(self, sql, *args, **kwargs):
        if "system.disks" in sql:
            return type("R", (), {"result_rows": [(0,)]})()
        return self._client.query(sql, *args, **kwargs)


def test_refuses_to_start_without_room_for_the_copy(ch_client):
    _seed(ch_client)
    before = _stamps(ch_client)
    with pytest.raises(RuntimeError, match="free"):
        restamp_archive_rows(_FullDisk(ch_client))
    assert _stamps(ch_client) == before


class _DiesAfterSwap:
    """A client whose process dies right after the swap, before the
    swapped-out table is renamed."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def command(self, sql, *args, **kwargs):
        result = self._client.command(sql, *args, **kwargs)
        if sql.startswith("EXCHANGE TABLES"):
            raise KeyboardInterrupt("killed after the swap")
        return result


def test_a_retry_after_a_run_killed_at_the_swap_keeps_the_original_rows(ch_client):
    _seed(ch_client)
    original = _stamps(ch_client)
    with pytest.raises(KeyboardInterrupt):
        restamp_archive_rows(_DiesAfterSwap(ch_client))
    restamp_archive_rows(ch_client)
    backup = dict(ch_client.query(f"SELECT trip_id, captured_at FROM {BACKUP_TABLE}").result_rows)
    assert backup == original


def test_a_retry_after_a_run_killed_during_the_copy_starts_over(ch_client):
    expected = _seed(ch_client)
    ch_client.command("CREATE TABLE updates_restamp_staging AS updates")
    restamp_archive_rows(ch_client)
    assert _stamps(ch_client) == expected


@pytest.mark.parametrize("header", [1757000000, 4300000000])
def test_a_name_without_a_date_directory_takes_any_convertible_header(ch_client, header):
    pb = "TripUpdate_010000.pb"
    insert_updates(ch_client, 1, [_row(f"/{pb}", "2026-09-05T00:00:00+00:00", "undated", header)])
    restamp_archive_rows(ch_client)
    assert _stamps(ch_client)["undated"] == _utc(archive_captured_at(header_only_feed(header), "", pb))


def _comment(ch_client, table: str) -> str:
    return ch_client.query(
        "SELECT comment FROM system.tables WHERE database = currentDatabase() AND name = {t:String}",
        parameters={"t": table},
    ).result_rows[0][0]


def test_a_copy_left_before_the_swap_never_becomes_the_backup(ch_client):
    """Nothing to move does not mean a swap happened: a fresh table whose
    rows are all stamped right still has its copy dropped, not kept as the
    backup that a later run would then trust over the real original."""
    d, pb = "20260913", "TripUpdate_113312.pb"
    header = _epoch("2026-09-13T11:33:05+00:00")
    stamp = archive_captured_at(header_only_feed(header), d, pb)
    insert_updates(ch_client, 1, [_row(f"{d}/{pb}", stamp, "stamped right", header)])
    ch_client.command("CREATE TABLE updates_restamp_staging AS updates")
    original = _stamps(ch_client)
    restamp_archive_rows(ch_client)
    backup = dict(ch_client.query(f"SELECT trip_id, captured_at FROM {BACKUP_TABLE}").result_rows)
    assert backup == original


def test_a_finished_run_leaves_updates_unmarked(ch_client):
    _seed(ch_client)
    restamp_archive_rows(ch_client)
    assert _comment(ch_client, "updates") == ""
