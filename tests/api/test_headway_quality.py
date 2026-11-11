"""API tests for GET /api/{agency_id}/headway_quality.

Seeds `agg_route_headway`/`agg_route_headway_daily` directly via asyncpg
(like tests/api/test_forecast_overview_endpoint.py) rather than driving the
full ClickHouse-backed `analyze()` pipeline -- this endpoint only ever reads
those two Postgres aggregates, so exercising `pipeline.analyze`'s own
population of them is `tests/pipeline/test_analyze.py`'s job, not this
file's.
"""

import os
from datetime import date

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from pipeline.db import DEFAULT_DATABASE_URL
from tests.conftest import _test_pool, confirm_rt_field_coverage

DATABASE_URL = os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


# A day's scheduled headways, read from the timetable in force that day:
# (median, high-frequency, sum, sum of squares). Ten even gaps, so the
# scheduled mean wait is half the headway.
_EVERY_8_MIN = (480.0, True, 4800.0, 2304000.0)
_EVERY_30_MIN = (1800.0, False, 18000.0, 32400000.0)
# A day whose timetable keeps no scheduled headways, and so is not judged.
_NOT_KEPT = (None, None, None, None)


async def _insert_days(pool, rows) -> None:
    await pool.executemany(
        "INSERT INTO agg_route_headway_daily "
        "(agency_id, route_code, date, actual_headway_median_sec, actual_samples, "
        " actual_headway_sum_sec, actual_headway_sumsq_sec2, long_gap_count, scheduled_headway_median_sec, "
        " is_high_frequency, scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
        rows,
    )


