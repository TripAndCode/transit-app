"""Typed time_band SQL fragments and end-to-end filtering."""

from datetime import date

import pytest

from api.range import RangeCtx, build_updates_filter, time_band_clause


def test_time_band_clause_uses_typed_cast():
    """time_band_clause must compare against ${n}::time, not raw text."""
    today = date.today()
    ctx = RangeCtx(from_date=today, to_date=today, time_band="morning")
    frag, params, _ = time_band_clause("scheduled_time", ctx, next_param=2)
    assert "::time" in frag
    assert params == ["05:00", "09:00"]


def test_time_band_clause_all_is_unfiltered():
    today = date.today()
    ctx = RangeCtx(from_date=today, to_date=today, time_band="all")
    frag, params, _ = time_band_clause("scheduled_time", ctx, next_param=2)
    assert frag == "TRUE"
    assert params == []


@pytest.mark.asyncio
async def test_time_band_filter_against_real_column(aconn, aagency_id):
    """End-to-end: insert TIME values, run the morning filter, count what matches."""
    today = date.today()
    await aconn.execute(
        "INSERT INTO updates "
        "(agency_id, file_name, captured_at, trip_id, service_type, "
        " scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES "
        "  ($1, 'pb_08', now(), 't1', '平日', '08:30', '10', 1, 60),"
        "  ($1, 'pb_14', now(), 't2', '平日', '14:30', '10', 1, 60)",
        aagency_id,
    )
    ctx = RangeCtx(from_date=today, to_date=today, time_band="morning")
    where, params, _ = build_updates_filter(ctx, next_param=2)
    row = await aconn.fetchrow(
        f"SELECT count(*) AS n FROM updates WHERE agency_id=$1 AND {where}",
        aagency_id,
        *params,
    )
    assert row["n"] == 1  # only 08:30 matches morning (05:00-09:00)
