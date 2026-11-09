"""A departure scheduled at 24:00:00 or later is filed under its service day's
own hour (24:30 is hour 24) and under the wall-clock band of its time (24:30 is
late night), in analyze's tables and in the live scans alike.

Ingest stores no `scheduled_time` string for such a departure, only its
`scheduled_sec`. A departure whose ingest stores only the string (the
aomori_regex strategy's "HH:MM") still takes its hour and band from it.
"""

from datetime import date, datetime, timedelta

from api.range import TIME_BAND_RANGES, RangeCtx
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.live_filing import AS_INGESTED
from pipeline.reports.filters import _dedup_cte_ch
from pipeline.reports.overview import _fetch_grain, _peak_hour_by_dow
from pipeline.reports.rankings import compute_hourly_heatmap
from tests.pipeline.ch_agg_helpers import seed_schedule, update_row

DAY = date(2026, 9, 14)  # a Monday
_NEXT = datetime(2026, 9, 15)
# (trip, captured JST, scheduled_time, scheduled_sec, delay): three departures in
# each hour, enough for every hourly heatmap cell.
_DEPARTURES = [
    *((f"m{i}", datetime(2026, 9, 14, 7, 20), "07:20", None, 90) for i in range(3)),
    *((f"e{i}", datetime(2026, 9, 14, 23, 40), "23:40:00", 23 * 3600 + 40 * 60, 10 * (i + 1)) for i in range(3)),
    *((f"n{i}", _NEXT.replace(minute=30), None, 24 * 3600 + 30 * 60, 120 - 60 * i) for i in range(3)),
    *((f"p{i}", _NEXT.replace(hour=1, minute=5), None, 25 * 3600 + 5 * 60, 30) for i in range(3)),
]
# Per hour of DAY: (departures, delay sum).
_BY_HOUR = {7: (3, 270), 23: (3, 60), 24: (3, 180), 25: (3, 90)}
_BY_BAND = {"morning": (3, 270), "night": (3, 60), "late_night": (6, 270)}


_STOP_BANDS = (
    "SELECT time_band, SUM(samples), SUM(delay_sum) FROM agg_stop_daily WHERE agency_id = %s GROUP BY time_band"
)


def _seed(pg_conn, agency_id: int, ch_client) -> None:
    seed_schedule(pg_conn, agency_id, [trip for trip, *_ in _DEPARTURES])
    insert_updates(
        ch_client,
        agency_id,
        [
            update_row(captured, trip, delay, scheduled_time=clock, scheduled_sec=sec)
            for trip, captured, clock, sec, delay in _DEPARTURES
        ],
    )
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()


def _fetch(pg_conn, sql: str, agency_id: int) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(sql, (agency_id,))
        return cur.fetchall()


def test_the_hourly_tables_file_a_departure_after_midnight_under_its_service_days_hour(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id, ch_client)

    hourly = _fetch(
        pg_conn, "SELECT date, hour, samples, sum_delay_sec FROM agg_hour_daily WHERE agency_id = %s", agency_id
    )
    assert {(d, h): (n, s) for d, h, n, s in hourly} == {(DAY, h): f for h, f in _BY_HOUR.items()}

    by_dow = _fetch(
        pg_conn,
        "SELECT route_code, service_type, dow, hour, samples, sum_delay_sec FROM agg_route_hour_dow "
        "WHERE agency_id = %s",
        agency_id,
    )
    assert {row[:4]: row[4:] for row in by_dow} == {("R1", "平日", 1, h): f for h, f in _BY_HOUR.items()}

    by_time = _fetch(
        pg_conn,
        "SELECT scheduled_time, samples, sum_delay_sec FROM agg_route_hour WHERE agency_id = %s",
        agency_id,
    )
    assert {t: (n, s) for t, n, s in by_time} == {
        timedelta(hours=7, minutes=20): (3, 270),
        timedelta(hours=23, minutes=40): (3, 60),
        timedelta(hours=24, minutes=30): (3, 180),
        timedelta(hours=25, minutes=5): (3, 90),
    }

    ((daily_samples, daily_sum),) = _fetch(
        pg_conn, "SELECT SUM(samples), SUM(sum_delay_sec) FROM agg_route_daily WHERE agency_id = %s", agency_id
    )
    assert (daily_samples, daily_sum) == (sum(n for n, _ in _BY_HOUR.values()), sum(s for _, s in _BY_HOUR.values()))


def test_the_stop_tables_band_a_departure_after_midnight_by_its_clock_time(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id, ch_client)

    stop = _fetch(pg_conn, _STOP_BANDS, agency_id)
    route_stop = _fetch(
        pg_conn,
        "SELECT time_band, SUM(samples)::int, SUM(delay_sum) FROM agg_route_stop_daily "
        "WHERE agency_id = %s GROUP BY time_band",
        agency_id,
    )
    assert {band: (n, s) for band, n, s in stop} == _BY_BAND
    assert {band: (n, s) for band, n, s in route_stop} == _BY_BAND


def test_the_live_time_band_filter_selects_what_the_stored_band_holds(pg_conn, agency_id, ch_client):
    _seed(pg_conn, agency_id, ch_client)
    stored = {band: (n, s) for band, n, s in _fetch(pg_conn, _STOP_BANDS, agency_id)}

    for band in TIME_BAND_RANGES:
        cte, params = _dedup_cte_ch(RangeCtx(from_date=DAY, to_date=DAY, time_band=band), AS_INGESTED)
        ((n, s),) = ch_client.query(
            f"WITH {cte} SELECT count(), ifNull(sum(dep_delay), 0) FROM deduped",
            parameters={"agency_id": agency_id, **params},
        ).result_rows
        assert (n, s) == stored.get(band, (0, 0)), band


async def test_the_live_hourly_heatmap_matches_agg_hour_daily_past_midnight(
    pg_conn, agency_id, ch_client, aconn, ch_async_client
):
    _seed(pg_conn, agency_id, ch_client)
    whole = RangeCtx(from_date=DAY, to_date=DAY)
    routed = RangeCtx(from_date=DAY, to_date=DAY, routes=("R1",))

    from_aggregate = await compute_hourly_heatmap(agency_id, whole, aconn)
    live = await compute_hourly_heatmap(agency_id, routed, aconn, ch=ch_async_client)

    assert {(c["hour"], c["samples"], c["sum_delay_sec"]) for c in from_aggregate} == {
        (h, n, s) for h, (n, s) in _BY_HOUR.items()
    }
    assert live == from_aggregate


async def test_the_live_peak_hour_profile_matches_agg_hour_daily_past_midnight(
    pg_conn, agency_id, ch_client, aconn, ch_async_client
):
    _seed(pg_conn, agency_id, ch_client)
    whole = RangeCtx(from_date=DAY, to_date=DAY)
    routed = RangeCtx(from_date=DAY, to_date=DAY, routes=("R1",))

    from_aggregate = await _peak_hour_by_dow(agency_id, whole, aconn, "all")
    grain = await _fetch_grain(agency_id, routed, aconn, ch_async_client, shape="period_hours")
    live = await _peak_hour_by_dow(agency_id, routed, aconn, "all", grain=grain)

    assert from_aggregate is not None
    by_hour = from_aggregate["by_hour"]
    assert len(by_hour) == 26
    assert {h for h, v in enumerate(by_hour) if v is not None} == set(_BY_HOUR)
    assert by_hour[24] == 1.0  # 180 s over 3 departures
    assert live == from_aggregate
