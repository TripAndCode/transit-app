import io
import tarfile
from unittest.mock import patch

from pipeline.ingest import ingest, parse_trip_id

_FAKE_ROW = (
    "20260401/ok.pb",
    "2026-04-01T11:37:00",
    "平日_11時37分_系統44372",
    "平日",
    "11:37",
    "44372",
    1,
    120,
)


def test_parse_trip_id_weekday():
    result = parse_trip_id("平日_11時37分_系統44372")
    assert result is not None
    assert result["service"] == "平日"
    assert result["hour"] == "11"
    assert result["minute"] == "37"
    assert result["route"] == "44372"


def test_parse_trip_id_invalid():
    result = parse_trip_id("invalid")
    assert result is None


def test_ingest_creates_rows(pg_conn, agency_id, tmp_path):
    """Ingest a fake tarball and verify rows land in updates with correct agency_id."""
    # 8-tuple shape: (file_name, captured_at, trip_id, service_type, scheduled_time,
    #                 route_code, stop_sequence, dep_delay)
    fake_row = (
        "20260401/TripUpdate_113700.pb",  # file_name
        "2026-04-01T11:37:00",  # captured_at
        "平日_11時37分_系統44372",  # trip_id
        "平日",  # service_type
        "11:37",  # scheduled_time
        "44372",  # route_code
        1,  # stop_sequence
        120,  # dep_delay (seconds)
    )
    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row]):
        pb_data = b"\x00"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="20260401/TripUpdate_113700.pb")
            info.size = len(pb_data)
            tf.addfile(info, io.BytesIO(pb_data))
        tgz_path = tmp_path / "20260401.tar.gz"
        tgz_path.write_bytes(buf.getvalue())

        count = ingest(str(tmp_path), agency_id, pg_conn)

    assert count == 1
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT route_code, dep_delay, agency_id FROM updates WHERE agency_id = %s",
            (agency_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "44372"
    assert rows[0][1] == 120
    assert rows[0][2] == agency_id


def test_ingest_tarball_member_failure_does_not_wipe_an_earlier_good_members_insert(pg_conn, agency_id, tmp_path):
    """One malformed member inside a tarball must only roll back ITS OWN
    insert, not every good member already inserted earlier in the same
    tarball since the last 300-member commit boundary - the identical bug
    class fixed for the loose-.pb loop, reproduced at tarball-member grain."""
    ok_data, bad_data = b"\x00", b"\x01"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("20260401/a_ok.pb", ok_data), ("20260401/z_bad.pb", bad_data)):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tgz_path = tmp_path / "20260401.tar.gz"
    tgz_path.write_bytes(buf.getvalue())

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        if file_name.endswith("z_bad.pb"):
            raise ValueError("boom")
        return [_FAKE_ROW]

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        ingest(str(tmp_path), agency_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_code FROM updates WHERE agency_id = %s", (agency_id,))
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["44372"]  # a_ok.pb's row survives z_bad.pb's failure


def test_ingest_loose_pb_continues_past_one_malformed_file(pg_conn, agency_id, tmp_path):
    """One malformed loose .pb file must not abort ingestion of the rest -
    matching the tarball loop right above it in ingest(), which already
    isolates per-tarball failures via try/except+rollback+continue."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir()
    (day_dir / "bad.pb").write_bytes(b"\x00")
    (day_dir / "ok.pb").write_bytes(b"\x00")

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        if file_name.endswith("bad.pb"):
            raise ValueError("boom")
        return [_FAKE_ROW]

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        count = ingest(str(tmp_path), agency_id, pg_conn)

    assert count == 1  # the good file's row still landed
    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_code FROM updates WHERE agency_id = %s", (agency_id,))
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["44372"]


def test_ingest_loose_pb_failure_does_not_wipe_an_earlier_good_files_insert(pg_conn, agency_id, tmp_path):
    """A file that fails must only roll back ITS OWN work, not every good
    file already inserted earlier in the same (not-yet-committed) batch.
    a_ok.pb sorts before z_bad.pb, so the good insert happens first and,
    with a bare conn.rollback() on the later failure, would be wiped too -
    isolation must use a per-file SAVEPOINT, not the whole transaction."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir()
    (day_dir / "a_ok.pb").write_bytes(b"\x00")
    (day_dir / "z_bad.pb").write_bytes(b"\x00")

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        if file_name.endswith("z_bad.pb"):
            raise ValueError("boom")
        return [_FAKE_ROW]

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        ingest(str(tmp_path), agency_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_code FROM updates WHERE agency_id = %s", (agency_id,))
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["44372"]  # a_ok.pb's row survives z_bad.pb's failure


def test_ingest_tarball_extractfile_failure_does_not_wipe_an_earlier_good_members_insert(pg_conn, agency_id, tmp_path):
    """tarfile.extractfile() can raise on a corrupt member (bad header/index)
    even when tarfile.open() and getmembers() succeeded. That must isolate
    like any other per-member failure, not escape the savepoint and roll
    back every good member already inserted earlier in this tarball."""
    ok_data, bad_data = b"\x00", b"\x01"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("20260401/a_ok.pb", ok_data), ("20260401/z_bad.pb", bad_data)):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tgz_path = tmp_path / "20260401.tar.gz"
    tgz_path.write_bytes(buf.getvalue())

    real_extractfile = tarfile.TarFile.extractfile

    def flaky_extractfile(self, member):
        if member.name.endswith("z_bad.pb"):
            raise tarfile.ReadError("corrupt member")
        return real_extractfile(self, member)

    with (
        patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[_FAKE_ROW]),
        patch.object(tarfile.TarFile, "extractfile", flaky_extractfile),
    ):
        ingest(str(tmp_path), agency_id, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT route_code FROM updates WHERE agency_id = %s", (agency_id,))
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["44372"]  # a_ok.pb's row survives z_bad.pb's extractfile failure


def test_ingest_dedup_skips_seen_files(pg_conn, agency_id, tmp_path):
    """Running ingest twice on the same folder inserts 0 rows the second time."""
    fake_row = (
        "20260401/TripUpdate_113700.pb",
        "2026-04-01T11:37:00",
        "平日_11時37分_系統44372",
        "平日",
        "11:37",
        "44372",
        1,
        120,
    )
    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row]):
        pb_data = b"\x00"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="20260401/TripUpdate_113700.pb")
            info.size = 1
            tf.addfile(info, io.BytesIO(pb_data))
        tgz_path = tmp_path / "20260401.tar.gz"
        tgz_path.write_bytes(buf.getvalue())

        first = ingest(str(tmp_path), agency_id, pg_conn)
        second = ingest(str(tmp_path), agency_id, pg_conn)

    assert first == 1
    assert second == 0


