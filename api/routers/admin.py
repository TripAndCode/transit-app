"""Admin user-management endpoints. All routes require role=admin.

Every mutating route records an entry through
``api.admin_audit.record_admin_action`` as well as the per-user
``login_events`` trail, so an operator's audit visibility does not depend on
which control they used. The bulk route's self-guard is narrower than the
single-user one by design: it blocks only the two transitions that could
lock the operator out, where the single-user route refuses any self-edit.

Mutating routes (PATCH, DELETE) carry two structural guards:

- **self-guard**: an admin cannot mutate or delete their own row. Prevents
  accidental self-demotion / self-deletion that would lock the operator
  out of the admin surface.
- **last-admin guard**: a transition that would leave zero active
  (non-suspended) admins is rejected. The count + UPDATE are wrapped in
  ``async with conn.transaction():`` with a ``SELECT ... FOR UPDATE`` that
  locks the target row *and* every other active-admin row (in a fixed
  ``user_id`` order) so two parallel demotes of two different admins
  can't each independently observe "one other admin exists" and race
  past the guard.

Soft-delete preserves the audit trail: rows in ``login_events`` survive
(FK is ``ON DELETE SET NULL``), but the user's PII is anonymized
(``email -> deleted-{uid}@local``, name/avatar nulled), sessions are
killed, and OAuth identities are removed so re-login under the same
provider sub creates a fresh user.
"""

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from api.admin_audit import record_admin_action
from api.admin_board import board_alerts, board_freshness, board_window, collector_tiles
from api.deps import get_ch, get_conn
from api.routers.agencies import AdminAgencyOut
from api.security import User, csrf_guard, require_admin, token_hash
from api.sqlutil import escape_like
from pipeline.admin_users import session_id_prefix, unique_prefix_match
from pipeline.audit import record_event
from pipeline.query import admin_audit as _admin_audit
from pipeline.query import agencies as _agencies

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_BULK_USER_IDS = 200
MAX_API_KEYS_LISTED = 500


class UserRow(BaseModel):
    """One user row as returned by the admin list/detail endpoints."""

    user_id: int
    email: str
    name: str | None
    avatar_url: str | None
    role: str
    suspended_at: Any
    llm_approved: bool
    created_at: Any


class UserList(BaseModel):
    """Paginated admin user listing wrapper."""

    users: list[UserRow]
    total: int


