"""The running time per timetable segment, read from agg_route_segment_runtime."""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.reports.segment_runtime import compute_segment_runtime
from tests.conftest import confirm_rt_field_coverage

D1, D2 = date(2026, 4, 1), date(2026, 4, 2)
CTX = RangeCtx(from_date=D1, to_date=D2)


async def _trusted(conn, agency_id):
    await conn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
    await confirm_rt_field_coverage(conn, agency_id)


async def _segment(
    conn,
    agency_id,
    day,
    from_stop,
    to_stop,
    band,
    run_sec,
    *,
    seq=2,
    scheduled=(0, 0),
    holds=(0, 0),
    terminal=(0, 0),
    route="R9",
):
    """One agg row; *scheduled* is (samples, sum_sec), *holds* (eligible, held),
    *terminal* (arrivals, early)."""
    await conn.execute(
        "INSERT INTO agg_route_segment_runtime (agency_id, date, route_code, from_stop_id, to_stop_id, time_band, "
        "to_stop_sequence, run_sec, scheduled_samples, scheduled_sum_sec, hold_eligible, holds, "
        "terminal_arrivals, terminal_early) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
        agency_id,
        day,
        route,
        from_stop,
        to_stop,
        band,
        seq,
        run_sec,
        *scheduled,
        *holds,
        *terminal,
    )


