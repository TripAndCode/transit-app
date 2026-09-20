"""The single entry point every admin mutation writes its audit trail through.

The body is deliberately a log line for now: the `admin_audit` table and the
`/admin/audit` feed that reads it land with the audit-log work, and this
module's implementation is replaced then. Call sites are written against this
signature from the start so that swap is a one-file change rather than an
edit to every mutating route.

`before`/`after` hold the raw column values of the row being changed —
emails, names, roles. Only their *keys* are logged: a log stream is a
different retention and access boundary from the audit table, and printing a
user's email into it as a side effect of an admin action is a data-protection
leak the audit table itself does not have. The replacement implementation
must keep that split — values go to the table, field names go to the log.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

import asyncpg

_log = logging.getLogger(__name__)


def _changed_fields(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[str]:
    return sorted({*(before or {}), *(after or {})})


async def record_admin_action(
    conn: asyncpg.Connection,
    *,
    actor_id: int,
    action: str,
    target_type: str,
    target_id: str | None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    """Record one administrative action.

    ``action`` is a dotted verb scoped to its surface (``user.suspend``,
    ``agency.disable``, ``flag.set``); ``target_type``/``target_id`` identify
    the row it acted on, with ``target_id`` ``None`` for an action against the
    system rather than a row. ``reason`` is the operator's free-text
    justification where the surface collects one.
    """
    _log.info(
        "admin action actor=%s action=%s target=%s/%s fields=%s reason=%s",
        actor_id,
        action,
        target_type,
        target_id,
        ",".join(_changed_fields(before, after)) or "-",
        "yes" if reason else "no",
    )
