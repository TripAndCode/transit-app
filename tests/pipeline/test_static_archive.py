"""The timetable archive keeps every loaded timetable once per content."""

import re

import pytest

from pipeline import ch_aggregates, static_archive
from pipeline.static_content import static_parts
from tests.pipeline.test_analyze import _seed_route_headway_schedule


def _seed(pg_conn, agency_id, stem="gtfs_static_20261025"):
    _seed_route_headway_schedule(pg_conn, agency_id, "R1", "WD", "s1", ["08:00:00", "08:10:00", "08:20:00"])
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_trips SET static_version_id = %s WHERE agency_id = %s", (stem, agency_id))
    pg_conn.commit()


def _archived(ch_client, agency_id):
    return ch_client.query(
        f"SELECT content_hash, count() FROM {static_archive.ARCHIVE} WHERE agency_id = {{a:Int32}} "
        "GROUP BY content_hash ORDER BY content_hash",
        parameters={"a": agency_id},
    ).result_rows


def _map(ch_client, agency_id):
    return ch_client.query(
        f"SELECT static_version_id, content_hash FROM {static_archive.VERSION_MAP} "
        "WHERE agency_id = {a:Int32} ORDER BY archived_at",
        parameters={"a": agency_id},
    ).result_rows


def _set_stem(pg_conn, agency_id, stem):
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_trips SET static_version_id = %s WHERE agency_id = %s", (stem, agency_id))
    pg_conn.commit()


