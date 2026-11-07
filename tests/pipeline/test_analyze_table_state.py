"""analyze records a fingerprint per aggregate table and rebuilds each by its own."""

import dataclasses
import logging

import pytest

from pipeline import agg_registry
from pipeline.agg_registry import Timetable
from pipeline.analyze import analyze
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from tests.pipeline.test_analyze import _analyze, _seed_for_stop_agg, _seed_route_headway_schedule


def test_the_legacy_fingerprint_is_the_retired_one_byte_for_byte():
    from pipeline.analyze import _legacy_fingerprint

    parts = ["static_stop_times:3:42", "static_trips:1:7", "static_routes:1:9"]
    assert _legacy_fingerprint("static_join", parts) == (
        f"logic:7|clamp:{MAX_PLAUSIBLE_DELAY_SEC}|strategy:static_join|" + "|".join(parts)
    )
    assert _legacy_fingerprint(None, []) == f"logic:7|clamp:{MAX_PLAUSIBLE_DELAY_SEC}|strategy:None"


# Runs after a test's first call analyze() directly: the _analyze helper mirrors
# the Postgres `updates` seed into ClickHouse again, which would add rows.


def _scopes_logged(caplog) -> dict[str, str]:
    lines = [r.getMessage().strip() for r in caplog.records if r.getMessage().strip().startswith("scope ")]
    return {line.split()[1].rstrip(":"): line.split(":", 1)[1].strip() for line in lines}


def _states(pg_conn, agency_id) -> dict[str, str]:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT table_name, fingerprint FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        return dict(cur.fetchall())


def _built_at(pg_conn, agency_id) -> dict:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT table_name, built_at FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        return dict(cur.fetchall())


def _moved(before: dict, after: dict) -> set[str]:
    return {name for name in after if after[name] != before.get(name)}


def _count(pg_conn, table, agency_id) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE agency_id = %s", (agency_id,))
        return cur.fetchone()[0]


def test_a_run_records_every_tables_fingerprint(pg_conn, agency_id, ch_client):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    assert set(_states(pg_conn, agency_id)) == set(agg_registry.TABLES)


def test_a_run_with_nothing_new_rebuilds_nothing(pg_conn, agency_id, ch_client, caplog):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert set(_scopes_logged(caplog).values()) == {"unchanged"}


def test_a_run_with_nothing_new_keeps_every_build_time(pg_conn, agency_id, ch_client):
    """built_at says when a table's rows last changed, which a run that
    rebuilds nothing does not, so a report's stamp and its cached rows hold."""
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    before = _built_at(pg_conn, agency_id)
    analyze(agency_id, pg_conn, ch_client)
    assert _built_at(pg_conn, agency_id) == before


def test_a_run_moves_the_build_time_of_what_it_rebuilt_and_only_that(pg_conn, agency_id, ch_client, monkeypatch):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    before = _built_at(pg_conn, agency_id)
    bumped = dataclasses.replace(agg_registry.TABLES["agg_hour_daily"], version=9)
    monkeypatch.setitem(agg_registry.TABLES, "agg_hour_daily", bumped)
    analyze(agency_id, pg_conn, ch_client)
    assert _moved(before, _built_at(pg_conn, agency_id)) == {"agg_hour_daily"}


def test_a_run_over_changed_dates_moves_the_build_time_of_the_tables_it_rebuilt(pg_conn, agency_id, ch_client, caplog):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    before = _built_at(pg_conn, agency_id)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        # Mirroring the seed again adds rows to its dates, so the ledger reports them.
        _analyze(agency_id, pg_conn, ch_client)
    rebuilt = {name for name, scope in _scopes_logged(caplog).items() if scope != "unchanged"}
    assert rebuilt and rebuilt != set(agg_registry.TABLES)
    assert _moved(before, _built_at(pg_conn, agency_id)) == rebuilt