@pytest.fixture
async def headway_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES ($1, $2, 'static_join') "
        "RETURNING agency_id",
        "Headway Quality Test Agency",
        "http://headway-quality-test.example.com",
    )
    aid = row["agency_id"]
    # compute_headway_quality gates on pipeline.strategies.static_join's
    # RT_INGEST_STRATEGIES ∩ live-probe-verdict check
    # (rt_field_coverage_confirmed) -- a freshly-inserted test agency has no
    # rows in rt_field_coverage_probes, so record a verdict for just this
    # fixture's agency to exercise the normal "available" path;
    # test_headway_quality_unconfirmed_static_join_agency_returns_no_rows
    # below covers the gate itself.
    await confirm_rt_field_coverage(pool, aid)

    await pool.executemany(
        "INSERT INTO agg_route_headway "
        "(agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, is_high_frequency) "
        "VALUES ($1,$2,$3,$4,$5)",
        [
            # Perfectly regular 8-minute (480s) scheduled headway -> mean
            # wait = headway/2 = 240s (pipeline.headways.mean_wait_from_moments).
            (aid, "R_EVEN", 480.0, 10, True),
            (aid, "R_BUNCH", 480.0, 10, True),
            # Classified NOT high-frequency -- must never appear below,
            # regardless of how much daily data it has.
            (aid, "R_SLOW", 1800.0, 10, False),
            # High-frequency, but every agg_route_headway_daily row seeded
            # for it below falls outside the requested date range.
            (aid, "R_NODATA", 480.0, 10, True),
            # High-frequency, in-range daily data exists, but that data
            # predates migration 0040's sufficient-statistics columns and
            # hasn't been re-`analyze()`-d yet (see below).
            (aid, "R_PREMIGRATE", 480.0, 10, True),
        ],
    )
    await _insert_days(
        pool,
        [
            # R_EVEN: two identical-shape days of [480, 480] gaps -- pooled
            # mean wait stays 240s (== scheduled) -> EWT == 0, CoV == 0.
            (aid, "R_EVEN", date(2026, 4, 1), 480.0, 2, 960.0, 460800.0, 0, *_EVERY_8_MIN),
            (aid, "R_EVEN", date(2026, 4, 2), 480.0, 2, 960.0, 460800.0, 0, *_EVERY_8_MIN),
            # R_BUNCH: one day of [60, 900] gaps (same total as two scheduled
            # 480s headways) -- bunching raises the pooled mean wait above
            # scheduled -> EWT > 0; the 900s gap exceeds 1.75*480=840
            # -> long_gap_rate == 0.5.
            (aid, "R_BUNCH", date(2026, 4, 1), 480.0, 2, 960.0, 813600.0, 1, *_EVERY_8_MIN),
            # R_SLOW has real daily data too, but its day's timetable runs it every 30 min.
            (aid, "R_SLOW", date(2026, 4, 1), 1800.0, 1, 1800.0, 3240000.0, 0, *_EVERY_30_MIN),
            # R_NODATA's only row is well outside the [2026-04-01, 2026-04-02]
            # range the tests below request.
            (aid, "R_NODATA", date(2026, 5, 1), 480.0, 2, 960.0, 460800.0, 0, *_EVERY_8_MIN),
            # R_PREMIGRATE: a route whose actual-side statistics are missing:
            # `actual_samples` is populated, but the actual sums and long-gap
            # count are NULL, as a row built before those columns existed
            # reads. Must degrade to None metrics, not a 500.
            (aid, "R_PREMIGRATE", date(2026, 4, 1), 480.0, 2, None, None, None, *_EVERY_8_MIN),
        ],
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, agg_route_headway, agg_route_headway_daily CASCADE")
    await pool.close()


async def test_headway_quality_even_fixture_yields_zero_ewt_and_cov(headway_client):
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    by_route = {row["route_code"]: row for row in r.json()["rows"]}

    even = by_route["R_EVEN"]
    assert even["ewt_sec"] == pytest.approx(0.0, abs=1e-6)
    assert even["cov"] == pytest.approx(0.0, abs=1e-6)
    assert even["long_gap_rate"] == pytest.approx(0.0, abs=1e-6)
    assert even["samples"] == 4
    assert even["day_type"] is None  # no calendar loaded: every day is pooled


async def test_headway_quality_bunched_fixture_yields_positive_ewt_and_long_gap_rate(headway_client):
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    by_route = {row["route_code"]: row for row in r.json()["rows"]}

    bunched = by_route["R_BUNCH"]
    assert bunched["ewt_sec"] == pytest.approx(183.75, abs=0.01)
    assert bunched["cov"] > 0
    assert bunched["long_gap_rate"] == pytest.approx(0.5, abs=1e-6)
    assert bunched["samples"] == 2


async def test_headway_quality_excludes_non_high_frequency_and_out_of_range_routes(headway_client):
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    codes = {row["route_code"] for row in r.json()["rows"]}

    assert "R_SLOW" not in codes  # never surfaced, however much daily data it has
    assert "R_NODATA" not in codes  # high-frequency, but no data in the requested range


async def test_headway_quality_premigration_daily_row_yields_none_metrics(headway_client):
    """A high-frequency route whose in-range daily row predates migration
    0040's sufficient-statistics columns (NULL sum/sumsq/long_gap_count,
    non-null `actual_samples`) must surface as a 200 with unresolved (None)
    metric fields, per `HeadwayQualityRow`'s documented contract -- not a
    500 from `TypeError`s in the pooled-statistics arithmetic.
    """
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    by_route = {row["route_code"]: row for row in r.json()["rows"]}

    premigrate = by_route["R_PREMIGRATE"]
    assert premigrate["ewt_sec"] is None
    assert premigrate["cov"] is None
    assert premigrate["long_gap_rate"] is None
    assert premigrate["samples"] == 2


async def test_headway_quality_empty_agency_returns_empty_rows(headway_client):
    client, _aid = headway_client
    empty = await asyncpg.connect(DATABASE_URL)
    try:
        row = await empty.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
            "Headway Quality Empty Agency",
            "http://headway-quality-empty.example.com",
        )
        empty_aid = row["agency_id"]
    finally:
        await empty.close()

    r = await client.get(f"/api/{empty_aid}/headway_quality")
    assert r.status_code == 200
    assert r.json()["rows"] == []


