"""Regression tests for the council report template.

These tests cover the delay-certificate export trip-level dedupe: one
physical trip/run should be represented once, using the earliest
origin-stop observation.
"""

from datetime import datetime, time

import pytest


async def _seed_trip_with_multiple_stops(pg_conn, agency_id, route_code, service_type, day, trip_id, delays):
    for stop_sequence, dep_delay in enumerate(delays, start=1):
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO updates "
                "(agency_id, trip_id, route_code, service_type, scheduled_time, "
                " stop_sequence, dep_delay, captured_at, file_name) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agency_id,
                    trip_id,
                    route_code,
                    service_type,
                    time(10, 0),
                    stop_sequence,
                    dep_delay,
                    datetime.fromisoformat(f"{day}T10:00:0{stop_sequence - 1}"),
                    f"test/{route_code}/{day}/{trip_id}/{stop_sequence}.pb",
                ),
            )
    pg_conn.commit()


@pytest.mark.asyncio
async def test_delay_certificate_uses_origin_stop_only_for_each_trip(
    client, agency_id, pg_conn, ch_client, ch_async_client
):
    """A later stop on the same trip must not create a duplicate export row.

    If the origin-stop delay is under threshold and a later stop is above it,
    the trip should still be excluded. That proves the export is keyed by
    (trip_id, date) and reads the earliest stop row via argMin(..., stop_sequence).
    """
    from api.main import app
    from tests.conftest import mirror_updates_to_ch

    app.state.ch_client = ch_async_client

    day = "2026-06-24"
    await _seed_trip_with_multiple_stops(pg_conn, agency_id, "RDEDUP", "平日", day, "T-1", [100, 500])
    await _seed_trip_with_multiple_stops(pg_conn, agency_id, "RKEEP", "平日", day, "T-2", [400, 50])

    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec=300")
    assert resp.status_code == 200
    rows = resp.json()["rows"]

    dedup_rows = [r for r in rows if r[1] == "RDEDUP"]
    keep_rows = [r for r in rows if r[1] == "RKEEP"]

    assert dedup_rows == []
    assert len(keep_rows) == 1
    assert keep_rows[0][1] == "RKEEP"
    assert keep_rows[0][4] == "10:00:00"
    assert keep_rows[0][5] == "10:06:40"
    assert keep_rows[0][6] == 400


def _seed_static_trip(pg_conn, agency_id, trip_id, stops):
    """The loaded timetable's stops for one trip, as ``(stop_sequence, stop_id, stop_name)``."""
    with pg_conn.cursor() as cur:
        for stop_sequence, stop_id, stop_name in stops:
            cur.execute(
                "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (agency_id, stop_id, stop_name),
            )
            cur.execute(
                "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES (%s, %s, %s, %s)",
                (agency_id, trip_id, stop_sequence, stop_id),
            )
    pg_conn.commit()


def _seed_observation(pg_conn, agency_id, route_code, day, trip_id, stop_sequence, dep_delay):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates "
            "(agency_id, trip_id, route_code, service_type, scheduled_time, "
            " stop_sequence, dep_delay, captured_at, file_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                agency_id,
                trip_id,
                route_code,
                "平日",
                time(10, 0),
                stop_sequence,
                dep_delay,
                datetime.fromisoformat(f"{day}T10:00:00"),
                f"test/{route_code}/{day}/{trip_id}/{stop_sequence}.pb",
            ),
        )
    pg_conn.commit()


@pytest.mark.asyncio
async def test_delay_certificate_names_the_recorded_stop_and_whether_the_timetable_starts_there(
    client, agency_id, pg_conn, ch_client, ch_async_client
):
    """Each row names the stop its times were recorded at, from the loaded
    timetable, and says whether that stop is where the timetable starts the
    trip. A feed often has no report for a trip's first stop, so the earliest
    recorded stop can be a later one; a trip the timetable no longer holds
    has neither."""
    from api.main import app
    from tests.conftest import mirror_updates_to_ch

    app.state.ch_client = ch_async_client
    day = "2026-06-25"
    for trip_id in ("T-ORIGIN", "T-LATER"):
        _seed_static_trip(pg_conn, agency_id, trip_id, [(1, "S1", "広島駅"), (2, "S2", "紙屋町")])
    _seed_observation(pg_conn, agency_id, "RORIGIN", day, "T-ORIGIN", 1, 400)
    _seed_observation(pg_conn, agency_id, "RLATER", day, "T-LATER", 2, 500)
    _seed_observation(pg_conn, agency_id, "RGONE", day, "T-GONE", 1, 600)
    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec=300")
    assert resp.status_code == 200
    by_route = {r[1]: r for r in resp.json()["rows"]}

    assert by_route["RORIGIN"][7:] == ["広島駅", True]
    assert by_route["RLATER"][7:] == ["紙屋町", False]
    assert by_route["RGONE"][7:] == [None, None]


@pytest.mark.asyncio
async def test_delay_certificate_csv_names_the_recorded_stop(client, agency_id, pg_conn, ch_client, ch_async_client):
    import csv
    import io

    from api.main import app
    from tests.conftest import mirror_updates_to_ch

    app.state.ch_client = ch_async_client
    day = "2026-06-26"
    for trip_id in ("T-ORIGIN", "T-LATER"):
        _seed_static_trip(pg_conn, agency_id, trip_id, [(1, "S1", "広島駅"), (2, "S2", "紙屋町")])
    _seed_observation(pg_conn, agency_id, "RORIGIN", day, "T-ORIGIN", 1, 400)
    _seed_observation(pg_conn, agency_id, "RLATER", day, "T-LATER", 2, 500)
    _seed_observation(pg_conn, agency_id, "RGONE", day, "T-GONE", 1, 600)
    mirror_updates_to_ch(ch_client, agency_id)

    resp = await client.get(
        f"/api/{agency_id}/reports/delay_certificate?from={day}&to={day}&threshold_sec=300&format=csv"
    )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    header_idx = next(i for i, r in enumerate(rows) if r[:1] == ["事業者名"])
    assert rows[header_idx][7:] == ["記録した停留所", "始発の停留所"]
    by_route = {r[1]: r[7:] for r in rows[header_idx + 1 :]}
    assert by_route == {"RORIGIN": ["広島駅", "はい"], "RLATER": ["紙屋町", "いいえ"], "RGONE": ["", ""]}
