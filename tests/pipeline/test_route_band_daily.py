"""agg_route_band_daily, and the time-band Pulse it serves.

analyze files each departure under its route, service date and wall-clock
band. compute_overview_summary reads a time band from it and agg_hour_daily,
with no ClickHouse at all unless a routes filter asks for hour profiles, and
its payload equals the one the ClickHouse grain it replaces computes.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from api.range import RangeCtx
from pipeline.analyze import analyze
from pipeline.cache import clear_all
from pipeline.clickhouse import insert_updates
from pipeline.strategies._time import parse_departure_time

JST = timezone(timedelta(hours=9))
DAY = date(2026, 4, 2)
BEFORE = date(2026, 4, 1)


def _row(trip_id, day, scheduled, route_code, dep_delay, *, service="平日", seq=1):
    """One ClickHouse `updates` row captured five minutes before its departure.

    *scheduled* is ``"HH:MM:SS"`` (an hour past 23 is stored in scheduled_sec
    alone, as static_join stores it) or None for a departure with no time.
    """
    if scheduled is None:
        scheduled_time, scheduled_sec = None, None
        captured = datetime.combine(day, datetime.min.time(), JST) + timedelta(hours=12)
    else:
        scheduled_sec = parse_departure_time(scheduled)[2]
        scheduled_time = scheduled if scheduled_sec < 86400 else None
        captured = datetime.combine(day, datetime.min.time(), JST) + timedelta(seconds=scheduled_sec - 300)
    return (
        f"{trip_id}_{seq}.pb",
        captured.astimezone(timezone.utc),
        trip_id,
        service,
        scheduled_time,
        route_code,
        seq,
        dep_delay,
        *(None,) * 5,
        scheduled_sec,
    )


def _band_rows(pg_conn, agency_id) -> dict:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, route_code, time_band, samples, sum_delay_sec, sum_late_sec "
            "FROM agg_route_band_daily WHERE agency_id = %s",
            (agency_id,),
        )
        return {(d, route, band): (n, s, late) for d, route, band, n, s, late in cur.fetchall()}


def test_analyze_files_each_departure_under_its_route_date_and_wall_clock_band(pg_conn, agency_id, ch_client):
    insert_updates(
        ch_client,
        agency_id,
        [
            _row("A", DAY, "07:00:00", "R1", 120),
            _row("B", DAY, "07:30:00", "R1", -60),
            _row("C", DAY, "13:00:00", "R1", 300),
            # No route resolves: kept under '', not dropped.
            _row("D", DAY, "08:00:00", None, 600),
            # No service id: pooled with the route's others.
            _row("E", DAY, "18:00:00", "R2", 30, service=None),
            _row("F", DAY, "18:10:00", "R2", 90),
            # 24:30 on the service day's clock is 00:30 on the wall clock.
            _row("G", DAY, "24:30:00", "R2", 45),
            # No scheduled time: in no band.
            _row("H", DAY, None, "R3", 50),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()

    assert _band_rows(pg_conn, agency_id) == {
        (DAY, "R1", "morning"): (2, 60, 120),
        (DAY, "R1", "noon"): (1, 300, 300),
        (DAY, "", "morning"): (1, 600, 600),
        (DAY, "R2", "evening"): (2, 120, 120),
        (DAY, "R2", "late_night"): (1, 45, 45),
    }


def _seed_two_days(ch_client, agency_id) -> None:
    """Two days of several routes, enough per route for the movers and the
    routes-to-check list, plus an unrouted reading and a departure past midnight."""
    rows = []
    for day, shift in ((BEFORE, 0), (DAY, 45)):
        for i in range(12):
            minute = 5 * i
            rows.append(_row(f"R1_{day}_{i}", day, f"07:{minute:02d}:00", "R1", 60 + 7 * i + shift))
            rows.append(_row(f"R2_{day}_{i}", day, f"08:{minute:02d}:00", "R2", 200 - 11 * i - shift))
            rows.append(_row(f"R3_{day}_{i}", day, f"13:{minute:02d}:00", "R3", 30 * (i % 4) - 20))
            rows.append(_row(f"R4_{day}_{i}", day, f"24:{minute:02d}:00", "R4", 15 * i))
        rows.append(_row(f"X_{day}", day, "06:30:00", None, 900))
        rows.append(_row(f"Y_{day}", day, "07:15:00", "R1", -90, service=None))
    insert_updates(ch_client, agency_id, rows)


async def _payload(agency_id, ctx, aconn, ch, *, grain: bool, monkeypatch) -> dict:
    from pipeline.reports import compute_overview_summary

    clear_all()
    with monkeypatch.context() as mp:
        if grain:
            mp.setattr("pipeline.reports.overview._band_aggregate_answers", lambda ctx: False)
        return await compute_overview_summary(agency_id, ctx, aconn, ch=ch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ctx",
    [
        RangeCtx(from_date=DAY, to_date=DAY, time_band="morning"),
        RangeCtx(from_date=DAY, to_date=DAY, time_band="noon"),
        RangeCtx(from_date=DAY, to_date=DAY, time_band="late_night"),
        RangeCtx(from_date=BEFORE, to_date=DAY, time_band="morning", dow="thu"),
        RangeCtx(from_date=DAY, to_date=DAY, time_band="morning", routes=("R1", "R2")),
    ],
    ids=["morning", "noon", "late_night", "dow", "routes"],
)
async def test_a_time_band_pulse_from_the_aggregate_equals_the_grain(
    pg_conn, agency_id, ch_client, ch_async_client, aconn, ctx, monkeypatch
):
    _seed_two_days(ch_client, agency_id)
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()

    from_grain = await _payload(agency_id, ctx, aconn, ch_async_client, grain=True, monkeypatch=monkeypatch)
    from_aggregate = await _payload(agency_id, ctx, aconn, ch_async_client, grain=False, monkeypatch=monkeypatch)

    assert from_grain["headline"]["samples"] > 0
    assert from_aggregate == from_grain


@pytest.mark.asyncio
async def test_a_time_band_pulse_reads_no_clickhouse(pg_conn, agency_id, ch_client, aconn, monkeypatch):
    _seed_two_days(ch_client, agency_id)
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()

    ctx = RangeCtx(from_date=DAY, to_date=DAY, time_band="morning")
    out = await _payload(agency_id, ctx, aconn, None, grain=False, monkeypatch=monkeypatch)

    # R1's twelve, R2's twelve, the unrouted reading and the reading with no service id.
    assert out["headline"]["samples"] == 26
    assert out["headline"]["baseline_avg_min"] is not None
    assert {m["route_code"] for m in out["movers"]["worse"] + out["movers"]["better"]} == {"R1", "R2"}
    assert out["peak_hour"]["peak_hour"] in (6, 7, 8)
    assert "" not in {r["route_code"] for r in out["concentration"]["top_routes"]}


@pytest.mark.asyncio
async def test_a_routes_filter_reads_only_its_hour_profiles_from_clickhouse(
    pg_conn, agency_id, ch_client, ch_async_client, aconn, monkeypatch
):
    _seed_two_days(ch_client, agency_id)
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()

    queries: list[str] = []

    class _Recording:
        async def query(self, sql, parameters=None, **kwargs):
            queries.append(sql)
            return await ch_async_client.query(sql, parameters=parameters, **kwargs)

    ctx = RangeCtx(from_date=DAY, to_date=DAY, time_band="morning", routes=("R1",))
    out = await _payload(agency_id, ctx, aconn, _Recording(), grain=False, monkeypatch=monkeypatch)

    assert len(queries) == 1
    assert "GROUP BY date, " in queries[0] and "GROUPING SETS" not in queries[0]
    assert out["peak_hour"] is not None
    assert out["headline"]["samples"] == 13
