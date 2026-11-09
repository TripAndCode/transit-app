"""A live read binds the versions in force over the dates it reads, refuses a
read outside them, and its definition stamp names the version each day was
filed under."""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.live_filing import AS_INGESTED, Filing, refits
from pipeline.reports.filters import _dedup_cte_ch
from pipeline.reports.schedule_revision import ScheduleDays
from pipeline.static_archive import ScheduleJoin, Stamp
from pipeline.timetable_in_force import Segment

JOIN = ScheduleJoin(
    ["v1", "v2"],
    ["c1", "c2"],
    "",
    (Segment(date(2026, 9, 1), date(2026, 9, 30), "v1"), Segment(date(2026, 10, 1), date(2026, 12, 31), "v2")),
)


def test_a_filing_binds_only_the_versions_in_force_over_its_dates():
    params = JOIN.filing_params((date(2026, 10, 3), date(2026, 10, 9)))
    assert params == {
        "join_stems": ["v1", "v2"],
        "join_hashes": ["c1", "c2"],
        "force_firsts": [date(2026, 10, 1)],
        "force_lasts": [date(2026, 12, 31)],
        "force_stems": ["v2"],
        "force_contents": ["c2"],
    }


def test_a_read_outside_its_filings_dates_is_refused():
    filing = Filing(Stamp.REFIT, (date(2026, 10, 3), date(2026, 10, 9)), JOIN.filing_params((date(2026, 10, 3),) * 2))
    with pytest.raises(ValueError, match="reaches outside"):
        _dedup_cte_ch(RangeCtx(from_date=date(2026, 10, 2), to_date=date(2026, 10, 9)), filing)
    _dedup_cte_ch(RangeCtx(from_date=date(2026, 10, 3), to_date=date(2026, 10, 9)), filing)


def test_rows_read_as_ingested_bind_nothing_and_never_join_the_archive():
    sql, params = _dedup_cte_ch(RangeCtx(from_date=date(2026, 10, 3), to_date=date(2026, 10, 9)), AS_INGESTED)
    assert "static_schedule_archive" not in sql
    assert not [name for name in params if name.startswith(("force_", "join_"))]


def test_a_refit_read_joins_the_archive_and_tests_the_band_and_route_on_the_filed_row():
    ctx = RangeCtx(from_date=date(2026, 10, 3), to_date=date(2026, 10, 9), time_band="noon", routes=("R1",))
    between = (ctx.from_date, ctx.to_date)
    sql, params = _dedup_cte_ch(ctx, Filing(Stamp.REFIT, between, JOIN.filing_params(between)))
    # Narrowed first to the trips the band can keep, then joined on the hashed key.
    assert "u.trip_id IN (SELECT trip_id FROM static_schedule_archive" in sql
    filed = sql[sql.index("ANY LEFT JOIN") :]
    assert "archive_key" in filed
    assert "route_code IN {ch_routes:Array(String)}" in filed
    assert "{ch_tb_start:UInt32}" in filed
    assert params["force_stems"] == ["v2"]


def test_the_stamp_of_a_live_scan_names_the_version_each_day_was_filed_under():
    schedule = ScheduleDays(
        date(2026, 9, 30),
        date(2026, 10, 2),
        [
            (8, date(2026, 9, 29), "v1"),
            (8, date(2026, 9, 30), "v1"),
            # Recorded by an analyze that ran before v2's span was known.
            (8, date(2026, 10, 1), "v1"),
            (8, date(2026, 10, 2), "v1"),
            (9, date(2026, 10, 1), "other"),
        ],
    )
    filed = schedule.filed_under(8, JOIN.force)
    assert filed.rows == [
        (8, date(2026, 9, 29), "v1"),
        (8, date(2026, 9, 30), "v1"),
        (8, date(2026, 10, 1), "v2"),
        (8, date(2026, 10, 2), "v2"),
        (9, date(2026, 10, 1), "other"),
    ]
    assert filed.boundaries() == ["2026-10-01"]


def test_a_day_no_version_covers_keeps_the_version_analyze_recorded():
    schedule = ScheduleDays(date(2027, 1, 1), date(2027, 1, 1), [(8, date(2027, 1, 1), "ingested"), (8, None, None)])
    assert schedule.filed_under(8, JOIN.force).rows == schedule.rows


def test_a_name_stamped_only_on_days_its_content_is_in_force_needs_no_refit():
    assert not refits(JOIN, [("v1", date(2026, 9, 3), date(2026, 9, 30)), ("v2", date(2026, 10, 1), date(2026, 10, 9))])


def test_a_name_stamped_on_a_day_another_content_is_in_force_is_refit():
    # v2 loaded the day before it took effect, and stamped that day's polls.
    assert refits(JOIN, [("v1", date(2026, 9, 3), date(2026, 9, 29)), ("v2", date(2026, 9, 30), date(2026, 10, 9))])


def test_rows_without_a_name_or_with_one_the_archive_does_not_map_are_refit_where_a_version_is_in_force():
    assert refits(JOIN, [("", date(2026, 10, 2), date(2026, 10, 2))])
    assert refits(JOIN, [("unmapped", date(2026, 10, 2), date(2026, 10, 2))])
    assert not refits(JOIN, [("", date(2027, 1, 2), date(2027, 1, 9))])