def test_the_loaded_timetable_is_archived_under_its_content(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    key = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert key == static_archive.content_hash(static_parts(agency_id, pg_conn, True))
    assert _archived(ch_client, agency_id) == [(key, 3)]
    assert _map(ch_client, agency_id) == [("gtfs_static_20261025", key)]


def test_archiving_the_same_content_again_adds_nothing(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    key = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert static_archive.archive_loaded(pg_conn, ch_client, agency_id) == key
    assert _archived(ch_client, agency_id) == [(key, 3)]
    assert len(_map(ch_client, agency_id)) == 1


def test_a_new_stem_for_the_same_content_adds_a_map_row_only(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    key = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    _set_stem(pg_conn, agency_id, "gtfs_static_20261101")
    assert static_archive.archive_loaded(pg_conn, ch_client, agency_id) == key
    assert _archived(ch_client, agency_id) == [(key, 3)]
    assert _map(ch_client, agency_id) == [("gtfs_static_20261025", key), ("gtfs_static_20261101", key)]


def test_the_same_stem_with_new_content_maps_to_the_new_content(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    first = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE static_stop_times SET departure_time = '09:00:00' "
            "WHERE agency_id = %s AND trip_id = (SELECT min(trip_id) FROM static_stop_times WHERE agency_id = %s)",
            (agency_id, agency_id),
        )
    pg_conn.commit()
    second = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert second != first
    assert sorted(h for h, _ in _archived(ch_client, agency_id)) == sorted([first, second])
    assert _map(ch_client, agency_id)[-1] == ("gtfs_static_20261025", second)


def test_a_timetable_without_a_stem_is_archived_but_not_mapped(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    _set_stem(pg_conn, agency_id, None)
    key = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert _archived(ch_client, agency_id) == [(key, 3)]
    assert _map(ch_client, agency_id) == []


def test_nothing_loaded_archives_nothing(pg_conn, agency_id, ch_client):
    assert static_archive.archive_loaded(pg_conn, ch_client, agency_id) is None
    assert _archived(ch_client, agency_id) == []


def test_a_timetable_that_changes_mid_copy_is_not_archived(pg_conn, agency_id, ch_client, monkeypatch):
    _seed(pg_conn, agency_id)
    real = static_archive.static_parts
    calls = []

    def changing(agency, conn, has_static):
        calls.append(1)
        parts = real(agency, conn, has_static)
        return parts if len(calls) == 1 else [*parts, "static_routes:9:9"]

    monkeypatch.setattr(static_archive, "static_parts", changing)
    assert static_archive.archive_loaded(pg_conn, ch_client, agency_id) is None
    assert _archived(ch_client, agency_id) == []
    leftover = ch_client.query(
        "SELECT name FROM system.tables WHERE database = currentDatabase() "
        "AND startsWith(name, {archive:String}) AND match(name, {pattern:String})",
        parameters={"archive": static_archive.ARCHIVE, "pattern": ch_aggregates.WORK_TABLE_PATTERN},
    )
    assert leftover.result_rows == []


def test_each_attempt_works_in_its_own_table():
    assert static_archive._work_table(7, "a" * 32) != static_archive._work_table(7, "a" * 32)


def test_a_work_table_a_killed_copy_left_behind_matches_the_work_table_pattern():
    assert re.search(ch_aggregates.WORK_TABLE_PATTERN, static_archive._work_table(7, "ab12cd34" * 4))


def test_missing_tables_fail_closed_with_the_bootstrap_hint(ch_client):
    from db.clickhouse.bootstrap import apply_schema

    ch_client.command(f"DROP TABLE IF EXISTS {static_archive.VERSION_MAP}")
    try:
        with pytest.raises(RuntimeError, match="make ch-bootstrap"):
            static_archive.require_tables(ch_client)
    finally:
        apply_schema(ch_client)


def _zip(tmp_path, name, departure):
    from tests.fixtures.synthetic_gtfs import build_static_zip

    return build_static_zip(
        tmp_path,
        stops_rows=["S1,A,40.80,140.70", "S2,B,40.81,140.71"],
        trips_rows=["T1,R1,,"],
        routes_rows=["R1,1"],
        stop_times_rows=[f"T1,1,S1,{departure},{departure}", f"T1,2,S2,{departure},{departure}"],
        filename=f"{name}.zip",
    )


def _stop_times(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT departure_time FROM static_stop_times WHERE agency_id = %s ORDER BY stop_sequence", (agency_id,)
        )
        return [r[0] for r in cur.fetchall()]


def test_a_load_archives_the_timetable_it_replaces(pg_conn, agency_id, ch_client, tmp_path):
    from pipeline.static_loader import load_static

    load_static(_zip(tmp_path, "gtfs_static_20261025", "08:00:00"), agency_id, pg_conn, ch_client=ch_client)
    outgoing = static_archive.content_hash(static_parts(agency_id, pg_conn, True))
    load_static(_zip(tmp_path, "gtfs_static_20261028", "09:00:00"), agency_id, pg_conn, ch_client=ch_client)
    assert _archived(ch_client, agency_id) == [(outgoing, 2)]
    assert _map(ch_client, agency_id) == [("gtfs_static_20261025", outgoing)]
    assert _stop_times(pg_conn, agency_id) == ["09:00:00", "09:00:00"]


def test_a_first_load_archives_nothing(pg_conn, agency_id, ch_client, tmp_path):
    from pipeline.static_loader import load_static

    load_static(_zip(tmp_path, "gtfs_static_20261025", "08:00:00"), agency_id, pg_conn, ch_client=ch_client)
    assert _archived(ch_client, agency_id) == []


def test_a_failed_archive_stops_the_load_before_anything_is_replaced(
    pg_conn, agency_id, ch_client, tmp_path, monkeypatch
):
    from pipeline.static_loader import load_static

    load_static(_zip(tmp_path, "gtfs_static_20261025", "08:00:00"), agency_id, pg_conn, ch_client=ch_client)

    def down(*args, **kwargs):
        raise RuntimeError("archive down")

    monkeypatch.setattr(static_archive, "archive_loaded", down)
    with pytest.raises(RuntimeError, match="archive down"):
        load_static(_zip(tmp_path, "gtfs_static_20261028", "09:00:00"), agency_id, pg_conn, ch_client=ch_client)
    pg_conn.rollback()
    assert _stop_times(pg_conn, agency_id) == ["08:00:00", "08:00:00"]


def test_a_load_without_the_archive_tables_fails_closed(pg_conn, agency_id, ch_client, tmp_path):
    from db.clickhouse.bootstrap import apply_schema
    from pipeline.static_loader import load_static

    load_static(_zip(tmp_path, "gtfs_static_20261025", "08:00:00"), agency_id, pg_conn, ch_client=ch_client)
    ch_client.command(f"DROP TABLE IF EXISTS {static_archive.VERSION_MAP}")
    try:
        with pytest.raises(RuntimeError, match="make ch-bootstrap"):
            load_static(_zip(tmp_path, "gtfs_static_20261028", "09:00:00"), agency_id, pg_conn, ch_client=ch_client)
        pg_conn.rollback()
        assert _stop_times(pg_conn, agency_id) == ["08:00:00", "08:00:00"]
    finally:
        apply_schema(ch_client)


def test_analyze_archives_the_loaded_timetable(pg_conn, agency_id, ch_client):
    from tests.pipeline.test_analyze import _analyze, _seed_for_stop_agg

    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    key = static_archive.content_hash(static_parts(agency_id, pg_conn, True))
    assert [h for h, _ in _archived(ch_client, agency_id)] == [key]


def test_analyze_refuses_without_the_archive_tables(pg_conn, agency_id, ch_client):
    from db.clickhouse.bootstrap import apply_schema
    from tests.pipeline.test_analyze import _analyze, _seed_for_stop_agg

    _seed_for_stop_agg(pg_conn, agency_id)
    ch_client.command(f"DROP TABLE IF EXISTS {static_archive.ARCHIVE}")
    try:
        with pytest.raises(RuntimeError, match="make ch-bootstrap"):
            _analyze(agency_id, pg_conn, ch_client)
    finally:
        pg_conn.rollback()
        apply_schema(ch_client)


def test_a_stem_read_after_a_concurrent_load_is_not_mapped_to_the_old_content(
    pg_conn, agency_id, ch_client, monkeypatch
):
    """A load that commits a new name and content between the key and the
    name read must not leave the new name pointing at the old content."""
    _seed(pg_conn, agency_id)
    parts = static_parts(agency_id, pg_conn, True)
    key = static_archive.archive_loaded(pg_conn, ch_client, agency_id, parts)
    monkeypatch.setattr(static_archive, "loaded_stem", lambda conn, agency: "gtfs_static_20261101")
    monkeypatch.setattr(static_archive, "static_parts", lambda agency, conn, has_static: [*parts, "static_routes:9:9"])
    assert static_archive.archive_loaded(pg_conn, ch_client, agency_id, parts) == key
    assert ("gtfs_static_20261101", key) not in _map(ch_client, agency_id)
