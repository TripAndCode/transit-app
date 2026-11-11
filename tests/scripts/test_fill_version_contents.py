"""scripts/fill_version_contents.py fills the Postgres mirror from every name ClickHouse's version map holds."""

from datetime import datetime, timezone

from pipeline import static_archive
from scripts import fill_version_contents as fill

_EARLY = datetime(2026, 10, 1, tzinfo=timezone.utc)
_LATE = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _map(ch_client, rows):
    ch_client.insert(
        static_archive.VERSION_MAP, rows, column_names=["agency_id", "static_version_id", "content_hash", "archived_at"]
    )


def _mirrored(pg_conn, agency_id) -> dict:
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT static_version_id, content_hash FROM {static_archive.MIRROR} WHERE agency_id = %s",
            (agency_id,),
        )
        return dict(cur.fetchall())


def test_the_dry_run_counts_what_it_would_write_and_writes_nothing(pg_conn, agency_id, ch_client, capsys):
    _map(ch_client, [(agency_id, "gtfs_static_20261001", "a" * 32, _EARLY)])
    assert fill.main([]) == 0
    out = capsys.readouterr().out
    assert f"agency {agency_id}: 1 name(s) mapped, 0 mirrored, 1 to write" in out
    assert "dry run: 1 row(s) would be written; --apply writes them" in out
    assert _mirrored(pg_conn, agency_id) == {}


def test_apply_mirrors_each_names_latest_content_and_a_second_run_writes_nothing(pg_conn, agency_id, ch_client, capsys):
    _map(
        ch_client,
        [
            (agency_id, "gtfs_static_20261001", "a" * 32, _EARLY),
            (agency_id, "gtfs_static_20261001", "b" * 32, _LATE),
            (agency_id, "gtfs_static_20261101", "c" * 32, _EARLY),
        ],
    )
    assert fill.main(["--apply"]) == 0
    assert "wrote 2 row(s)" in capsys.readouterr().out
    assert _mirrored(pg_conn, agency_id) == {"gtfs_static_20261001": "b" * 32, "gtfs_static_20261101": "c" * 32}
    assert fill.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert f"agency {agency_id}: 2 name(s) mapped, 2 mirrored, 0 to write" in out
    assert "nothing to write" in out


def test_a_mirror_row_behind_the_map_is_brought_up_to_its_latest_content(pg_conn, agency_id, ch_client, capsys):
    static_archive.mirror(pg_conn, [(agency_id, "gtfs_static_20261001", "a" * 32, _EARLY)])
    _map(
        ch_client,
        [(agency_id, "gtfs_static_20261001", "a" * 32, _EARLY), (agency_id, "gtfs_static_20261001", "b" * 32, _LATE)],
    )
    assert fill.main(["--apply"]) == 0
    assert f"agency {agency_id}: 1 name(s) mapped, 0 mirrored, 1 to write" in capsys.readouterr().out
    assert _mirrored(pg_conn, agency_id) == {"gtfs_static_20261001": "b" * 32}


def test_a_name_of_an_agency_postgres_does_not_hold_is_reported_and_skipped(pg_conn, agency_id, ch_client, capsys):
    gone = agency_id + 1000
    _map(
        ch_client,
        [(gone, "gtfs_static_20261001", "a" * 32, _EARLY), (agency_id, "gtfs_static_20261001", "b" * 32, _EARLY)],
    )
    assert fill.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert f"agency {gone}: 1 name(s) mapped, not in Postgres; skipped" in out
    assert "wrote 1 row(s)" in out
    assert _mirrored(pg_conn, agency_id) == {"gtfs_static_20261001": "b" * 32}


def test_a_postgres_without_the_mirror_is_refused(pg_conn, agency_id, ch_client, capsys, monkeypatch):
    _map(ch_client, [(agency_id, "gtfs_static_20261001", "a" * 32, _EARLY)])
    monkeypatch.setattr(static_archive, "MIRROR", "static_version_contents_absent")
    assert fill.main(["--apply"]) == 1
    assert "make migrate" in capsys.readouterr().err
