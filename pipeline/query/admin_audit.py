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
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

Row = dict[str, Any]
Cursor = dict[str, Any]

LOGIN_ACTION_BY_KIND: dict[str, str] = {"login": "login.ok", "login_failed": "login.fail"}

#: A bare `YYYY-MM-DD` bound is the JST civil day an operator means (matching
#: `api/admin_runs.py`'s day window), not a UTC one -- a UTC reading of "today"
#: would clip up to nine hours off the JST day the operator is actually asking
#: about.
_JST = ZoneInfo("Asia/Tokyo")


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


def cursor_bound(source: str, cursor: Cursor) -> str:
    """Which boundary `source`'s own query needs for a keyset page.

    The total order is `(at, source, id)` descending, so a bound on `at`
    alone is not enough: rows sharing the cursor's timestamp are ordered
    among themselves by source and then id. Bounding only on `at` re-fetches
    the same highest-id tied rows on every page, so once one source has more
    rows at a single timestamp than the page's fetch limit, the lower-id ones
    never enter the window and are lost from the timeline for good -- while
    the ones that do come back are served twice.

    - `compound`: same source as the cursor, so ties are split by id.
    - `inclusive`: sorts below the cursor's source, so its tied rows are all
      still to come.
    - `exclusive`: sorts above it, so its tied rows were already served.
    """
    if source == cursor["source"]:
        return "compound"
    return "inclusive" if source < cursor["source"] else "exclusive"


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
    a previous page. Each source query is bounded by `cursor_bound`, which
    excludes everything already served -- including the boundary row itself
    -- so nothing has to be filtered back out here.

    Returns `(page, next_cursor)`; `next_cursor` is `None` once the combined
    pool (after the cursor-row exclusion) fits within `limit`.
    """
    pool = list(audit_rows) + list(login_rows)
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
        id_ = payload["id"]
        source = payload["source"]
        if not isinstance(id_, int):
            raise ValueError(f"cursor id must be an int, got {id_!r}")
        if source not in ("audit", "login"):
            raise ValueError(f"cursor source must be 'audit' or 'login', got {source!r}")
        return {
            "at": datetime.fromisoformat(payload["at"]),
            "source": source,
            "id": id_,
        }
    except Exception as exc:
        raise ValueError(f"invalid cursor: {token!r}") from exc


def parse_bound(value: str | None, *, end: bool) -> datetime | None:
    """Parse a `from`/`to` query bound. Accepts a bare date (`YYYY-MM-DD`,
    clamped to that JST civil day's start or end, expressed in UTC) or a full
    ISO datetime (assumed UTC when it carries no offset). Raises `ValueError`
    on anything else -- the router turns that into a 422."""
    if not value:
        return None
    try:
        if len(value) == 10:
            d = date.fromisoformat(value)
            day_start = datetime.combine(d, time.min, tzinfo=_JST).astimezone(timezone.utc)
            if not end:
                return day_start
            return day_start + timedelta(days=1) - timedelta(microseconds=1)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise ValueError(f"invalid date: {value!r}") from exc
