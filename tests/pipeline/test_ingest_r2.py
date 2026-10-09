import io
import tarfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from pipeline import archive_store
from pipeline.archive_store import ingest_from_store

_STATIC_ZIP = (Path(__file__).parent.parent / "fixtures" / "static_no_shapes.zip").read_bytes()


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

    def list_keys(self, prefix, *, start_after=None):
        return sorted(key for key in self.objects if key.startswith(prefix) and key > (start_after or ""))

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
        first = list(ingest_from_store(store, agency_id, pg_conn, ch_client, **window))
        again = list(ingest_from_store(store, agency_id, pg_conn, ch_client, **window))

    assert first == [(date(2026, 4, 1), 2), (date(2026, 4, 2), 1)]
    assert again == [(date(2026, 4, 1), 0), (date(2026, 4, 2), 0)]
    assert _files(ch_client, agency_id) == [
        "20260401/TripUpdate_113700.pb",
        "20260401/TripUpdate_113800.pb",
        "20260402/TripUpdate_090000.pb",
    ]
    assert store.downloads == [f"rt/{agency_id}/20260401.tar.gz", f"rt/{agency_id}/20260402.tar.gz"] * 2


def _recording(events):
    real_load, real_ingest = archive_store.load_static, archive_store.ingest

    def load(path, agency_id, conn):
        events.append(("static", Path(path).stem))
        real_load(path, agency_id, conn)

    def ingest(folder, agency_id, conn, ch_client):
        events.append(("rt", next(Path(folder).glob("*.tar.gz")).name[:8]))
        return real_ingest(folder, agency_id, conn, ch_client)

    return patch.multiple(archive_store, load_static=load, ingest=ingest)


def _loaded_version(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT static_version_id FROM static_trips WHERE agency_id = %s", (agency_id,))
        return [r[0] for r in cur.fetchall()]


def test_each_day_is_read_against_the_latest_static_version_named_on_or_before_it(pg_conn, ch_client, agency_id):
    store = _Store(
        {
            f"static/{agency_id}/gtfs_static_20260401.zip": _STATIC_ZIP,
            f"static/{agency_id}/gtfs_static_20260402.zip": _STATIC_ZIP,
            f"rt/{agency_id}/20260331.tar.gz": _tarball("20260331", "120000"),
            f"rt/{agency_id}/20260401.tar.gz": _tarball("20260401", "113700"),
            f"rt/{agency_id}/20260402.tar.gz": _tarball("20260402", "090000"),
            f"rt/{agency_id}/20260403.tar.gz": _tarball("20260403", "090000"),
        }
    )
    events: list[tuple[str, str]] = []

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=_row), _recording(events):
        list(ingest_from_store(store, agency_id, pg_conn, ch_client))

    assert events == [
        ("static", "gtfs_static_20260401"),
        ("rt", "20260331"),
        ("rt", "20260401"),
        ("static", "gtfs_static_20260402"),
        ("rt", "20260402"),
        ("rt", "20260403"),
    ]
    assert _loaded_version(pg_conn, agency_id) == ["gtfs_static_20260402"]


def test_static_never_moves_back_to_an_older_version(pg_conn, ch_client, agency_id):
    store = _Store(
        {
            f"static/{agency_id}/gtfs_static_20260401.zip": _STATIC_ZIP,
            f"static/{agency_id}/gtfs_static_20260402.zip": _STATIC_ZIP,
            f"rt/{agency_id}/20260401.tar.gz": _tarball("20260401", "113700"),
            f"rt/{agency_id}/20260402.tar.gz": _tarball("20260402", "090000"),
        }
    )
    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=_row):
        list(ingest_from_store(store, agency_id, pg_conn, ch_client))
    events: list[tuple[str, str]] = []

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=_row), _recording(events):
        list(ingest_from_store(store, agency_id, pg_conn, ch_client, since=date(2026, 4, 1)))

    assert events == [("rt", "20260401"), ("rt", "20260402")]
    assert _loaded_version(pg_conn, agency_id) == ["gtfs_static_20260402"]
