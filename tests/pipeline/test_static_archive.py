"""The timetable archive keeps every loaded timetable once per content."""

import re
from datetime import date, datetime, timezone

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
    assert static_archive._work_table(static_archive.ARCHIVE, 7, "a" * 32) != static_archive._work_table(
        static_archive.ARCHIVE, 7, "a" * 32
    )


def test_a_work_table_a_killed_copy_left_behind_matches_the_work_table_pattern():
    assert re.search(
        ch_aggregates.WORK_TABLE_PATTERN, static_archive._work_table(static_archive.SCHEDULE, 7, "ab12cd34" * 4)
    )


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


def test_the_join_maps_each_name_to_its_latest_content(ch_client):
    rows = [
        (7, "gtfs_static_20261001", "a" * 32, datetime(2026, 10, 1, tzinfo=timezone.utc)),
        (7, "gtfs_static_20261001", "b" * 32, datetime(2026, 10, 2, tzinfo=timezone.utc)),
        (7, "gtfs_static_20261101", "c" * 32, datetime(2026, 11, 1, tzinfo=timezone.utc)),
        (8, "gtfs_static_20261001", "d" * 32, datetime(2026, 10, 3, tzinfo=timezone.utc)),
    ]
    ch_client.insert(
        static_archive.VERSION_MAP, rows, column_names=["agency_id", "static_version_id", "content_hash", "archived_at"]
    )
    expected = static_archive.ScheduleJoin(
        ["gtfs_static_20261001", "gtfs_static_20261101"], ["b" * 32, "c" * 32], "c" * 32
    )
    assert static_archive.schedule_join(ch_client, 7, "c" * 32) == expected


def test_analyze_refuses_when_the_loaded_timetable_is_not_archived(pg_conn, agency_id, ch_client, monkeypatch):
    from tests.pipeline.test_analyze import _analyze, _seed_for_stop_agg

    _seed_for_stop_agg(pg_conn, agency_id)
    monkeypatch.setattr(static_archive, "archive_loaded", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="not archived"):
        _analyze(agency_id, pg_conn, ch_client)
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == 0


def _give_every_table_a_state(pg_conn, agency_id):
    from pipeline import agg_registry

    with pg_conn.cursor() as cur:
        for name in agg_registry.TABLES:
            cur.execute(
                "INSERT INTO agg_table_state (agency_id, table_name, fingerprint) VALUES (%s, %s, 'x')",
                (agency_id, name),
            )
    pg_conn.commit()


def _state_names(pg_conn, agency_id):
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT table_name FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        return {row[0] for row in cur.fetchall()}


def _change_the_content(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE static_stop_times SET departure_time = '09:00:00' "
            "WHERE agency_id = %s AND trip_id = (SELECT min(trip_id) FROM static_stop_times WHERE agency_id = %s)",
            (agency_id, agency_id),
        )
    pg_conn.commit()


def _archive_joined():
    """The tables that read the archive: by each row's version, or by the version in force on its service date."""
    from pipeline import agg_registry
    from pipeline.agg_registry import Timetable

    return {
        name for name, table in agg_registry.TABLES.items() if table.timetable is Timetable.ARCHIVED or table.in_force
    }


def test_a_name_remapped_to_new_content_clears_the_archive_joined_states(pg_conn, agency_id, ch_client):
    from pipeline import agg_registry

    _seed(pg_conn, agency_id)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    _give_every_table_a_state(pg_conn, agency_id)
    _change_the_content(pg_conn, agency_id)
    second = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert _state_names(pg_conn, agency_id) == set(agg_registry.TABLES) - _archive_joined()
    assert _map(ch_client, agency_id)[-1] == ("gtfs_static_20261025", second)


def test_a_first_mapping_clears_nothing(pg_conn, agency_id, ch_client):
    from pipeline import agg_registry

    _seed(pg_conn, agency_id)
    _give_every_table_a_state(pg_conn, agency_id)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert _state_names(pg_conn, agency_id) == set(agg_registry.TABLES)


def test_the_states_are_cleared_before_the_name_is_remapped(pg_conn, agency_id, ch_client, monkeypatch):
    """So a process that dies before the map row lands still rebuilds the archive-joined tables."""
    from pipeline import agg_registry

    _seed(pg_conn, agency_id)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    _give_every_table_a_state(pg_conn, agency_id)
    _change_the_content(pg_conn, agency_id)
    real_insert = ch_client.insert

    def insert(table, *args, **kwargs):
        if table == static_archive.VERSION_MAP:
            raise RuntimeError("injected failure before the map row")
        return real_insert(table, *args, **kwargs)

    monkeypatch.setattr(ch_client, "insert", insert)
    with pytest.raises(RuntimeError, match="injected failure"):
        static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert _state_names(pg_conn, agency_id) == set(agg_registry.TABLES) - _archive_joined()


