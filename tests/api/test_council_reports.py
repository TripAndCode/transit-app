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
async def test_delay_certificate_uses_origin_stop_only_for_each_trip(client, agency_id, pg_conn, ch_client, ch_async_client):
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