async def test_headway_quality_unconfirmed_static_join_agency_returns_no_rows(headway_client):
    """A `static_join` agency with no recorded RT field-coverage verdict
    must not have its `agg_route_headway_daily` rows pooled and served here,
    even with real high-frequency data present -- sharing the wire shape
    that CAN populate `stop_id` (`ingest_strategy == 'static_join'`) is not
    the same as being confirmed to actually populate it on this specific
    agency's own live feed. Mirrors `pipeline.reports.dwell_run`'s and
    `schedule_realism_padding`'s use of the same underlying trust model
    (`pipeline.strategies.static_join.rt_field_coverage_confirmed`).
    """
    client, _aid = headway_client
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES ($1, $2, 'static_join') "
            "RETURNING agency_id",
            "Headway Quality Unconfirmed Agency",
            "http://headway-quality-unconfirmed.example.com",
        )
        unconfirmed_aid = row["agency_id"]
        await conn.execute(
            "INSERT INTO agg_route_headway "
            "(agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
            " is_high_frequency) VALUES ($1, 'R_UNCONFIRMED', 480.0, 10, TRUE)",
            unconfirmed_aid,
        )
    finally:
        await conn.close()
    from api.main import app

    await _insert_days(
        app.state.pool,
        [(unconfirmed_aid, "R_UNCONFIRMED", date(2026, 4, 1), 480.0, 2, 960.0, 460800.0, 0, *_EVERY_8_MIN)],
    )

    r = await client.get(f"/api/{unconfirmed_aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    assert r.json()["rows"] == []
    assert r.json()["unavailable_reason"] == "never_probed"
    assert r.json()["last_checked"] is None


async def test_headway_quality_confirmed_agency_names_no_reason(headway_client):
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    assert r.json()["rows"]
    assert r.json()["unavailable_reason"] is None


async def test_headway_quality_says_when_no_route_is_scheduled_often_enough(headway_client):
    """An agency whose timetable has no high-frequency route has nothing for
    this panel to measure, whatever its feed's coverage; blaming the data or
    an awaited check would promise figures that can never appear. The
    classification comes from the static timetable, so it is told first."""
    client, _aid = headway_client
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES ($1, $2, 'static_join') "
            "RETURNING agency_id",
            "Headway Quality Infrequent Agency",
            "http://headway-quality-infrequent.example.com",
        )
        infrequent_aid = row["agency_id"]
        await conn.execute(
            "INSERT INTO agg_route_headway "
            "(agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
            " is_high_frequency) VALUES ($1, 'R_HOURLY', 3600.0, 10, FALSE)",
            infrequent_aid,
        )
    finally:
        await conn.close()

    r = await client.get(f"/api/{infrequent_aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    assert r.json()["rows"] == []
    assert r.json()["unavailable_reason"] == "no_high_frequency_routes"
    assert r.json()["last_checked"] is None


async def _seed_weekday_dominant_route(aid: int) -> None:
    """R_DAY runs every 10 min on its weekday (dominant) timetable and every 20 min
    at weekends, exactly to schedule on both days seeded below."""
    from api.main import app

    pool = app.state.pool
    await pool.execute("INSERT INTO static_routes (agency_id, route_id) VALUES ($1, 'R_DAY')", aid)
    await pool.executemany(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1, $2, 'R_DAY', $3)",
        [(aid, f"wk{i}", "WK") for i in range(6)] + [(aid, f"we{i}", "WE") for i in range(3)],
    )
    await pool.executemany(
        "INSERT INTO static_calendar (agency_id, service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date, trips) VALUES ($1, $2, $3, $3, $3, $3, $3, $4, $4, $5, $6, $7)",
        [
            (aid, "WK", True, False, date(2026, 1, 1), date(2026, 12, 31), 6),
            (aid, "WE", False, True, date(2026, 1, 1), date(2026, 12, 31), 3),
        ],
    )
    await pool.execute(
        "INSERT INTO agg_route_headway (agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
        "is_high_frequency) VALUES ($1, 'R_DAY', 600.0, 10, TRUE)",
        aid,
    )
    every_10_min = (600.0, True, 6000.0, 3600000.0)
    await _insert_days(
        pool,
        [
            (aid, "R_DAY", date(2026, 4, 1), 600.0, 2, 1200.0, 720000.0, 0, *every_10_min),  # Wednesday: [600, 600]
            # Saturday: [1200, 1200], each past 1.75 x 600
            (aid, "R_DAY", date(2026, 4, 4), 1200.0, 2, 2400.0, 2880000.0, 2, *every_10_min),
        ],
    )


