"""Query + merge helpers for the unified `/api/admin/audit` timeline.

`admin_audit` holds admin-initiated mutations (see
`api.admin_audit.record_admin_action`). `login_events` separately holds
user/session lifecycle events; only its `login` and `login_failed` kinds are
merged into the audit timeline (mapped to `login.ok` / `login.fail`) because
every other kind there (`role_changed`, `suspended`, ...) is itself an admin
action and is now recorded directly into `admin_audit` going forward, so
merging it too would double an event that already has its own `admin_audit`
row.

Pagination is a two-source keyset merge: each source is queried with an
`at <= cursor.at` boundary (inclusive, since the two sources' timestamps can
tie) and its own `LIMIT (page limit + 1)`, then the results are merged here
in Python. `(at, source, id)`, all descending, is the total order both the
merge and the cursor rely on -- `source`/`id` only break ties between rows
whose `at` is identical to microsecond resolution.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, time, timezone
from typing import Any

Row = dict[str, Any]
Cursor = dict[str, Any]

LOGIN_ACTION_BY_KIND: dict[str, str] = {"login": "login.ok", "login_failed": "login.fail"}


def normalize_admin_audit(row: Row) -> Row:
    """Map one `admin_audit` row (as fetched, with `before`/`after` selected
    as `::text`) onto the unified timeline shape."""
    before = row.get("before")
    after = row.get("after")
    return {
        "source": "audit",
        "id": row["id"],
        "at": row["at"],
        "actor_id": row.get("actor_id"),
        "action": row["action"],
        "target_type": row["target_type"],
        "target_id": row.get("target_id"),
        "before": json.loads(before) if isinstance(before, str) else before,
        "after": json.loads(after) if isinstance(after, str) else after,
        "reason": row.get("reason"),
        "ip": row.get("ip"),
    }


def normalize_login_event(row: Row) -> Row:
    """Map one `login_events` row (`kind` must be `login` or `login_failed`)
    onto the unified timeline shape. Raises `KeyError` for any other kind --
    callers only fetch rows with a kind in `LOGIN_ACTION_BY_KIND`."""
    action = LOGIN_ACTION_BY_KIND[row["kind"]]
    meta = row.get("meta")
    if isinstance(meta, str):
        meta = json.loads(meta) if meta else None
    reason = meta.get("reason") if isinstance(meta, dict) else None
    user_id = row.get("user_id")
    return {
        "source": "login",
        "id": row["event_id"],
        "at": row["at"],
        "actor_id": row.get("actor_id"),
        "action": action,
        "target_type": "user",
        "target_id": str(user_id) if user_id is not None else None,
        "before": None,
        "after": None,
        "reason": reason,
        "ip": row.get("ip"),
    }


def _sort_key(row: Row) -> tuple[Any, str, Any]:
    return (row["at"], row["source"], row["id"])


def merge_audit_pages(
    audit_rows: list[Row],
    login_rows: list[Row],
    *,
    limit: int,
    cursor: Cursor | None = None,
) -> tuple[list[Row], Cursor | None]:
    """Merge two already-normalized row lists into one page of at most
    `limit` rows, newest first.

    `cursor`, if given, is the identity of the last row already returned on
    a previous page -- both source queries re-include that boundary row (via
    `at <= cursor.at`) to stay correct across ties, so it is filtered back
    out here before merging.

    Returns `(page, next_cursor)`; `next_cursor` is `None` once the combined
    pool (after the cursor-row exclusion) fits within `limit`.
    """
    pool = list(audit_rows) + list(login_rows)
    if cursor is not None:
        pool = [r for r in pool if not (r["source"] == cursor["source"] and r["id"] == cursor["id"])]
    pool.sort(key=_sort_key, reverse=True)
    has_more = len(pool) > limit
    page = pool[:limit]
    next_cursor: Cursor | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = {"at": last["at"], "source": last["source"], "id": last["id"]}
    return page, next_cursor


def encode_cursor(cursor: Cursor) -> str:
    """Opaque, URL-safe cursor token for a page boundary."""
    payload = {"at": cursor["at"].isoformat(), "source": cursor["source"], "id": cursor["id"]}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(token: str) -> Cursor:
    """Inverse of `encode_cursor`. Raises `ValueError` for any malformed or
    tampered token instead of letting a `KeyError`/`json.JSONDecodeError`/
    base64 error escape to the caller."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        return {
            "at": datetime.fromisoformat(payload["at"]),
            "source": payload["source"],
            "id": payload["id"],
        }
    except Exception as exc:
        raise ValueError(f"invalid cursor: {token!r}") from exc


def parse_bound(value: str | None, *, end: bool) -> datetime | None:
    """Parse a `from`/`to` query bound. Accepts a bare date (`YYYY-MM-DD`,
    clamped to that day's start or end in UTC) or a full ISO datetime
    (assumed UTC when it carries no offset). Raises `ValueError` on anything
    else -- the router turns that into a 422."""
    if not value:
        return None
    try:
        if len(value) == 10:
            d = date.fromisoformat(value)
            t = time.max if end else time.min
            return datetime.combine(d, t, tzinfo=timezone.utc)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise ValueError(f"invalid date: {value!r}") from exc