def test_a_names_span_is_its_latest_recording_both_ends_together(ch_client, agency_id):
    from datetime import date

    static_archive.record_span(ch_client, agency_id, "gtfs_static_20261025", (date(2026, 10, 25), date(2027, 4, 25)))
    static_archive.record_span(ch_client, agency_id, "gtfs_static_20261025", (date(2026, 10, 20), date(2026, 12, 31)))
    assert static_archive.spans(ch_client, agency_id) == {
        "gtfs_static_20261025": (date(2026, 10, 20), date(2026, 12, 31))
    }


# The Postgres mirror of the version map, which the stamp reads each name's content from.


def _mirrored(pg_conn, agency_id):
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT static_version_id, content_hash, archived_at FROM {static_archive.MIRROR} "
            "WHERE agency_id = %s ORDER BY static_version_id",
            (agency_id,),
        )
        return cur.fetchall()


def _mapped_at(ch_client, agency_id):
    rows = ch_client.query(
        f"SELECT static_version_id, content_hash, archived_at FROM {static_archive.VERSION_MAP} "
        "WHERE agency_id = {a:Int32} ORDER BY static_version_id, archived_at",
        parameters={"a": agency_id},
    ).result_rows
    return [(stem, content, at if at.tzinfo else at.replace(tzinfo=timezone.utc)) for stem, content, at in rows]


