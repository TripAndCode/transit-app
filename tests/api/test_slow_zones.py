"""Slow zones read from agg_route_segment_runtime (pipeline.reports.slow_zones,
api.routers.segments).

The window ending Sunday 2026-04-12 covers the eight weeks from Monday
2026-02-16 to Monday 2026-04-06. S1 and S2 sit about 460 m apart on one
parallel, and every timetable gives the segment 120 s.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import pytest

from api.range import jst_today
from api.routers.segments import UNAVAILABLE_CSV
from pipeline.agg_registry import METHOD_VERSION
from pipeline.reports.slow_zones import read_slow_zones, window_dates, window_weeks
from tests.conftest import confirm_rt_field_coverage

LAST = date(2026, 4, 12)
SCHEDULED = 120


async def _trusted(conn, agency_id):
    await conn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
    await confirm_rt_field_coverage(conn, agency_id)


async def _stops(conn, agency_id):
    await conn.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) VALUES "
        "($1, 'S1', '紙屋町', 34.395, 132.455, ST_SetSRID(ST_MakePoint(132.455, 34.395), 4326)), "
        "($1, 'S2', '八丁堀', 34.395, 132.460, ST_SetSRID(ST_MakePoint(132.460, 34.395), 4326))",
        agency_id,
    )


async def _runs(conn, agency_id, day, run_sec, *, route="R1", band="morning", pair=("S1", "S2")):
    """One agg row: *run_sec* observed runs, each scheduled ``SCHEDULED``."""
    await conn.execute(
        "INSERT INTO agg_route_segment_runtime (agency_id, date, route_code, from_stop_id, to_stop_id, time_band, "
        "to_stop_sequence, run_sec, scheduled_samples, scheduled_sum_sec, hold_eligible, holds, "
        "terminal_arrivals, terminal_early) VALUES ($1, $2, $3, $4, $5, $6, 2, $7, $8, $9, 0, 0, 0, 0)",
        agency_id,
        day,
        route,
        *pair,
        band,
        run_sec,
        len(run_sec),
        len(run_sec) * SCHEDULED,
    )


def _read(conn, agency_id, *, last_day=LAST, day_type="weekday", band="morning"):
    return read_slow_zones(agency_id, conn, last_day=last_day, day_type=day_type, time_band=band)


@pytest.mark.asyncio
async def test_a_segment_slow_three_weeks_running_is_a_slow_zone(aconn, aagency_id):
    """Two routes share S1→S2. Each week neither alone reaches the 21-run
    floor; pooled, they do. Each run takes 200 s, 80 s over the timetable."""
    await _trusted(aconn, aagency_id)
    await _stops(aconn, aagency_id)
    for monday in (date(2026, 3, 23), date(2026, 3, 30), date(2026, 4, 6)):
        await _runs(aconn, aagency_id, monday, [200] * 11, route="R1")
        await _runs(aconn, aagency_id, monday + timedelta(days=1), [200] * 10, route="R2")
    await _runs(aconn, aagency_id, date(2026, 4, 11), [900] * 30)  # a Saturday
    await _runs(aconn, aagency_id, date(2026, 4, 6), [900] * 30, band="evening")

    result = await _read(aconn, aagency_id)

    assert result.available is True
    assert result.value_basis == "feed_prediction"
    assert result.weeks[0] == date(2026, 2, 16) and result.weeks[-1] == date(2026, 4, 6)
    [segment] = result.segments
    assert segment.routes == ["R1", "R2"]
    assert segment.from_stop_name == "紙屋町"
    assert segment.distance_m == pytest.approx(459, abs=5)
    latest = segment.latest
    assert latest is not None
    assert (latest.samples, latest.days) == (21, 2)
    assert latest.run_p50_sec == pytest.approx(200.0)
    assert latest.scheduled_run_sec == pytest.approx(120.0)
    assert latest.excess_sec == pytest.approx(80.0)
    assert segment.weeks[:5] == [None] * 5
    assert segment.slow_weeks == 3
    assert [z.from_stop_id for z in result.slow_zones] == ["S1"]


@pytest.mark.asyncio
async def test_a_week_under_two_timetables_reads_the_newer_one_alone(aconn, aagency_id):
    """The timetable changes on Wednesday 2026-04-01. The week of 03-30 is read
    under the new one, so its slow Monday drops out and its on-time Thursday
    alone leaves the week not slow, ending the streak at the newest week."""
    await _trusted(aconn, aagency_id)
    day = date(2026, 3, 16)
    while day <= LAST:
        await aconn.execute(
            "INSERT INTO agg_schedule_revision_daily (agency_id, date, static_version_id) VALUES ($1, $2, $3)",
            aagency_id,
            day,
            "v1" if day < date(2026, 4, 1) else "v2",
        )
        day += timedelta(days=1)
    await _runs(aconn, aagency_id, date(2026, 3, 23), [200] * 21)
    await _runs(aconn, aagency_id, date(2026, 3, 30), [200] * 30)
    await _runs(aconn, aagency_id, date(2026, 4, 2), [120] * 21)
    await _runs(aconn, aagency_id, date(2026, 4, 6), [200] * 21)

    [segment] = (await _read(aconn, aagency_id)).segments

    week_of_revision = segment.weeks[-2]
    assert week_of_revision is not None
    assert (week_of_revision.samples, week_of_revision.run_p50_sec) == (21, 120.0)
    assert segment.slow_weeks == 1
    assert segment.is_slow_zone is False


@pytest.mark.asyncio
async def test_an_untrusted_feed_reads_nothing(aconn, aagency_id):
    await _runs(aconn, aagency_id, date(2026, 4, 6), [200] * 30)

    result = await _read(aconn, aagency_id)

    assert result.available is False
    assert result.unavailable_reason is not None
    assert result.segments == []


async def _seed_recent_slow_zone(conn, agency_id) -> list[date]:
    """A slow S1→S2 in each of the three newest complete weeks before today's
    last closed day; returns those weeks' Mondays."""
    weeks = window_weeks(jst_today() - timedelta(days=1))[-3:]
    for monday in weeks:
        day = window_dates([monday], "weekday")[0]
        await _runs(conn, agency_id, day, [200] * 21)
    return weeks