async def test_a_weekend_on_its_own_timetable_does_not_read_as_late_against_the_weekday_one(headway_client):
    client, aid = headway_client
    await _seed_weekday_dominant_route(aid)

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-05")

    assert r.status_code == 200
    row = next(row for row in r.json()["rows"] if row["route_code"] == "R_DAY")
    assert row["ewt_sec"] == pytest.approx(0.0, abs=1e-6)
    assert row["long_gap_rate"] == pytest.approx(0.0, abs=1e-6)
    assert row["samples"] == 2
    assert row["day_type"] == "weekday"


async def test_a_route_with_no_day_of_its_timetable_in_range_is_counted_off_timetable(headway_client):
    client, aid = headway_client
    await _seed_weekday_dominant_route(aid)

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-05&dow=weekend")

    assert r.status_code == 200
    body = r.json()
    assert "R_DAY" not in {row["route_code"] for row in body["rows"]}
    assert body["routes_off_timetable"] == 1


class _RecordingConn:
    """An asyncpg connection that keeps every statement it is asked to run."""

    def __init__(self, conn):
        self._conn = conn
        self.sql: list[str] = []

    def __getattr__(self, name):
        target = getattr(self._conn, name)
        if name not in ("fetch", "fetchrow", "fetchval", "execute"):
            return target

        async def recorded(sql, *args, **kwargs):
            self.sql.append(sql)
            return await target(sql, *args, **kwargs)

        return recorded


async def test_a_full_answer_skips_the_frequency_count(headway_client):
    """The high-frequency count only explains an empty answer, so an answer
    with rows never pays for it, while an agency whose feed is untrusted
    still has it asked first."""
    from api.range import RangeCtx
    from pipeline.reports.headway_quality import _FREQUENCY_SQL, compute_headway_quality_report

    _client, aid = headway_client
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 2))
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        recording = _RecordingConn(conn)
        report = await compute_headway_quality_report(aid, ctx, recording)
        assert report.rows and report.unavailable["unavailable_reason"] is None
        assert _FREQUENCY_SQL not in recording.sql

        await conn.execute("DELETE FROM rt_field_coverage_probes WHERE agency_id = $1", aid)
        recording = _RecordingConn(conn)
        report = await compute_headway_quality_report(aid, ctx, recording)
        assert report.rows == [] and report.unavailable["unavailable_reason"] == "never_probed"
        assert _FREQUENCY_SQL in recording.sql
    finally:
        await conn.close()


async def _a_route_whose_timetable_changed(aid: int) -> None:
    """R_CHANGE runs every 12 min on 4/1 and, under a revision, 12 gaps of 4 min and
    6 of 56 min on 4/2, both high-frequency; on 4/3 a third timetable runs it every
    30 min. The timetable loaded now classes it as not high-frequency at all. Each
    day it runs every 10 min exactly."""
    from api.main import app

    pool = app.state.pool
    await pool.execute(
        "INSERT INTO agg_route_headway (agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
        "is_high_frequency) VALUES ($1, 'R_CHANGE', 1800.0, 10, FALSE)",
        aid,
    )
    await _insert_days(
        pool,
        [
            (aid, "R_CHANGE", date(2026, 4, 1), 600.0, 2, 1200.0, 720000.0, 0, 720.0, True, 8640.0, 6220800.0),
            (aid, "R_CHANGE", date(2026, 4, 2), 600.0, 2, 1200.0, 720000.0, 0, 240.0, True, 23040.0, 68428800.0),
            (aid, "R_CHANGE", date(2026, 4, 3), 600.0, 2, 1200.0, 720000.0, 0, *_EVERY_30_MIN),
        ],
    )