def test_a_mapped_name_is_mirrored_at_the_maps_own_time(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    key = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    mapped = _mapped_at(ch_client, agency_id)
    assert [(stem, content) for stem, content, _ in mapped] == [("gtfs_static_20261025", key)]
    assert _mirrored(pg_conn, agency_id) == mapped


def test_a_new_name_for_the_same_content_is_mirrored_beside_the_old_one(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    key = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    _set_stem(pg_conn, agency_id, "gtfs_static_20261101")
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert [(stem, content) for stem, content, _ in _mirrored(pg_conn, agency_id)] == [
        ("gtfs_static_20261025", key),
        ("gtfs_static_20261101", key),
    ]


def test_a_name_remapped_to_new_content_mirrors_the_new_content(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    first = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    _change_the_content(pg_conn, agency_id)
    second = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert second != first
    assert _mirrored(pg_conn, agency_id) == [_mapped_at(ch_client, agency_id)[-1]]
    assert _mirrored(pg_conn, agency_id)[0][1] == second


def test_a_name_the_map_holds_and_the_mirror_lacks_is_mirrored_when_archived_again(pg_conn, agency_id, ch_client):
    """A name mapped before the mirror existed reaches it the next time its
    timetable is archived, at the map's own time, and the map gains no row."""
    _seed(pg_conn, agency_id)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    with pg_conn.cursor() as cur:
        cur.execute(f"DELETE FROM {static_archive.MIRROR} WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert _mirrored(pg_conn, agency_id) == _mapped_at(ch_client, agency_id)
    assert len(_mapped_at(ch_client, agency_id)) == 1


def test_a_timetable_without_a_stem_mirrors_nothing(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id)
    _set_stem(pg_conn, agency_id, None)
    static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert _mirrored(pg_conn, agency_id) == []


def test_a_map_row_that_never_lands_is_never_mirrored(pg_conn, agency_id, ch_client, monkeypatch):
    """The mirror never names a content the map does not: it is written after the map row."""
    _seed(pg_conn, agency_id)
    real_insert = ch_client.insert

    def insert(table, *args, **kwargs):
        if table == static_archive.VERSION_MAP:
            raise RuntimeError("injected failure before the map row")
        return real_insert(table, *args, **kwargs)

    monkeypatch.setattr(ch_client, "insert", insert)
    with pytest.raises(RuntimeError, match="injected failure"):
        static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert _mirrored(pg_conn, agency_id) == []


def test_the_mirror_never_moves_back_to_an_older_mapping(pg_conn, agency_id):
    newer = datetime(2026, 10, 2, tzinfo=timezone.utc)
    older = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert static_archive.mirror(pg_conn, [(agency_id, "gtfs_static_20261001", "b" * 32, newer)]) == 1
    assert static_archive.mirror(pg_conn, [(agency_id, "gtfs_static_20261001", "a" * 32, older)]) == 0
    assert static_archive.mirror(pg_conn, [(agency_id, "gtfs_static_20261001", "b" * 32, newer)]) == 0
    assert _mirrored(pg_conn, agency_id) == [("gtfs_static_20261001", "b" * 32, newer)]


def test_a_load_mirrors_the_timetable_it_replaces(pg_conn, agency_id, ch_client, tmp_path):
    from pipeline.static_loader import load_static

    load_static(_zip(tmp_path, "gtfs_static_20261025", "08:00:00"), agency_id, pg_conn, ch_client=ch_client)
    outgoing = static_archive.content_hash(static_parts(agency_id, pg_conn, True))
    load_static(_zip(tmp_path, "gtfs_static_20261028", "09:00:00"), agency_id, pg_conn, ch_client=ch_client)
    assert [(stem, content) for stem, content, _ in _mirrored(pg_conn, agency_id)] == [
        ("gtfs_static_20261025", outgoing)
    ]


def test_a_load_without_the_mirror_fails_closed(pg_conn, agency_id, ch_client, tmp_path, monkeypatch):
    """Before anything is mapped or replaced, so the map never runs ahead of a mirror that cannot follow."""
    from pipeline.static_loader import load_static

    load_static(_zip(tmp_path, "gtfs_static_20261025", "08:00:00"), agency_id, pg_conn, ch_client=ch_client)
    monkeypatch.setattr(static_archive, "MIRROR", "static_version_contents_absent")
    with pytest.raises(RuntimeError, match="make migrate"):
        load_static(_zip(tmp_path, "gtfs_static_20261028", "09:00:00"), agency_id, pg_conn, ch_client=ch_client)
    pg_conn.rollback()
    assert _stop_times(pg_conn, agency_id) == ["08:00:00", "08:00:00"]
    assert _map(ch_client, agency_id) == []


def test_analyze_mirrors_the_loaded_timetable(pg_conn, agency_id, ch_client):
    from tests.pipeline.test_analyze import _analyze

    _seed(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    key = static_archive.content_hash(static_parts(agency_id, pg_conn, True))
    assert [(stem, content) for stem, content, _ in _mirrored(pg_conn, agency_id)] == [("gtfs_static_20261025", key)]


def test_analyze_refuses_without_the_mirror(pg_conn, agency_id, ch_client, monkeypatch):
    from tests.pipeline.test_analyze import _analyze

    _seed(pg_conn, agency_id)
    monkeypatch.setattr(static_archive, "MIRROR", "static_version_contents_absent")
    with pytest.raises(RuntimeError, match="make migrate"):
        _analyze(agency_id, pg_conn, ch_client)
    pg_conn.rollback()
    assert _map(ch_client, agency_id) == []


async def _stamped_content(aconn, agency_id, stem, *, live=False):
    """The content hash a stamp names *stem* by, on a day it was in force, beside aggregates or a live scan."""
    from pipeline.reports.definition import resolve_definition_meta, stamp_build
    from pipeline.reports.schedule_revision import ScheduleDays

    day = date(2026, 10, 25)
    meta = resolve_definition_meta("ranking", None, None, live=live)
    meta = await stamp_build(aconn, meta, agency_id, ScheduleDays(day, day, [(agency_id, day, stem)]))
    return meta.timetable_versions[0].content_hash


async def test_a_remapped_name_is_named_by_its_new_content_beside_aggregates_only_once_they_are_rebuilt(
    pg_conn, aconn, agency_id, ch_client
):
    """Aggregates built before a name was remapped hold figures of its old
    content, so the stamp beside them names no content until analyze rebuilds
    them under the new one; a live scan joins the new content at once."""
    from tests.pipeline.test_analyze import _analyze

    _seed(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    first = static_archive.content_hash(static_parts(agency_id, pg_conn, True))
    assert await _stamped_content(aconn, agency_id, "gtfs_static_20261025") == first[:8]

    _change_the_content(pg_conn, agency_id)
    second = static_archive.archive_loaded(pg_conn, ch_client, agency_id)
    assert second != first
    assert await _stamped_content(aconn, agency_id, "gtfs_static_20261025") is None
    assert await _stamped_content(aconn, agency_id, "gtfs_static_20261025", live=True) == second[:8]

    _analyze(agency_id, pg_conn, ch_client)
    assert await _stamped_content(aconn, agency_id, "gtfs_static_20261025") == second[:8]
