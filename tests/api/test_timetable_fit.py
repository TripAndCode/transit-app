"""The route page's timetable fit: GET /segments/timetable-fit."""

from datetime import timedelta

import pytest

from tests.api.test_segment_runtime_report import D1, D2, _segment, _trusted

RANGE = f"from={D1.isoformat()}&to={D2.isoformat()}"


@pytest.mark.asyncio
async def test_the_route_segments_come_rounded_with_their_definition(client, aconn, aagency_id):
    await _trusted(aconn, aagency_id)
    await aconn.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name) "
        "VALUES ($1, 'S2', 'Kamiyacho'), ($1, 'S3', 'Station')",
        aagency_id,
    )
    await aconn.execute(
        "INSERT INTO agg_schedule_revision_daily (agency_id, date, static_version_id) "
        "VALUES ($1, $2, 'v2'), ($1, $3, 'v2')",
        aagency_id,
        D1,
        D2,
    )
    await _segment(aconn, aagency_id, D1, "S2", "S3", "forenoon", [240, 241], scheduled=(3, 1000), holds=(3, 1))
    await _segment(aconn, aagency_id, D2, "S2", "S3", "forenoon", [541], terminal=(4, 1))
    await _segment(aconn, aagency_id, D1, "S2", "S3", "forenoon", [900], route="R1")

    r = await client.get(f"/api/{aagency_id}/segments/timetable-fit?{RANGE}&route=R9&routes=R1")

    assert r.status_code == 200
    body = r.json()
    assert (body["available"], body["value_basis"], body["static_version_id"]) == (True, "feed_prediction", "v2")
    assert (body["days"], body["terminus_arrivals"], body["terminus_early_rate"]) == (2, 4, 0.25)
    [segment] = body["segments"]
    assert segment["from_stop_name"] == "Kamiyacho" and segment["to_stop_name"] == "Station"
    assert segment["time_band"] == "forenoon"
    assert segment["samples"] == 3
    assert segment["run_p50_sec"] == 241.0
    assert segment["run_p85_sec"] == 451.0
    assert segment["scheduled_run_sec"] == 333.3
    assert segment["padding_sec"] == 92.3
    assert segment["hold_rate"] == pytest.approx(1 / 3)
    assert body["definition"]["value_basis"] == "feed_prediction"
    applied = body["scope_applied"]
    assert all(applied[field] for field in ("from", "to", "dow", "time_band", "service"))
    assert not any(applied[field] for field in ("routes", "hour", "stop", "dir", "late", "early"))


@pytest.mark.asyncio
async def test_the_definition_names_only_the_timetable_read(client, aconn, aagency_id):
    """Over a range two timetables ran in, the figures read the later one
    alone, and so does the stamp beside them."""
    await _trusted(aconn, aagency_id)
    await aconn.execute(
        "INSERT INTO agg_schedule_revision_daily (agency_id, date, static_version_id) "
        "VALUES ($1, $2, 'v1'), ($1, $3, 'v2')",
        aagency_id,
        D1,
        D2,
    )
    await _segment(aconn, aagency_id, D1, "S2", "S3", "forenoon", [240])
    await _segment(aconn, aagency_id, D2, "S2", "S3", "forenoon", [300])

    r = await client.get(f"/api/{aagency_id}/segments/timetable-fit?{RANGE}&route=R9")

    assert r.status_code == 200
    body = r.json()
    assert (body["static_version_id"], body["days"]) == ("v2", 1)
    assert [(v["name"], v["days"]) for v in body["definition"]["timetable_versions"]] == [("v2", 1)]


@pytest.mark.asyncio
async def test_a_range_no_timetable_was_recorded_in_keeps_the_ranges_stamp(client, aconn, aagency_id):
    """The version in force then comes from before the range and no day is
    read under it, so the stamp names no timetable it would contradict."""
    await _trusted(aconn, aagency_id)
    await aconn.execute(
        "INSERT INTO agg_schedule_revision_daily (agency_id, date, static_version_id) VALUES ($1, $2, 'v0')",
        aagency_id,
        D1 - timedelta(days=1),
    )
    await _segment(aconn, aagency_id, D1, "S2", "S3", "forenoon", [240])
    await _segment(aconn, aagency_id, D2, "S2", "S3", "forenoon", [300])

    r = await client.get(f"/api/{aagency_id}/segments/timetable-fit?{RANGE}&route=R9")

    assert r.status_code == 200
    body = r.json()
    assert (body["static_version_id"], body["days"], body["segments"]) == ("v0", 0, [])
    assert [(v["name"], v["days"]) for v in body["definition"]["timetable_versions"]] == [(None, 2)]


@pytest.mark.asyncio
async def test_an_untrusted_feed_says_why_it_shows_nothing(client, aagency_id):
    r = await client.get(f"/api/{aagency_id}/segments/timetable-fit?{RANGE}&route=R9")

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["unavailable_reason"]
    assert body["segments"] == []
    assert body["definition"]["value_basis"] == "feed_prediction"


@pytest.mark.asyncio
async def test_the_route_is_required(client, aagency_id):
    r = await client.get(f"/api/{aagency_id}/segments/timetable-fit?{RANGE}")
    assert r.status_code == 422