@pytest.mark.asyncio
async def test_the_endpoint_reads_the_weeks_before_the_last_closed_day(client, aconn, aagency_id):
    await _trusted(aconn, aagency_id)
    await _stops(aconn, aagency_id)
    weeks = await _seed_recent_slow_zone(aconn, aagency_id)

    r = await client.get(f"/api/{aagency_id}/segments/slow-zones")

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert (body["day_type"], body["time_band"], body["value_basis"]) == ("weekday", "morning", "feed_prediction")
    assert body["weeks"][-1] == weeks[-1].isoformat()
    assert body["thresholds"] == {"excess_sec": 60, "min_scheduled_sec": 60, "min_weeks": 3, "min_samples": 21}
    assert body["slow_zones"] == [["S1", "S2"]]
    [segment] = body["segments"]
    assert segment["slow_weeks"] == 3
    assert segment["slow_since"] == weeks[0].isoformat()
    assert segment["weekly_excess_sec"][-3:] == [80.0, 80.0, 80.0]
    assert segment["weekly_excess_sec"][:-3] == [None] * 5
    latest = segment["latest"]
    assert latest["excess_sec"] == 80.0
    assert latest["speed_kmh"] == pytest.approx(segment["distance_m"] / 200 * 3.6, abs=0.1)
    assert latest["trips_per_day"] == 21.0
    assert latest["minutes_lost_per_day"] == 28.0
    assert latest["enough_samples"] is True


@pytest.mark.asyncio
async def test_the_csv_lists_the_slow_zones_as_feed_based_estimates(client, aconn, aagency_id):
    await _trusted(aconn, aagency_id)
    await _stops(aconn, aagency_id)
    weeks = await _seed_recent_slow_zone(aconn, aagency_id)

    r = await client.get(f"/api/{aagency_id}/segments/slow-zones?format=csv&lang=en")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    header = rows.index(next(row for row in rows if row and row[0] == "始点停留所ID"))
    assert any("feed_prediction" in row[0] for row in rows[:header])
    assert [f"算出方法：{METHOD_VERSION}"] in rows[:header]
    [data] = rows[header + 1 :]
    assert data[:7] == ["S1", "紙屋町", "S2", "八丁堀", "R1", "3", weeks[0].isoformat()]
    assert data[9] == "80.0"
    assert data[-1] == "28.0"


@pytest.mark.asyncio
async def test_the_csv_of_an_untrusted_feed_says_why(client, aconn, aagency_id):
    r = await client.get(f"/api/{aagency_id}/segments/slow-zones?format=csv&lang=en")

    assert r.status_code == 200
    assert UNAVAILABLE_CSV["en"] in r.text


@pytest.mark.asyncio
async def test_lang_is_for_the_csv_only(client, aagency_id):
    r = await client.get(f"/api/{aagency_id}/segments/slow-zones?lang=en")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_an_unknown_band_is_rejected(client, aagency_id):
    r = await client.get(f"/api/{aagency_id}/segments/slow-zones?time_band=all")
    assert r.status_code == 422
