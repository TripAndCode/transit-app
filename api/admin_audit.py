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

import ipaddress
import json
import logging
from typing import Any, Mapping, Sequence

import asyncpg

_log = logging.getLogger(__name__)

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


def _to_inet(ip: str | None) -> str | None:
    """Return `ip` as-is if it parses as an IP address, else `None`.

    A value that fails `::inet` at insert time would raise before the audit
    row -- and the mutation it describes -- can commit, so anything
    unparsable (a proxy that forwarded garbage) is dropped here instead.
    """
    if ip is None:
        return None
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


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
    ip: str | None = None,
) -> None:
    """Record one administrative action.

    ``action`` is a dotted verb scoped to its surface (``user.suspend``,
    ``agency.disable``, ``flag.set``); ``target_type``/``target_id`` identify
    the row it acted on, with ``target_id`` ``None`` for an action against the
    system rather than a row. ``reason`` is the operator's free-text
    justification where the surface collects one. ``ip`` is the actor's
    client address; unparsable or absent values are stored as ``NULL``
    rather than failing the insert.

    A failed insert is logged at WARNING and swallowed, per this module's
    docstring invariant that a missed audit entry must not turn an
    already-committed administrative change into a 500.

    Swallowed means database and connection failures -- the recoverable case
    the invariant is about. A `TypeError` or `AttributeError` from a call site
    is a bug in this repository, not an operational hazard, and catching it
    here would turn every such bug into audit rows that silently stop being
    written. Those propagate.

    The insert runs inside a nested transaction -- a savepoint -- because
    swallowing alone does not deliver that promise. A statement that fails
    inside a caller's open transaction aborts it at the server; catching the
    Python exception leaves the connection in a state where the caller's next
    statement, or its commit, raises `InFailedSQLTransactionError`. The
    change is lost anyway and the operator gets that instead of the real
    cause, which went to the log. Rolling back to a savepoint confines the
    failure to this insert, so the caller's transaction stays usable and the
    change it already made commits without its audit row.

    The savepoint does not weaken the module's other invariant. It only
    releases on success, so an audit row written here still rolls back with
    the caller's transaction if that transaction later fails: the two are
    still atomic in the direction that matters. What it gives up is the
    reverse -- a change can now commit unaudited -- which is the trade this
    module's docstring already names as the acceptable one.
    """
    try:
        # Nested `transaction()` is a savepoint when one is already open, and
        # a plain transaction when none is -- correct in both cases.
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO admin_audit (actor_id, action, target_type, target_id, before, after, reason, ip)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8::inet)
                """,
                actor_id,
                action,
                target_type,
                None if target_id is None else str(target_id),
                _to_jsonb(before),
                _to_jsonb(after),
                reason,
                _to_inet(ip),
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        _log.warning(
            "admin_audit: failed to record action=%s target_type=%s target_id=%s",
            action,
            target_type,
            target_id,
            exc_info=True,
        )
