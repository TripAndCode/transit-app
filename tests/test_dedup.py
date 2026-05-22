"""Latest-by-captured_at dedup, replacing the old MAX semantics.

Pins behavior for all three dedup definitions in the codebase:

- pipeline.db._DEDUP_INNER          (psycopg2 / %(agency_id)s)
- pipeline.query.executor._DEDUP_INNER  (asyncpg / $1)
- pipeline.reports._dedup_cte()     (asyncpg / $1 + WHERE fragment)

Each test seeds one trip × stop_sequence with three observations,
inserts them deliberately out of captured_at order to prove the CTE
relies on the column (not insertion order), then asserts the deduped
row carries the LATEST dep_delay (120s), not the MAX (300s).
"""

from datetime import datetime, timedelta, timezone

import pytest

from api.range import RangeCtx, build_updates_filter
from pipeline.db import _DEDUP_INNER as DB_DEDUP_INNER
from pipeline.query.executor import _DEDUP_INNER as EXEC_DEDUP_INNER
from pipeline.reports import _dedup_cte


_SEED = [
    # (file_name, captured_at_offset_sec, dep_delay)
    ("pb_t_minus_120", -120, 300),  # worst, also earliest
    ("pb_t_minus_60",   -60,  60),
    ("pb_t",              0, 120),  # latest
]


def _seed_three_observations_sync(pg_conn, agency_id: int) -> None:
    """psycopg2 variant for tests that go through pipeline.db._DEDUP_INNER."""
    now = datetime.now(timezone.utc)
    with pg_conn.cursor() as cur:
        for file_name, offset, dep_delay in _SEED:
            cur.execute(
                "INSERT INTO updates "
                "(agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES (%s, %s, %s, 'trip_a', '平日', '10:00', '16071', 1, %s)",
                (agency_id, file_name, now + timedelta(seconds=offset), dep_delay),
            )
    pg_conn.commit()


async def _seed_three_observations_async(aconn, agency_id: int) -> None:
    """asyncpg variant for tests that go through asyncpg-typed CTEs."""
    now = datetime.now(timezone.utc)
    for file_name, offset, dep_delay in _SEED:
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_a', '平日', '10:00', '16071', 1, $4)",
            agency_id,
            file_name,
            now + timedelta(seconds=offset),
            dep_delay,
        )


def test_db_dedup_inner_picks_latest_observation(pg_conn, agency_id):
    """pipeline.db._DEDUP_INNER (psycopg2 / used by analyze.py)."""
    _seed_three_observations_sync(pg_conn, agency_id)
    sql = f"WITH deduped AS ({DB_DEDUP_INNER}) SELECT dep_delay FROM deduped"
    with pg_conn.cursor() as cur:
        cur.execute(sql, {"agency_id": agency_id})
        rows = cur.fetchall()
    assert len(rows) == 1, f"expected one deduped row, got {len(rows)}"
    assert rows[0][0] == 120, (
        f"expected latest (120s), got {rows[0][0]} "
        "(would be 300 under old MAX semantics)"
    )


@pytest.mark.asyncio
async def test_executor_dedup_inner_picks_latest_observation(aconn, aagency_id):
    """pipeline.query.executor._DEDUP_INNER (asyncpg / used by /api/{id}/query)."""
    await _seed_three_observations_async(aconn, aagency_id)
    sql = f"WITH deduped AS ({EXEC_DEDUP_INNER}) SELECT dep_delay FROM deduped"
    rows = await aconn.fetch(sql, aagency_id)
    assert len(rows) == 1
    assert rows[0]["dep_delay"] == 120, (
        f"expected latest (120s), got {rows[0]['dep_delay']} "
        "(would be 300 under old MAX semantics)"
    )


@pytest.mark.asyncio
async def test_reports_dedup_cte_picks_latest_observation(aconn, aagency_id):
    """pipeline.reports._dedup_cte() (asyncpg / used by every compute_* report)."""
    await _seed_three_observations_async(aconn, aagency_id)
    today = datetime.now(timezone.utc).date()
    ctx = RangeCtx(from_date=today, to_date=today)
    where, params, _ = build_updates_filter(ctx, next_param=2)
    sql = f"WITH {_dedup_cte(where)} SELECT dep_delay FROM deduped"
    rows = await aconn.fetch(sql, aagency_id, *params)
    assert len(rows) == 1
    assert rows[0]["dep_delay"] == 120, (
        f"expected latest (120s), got {rows[0]['dep_delay']} "
        "(would be 300 under old MAX semantics)"
    )