async def test_each_day_is_judged_by_the_timetable_in_force_that_day(headway_client):
    """4/1 and 4/2 are high-frequency by their own timetables, whatever the
    loaded one says, and pool their scheduled sums: the scheduled wait is
    (6220800 + 68428800) / (2 x (8640 + 23040)) = 1178.18 s against an actual
    300 s. 4/3's timetable is not high-frequency, so 4/3 is left out."""
    client, aid = headway_client
    await _a_route_whose_timetable_changed(aid)

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-03")

    assert r.status_code == 200
    row = next(row for row in r.json()["rows"] if row["route_code"] == "R_CHANGE")
    assert row["ewt_sec"] == pytest.approx(300.0 - 74649600.0 / 63360.0, abs=0.01)
    assert row["samples"] == 4


async def test_a_day_whose_timetable_keeps_no_scheduled_headways_is_left_out_and_counted(headway_client):
    """It is not judged by the loaded timetable instead, even where that one
    classes the route high-frequency."""
    from api.main import app

    client, aid = headway_client
    await app.state.pool.execute(
        "INSERT INTO agg_route_headway (agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
        "is_high_frequency) VALUES ($1, 'R_UNKEPT', 480.0, 10, TRUE)",
        aid,
    )
    await _insert_days(
        app.state.pool,
        [
            (aid, "R_UNKEPT", date(2026, 4, 2), 480.0, 2, 960.0, 460800.0, 0, *_NOT_KEPT),
            (aid, "R_EVEN", date(2026, 4, 3), 480.0, 2, 960.0, 460800.0, 0, *_NOT_KEPT),
        ],
    )

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-03")

    assert r.status_code == 200
    body = r.json()
    by_route = {row["route_code"]: row for row in body["rows"]}
    assert "R_UNKEPT" not in by_route
    assert by_route["R_EVEN"]["samples"] == 4
    assert body["days_timetable_not_kept"] == 2
    assert body["days_scheduled_wait_not_kept"] == 0


async def test_a_pooled_day_kept_without_its_scheduled_sums_leaves_the_excess_wait_unknown(headway_client):
    """Its timetable's median still judges it high-frequency, and its gaps still
    count, but the scheduled wait cannot be pooled without its sums."""
    from api.main import app

    client, aid = headway_client
    await _insert_days(
        app.state.pool,
        [
            (aid, "R_OLD", date(2026, 4, 1), 480.0, 2, 960.0, 460800.0, 0, 480.0, True, None, None),
            (aid, "R_OLD", date(2026, 4, 2), 480.0, 2, 960.0, 813600.0, 1, *_EVERY_8_MIN),
        ],
    )

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")

    row = next(row for row in r.json()["rows"] if row["route_code"] == "R_OLD")
    assert row["ewt_sec"] is None
    assert row["long_gap_rate"] == pytest.approx(0.25, abs=1e-6)
    assert row["cov"] is not None
    assert row["samples"] == 4
    assert r.json()["days_scheduled_wait_not_kept"] == 1


async def _an_agency(name: str) -> int:
    """A static_join agency whose feed coverage is confirmed, with nothing else seeded."""
    from api.main import app

    pool = app.state.pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES ($1, $2, 'static_join') "
        "RETURNING agency_id",
        name,
        f"http://{name.lower().replace(' ', '-')}.example.com",
    )
    await confirm_rt_field_coverage(pool, row["agency_id"])
    return row["agency_id"]


async def _loaded_classes(aid: int, routes: dict[str, float]) -> None:
    """The timetable loaded now's median per route (agg_route_headway)."""
    from api.main import app

    await app.state.pool.executemany(
        "INSERT INTO agg_route_headway (agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
        "is_high_frequency) VALUES ($1, $2, $3, 10, $4)",
        [(aid, route, median, median <= 720) for route, median in routes.items()],
    )