def _as_if_last_built_before_table_state(pg_conn, agency_id):
    """Leave the agency as the agency-wide scheme left it: no table states, and
    agg_meta holding the fingerprint that scheme recorded."""
    from pipeline.analyze import _legacy_fingerprint, _static_loaded, _static_parts

    with pg_conn.cursor() as cur:
        cur.execute("SELECT ingest_strategy FROM agencies WHERE agency_id = %s", (agency_id,))
        (strategy,) = cur.fetchone()
        legacy = _legacy_fingerprint(strategy, _static_parts(agency_id, pg_conn, _static_loaded(pg_conn, agency_id)))
        cur.execute("DELETE FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        cur.execute("UPDATE agg_meta SET static_fingerprint = %s WHERE agency_id = %s", (legacy, agency_id))
    pg_conn.commit()


def test_a_run_clears_the_agency_wide_fingerprint(pg_conn, agency_id, ch_client):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT static_fingerprint FROM agg_meta WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] is None


def test_states_lost_after_the_switch_rebuild_every_table(pg_conn, agency_id, ch_client, caplog):
    """Seeding vouches only for what the agency-wide scheme built. Once a run
    has recorded table states, losing them must rebuild, not re-seed."""
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM agg_table_state WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert set(_scopes_logged(caplog).values()) == {"every date (new)"}


def test_deploy_seeds_the_states_without_a_rebuild(pg_conn, agency_id, ch_client, caplog):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    _as_if_last_built_before_table_state(pg_conn, agency_id)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert any("seeded" in r.getMessage() for r in caplog.records)
    assert set(_scopes_logged(caplog).values()) == {"unchanged"}
    assert set(_states(pg_conn, agency_id)) == set(agg_registry.TABLES)