@pytest.mark.asyncio
async def test_days_pool_into_exact_percentiles_and_the_padding(aconn, aagency_id):
    """Two days of one segment, each a 540 s timetable: one run of 240 s, one
    of 540 s. The median is 390 s and the 85th percentile 240 + 0.85 * 300 =
    495 s, so the timetable allows 150 s more than the median run."""
    await _trusted(aconn, aagency_id)
    await aconn.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name) "
        "VALUES ($1, 'S2', 'Kamiyacho'), ($1, 'S3', 'Station')",
        aagency_id,
    )
    await _segment(
        aconn, aagency_id, D1, "S2", "S3", "forenoon", [240], scheduled=(1, 540), holds=(1, 1), terminal=(1, 1)
    )
    await _segment(
        aconn, aagency_id, D2, "S2", "S3", "forenoon", [540], scheduled=(1, 540), holds=(1, 0), terminal=(1, 0)
    )

    result = await compute_segment_runtime(aagency_id, CTX, aconn, route="R9")

    assert result["available"] is True
    assert result["value_basis"] == "feed_prediction"
    assert result["days"] == 2
    assert result["segments"] == [
        {
            "from_stop_id": "S2",
            "from_stop_name": "Kamiyacho",
            "to_stop_id": "S3",
            "to_stop_name": "Station",
            "time_band": "forenoon",
            "to_stop_sequence": 2,
            "samples": 2,
            "run_p50_sec": pytest.approx(390.0),
            "run_p85_sec": pytest.approx(495.0),
            "scheduled_run_sec": pytest.approx(540.0),
            "padding_sec": pytest.approx(150.0),
            "hold_eligible": 2,
            "hold_rate": pytest.approx(0.5),
        }
    ]
    assert result["terminus_arrivals"] == 2
    assert result["terminus_early_rate"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_only_the_timetable_in_force_on_the_newest_date_is_read(aconn, aagency_id):
    """The scheduled time changed at the revision between the two days, so
    pooling them would state a timetable neither day ran."""
    await _trusted(aconn, aagency_id)
    await aconn.execute(
        "INSERT INTO agg_schedule_revision_daily (agency_id, date, static_version_id) "
        "VALUES ($1, $2, 'v1'), ($1, $3, 'v2')",
        aagency_id,
        D1,
        D2,
    )
    await _segment(aconn, aagency_id, D1, "S2", "S3", "forenoon", [240], scheduled=(1, 300))
    await _segment(aconn, aagency_id, D2, "S2", "S3", "forenoon", [540], scheduled=(1, 600))

    result = await compute_segment_runtime(aagency_id, CTX, aconn, route="R9")

    assert result["static_version_id"] == "v2"
    assert result["days"] == 1
    [segment] = result["segments"]
    assert (segment["samples"], segment["run_p50_sec"], segment["scheduled_run_sec"]) == (1, 540.0, 600.0)


@pytest.mark.asyncio
async def test_the_timetable_is_the_one_in_force_on_the_last_day_not_the_routes_newest(aconn, aagency_id):
    """The route ran only on the first day, under v1; v2 came into force on the
    last day. The figures are v2's, and there are none yet; a revision after
    the range does not count."""
    await _trusted(aconn, aagency_id)
    await aconn.execute(
        "INSERT INTO agg_schedule_revision_daily (agency_id, date, static_version_id) "
        "VALUES ($1, $2, 'v1'), ($1, $3, 'v2'), ($1, $4, 'v3')",
        aagency_id,
        D1,
        D2,
        date(2026, 4, 3),
    )
    await _segment(aconn, aagency_id, D1, "S2", "S3", "forenoon", [240], scheduled=(1, 300))

    result = await compute_segment_runtime(aagency_id, CTX, aconn, route="R9")
    first_day = await compute_segment_runtime(aagency_id, RangeCtx(from_date=D1, to_date=D1), aconn, route="R9")

    assert (result["static_version_id"], result["segments"]) == ("v2", [])
    assert first_day["static_version_id"] == "v1"
    assert [s["run_p50_sec"] for s in first_day["segments"]] == [240.0]


@pytest.mark.asyncio
async def test_a_time_band_reads_only_its_rows_and_segments_follow_the_route(aconn, aagency_id):
    await _trusted(aconn, aagency_id)
    await _segment(aconn, aagency_id, D1, "S2", "S3", "morning", [100], seq=3)
    await _segment(aconn, aagency_id, D1, "S1", "S2", "morning", [200], seq=2)
    await _segment(aconn, aagency_id, D1, "S1", "S2", "evening", [300], seq=2)
    await _segment(aconn, aagency_id, D1, "S1", "S2", "none", [400], seq=2)

    every = await compute_segment_runtime(aagency_id, CTX, aconn, route="R9")
    morning = await compute_segment_runtime(
        aagency_id, RangeCtx(from_date=D1, to_date=D2, time_band="morning"), aconn, route="R9"
    )

    assert [(s["from_stop_id"], s["time_band"]) for s in every["segments"]] == [
        ("S1", "morning"),
        ("S1", "evening"),
        ("S1", None),
        ("S2", "morning"),
    ]
    assert [(s["from_stop_id"], s["run_p50_sec"]) for s in morning["segments"]] == [("S1", 200.0), ("S2", 100.0)]


@pytest.mark.asyncio
async def test_a_segment_with_no_running_time_has_no_percentile_or_padding(aconn, aagency_id):
    await _trusted(aconn, aagency_id)
    await _segment(aconn, aagency_id, D1, "S4", "S5", "morning", [], terminal=(1, 0))

    [segment] = (await compute_segment_runtime(aagency_id, CTX, aconn, route="R9"))["segments"]

    assert segment["samples"] == 0
    assert segment["run_p50_sec"] is None and segment["padding_sec"] is None
    assert segment["scheduled_run_sec"] is None and segment["hold_rate"] is None


@pytest.mark.asyncio
async def test_another_route_and_dates_outside_the_range_are_not_read(aconn, aagency_id):
    await _trusted(aconn, aagency_id)
    await _segment(aconn, aagency_id, D1, "S1", "S2", "morning", [100], route="R1")
    await _segment(aconn, aagency_id, date(2026, 4, 5), "S1", "S2", "morning", [100])

    result = await compute_segment_runtime(aagency_id, CTX, aconn, route="R9")

    assert result["available"] is True
    assert result["segments"] == [] and result["terminus_early_rate"] is None


@pytest.mark.asyncio
async def test_an_agency_without_a_coverage_verdict_reads_nothing(aconn, aagency_id):
    """ingest_strategy alone is not trust: no verdict, no figures, and a reason."""
    await aconn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", aagency_id)
    await _segment(aconn, aagency_id, D1, "S1", "S2", "morning", [100])

    result = await compute_segment_runtime(aagency_id, CTX, aconn, route="R9")

    assert result["available"] is False
    assert result["unavailable_reason"]
    assert result["segments"] == []


@pytest.mark.asyncio
async def test_the_named_route_is_read_whatever_route_filter_the_request_carries(aconn, aagency_id):
    await _trusted(aconn, aagency_id)
    await _segment(aconn, aagency_id, D1, "S1", "S2", "morning", [100])

    filtered = RangeCtx(from_date=D1, to_date=D2, routes=("R1",))
    result = await compute_segment_runtime(aagency_id, filtered, aconn, route="R9")

    assert [s["run_p50_sec"] for s in result["segments"]] == [100.0]