async def _in_force(aid: int, first: date, last: date, content: str, routes: dict[str, float]) -> None:
    """*content* in force from *first* to *last*, as analyze recorded it, and its kept medians per route."""
    from api.main import app

    pool = app.state.pool
    await pool.execute(
        "INSERT INTO agg_timetable_in_force (agency_id, first_day, last_day, static_version_id, content_hash) "
        "VALUES ($1, $2, $3, $4, $4)",
        aid,
        first,
        last,
        content,
    )
    await pool.executemany(
        "INSERT INTO route_headway_by_content (agency_id, content_hash, route_code, logic_version, "
        "scheduled_headway_median_sec) VALUES ($1, $2, $3, 3, $4)",
        [(aid, content, route, median) for route, median in routes.items()],
    )


_APRIL_1_DAY_OF_B = (date(2026, 4, 1), 1800.0, 1, 1800.0, 3240000.0, 0, *_EVERY_30_MIN)


async def test_days_whose_own_timetables_class_no_route_high_frequency_say_so(headway_client):
    """The timetable in force over the range has no high-frequency route, so the
    loaded timetable classing one high-frequency does not hold the panel open."""
    from api.main import app

    client, _aid = headway_client
    aid = await _an_agency("Headway Quality Once Infrequent Agency")
    await _loaded_classes(aid, {"R_NOW_FREQUENT": 480.0})
    await _in_force(aid, date(2026, 3, 1), date(2026, 4, 30), "c_then", {"R_NOW_FREQUENT": 1800.0})
    await _insert_days(app.state.pool, [(aid, "R_NOW_FREQUENT", *_APRIL_1_DAY_OF_B)])

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")

    assert r.status_code == 200
    assert r.json()["rows"] == []
    assert r.json()["unavailable_reason"] == "no_high_frequency_routes"


async def test_a_high_frequency_route_with_no_day_in_range_is_still_a_high_frequency_route(headway_client):
    """A runs every 8 min but has no day in range; B has the only day, every 30
    min. The panel is empty for want of A's data, not for want of a route."""
    from api.main import app

    client, _aid = headway_client
    aid = await _an_agency("Headway Quality Quiet Frequent Route Agency")
    await _loaded_classes(aid, {"A": 480.0, "B": 1800.0})
    await _insert_days(app.state.pool, [(aid, "B", *_APRIL_1_DAY_OF_B)])

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")

    assert r.status_code == 200
    assert r.json()["rows"] == []
    assert r.json()["unavailable_reason"] is None


async def test_the_timetable_in_force_over_the_range_classes_a_route_the_loaded_one_does_not(headway_client):
    """In force over the range, A ran every 8 min; the timetable loaded now runs it every 30."""
    from api.main import app

    client, _aid = headway_client
    aid = await _an_agency("Headway Quality Once Frequent Agency")
    await _loaded_classes(aid, {"A": 1800.0, "B": 1800.0})
    await _in_force(aid, date(2026, 3, 1), date(2026, 4, 30), "c_then", {"A": 480.0, "B": 1800.0})
    await _insert_days(app.state.pool, [(aid, "B", *_APRIL_1_DAY_OF_B)])

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")

    assert r.json()["rows"] == []
    assert r.json()["unavailable_reason"] is None


async def test_a_range_the_timetables_in_force_leave_partly_uncovered_falls_back_to_the_loaded_one(headway_client):
    """Only 4/1 has a timetable in force, and it has no high-frequency route; for
    4/2 the timetable loaded now stands in, and it runs A every 8 min."""
    from api.main import app

    client, _aid = headway_client
    aid = await _an_agency("Headway Quality Partly Covered Agency")
    await _loaded_classes(aid, {"A": 480.0, "B": 1800.0})
    await _in_force(aid, date(2026, 3, 1), date(2026, 4, 1), "c_then", {"A": 1800.0, "B": 1800.0})
    await _insert_days(app.state.pool, [(aid, "B", *_APRIL_1_DAY_OF_B)])

    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")

    assert r.json()["rows"] == []
    assert r.json()["unavailable_reason"] is None