def test_ingest_agency_isolated(pg_conn, tmp_path):
    """Two agencies ingesting same file_name don't interfere."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency A", "http://a.example.com/feed.pb"),
        )
        aid_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency B", "http://b.example.com/feed.pb"),
        )
        aid_b = cur.fetchone()[0]
    pg_conn.commit()

    fake_row_a = (
        "20260401/TripUpdate_113700.pb",
        "2026-04-01T11:37:00",
        "平日_11時37分_系統44372",
        "平日",
        "11:37",
        "44372",
        1,
        60,
    )
    fake_row_b = (
        "20260401/TripUpdate_113700.pb",
        "2026-04-01T11:37:00",
        "平日_11時37分_系統44372",
        "平日",
        "11:37",
        "44372",
        1,
        90,
    )
    pb_data = b"\x00"

    def make_tgz(path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="20260401/TripUpdate_113700.pb")
            info.size = 1
            tf.addfile(info, io.BytesIO(pb_data))
        path.write_bytes(buf.getvalue())

    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    make_tgz(dir_a / "20260401.tar.gz")
    make_tgz(dir_b / "20260401.tar.gz")

    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row_a]):
        ingest(str(dir_a), aid_a, pg_conn)
    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row_b]):
        ingest(str(dir_b), aid_b, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT dep_delay FROM updates WHERE agency_id = %s", (aid_a,))
        assert cur.fetchone()[0] == 60
        cur.execute("SELECT dep_delay FROM updates WHERE agency_id = %s", (aid_b,))
        assert cur.fetchone()[0] == 90