@router.get("/users", response_model=UserList)
async def list_users(
    q: str | None = None,
    role: str | None = None,
    suspended: bool | None = None,
    llm_approved: bool | None = None,
    limit: int = 50,
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> UserList:
    """List users with optional filters.

    - ``q``: substring match on email OR name (ILIKE).
    - ``role``: exact match, restricted to ``user`` / ``admin`` (silently
      ignored otherwise so a malformed query param doesn't 500).
    - ``suspended``: ``True`` filters to suspended only, ``False`` to active.
    - ``llm_approved``: filters to the matching approval state -- backs the
      "awaiting approval" saved view and its badge count.
    - ``limit`` clamped to [1, 200] to keep response sizes bounded.

    ``total`` counts every match, not just the returned page, so a caller
    that only wants "how many are there" can ask for one row and read it.
    """
    limit = max(1, min(200, limit))
    where = []
    args: list[Any] = []
    if q:
        args.append(f"%{escape_like(q)}%")
        where.append(f"(email ILIKE ${len(args)} ESCAPE '\\' OR name ILIKE ${len(args)} ESCAPE '\\')")
    if role in ("user", "admin"):
        args.append(role)
        where.append(f"role = ${len(args)}")
    if suspended is True:
        where.append("suspended_at IS NOT NULL")
    elif suspended is False:
        where.append("suspended_at IS NULL")
    if llm_approved is True:
        where.append("llm_approved")
    elif llm_approved is False:
        where.append("NOT llm_approved")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = await conn.fetchval(f"SELECT count(*) FROM users {where_sql}", *args)
    rows = await conn.fetch(
        f"""
        SELECT user_id, email, name, avatar_url, role, suspended_at, llm_approved, created_at
        FROM users {where_sql}
        ORDER BY created_at DESC
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """,
        *args,
        limit,
        offset,
    )
    return UserList(users=[UserRow(**dict(r)) for r in rows], total=total)


class UserDetail(UserRow):
    """User row + linked OAuth identities + recent audit events."""

    identities: list[dict]
    recent_events: list[dict]
    # BYOK (bring-your-own-key) presence only -- never the key or its
    # suffix. `None` means the user hasn't configured a provider key.
    byok_provider: str | None


@router.get("/users/{uid}", response_model=UserDetail)
async def user_detail(
    uid: int,
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> UserDetail:
    """Return a user plus their linked OAuth identities and last 20 audit
    events. ``meta`` is stored as jsonb but cast to text and re-parsed here
    so the JSON shape is preserved in the response without asyncpg's
    string-of-json quirk.
    """
    row = await conn.fetchrow(
        "SELECT user_id, email, name, avatar_url, role, suspended_at, llm_approved, created_at "
        "FROM users WHERE user_id=$1",
        uid,
    )
    if not row:
        raise HTTPException(404, "user not found")
    ids = await conn.fetch(
        "SELECT provider, provider_sub, email_at_link, created_at "
        "FROM oauth_identities WHERE user_id=$1 ORDER BY created_at DESC",
        uid,
    )
    events = await conn.fetch(
        "SELECT event_id, kind, provider, meta::text AS meta_text, created_at "
        "FROM login_events WHERE user_id=$1 ORDER BY created_at DESC LIMIT 20",
        uid,
    )
    # Presence-only check -- deliberately not pipeline.query.user_llm_keys's
    # get_user_llm_key, which decrypts the stored value and requires
    # LLM_KEY_ENCRYPTION_KEY to be configured. This admin view never needs
    # (or should see) the key itself, so it skips that dependency entirely.
    byok_row = await conn.fetchrow("SELECT provider FROM user_llm_keys WHERE user_id=$1", uid)
    return UserDetail(
        **dict(row),
        identities=[dict(i) for i in ids],
        recent_events=[
            {
                "event_id": e["event_id"],
                "kind": e["kind"],
                "provider": e["provider"],
                "meta": json.loads(e["meta_text"]) if e["meta_text"] else None,
                "created_at": e["created_at"],
            }
            for e in events
        ],
        byok_provider=byok_row["provider"] if byok_row else None,
    )


class UserPatch(BaseModel):
    """Partial update body for an admin PATCH on a user."""

    role: str | None = None
    suspended: bool | None = None
    llm_approved: bool | None = None


class UserBulkPatch(BaseModel):
    """Request body for a bulk admin PATCH across many users at once."""

    ids: list[int] = Field(min_length=1, max_length=MAX_BULK_USER_IDS)
    patch: UserPatch


def _bulk_self_guard(patch: UserPatch, actor_id: int, ids: list[int]) -> None:
    """Refuse a bulk patch that would demote (role -> non-admin) or suspend
    the calling admin, even when their id is only one of many in ``ids``.
    Approving/revoking LLM access for one's own id is not blocked -- only
    the two transitions that could lock the operator out of the admin
    surface are.
    """
    demotes_or_suspends = (patch.role is not None and patch.role != "admin") or patch.suspended is True
    if demotes_or_suspends and actor_id in ids:
        raise HTTPException(400, "cannot demote or suspend self")


@router.patch("/users/bulk", response_model=list[UserRow])
async def bulk_patch_users(
    body: UserBulkPatch,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[UserRow]:
    """Apply one patch to many users in a single transaction.

    Registered ahead of ``PATCH /users/{uid}`` so ``/users/bulk`` doesn't
    fall into that route's ``uid: int`` path parameter first.

    Duplicate ids are collapsed (order preserved). The self-guard and the
    invalid-role check run before any row is locked, so a rejected request
    never touches the database. The last-admin guard mirrors the single-user
    endpoint's: it locks every targeted row plus every other active admin
    row (fixed ``user_id`` order, same deadlock-avoidance rationale as
    ``_lock_target_and_active_admins``) and simulates the patch across the
    whole set before committing, so a bulk suspend/demote can't zero out the
    active-admin count even when no single id in the batch is the caller.
    One audit action is recorded for the whole batch, carrying the id list.
    """
    csrf_guard(request)
    patch = body.patch
    if patch.role is not None and patch.role not in ("user", "admin"):
        raise HTTPException(400, "invalid role")
    if patch.role is None and patch.suspended is None and patch.llm_approved is None:
        raise HTTPException(400, "empty patch")

    ids = list(dict.fromkeys(body.ids))
    _bulk_self_guard(patch, admin.user_id, ids)

    async with conn.transaction():
        rows = await conn.fetch(
            """
            SELECT user_id, email, role, suspended_at, llm_approved FROM users
            WHERE user_id = ANY($1::int[]) OR (role='admin' AND suspended_at IS NULL)
            ORDER BY user_id
            FOR UPDATE
            """,
            ids,
        )
        by_id = {r["user_id"]: r for r in rows}
        missing = [uid for uid in ids if uid not in by_id]
        if missing:
            raise HTTPException(404, f"user(s) not found: {missing}")

        target_ids = set(ids)
        remaining_active_admins = 0
        for r in rows:
            if r["user_id"] in target_ids:
                new_role = patch.role if patch.role is not None else r["role"]
                new_suspended = patch.suspended if patch.suspended is not None else (r["suspended_at"] is not None)
            else:
                new_role, new_suspended = r["role"], r["suspended_at"] is not None
            if new_role == "admin" and not new_suspended:
                remaining_active_admins += 1
        if remaining_active_admins == 0:
            raise HTTPException(400, "would leave no admins")

        set_clauses = ["updated_at = now()"]
        args: list[Any] = []
        if patch.role is not None:
            args.append(patch.role)
            set_clauses.append(f"role = ${len(args)}")
        if patch.suspended is not None:
            args.append(datetime.now(timezone.utc) if patch.suspended else None)
            set_clauses.append(f"suspended_at = ${len(args)}")
        if patch.llm_approved is not None:
            args.append(patch.llm_approved)
            set_clauses.append(f"llm_approved = ${len(args)}")
        args.append(ids)
        await conn.execute(
            f"UPDATE users SET {', '.join(set_clauses)} WHERE user_id = ANY(${len(args)}::int[])",
            *args,
        )
        if patch.suspended is True:
            await conn.execute("DELETE FROM sessions WHERE user_id = ANY($1::int[])", ids)

        # The same per-user `login_events` trail the single-user endpoint
        # writes. Without it the durable record of who was suspended or
        # demoted would depend on which control the operator happened to
        # use, and the bulk path is the one that touches the most rows.
        # Only actual transitions are recorded, so a no-op id in a batch
        # does not manufacture an event.
        for uid in ids:
            before_row = by_id[uid]
            old_suspended = before_row["suspended_at"] is not None
            if patch.suspended is not None and patch.suspended != old_suspended:
                await record_event(
                    conn,
                    user_id=uid,
                    actor_id=admin.user_id,
                    kind="suspended" if patch.suspended else "unsuspended",
                )
            if patch.role is not None and patch.role != before_row["role"]:
                await record_event(
                    conn,
                    user_id=uid,
                    actor_id=admin.user_id,
                    kind="role_changed",
                    meta={"old": before_row["role"], "new": patch.role},
                )
            if patch.llm_approved is not None and patch.llm_approved != before_row["llm_approved"]:
                await record_event(
                    conn,
                    user_id=uid,
                    actor_id=admin.user_id,
                    kind="llm_approved_changed",
                    meta={"old": before_row["llm_approved"], "new": patch.llm_approved},
                )

        out_rows = await conn.fetch(
            "SELECT user_id, email, name, avatar_url, role, suspended_at, llm_approved, created_at "
            "FROM users WHERE user_id = ANY($1::int[]) ORDER BY user_id",
            ids,
        )
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="users.bulk_patch",
            target_type="user",
            target_id=",".join(str(i) for i in ids),
            # The rows themselves, not the request body: the seam reports the
            # union of a payload's field names, so wrapping the body would
            # log "ids,patch" for every bulk action instead of naming the
            # columns that actually moved.
            before=[
                {
                    "user_id": r["user_id"],
                    "role": r["role"],
                    "suspended_at": r["suspended_at"],
                    "llm_approved": r["llm_approved"],
                }
                for r in rows
                if r["user_id"] in target_ids
            ],
            after=[
                {
                    "user_id": r["user_id"],
                    "role": r["role"],
                    "suspended_at": r["suspended_at"],
                    "llm_approved": r["llm_approved"],
                }
                for r in out_rows
            ],
        )
    return [UserRow(**dict(r)) for r in out_rows]


async def _lock_target_and_active_admins(conn: asyncpg.Connection, uid: int) -> tuple[asyncpg.Record | None, int]:
    """Lock ``uid``'s row plus every active-admin row in one statement,
    always in ``user_id`` order.

    Locking only the target row (the previous approach) lets two
    transactions demoting two *different* admins each read the other's
    row as still-active before either commits, so both pass the guard and
    together leave zero admins. Locking the whole active-admin set makes
    concurrent admin-mutating transactions contend for the same rows and
    serialize; the fixed ``ORDER BY user_id`` keeps that contention from
    forming a lock-order deadlock (two transactions each locking their own
    target first, then trying to also lock the other's row).

    Returns ``(target_row_or_None, count_of_other_active_admins)``.
    """
    rows = await conn.fetch(
        """
        SELECT user_id, email, role, suspended_at, llm_approved FROM users
        WHERE user_id = $1 OR (role='admin' AND suspended_at IS NULL)
        ORDER BY user_id
        FOR UPDATE
        """,
        uid,
    )
    target = next((r for r in rows if r["user_id"] == uid), None)
    other_active_admins = sum(
        1 for r in rows if r["user_id"] != uid and r["role"] == "admin" and r["suspended_at"] is None
    )
    return target, other_active_admins


@router.patch("/users/{uid}", response_model=UserRow)
async def patch_user(
    uid: int,
    body: UserPatch,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> UserRow:
    """Update role and/or suspended flag.

    On suspend transition: kill all sessions for the target so the next
    request from them is a 401 instead of acting as a still-logged-in
    suspended user. Audit events fire for each transition kind.

    Last-admin guard catches both demotion (admin -> user) AND suspension
    of the sole remaining active admin.
    """
    csrf_guard(request)
    if uid == admin.user_id:
        raise HTTPException(400, "cannot modify self")
    if body.role is not None and body.role not in ("user", "admin"):
        raise HTTPException(400, "invalid role")

    async with conn.transaction():
        row, other_active_admins = await _lock_target_and_active_admins(conn, uid)
        if not row:
            raise HTTPException(404, "user not found")
        old_role = row["role"]
        old_suspended = row["suspended_at"] is not None
        old_llm_approved = row["llm_approved"]
        new_role = body.role if body.role is not None else old_role
        new_suspended = body.suspended if body.suspended is not None else old_suspended
        new_llm_approved = body.llm_approved if body.llm_approved is not None else old_llm_approved

        # last-admin guard: trip if this admin is demoted OR newly suspended
        becoming_non_admin = old_role == "admin" and new_role != "admin"
        becoming_suspended = old_role == "admin" and (not old_suspended) and new_suspended
        if (becoming_non_admin or becoming_suspended) and other_active_admins == 0:
            raise HTTPException(400, "would leave no admins")

        await conn.execute(
            "UPDATE users SET role=$1, suspended_at=$2, llm_approved=$3, updated_at=now() WHERE user_id=$4",
            new_role,
            datetime.now(timezone.utc) if new_suspended else None,
            new_llm_approved,
            uid,
        )
        if new_suspended and not old_suspended:
            await conn.execute("DELETE FROM sessions WHERE user_id=$1", uid)
            await record_event(conn, user_id=uid, actor_id=admin.user_id, kind="suspended")
        elif old_suspended and not new_suspended:
            await record_event(conn, user_id=uid, actor_id=admin.user_id, kind="unsuspended")
        if new_role != old_role:
            await record_event(
                conn, user_id=uid, actor_id=admin.user_id, kind="role_changed", meta={"old": old_role, "new": new_role}
            )
        if new_llm_approved != old_llm_approved:
            await record_event(
                conn,
                user_id=uid,
                actor_id=admin.user_id,
                kind="llm_approved_changed",
                meta={"old": old_llm_approved, "new": new_llm_approved},
            )

        before_fields = {"role": old_role, "suspended": old_suspended, "llm_approved": old_llm_approved}
        after_fields = {"role": new_role, "suspended": new_suspended, "llm_approved": new_llm_approved}
        changed = {k for k in before_fields if before_fields[k] != after_fields[k]}
        if changed:
            await record_admin_action(
                conn,
                actor_id=admin.user_id,
                action="user.updated",
                target_type="user",
                target_id=str(uid),
                before={k: before_fields[k] for k in changed},
                after={k: after_fields[k] for k in changed},
            )

        out = await conn.fetchrow(
            "SELECT user_id, email, name, avatar_url, role, suspended_at, llm_approved, created_at "
            "FROM users WHERE user_id=$1",
            uid,
        )
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="users.patch",
            target_type="user",
            target_id=uid,
            before={"role": old_role, "suspended": old_suspended, "llm_approved": old_llm_approved},
            after={"role": new_role, "suspended": new_suspended, "llm_approved": new_llm_approved},
        )
    return UserRow(**dict(out))


@router.delete("/users/{uid}", status_code=204)
async def delete_user(
    uid: int,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> Response:
    """Soft-delete: anonymize PII, suspend, drop sessions + identities,
    keep ``login_events`` intact for audit.

    Last-admin guard fires only if the target is currently an active
    admin (i.e. role=admin AND not suspended) — suspending an already-
    suspended admin doesn't remove an active admin from the pool.
    """
    csrf_guard(request)
    if uid == admin.user_id:
        raise HTTPException(400, "cannot modify self")
    async with conn.transaction():
        row, other_active_admins = await _lock_target_and_active_admins(conn, uid)
        if not row:
            raise HTTPException(404, "user not found")
        if row["role"] == "admin" and row["suspended_at"] is None and other_active_admins == 0:
            raise HTTPException(400, "would leave no admins")
        await conn.execute(
            """
            UPDATE users
            SET email = $1, name = NULL, avatar_url = NULL,
                suspended_at = now(), updated_at = now()
            WHERE user_id = $2
            """,
            f"deleted-{uid}@local",
            uid,
        )
        await conn.execute("DELETE FROM sessions WHERE user_id=$1", uid)
        await conn.execute("DELETE FROM oauth_identities WHERE user_id=$1", uid)
        await record_event(conn, user_id=uid, actor_id=admin.user_id, kind="deleted")
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="user.deleted",
            target_type="user",
            target_id=uid,
            before={
                "email": row["email"],
                "role": row["role"],
                "suspended_at": row["suspended_at"],
                "llm_approved": row["llm_approved"],
            },
            after={"email": f"deleted-{uid}@local"},
        )
    return Response(status_code=204)


# ── Sessions ──────────────────────────────────────────────────────────────


class SessionOut(BaseModel):
    """One active session, identified only by a display-safe prefix of its
    ``sid_hash`` -- the raw session id is a bearer credential, is never
    persisted, and is never returned to the admin UI."""

    sid_prefix: str
    created_at: Any
    last_seen_at: Any
    expires_at: Any
    user_agent: str | None
    ip: str | None


@router.get("/users/{uid}/sessions", response_model=list[SessionOut])
async def list_user_sessions(
    uid: int,
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[SessionOut]:
    rows = await conn.fetch(
        "SELECT sid_hash, created_at, last_seen_at, expires_at, user_agent, ip::text AS ip "
        "FROM sessions WHERE user_id=$1 ORDER BY created_at DESC",
        uid,
    )
    return [
        SessionOut(
            sid_prefix=session_id_prefix(r["sid_hash"]),
            created_at=r["created_at"],
            last_seen_at=r["last_seen_at"],
            expires_at=r["expires_at"],
            user_agent=r["user_agent"],
            ip=r["ip"],
        )
        for r in rows
    ]


@router.delete("/users/{uid}/sessions/{sid_prefix}", status_code=204)
async def revoke_user_session(
    uid: int,
    sid_prefix: str,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> Response:
    """Revoke one session identified by an admin-visible prefix of its
    ``sid_hash``.

    Looks up every session hash for the user and picks the prefix match in
    Python (:func:`pipeline.admin_users.unique_prefix_match`) rather than a
    SQL ``LIKE $1 || '%'`` -- an exact ``left(sid_hash, length($1)) = $1``
    comparison, driven by that lookup, never treats a caller-supplied
    prefix as a wildcard pattern. A prefix that matches zero or more than
    one session (astronomically unlikely for random tokens, but never
    assumed) 404s instead of deleting the wrong -- or multiple -- sessions.
    """
    csrf_guard(request)
    async with conn.transaction():
        rows = await conn.fetch("SELECT sid_hash FROM sessions WHERE user_id=$1", uid)
        sid_hash = unique_prefix_match([r["sid_hash"] for r in rows], sid_prefix)
        if sid_hash is None:
            raise HTTPException(404, "session not found")
        await conn.execute("DELETE FROM sessions WHERE user_id=$1 AND left(sid_hash, length($2)) = $2", uid, sid_hash)
        await record_event(conn, user_id=uid, actor_id=admin.user_id, kind="session_revoked")
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="session_revoked",
            target_type="user",
            target_id=str(uid),
        )
    return Response(status_code=204)


# ── API keys ──────────────────────────────────────────────────────────────


class ApiKeyOut(BaseModel):
    """One admin-issued API key's metadata. Never the raw key or its hash."""

    id: int
    owner_user_id: int | None
    tier: str
    label: str | None
    created_at: Any
    expires_at: Any
    revoked_at: Any


class ApiKeyIssued(ApiKeyOut):
    """Same shape as :class:`ApiKeyOut` plus the raw key -- returned exactly
    once, from the issuing POST response, and never persisted in this form."""

    key: str


class ApiKeyCreate(BaseModel):
    owner_user_id: int
    tier: str = "pro"
    label: str | None = None
    expires_at: Any = None


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    owner_user_id: int | None = None,
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[ApiKeyOut]:
    """List admin-issued API keys (rows with an ``owner_user_id``) -- excludes
    legacy operator-inserted rows that predate this table's ownership/label
    columns. ``key_hash`` no longer distinguishes the two: it is backfilled
    for every row, admin-issued or legacy.

    Bounded: unscoped, this returns every admin-issued key in the system, and
    that set only grows."""
    rows = await conn.fetch(
        """
        SELECT id, owner_user_id, tier, label, created_at, expires_at, revoked_at
        FROM api_keys
        WHERE owner_user_id IS NOT NULL AND ($1::int IS NULL OR owner_user_id = $1)
        ORDER BY created_at DESC
        LIMIT $2
        """,
        owner_user_id,
        MAX_API_KEYS_LISTED,
    )
    return [ApiKeyOut(**dict(r)) for r in rows]


@router.post("/api-keys", response_model=ApiKeyIssued, status_code=201)
async def issue_api_key(
    body: ApiKeyCreate,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> ApiKeyIssued:
    """Generate a raw API key, store only its hash, and return the raw value
    once. ``key_hash`` is ``api.security.token_hash`` of the raw key -- the
    same digest the auth stack uses for sessions -- so this key validates
    through the same ``key_hash`` lookup as any other row in the table."""
    csrf_guard(request)
    async with conn.transaction():
        owner = await conn.fetchval("SELECT 1 FROM users WHERE user_id=$1", body.owner_user_id)
        if not owner:
            raise HTTPException(404, "owner user not found")
        raw_key = f"sk_{secrets.token_urlsafe(32)}"
        digest = token_hash(raw_key)
        row = await conn.fetchrow(
            """
            INSERT INTO api_keys (key_hash, owner_user_id, tier, label, expires_at, owner_email)
            VALUES ($1, $2, $3, $4, $5, (SELECT email FROM users WHERE user_id=$2))
            RETURNING id, owner_user_id, tier, label, created_at, expires_at, revoked_at
            """,
            digest,
            body.owner_user_id,
            body.tier,
            body.label,
            body.expires_at,
        )
        await record_event(
            conn, user_id=body.owner_user_id, actor_id=admin.user_id, kind="api_key_issued", meta={"label": body.label}
        )
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="api_key_issued",
            target_type="user",
            target_id=str(body.owner_user_id),
            after={"label": body.label, "tier": body.tier},
        )
    return ApiKeyIssued(**dict(row), key=raw_key)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> Response:
    csrf_guard(request)
    async with conn.transaction():
        row = await conn.fetchrow(
            "UPDATE api_keys SET revoked_at = now() "
            "WHERE id=$1 AND owner_user_id IS NOT NULL AND revoked_at IS NULL "
            "RETURNING id, owner_user_id",
            key_id,
        )
        if not row:
            raise HTTPException(404, "api key not found")
        await record_event(conn, user_id=row["owner_user_id"], actor_id=admin.user_id, kind="api_key_revoked")
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="api_key_revoked",
            target_type="api_key",
            target_id=str(key_id),
        )
    return Response(status_code=204)


# ── Invites ───────────────────────────────────────────────────────────────


class InviteCreate(BaseModel):
    email: str
    role: str = "user"
    llm_approved: bool = False


class InviteOut(BaseModel):
    invite_id: int
    email: str
    role: str
    llm_approved: bool
    created_at: Any
    expires_at: Any


@router.post("/invites", response_model=InviteOut, status_code=201)
async def create_invite(
    body: InviteCreate,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> InviteOut:
    """Pre-approve a role (and optionally LLM access) for an email that
    hasn't signed in yet. Honored by the OAuth callback on that email's
    first login (see ``api/routers/auth.py``'s ``_upsert_user``)."""
    csrf_guard(request)
    async with conn.transaction():
        if body.role not in ("user", "admin"):
            raise HTTPException(400, "invalid role")
        row = await conn.fetchrow(
            """
            INSERT INTO user_invites (email, role, llm_approved, invited_by)
            VALUES ($1, $2, $3, $4)
            RETURNING invite_id, email, role, llm_approved, created_at, expires_at
            """,
            body.email,
            body.role,
            body.llm_approved,
            admin.user_id,
        )
        await record_event(
            conn,
            user_id=None,
            actor_id=admin.user_id,
            kind="invite_created",
            meta={"email": body.email, "role": body.role, "llm_approved": body.llm_approved},
        )
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="invite_created",
            target_type="invite",
            target_id=str(row["invite_id"]),
            after={"email": body.email, "role": body.role, "llm_approved": body.llm_approved},
        )
    return InviteOut(**dict(row))


# ── Ops health endpoint ──────────────────────────────────────────────────


class MigrationStatusOut(BaseModel):
    applied: str | None
    latest: str | None
    behind: int


class AgencyFreshnessOut(BaseModel):
    agency_id: int
    agency_name: str
    last_analyzed_at: Any  # datetime | None
    analyze_age_hours: Any  # float | None
    agg_fresh: bool
    agg_behind_days: int
    is_stale: bool
    data_to: Any  # str | None
    clamp_pct: Any  # float | None


class OpsHealth(BaseModel):
    migrations: MigrationStatusOut | None
    agencies: list[AgencyFreshnessOut]
    # False only when the agencies sub-check itself threw — lets the frontend
    # tell "checked, zero agencies" apart from "check failed" (both would
    # otherwise be an indistinguishable empty `agencies` list).
    agencies_ok: bool


@router.get("/ops", response_model=OpsHealth)
async def admin_ops(
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> OpsHealth:
    """Read-only ops health snapshot. Graceful degradation: failing sub-checks return null."""
    from pipeline.health import aggregate_freshness, migration_status

    mig: MigrationStatusOut | None = None
    try:
        ms = await migration_status(conn)
        mig = MigrationStatusOut(applied=ms.applied, latest=ms.latest, behind=ms.behind)
    except Exception:
        _log.warning("admin_ops: migration_status failed — degrading to null", exc_info=True)

    agencies_out: list[AgencyFreshnessOut] = []
    agencies_ok = True
    try:
        for af in await aggregate_freshness(conn, ch):
            agencies_out.append(
                AgencyFreshnessOut(
                    agency_id=af.agency_id,
                    agency_name=af.agency_name,
                    last_analyzed_at=af.last_analyzed_at.isoformat() if af.last_analyzed_at else None,
                    analyze_age_hours=af.analyze_age_hours,
                    agg_fresh=af.agg_fresh,
                    agg_behind_days=af.agg_behind_days,
                    is_stale=af.is_stale,
                    data_to=af.data_to,
                    clamp_pct=af.clamp_pct,
                )
            )
    except Exception:
        _log.warning("admin_ops: aggregate_freshness failed — degrading to empty agencies list", exc_info=True)
        agencies_out = []
        agencies_ok = False

    return OpsHealth(migrations=mig, agencies=agencies_out, agencies_ok=agencies_ok)


@router.get("/agencies", response_model=list[AdminAgencyOut])
async def list_admin_agencies(
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[dict[str, Any]]:
    """Admin list of ALL agencies including soft-deleted."""
    return await _agencies.list_agencies(conn, include_deleted=True)


# ── Unified audit log ────────────────────────────────────────────────────


class AuditRowOut(BaseModel):
    """One row of the merged `admin_audit` + `login_events` timeline."""

    at: Any  # datetime — kept Any to avoid asyncpg datetime serialization issues
    actor_id: int | None
    action: str
    target_type: str
    target_id: str | None
    before: Any
    after: Any
    reason: str | None
    ip: str | None


class AuditPage(BaseModel):
    items: list[AuditRowOut]
    next_cursor: str | None


@router.get("/audit", response_model=AuditPage)
async def list_admin_audit(
    actor: int | None = None,
    target: str | None = None,
    action: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> AuditPage:
    """Paged, filterable admin-action timeline: `admin_audit` merged with the
    `login`/`login_failed` events from `login_events` (mapped to
    `login.ok`/`login.fail`) -- every other `login_events` kind is an admin
    action already recorded directly into `admin_audit`.

    `target` matches either `target_type` or `target_id` (exact). `cursor`
    is opaque, from a previous page's `next_cursor`.
    """
    limit = max(1, min(200, limit))
    try:
        from_dt = _admin_audit.parse_bound(from_, end=False)
        to_dt = _admin_audit.parse_bound(to, end=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    cursor_row: dict[str, Any] | None = None
    if cursor:
        try:
            cursor_row = _admin_audit.decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    fetch_limit = limit + 1

    a_where: list[str] = []
    a_args: list[Any] = []
    if actor is not None:
        a_args.append(actor)
        a_where.append(f"actor_id = ${len(a_args)}")
    if target:
        a_args.append(target)
        a_where.append(f"(target_type = ${len(a_args)} OR target_id = ${len(a_args)})")
    if action:
        a_args.append(action)
        a_where.append(f"action = ${len(a_args)}")
    if from_dt is not None:
        a_args.append(from_dt)
        a_where.append(f"at >= ${len(a_args)}")
    if to_dt is not None:
        a_args.append(to_dt)
        a_where.append(f"at <= ${len(a_args)}")
    if cursor_row is not None:
        a_args.append(cursor_row["at"])
        a_where.append(f"at <= ${len(a_args)}")
    a_where_sql = ("WHERE " + " AND ".join(a_where)) if a_where else ""
    a_args.append(fetch_limit)
    audit_raw = await conn.fetch(
        f"""
        SELECT id, at, actor_id, action, target_type, target_id,
               before::text AS before, after::text AS after, reason, ip::text AS ip
        FROM admin_audit
        {a_where_sql}
        ORDER BY at DESC, id DESC
        LIMIT ${len(a_args)}
        """,
        *a_args,
    )
    audit_rows = [_admin_audit.normalize_admin_audit(dict(r)) for r in audit_raw]

    # Only the login-derived kinds are part of this timeline; an `action`
    # filter for something else (e.g. "user.updated") means login_events
    # cannot contribute any row, so skip that query entirely.
    login_kinds = [k for k, v in _admin_audit.LOGIN_ACTION_BY_KIND.items() if action is None or v == action]
    login_rows: list[dict[str, Any]] = []
    if login_kinds:
        l_args: list[Any] = [login_kinds]
        l_where = ["kind = ANY($1)"]
        if actor is not None:
            l_args.append(actor)
            l_where.append(f"actor_id = ${len(l_args)}")
        if target:
            l_args.append(target)
            l_where.append(f"(${len(l_args)} = 'user' OR user_id::text = ${len(l_args)})")
        if from_dt is not None:
            l_args.append(from_dt)
            l_where.append(f"created_at >= ${len(l_args)}")
        if to_dt is not None:
            l_args.append(to_dt)
            l_where.append(f"created_at <= ${len(l_args)}")
        if cursor_row is not None:
            l_args.append(cursor_row["at"])
            l_where.append(f"created_at <= ${len(l_args)}")
        l_args.append(fetch_limit)
        login_raw = await conn.fetch(
            f"""
            SELECT event_id, created_at AS at, actor_id, user_id, kind, meta::text AS meta, ip::text AS ip
            FROM login_events
            WHERE {" AND ".join(l_where)}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ${len(l_args)}
            """,
            *l_args,
        )
        login_rows = [_admin_audit.normalize_login_event(dict(r)) for r in login_raw]

    page, next_cursor_row = _admin_audit.merge_audit_pages(audit_rows, login_rows, limit=limit, cursor=cursor_row)
    next_cursor = _admin_audit.encode_cursor(next_cursor_row) if next_cursor_row else None
    return AuditPage(
        items=[
            AuditRowOut(
                at=r["at"],
                actor_id=r["actor_id"],
                action=r["action"],
                target_type=r["target_type"],
                target_id=r["target_id"],
                before=r["before"],
                after=r["after"],
                reason=r["reason"],
                ip=r["ip"],
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )


# ── Architecture docs (developer/internal) endpoints ─────────────────────
#
# Backs `/admin/architecture` (item 25): a developer-only page rendering
# CLAUDE.md's "Architecture pointers" as a Mermaid diagram plus a
# sidebar-navigable index of `docs/features/*.md`. Filesystem-only (no DB
# connection needed) -- gated on `require_admin` the same way every other
# `/api/admin/*` route is, per the item's explicit decision to reuse the
# existing `admin` role rather than add a new "internal/developer" flag.
_FEATURE_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "features"

_DOC_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_DOC_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


class ArchitectureDocSummary(BaseModel):
    """One `docs/features/*.md` file, for the page's sidebar index."""

    slug: str
    title: str


class ArchitectureDocDetail(ArchitectureDocSummary):
    """Full Markdown content of one feature doc."""

    content: str


def _feature_doc_title(text: str, fallback_slug: str) -> str:
    """The file's own leading `# Title` line, or ``fallback_slug`` if it has
    none (e.g. the doc is empty or malformed) -- never raises."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _DOC_H1_RE.match(stripped)
        return m.group(1) if m else fallback_slug
    return fallback_slug


def _has_real_content(text: str) -> bool:
    """Return whether ``text`` has anything beyond HTML comments/whitespace --
    filters placeholder/scratch docs (e.g. an HTML-comment-only file) out of
    the admin doc listing without needing to delete them from git history."""
    return bool(_HTML_COMMENT_RE.sub("", text).strip())


def _iter_feature_docs() -> Iterator[tuple[Path, str]]:
    """Yield ``(path, content)`` for every `docs/features/*.md` file with real
    content, sorted by filename for a stable, deterministic sidebar order.
    Never cached at import time -- a doc file added while the server is
    already running (this directory grows over time; see docs/refactor-log.md
    item 26) shows up on the next request with no restart needed. Reads each
    file's content exactly once, so callers should consume this instead of
    re-reading a path returned by ``_list_feature_docs``."""
    if not _FEATURE_DOCS_DIR.is_dir():
        return
    for path in sorted(_FEATURE_DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if _has_real_content(text):
            yield path, text


def _list_feature_docs() -> list[Path]:
    """Enumerate `docs/features/*.md` fresh on every call. Skips files with
    no real content (e.g. HTML-comment-only placeholders)."""
    return [path for path, _ in _iter_feature_docs()]


def _resolve_feature_doc_path(slug: str) -> Path | None:
    """Resolve ``slug`` to a `docs/features/<slug>.md` path inside
    ``_FEATURE_DOCS_DIR``, or ``None`` if the slug is malformed or would
    escape that directory (a `..` segment, an absolute path, or a
    symlink-following trick smuggled through the path param).

    Existence is not checked here -- the caller (which also needs to 404 on
    a real-but-empty file) does that with ``is_file()``.
    """
    if not _DOC_SLUG_RE.match(slug):
        return None
    candidate = (_FEATURE_DOCS_DIR / f"{slug}.md").resolve()
    if not candidate.is_relative_to(_FEATURE_DOCS_DIR.resolve()):
        return None
    return candidate


@router.get("/architecture/docs", response_model=list[ArchitectureDocSummary])
async def list_architecture_docs(_admin: User = Depends(require_admin)) -> list[ArchitectureDocSummary]:
    """List every `docs/features/*.md` file for the architecture page's
    sidebar. Read-only and filesystem-only -- no DB round trip."""
    return [
        ArchitectureDocSummary(slug=path.stem, title=_feature_doc_title(text, path.stem))
        for path, text in _iter_feature_docs()
    ]


@router.get("/architecture/docs/{slug}", response_model=ArchitectureDocDetail)
async def get_architecture_doc(slug: str, _admin: User = Depends(require_admin)) -> ArchitectureDocDetail:
    """Serve one feature doc's raw Markdown by slug (filename minus `.md`).

    ``slug`` is validated against a strict charset and resolved directly to
    `docs/features/<slug>.md` (see ``_resolve_feature_doc_path``), so this
    reads exactly the one file requested instead of scanning every doc in
    the directory to find it by filename.
    """
    path = _resolve_feature_doc_path(slug)
    if path is None or not path.is_file():
        raise HTTPException(404, "doc not found")
    text = path.read_text(encoding="utf-8")
    if not _has_real_content(text):
        raise HTTPException(404, "doc not found")
    return ArchitectureDocDetail(slug=slug, title=_feature_doc_title(text, slug), content=text)


# ── Control board ────────────────────────────────────────────────────────

#: Where the ops collectors find the git checkout they read. Named to match
#: the standalone status server's own override so one setting covers both.
_OPS_STATUS_REPO_ENV = "OPS_STATUS_REPO"

_COLLECTOR_BUDGET_SECONDS = 5.0

_BOARD_FRESHNESS_SQL = """
    SELECT a.agency_id, a.agency_name, m.analyzed_at, h.date, h.raw_samples, h.clamp_count
    FROM agencies a
    LEFT JOIN agg_meta m ON m.agency_id = a.agency_id
    LEFT JOIN agg_feed_health h ON h.agency_id = a.agency_id AND h.date >= $1
    WHERE a.deleted_at IS NULL
    ORDER BY a.agency_id, h.date
"""

_BOARD_AGENCIES_SQL = "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"

_PENDING_LLM_APPROVALS_SQL = "SELECT count(*) FROM users WHERE NOT llm_approved AND suspended_at IS NULL"


class CollectorTileOut(BaseModel):
    key: str
    label: str
    status: str  # ok | warn | down | unknown
    last_success_at: str | None
    detail: str | None
    history: list[int]


class FreshnessDayOut(BaseModel):
    date: str
    state: str  # fresh | stale | missing
    clamp_pct: float | None


class AgencyFreshnessRowOut(BaseModel):
    agency_id: int
    agency_name: str
    days: list[FreshnessDayOut]


class BoardAlertOut(BaseModel):
    level: str  # warn | info
    #: Stable identifier the UI translates; `text` is the untranslated
    #: summary, for consumers with no locale (logs, exports).
    code: str
    params: dict[str, Any]
    text: str
    href: str | None


class AdminBoard(BaseModel):
    collectors: list[CollectorTileOut]
    freshness: list[AgencyFreshnessRowOut]
    #: None only when the migration check itself threw — distinguishable from
    #: a genuine "0 behind", the same way `/admin/ops` already reports it.
    migrations: MigrationStatusOut | None
    alerts: list[BoardAlertOut]


def _collect_all() -> list[dict[str, Any]]:
    """The four ops collectors, imported lazily.

    `scripts/` is not part of the installed package set, and the collectors
    reach for `gh`/`aws` and the filesystem, so importing them at module scope
    would tie every admin route's importability to a tree the API does not
    otherwise need.
    """
    from scripts import ops_status_page

    # The collectors read a git checkout, and their default is the VPS's own
    # path. Any other host -- the API container among them -- has the tree
    # somewhere else or not at all, so the same `OPS_STATUS_REPO` override
    # the standalone status server reads decides where to look; without it
    # every tile degrades to `unknown` on every request rather than on a
    # real collector outage.
    override = os.environ.get(_OPS_STATUS_REPO_ENV)
    if override:
        return ops_status_page.collect_all(local_repo=Path(override))
    return ops_status_page.collect_all()


def _collector_reasons(documents: list[dict[str, Any]]) -> dict[str, str]:
    """Each non-healthy component's short human-facing explanation, reusing
    the combined status page's own reason builders rather than re-deriving
    them from the raw `details` bags."""
    try:
        from scripts import ops_status_page

        return {
            doc["component"]: reason for doc in documents if (reason := ops_status_page.reason_for(doc)) is not None
        }
    except Exception:
        _log.warning("board: collector reasons unavailable", exc_info=True)
        return {}


async def _collect_documents() -> list[dict[str, Any]]:
    """Collector documents, or `[]` once the budget is spent.

    The collectors are blocking and shell out, so they run in a worker thread
    under a wall-clock budget. A thread cannot be cancelled: on timeout the
    call is abandoned, the request answers with `unknown` tiles, and the
    orphaned thread finishes into the collectors' own caches — which the next
    poll then reads cheaply.

    At most one collection runs at a time. The default executor is shared
    with the embedder and the LLM calls and holds only a handful of threads,
    and this page polls on a timer from every operator who has it open, so
    without the guard abandoned collections would accumulate there and stall
    unrelated requests. Concurrent polls await the collection already in
    flight instead of starting another; `shield` keeps one poll's timeout
    from cancelling the shared task out from under the others.
    """
    global _collector_task
    task = _collector_task
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        task = _collector_task = asyncio.create_task(asyncio.to_thread(_collect_all))
    try:
        return await asyncio.wait_for(asyncio.shield(task), _COLLECTOR_BUDGET_SECONDS)
    except Exception:
        _log.warning("board: collectors unavailable within budget", exc_info=True)
        return []


#: The collection currently in flight, if any. Module-level rather than
#: per-request: its whole purpose is to be shared across requests.
_collector_task: asyncio.Task[list[dict[str, Any]]] | None = None


async def _freshness_rows(conn: asyncpg.Connection, window_start: date) -> list[Any]:
    """Agency x day feed-health rows, falling back to the bare agency list
    when the aggregate tables are absent (a freshly migrated environment), so
    the heatmap still shows who exists with every day missing."""
    for sql, args in ((_BOARD_FRESHNESS_SQL, (window_start,)), (_BOARD_AGENCIES_SQL, ())):
        try:
            return list(await conn.fetch(sql, *args))
        except Exception:
            _log.warning("board: freshness query failed, trying next fallback", exc_info=True)
            continue
    return []


@router.get("/board", response_model=AdminBoard)
async def admin_board(
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> AdminBoard:
    """The admin entry page's one snapshot: collectors, freshness, alerts."""
    from pipeline.health import migration_status

    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("Asia/Tokyo")).date()

    documents = await _collect_documents()
    collectors = collector_tiles(documents, now, reasons=_collector_reasons(documents))

    freshness = board_freshness(await _freshness_rows(conn, board_window(today)[0]), today)

    mig: MigrationStatusOut | None = None
    migrations = None
    try:
        migrations = await migration_status(conn)
        mig = MigrationStatusOut(applied=migrations.applied, latest=migrations.latest, behind=migrations.behind)
    except Exception:
        _log.warning("board: migration status unavailable", exc_info=True)  # mig stays None

    try:
        pending_llm_approvals = int(await conn.fetchval(_PENDING_LLM_APPROVALS_SQL) or 0)
    except Exception:
        _log.warning("board: pending-approvals count failed", exc_info=True)
        pending_llm_approvals = 0

    alerts = board_alerts(freshness=freshness, migrations=migrations, pending_llm_approvals=pending_llm_approvals)
    return AdminBoard(
        collectors=[CollectorTileOut(**tile) for tile in collectors],
        freshness=[AgencyFreshnessRowOut(**row) for row in freshness],
        migrations=mig,
        alerts=[BoardAlertOut(**alert) for alert in alerts],
    )