def test_without_a_matching_legacy_fingerprint_every_table_rebuilds(pg_conn, agency_id, ch_client, caplog):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM agg_table_state WHERE agency_id = %s", (agency_id,))
        cur.execute("UPDATE agg_meta SET static_fingerprint = 'logic:6|older code' WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert set(_scopes_logged(caplog).values()) == {"every date (new)"}


def test_a_changed_timetable_rebuilds_only_the_tables_that_read_it(pg_conn, agency_id, ch_client, caplog):
    """The agency_id fixture's ingest stamps no version, so its archive-joined
    tables join the loaded timetable and rebuild with it."""
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_stop_times SET stop_id = 's2' WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    logged = _scopes_logged(caplog)
    readers = {n for n, t in agg_registry.TABLES.items() if t.timetable is not Timetable.NONE}
    assert {n for n, s in logged.items() if s != "unchanged"} == readers


def test_a_stamping_agencys_changed_timetable_rebuilds_only_what_the_loaded_one_builds(
    pg_conn, agency_id, ch_client, caplog
):
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_stop_times SET stop_id = 's2' WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    logged = _scopes_logged(caplog)
    # _seed_for_stop_agg's rows carry no version, so their dates follow the
    # new loaded timetable in the archive-joined tables; nothing else moves.
    loaded = {n for n, t in agg_registry.TABLES.items() if t.timetable is Timetable.LOADED}
    archived = {n for n, t in agg_registry.TABLES.items() if t.timetable is Timetable.ARCHIVED}
    assert {n for n, s in logged.items() if s.startswith("every date")} == loaded | {"agg_stop_routes"}
    assert {n for n, s in logged.items() if s.endswith("date(s)")} == archived - {"agg_stop_routes"}


def test_a_version_bump_rebuilds_the_table_and_its_dependents(pg_conn, agency_id, ch_client, caplog, monkeypatch):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    stats_before = _count(pg_conn, "agg_route_stats", agency_id)
    bumped = dataclasses.replace(agg_registry.TABLES["route_delay_counts"], version=2)
    monkeypatch.setitem(agg_registry.TABLES, "route_delay_counts", bumped)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    rebuilt = {n for n, s in _scopes_logged(caplog).items() if s != "unchanged"}
    assert rebuilt == {"route_delay_counts", "agg_route_stats", "agg_route_hour", "agg_route_hour_dow"}
    assert stats_before > 0
    assert _count(pg_conn, "agg_route_stats", agency_id) == stats_before


def test_a_bump_and_changed_dates_rebuild_side_by_side(pg_conn, agency_id, ch_client, caplog, monkeypatch):
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    bumped = dataclasses.replace(agg_registry.TABLES["route_delay_counts"], version=2)
    monkeypatch.setitem(agg_registry.TABLES, "route_delay_counts", bumped)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        # Mirroring the seed again adds rows to its dates, so the ledger reports them.
        _analyze(agency_id, pg_conn, ch_client)
    logged = _scopes_logged(caplog)
    assert logged["route_delay_counts"] == "every date (fingerprint changed)"
    assert logged["agg_route_stats"] == "every date (depends on route_delay_counts)"
    assert logged["agg_route_daily"].endswith("date(s)")
    assert logged["agg_feed_health"].endswith("date(s)")


def test_a_skipped_table_keeps_its_rows(pg_conn, agency_id, ch_client):
    _seed_for_stop_agg(pg_conn, agency_id)
    _seed_route_headway_schedule(pg_conn, agency_id, "R_HW", "WD", "s1", ["08:00:00", "08:10:00", "08:20:00"])
    _analyze(agency_id, pg_conn, ch_client)
    before = {t: _count(pg_conn, t, agency_id) for t in ("agg_route_headway", "agg_feed_health", "agg_stop_daily")}
    assert all(before.values()), before
    analyze(agency_id, pg_conn, ch_client)
    assert {t: _count(pg_conn, t, agency_id) for t in before} == before


def test_a_failed_run_leaves_the_states_it_found(pg_conn, agency_id, ch_client, monkeypatch):
    import pipeline.analyze as analyze_module

    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    before = _states(pg_conn, agency_id)
    bumped = dataclasses.replace(agg_registry.TABLES["agg_hour_daily"], version=9)
    monkeypatch.setitem(agg_registry.TABLES, "agg_hour_daily", bumped)
    record = analyze_module._record_fingerprints

    def record_then_fail(*args, **kwargs):
        record(*args, **kwargs)
        raise RuntimeError("stop before commit")

    monkeypatch.setattr(analyze_module, "_record_fingerprints", record_then_fail)
    with pytest.raises(RuntimeError, match="stop before commit"):
        analyze(agency_id, pg_conn, ch_client)
    pg_conn.rollback()
    assert _states(pg_conn, agency_id) == before


def test_marking_for_full_rebuild_clears_the_states(pg_conn, agency_id, ch_client):
    from pipeline.analyze import mark_for_full_rebuild

    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    mark_for_full_rebuild(pg_conn, [agency_id])
    assert _states(pg_conn, agency_id) == {}


def _rewrite_to_the_parts_format(pg_conn, agency_id, strategy):
    """Rewrite the agency's states to the fingerprints its tables carried before
    the archive-joined tables stopped carrying the loaded timetable."""
    from pipeline.analyze import _parts_fingerprints
    from pipeline.static_content import static_parts

    previous = _parts_fingerprints(strategy, True, static_parts(agency_id, pg_conn, True))
    with pg_conn.cursor() as cur:
        for name, fingerprint in previous.items():
            cur.execute(
                "UPDATE agg_table_state SET fingerprint = %s WHERE agency_id = %s AND table_name = %s",
                (fingerprint, agency_id, name),
            )
    pg_conn.commit()


def test_fingerprints_recorded_before_the_archive_joins_carry_over_without_a_rebuild(
    pg_conn, agency_id, ch_client, caplog
):
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    current = _states(pg_conn, agency_id)
    _rewrite_to_the_parts_format(pg_conn, agency_id, "static_join")
    assert _states(pg_conn, agency_id) != current
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    assert set(_scopes_logged(caplog).values()) == {"unchanged"}
    assert _states(pg_conn, agency_id) == current


def test_a_carried_fingerprint_whose_timetable_changed_still_rebuilds(pg_conn, agency_id, ch_client, caplog):
    from pipeline.analyze import _PARTS_FINGERPRINT_TABLES
    from tests.pipeline.test_analyze import _set_ingest_strategy

    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    _seed_for_stop_agg(pg_conn, agency_id)
    _analyze(agency_id, pg_conn, ch_client)
    _rewrite_to_the_parts_format(pg_conn, agency_id, "static_join")
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_stop_times SET stop_id = 's2' WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze(agency_id, pg_conn, ch_client)
    rebuilt = {name for name, scope in _scopes_logged(caplog).items() if scope.startswith("every date")}
    assert rebuilt == set(_PARTS_FINGERPRINT_TABLES)
