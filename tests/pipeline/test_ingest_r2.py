import io
import tarfile
from datetime import date
from unittest.mock import patch

from pipeline.archive_store import ingest_from_store


def _tarball(day: str, *times: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for t in times:
            info = tarfile.TarInfo(name=f"{day}/TripUpdate_{t}.pb")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"\x00"))
    return buf.getvalue()


class _Store:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.downloads: list[str] = []

    def list_keys(self, prefix):
        return sorted(key for key in self.objects if key.startswith(prefix))

    def download(self, key, dest):
        self.downloads.append(key)
        dest.write_bytes(self.objects[key])


def _row(raw, captured_at, file_name, agency_id, conn):
    return [(file_name, captured_at, "平日_11時37分_系統44372", "平日", "11:37", "44372", 1, 60)]


def _files(ch_client, agency_id):
    return [
        r[0]
        for r in ch_client.query(
            "SELECT DISTINCT file_name FROM updates WHERE agency_id = {agency_id:UInt16} ORDER BY file_name",
            parameters={"agency_id": agency_id},
        ).result_rows
    ]


def test_ingest_from_store_reads_each_archived_day_in_the_range_and_a_rerun_writes_nothing(
    pg_conn, ch_client, agency_id
):
    store = _Store(
        {
            f"rt/{agency_id}/20260331.tar.gz": _tarball("20260331", "120000"),
            f"rt/{agency_id}/20260401.tar.gz": _tarball("20260401", "113700", "113800"),
            f"rt/{agency_id}/20260402.tar.gz": _tarball("20260402", "090000"),
        }
    )
    window = {"since": date(2026, 4, 1), "until": date(2026, 4, 2)}

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=_row):
        first = ingest_from_store(store, agency_id, pg_conn, ch_client, **window)
        again = ingest_from_store(store, agency_id, pg_conn, ch_client, **window)

    assert first == 3
    assert again == 0
    assert _files(ch_client, agency_id) == [
        "20260401/TripUpdate_113700.pb",
        "20260401/TripUpdate_113800.pb",
        "20260402/TripUpdate_090000.pb",
    ]
    assert store.downloads == [f"rt/{agency_id}/20260401.tar.gz", f"rt/{agency_id}/20260402.tar.gz"] * 2
