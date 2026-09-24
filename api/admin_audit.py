"""Unified admin-action audit trail, backed by the `admin_audit` table.

Distinct from `pipeline.audit.record_event` (`login_events`): that table
covers user/session lifecycle (login, logout, role/suspend/LLM-approval
changes tied to a specific user row); this one covers every admin mutation
across the whole admin surface (users, agencies, and siblings added
elsewhere), each with a before/after snapshot so `/api/admin/audit` can
render a diff. `pipeline.query.admin_audit` merges the two into one timeline
for that endpoint.

`before`/`after` hold the raw column values of what changed — emails, names,
roles, and whatever free text an operator typed — either as one row's column
map or, where a surface replaces a whole policy table at once, as the list of
rows. They land in the table, which is access-controlled; nothing here writes
them to a log stream, whose retention and reach are different.

Two invariants every caller depends on:

- It never raises in a way that turns a completed administrative change into
  a 500 for the operator who made it. A missed entry is recoverable; a
  mutation that committed while its caller was told it failed is not.
- `conn` is the connection the mutation itself ran on, so the entry is
  written inside the caller's transaction and the change and its audit
  record commit or roll back together.
"""

import json
from typing import Any, Mapping, Sequence

import asyncpg

#: One row's column map, or the rows of a table replaced wholesale.
AuditPayload = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None


def _to_jsonb(payload: AuditPayload) -> str | None:
    """Serialize a payload for the JSONB column.

    ``default=str`` because these are raw database rows: timestamps and
    other non-JSON scalars reach here verbatim, and an audit entry must not
    be what fails a mutation that already succeeded.
    """
    if payload is None:
        return None
    return json.dumps(payload, default=str)


async def record_admin_action(
    conn: asyncpg.Connection,
    *,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: str | int | None,
    before: AuditPayload = None,
    after: AuditPayload = None,
    reason: str | None = None,
) -> None:
    """Record one administrative action.

    ``action`` is a dotted verb scoped to its surface (``user.suspend``,
    ``agency.disable``, ``flag.set``); ``target_type``/``target_id`` identify
    the row it acted on, with ``target_id`` ``None`` for an action against the
    system rather than a row. ``reason`` is the operator's free-text
    justification where the surface collects one.
    """
    await conn.execute(
        """
        INSERT INTO admin_audit (actor_id, action, target_type, target_id, before, after, reason)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
        """,
        actor_id,
        action,
        target_type,
        None if target_id is None else str(target_id),
        _to_jsonb(before),
        _to_jsonb(after),
        reason,
    )
