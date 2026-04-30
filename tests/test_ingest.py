# tests/test_ingest.py
import pathlib
import tarfile
import io
from unittest.mock import patch
from pipeline.ingest import ingest, parse_trip_id


def test_parse_trip_id_weekday():
    service, time, route = parse_trip_id("平日_11時37分_系統44372")
    assert service == "平日"
    assert time == "11:37"
    assert route == "44372"


def test_parse_trip_id_invalid():
    service, time, route = parse_trip_id("invalid")
    assert service is None


def test_ingest_creates_rows(pg_conn, agency_id, tmp_path):
    """Ingest a fake tarball and verify rows land in updates with correct agency_id."""
    fake_row = (
        "20260401/TripUpdate_113700.pb",  # file_name
        "2026-04-01T11:37:00",            # captured_at
        "平日_11時37分_系統44372",          # trip_id
        "平日",                            # service_type
        "11:37",                           # scheduled_time
        "44372",                           # route_code
        1,                                 # stop_sequence
        None,                              # stop_id
        None, None,                        # arr_delay, arr_time
        120,                               # dep_delay (seconds)
        None,                              # dep_time
    )
    with patch("pipeline.ingest.parse_pb", return_value=[fake_row]):
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


def test_ingest_dedup_skips_seen_files(pg_conn, agency_id, tmp_path):
    """Running ingest twice on the same folder inserts 0 rows the second time."""
    fake_row = (
        "20260401/TripUpdate_113700.pb", "2026-04-01T11:37:00",
        "平日_11時37分_系統44372", "平日", "11:37", "44372", 1,
        None, None, None, 120, None,
    )
    with patch("pipeline.ingest.parse_pb", return_value=[fake_row]):
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
        "20260401/TripUpdate_113700.pb", "2026-04-01T11:37:00",
        "平日_11時37分_系統44372", "平日", "11:37", "44372", 1,
        None, None, None, 60, None,
    )
    fake_row_b = (
        "20260401/TripUpdate_113700.pb", "2026-04-01T11:37:00",
        "平日_11時37分_系統44372", "平日", "11:37", "44372", 1,
        None, None, None, 90, None,
    )
    pb_data = b"\x00"

    def make_tgz(path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="20260401/TripUpdate_113700.pb")
            info.size = 1
            tf.addfile(info, io.BytesIO(pb_data))
        path.write_bytes(buf.getvalue())

    dir_a = tmp_path / "a"; dir_a.mkdir()
    dir_b = tmp_path / "b"; dir_b.mkdir()
    make_tgz(dir_a / "20260401.tar.gz")
    make_tgz(dir_b / "20260401.tar.gz")

    with patch("pipeline.ingest.parse_pb", return_value=[fake_row_a]):
        ingest(str(dir_a), aid_a, pg_conn)
    with patch("pipeline.ingest.parse_pb", return_value=[fake_row_b]):
        ingest(str(dir_b), aid_b, pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT dep_delay FROM updates WHERE agency_id = %s", (aid_a,))
        assert cur.fetchone()[0] == 60
        cur.execute("SELECT dep_delay FROM updates WHERE agency_id = %s", (aid_b,))
        assert cur.fetchone()[0] == 90