def test_a_name_of_the_same_content_as_the_version_in_force_needs_no_refit():
    renamed = JOIN._replace(stems=["v1", "v2", "v2_renamed"], hashes=["c1", "c2", "c2"])
    assert not refits(renamed, [("v2_renamed", date(2026, 10, 1), date(2026, 10, 9))])


# A read the archive does not refile tests the route and the band on each row
# as stored, once, as an AND-chain the sort key can serve: no `OR route_code IS
# NULL`, which only a REFIT read needs to reach rows filing gives a route.
_BAND_ONLY = {"from_date": date(2026, 10, 3), "to_date": date(2026, 10, 9), "time_band": "noon"}
_ROUTED_BAND = RangeCtx(**_BAND_ONLY, routes=("R1",))


def test_a_shared_scan_read_as_ingested_tests_each_route_once_as_stored():
    from pipeline.reports.filters import _stop_visits_cte_ch

    for sql, _params in (_dedup_cte_ch(_ROUTED_BAND, AS_INGESTED), _stop_visits_cte_ch(_ROUTED_BAND, AS_INGESTED)):
        assert "route_code IS NULL" not in sql
        assert sql.count("route_code IN {ch_routes:Array(String)}") == 1
        assert sql.count("{ch_tb_start:UInt32}") == 1


def test_hand_written_reads_as_ingested_test_each_route_once_as_stored():
    from api.range import updates_filter_ch
    from api.routers.map import build_route_stop_profile_sql, build_route_trips_sql
    from pipeline.reports.delay_record import _record_filter, record_trips_sql
    from pipeline.reports.map import _route_shape_stats_dedup_sql, _route_shape_vote_dedup_sql

    where = updates_filter_ch(RangeCtx(from_date=date(2026, 10, 3), to_date=date(2026, 10, 9), time_band="noon"))
    for sql in (
        build_route_trips_sql("noon", stamp=Stamp.INGESTED)[0],
        build_route_stop_profile_sql("updates", Stamp.INGESTED),
        _route_shape_vote_dedup_sql(where, Stamp.INGESTED),
        _route_shape_stats_dedup_sql(where, "", Stamp.INGESTED),
    ):
        assert "route_code IS NULL" not in sql
        assert sql.count("u.route_code = {route:String}") == 1
    sql = record_trips_sql(_record_filter(date(2026, 10, 3), "R1"), "updates", Stamp.INGESTED)
    assert "route_code IS NULL" not in sql
    assert sql.count("route_code IN {ch_routes:Array(String)}") == 1


def test_a_shared_scan_read_as_ingested_is_the_scan_main_reads():
    from api.range import build_updates_filter_ch
    from pipeline.db import build_dedup_ch_sql

    where, params = build_updates_filter_ch(_ROUTED_BAND)
    assert _dedup_cte_ch(_ROUTED_BAND, AS_INGESTED) == (f"deduped AS ({build_dedup_ch_sql(extra_where=where)})", params)


def test_hand_written_refit_reads_also_read_rows_filing_gives_the_route():
    from api.range import updates_filter_ch
    from api.routers.map import _latest_route_observation_sql, build_route_stop_profile_sql, build_route_trips_sql
    from pipeline.reports.map import _route_shape_vote_dedup_sql

    where = updates_filter_ch(RangeCtx(**_BAND_ONLY), refit=True)
    for sql in (
        build_route_trips_sql("noon", stamp=Stamp.REFIT)[0],
        build_route_stop_profile_sql("updates", Stamp.REFIT),
        _route_shape_vote_dedup_sql(where, Stamp.REFIT),
        _latest_route_observation_sql("updates", Stamp.REFIT),
    ):
        assert "(u.route_code = {route:String} OR u.route_code IS NULL)" in sql
        assert "u.route_code = {route:String}" in sql[sql.index("ANY LEFT JOIN") :]
    assert "route_code IS NULL" not in _latest_route_observation_sql("updates", Stamp.INGESTED)


async def test_an_archive_state_read_across_a_load_is_not_kept():
    """A state read that began before a load and returned after it is the
    pre-load state: kept, every read for the TTL would see the archive as
    unmoved, and the compute caches would outlive the load."""
    import asyncio

    from pipeline import live_filing

    class _SlowCh:
        def __init__(self):
            self.reads = 0
            self.release = asyncio.Event()

        async def query(self, sql, parameters=None):
            self.reads += 1
            state = ((1, "before"), (1, "before")) if self.reads == 1 else ((2, "after"), (2, "after"))
            if self.reads == 1:
                await self.release.wait()

            class _Result:
                result_rows = [state]

            return _Result()

    live_filing.invalidate_schedule_joins()
    ch = _SlowCh()
    reading = asyncio.create_task(live_filing._archive_state(ch, 77))
    await asyncio.sleep(0)
    live_filing.invalidate_schedule_joins()
    ch.release.set()
    assert await reading == ((1, "before"), (1, "before"))
    assert await live_filing._archive_state(ch, 77) == ((2, "after"), (2, "after"))
    assert ch.reads == 2
    live_filing.invalidate_schedule_joins()
