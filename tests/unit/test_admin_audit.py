"""`record_admin_action` writes one `admin_audit` row on the caller's own
connection.

The seam used to be a log line, so its tests asserted that values stayed out
of the log. It now writes to a table instead, and these assert what the
insert actually carries -- including the two shapes callers pass (one row's
column map, or the rows of a policy table replaced wholesale) and the raw
database values that reach it.
"""

import json
from datetime import datetime, timezone

import pytest

from api.admin_audit import record_admin_action


class _RecordingConn:
    """Captures the insert instead of running it. No database involved."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "INSERT 1"


def _row(conn: _RecordingConn) -> dict:
    sql, args = conn.calls[0]
    assert "INSERT INTO admin_audit" in sql
    actor_id, action, target_type, target_id, before, after, reason = args
    return {
        "actor_id": actor_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "before": None if before is None else json.loads(before),
        "after": None if after is None else json.loads(after),
        "reason": reason,
    }


async def test_records_one_row_on_the_callers_connection():
    conn = _RecordingConn()
    await record_admin_action(
        conn,
        actor_id=1,
        action="user.patched",
        target_type="user",
        target_id=42,
        before={"role": "user"},
        after={"role": "admin"},
        reason="promotion",
    )
    row = _row(conn)
    assert row["action"] == "user.patched"
    assert row["before"] == {"role": "user"} and row["after"] == {"role": "admin"}
    assert row["reason"] == "promotion"


async def test_a_numeric_target_id_is_stored_as_text():
    """`target_id` is a TEXT column and callers pass whichever key type their
    table uses, so the coercion has to happen here rather than at each site."""
    conn = _RecordingConn()
    await record_admin_action(conn, actor_id=1, action="agency.updated", target_type="agency", target_id=7)
    assert _row(conn)["target_id"] == "7"


async def test_a_replaced_policy_table_is_recorded_as_its_rows():
    """Surfaces that swap a whole table pass the row list, not one row's
    columns; both shapes have to reach the JSONB column intact."""
    conn = _RecordingConn()
    rows = [{"route_code": "R1", "weight": 0.5}, {"route_code": "R2", "weight": 1.0}]
    await record_admin_action(
        conn, actor_id=1, action="agency.weights_updated", target_type="agency", target_id=3, after=rows
    )
    assert _row(conn)["after"] == rows


async def test_a_raw_timestamp_does_not_break_the_insert():
    """Callers hand over database rows verbatim, so non-JSON scalars reach
    here. An audit entry must not be what fails a completed mutation."""
    conn = _RecordingConn()
    await record_admin_action(
        conn,
        actor_id=1,
        action="user.deleted",
        target_type="user",
        target_id=9,
        before={"suspended_at": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)},
    )
    assert "2026-01-02" in _row(conn)["before"]["suspended_at"]


async def test_optional_arguments_may_be_omitted():
    conn = _RecordingConn()
    await record_admin_action(conn, actor_id=None, action="board.refresh", target_type="system", target_id=None)
    row = _row(conn)
    assert row["target_id"] is None and row["before"] is None and row["after"] is None and row["reason"] is None


async def test_every_argument_after_the_connection_is_keyword_only():
    with pytest.raises(TypeError):
        await record_admin_action(_RecordingConn(), 1, "user.patched", "user", "1")  # type: ignore[misc]
