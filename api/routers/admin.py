"""Admin user-management endpoints. All routes require role=admin.

Mutating routes (PATCH, DELETE) carry two structural guards:

- **self-guard**: an admin cannot mutate or delete their own row. Prevents
  accidental self-demotion / self-deletion that would lock the operator
  out of the admin surface.
- **last-admin guard**: a transition that would leave zero active
  (non-suspended) admins is rejected. The count + UPDATE are wrapped in
  ``async with conn.transaction():`` with a ``SELECT ... FOR UPDATE`` on
  the target row so two parallel demotes can't both observe "one other
  admin exists" and race past the guard.

Soft-delete preserves the audit trail: rows in ``login_events`` survive
(FK is ``ON DELETE SET NULL``), but the user's PII is anonymized
(``email -> deleted-{uid}@local``, name/avatar nulled), sessions are
killed, and OAuth identities are removed so re-login under the same
provider sub creates a fresh user.
"""

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.deps import get_conn
from api.routers.agencies import AdminAgencyOut
from api.security import User, csrf_guard, require_admin
from pipeline.audit import record_event

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserRow(BaseModel):
    """One user row as returned by the admin list/detail endpoints."""

    user_id: int
    email: str
    name: str | None
    avatar_url: str | None
    role: str
    suspended_at: Any
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
    limit: int = 50,
    offset: int = 0,
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """List users with optional filters.

    - ``q``: substring match on email OR name (ILIKE).
    - ``role``: exact match, restricted to ``user`` / ``admin`` (silently
      ignored otherwise so a malformed query param doesn't 500).
    - ``suspended``: ``True`` filters to suspended only, ``False`` to active.
    - ``limit`` clamped to [1, 200] to keep response sizes bounded.
    """
    limit = max(1, min(200, limit))
    where = []
    args: list[Any] = []
    if q:
        args.append(f"%{q}%")
        where.append(f"(email ILIKE ${len(args)} OR name ILIKE ${len(args)})")
    if role in ("user", "admin"):
        args.append(role)
        where.append(f"role = ${len(args)}")
    if suspended is True:
        where.append("suspended_at IS NOT NULL")
    elif suspended is False:
        where.append("suspended_at IS NULL")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = await conn.fetchval(f"SELECT count(*) FROM users {where_sql}", *args)
    rows = await conn.fetch(
        f"""
        SELECT user_id, email, name, avatar_url, role, suspended_at, created_at
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


@router.get("/users/{uid}", response_model=UserDetail)
async def user_detail(
    uid: int,
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Return a user plus their linked OAuth identities and last 20 audit
    events. ``meta`` is stored as jsonb but cast to text and re-parsed here
    so the JSON shape is preserved in the response without asyncpg's
    string-of-json quirk.
    """
    row = await conn.fetchrow(
        "SELECT user_id, email, name, avatar_url, role, suspended_at, created_at FROM users WHERE user_id=$1",
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
    )


class UserPatch(BaseModel):
    """Partial update body for an admin PATCH on a user."""

    role: str | None = None
    suspended: bool | None = None


async def _count_other_active_admins(conn: asyncpg.Connection, uid: int) -> int:
    """Count admins that are active (non-suspended) and not ``uid``.

    Used by the last-admin guard: the caller is about to change ``uid`` in
    a way that may strip the admin role / suspend it; if no OTHER active
    admin exists, the change would lock everyone out, so reject.
    """
    return await conn.fetchval(
        "SELECT count(*) FROM users WHERE role='admin' AND suspended_at IS NULL AND user_id != $1",
        uid,
    )


@router.patch("/users/{uid}", response_model=UserRow)
async def patch_user(
    uid: int,
    body: UserPatch,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
):
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
        row = await conn.fetchrow(
            "SELECT role, suspended_at FROM users WHERE user_id=$1 FOR UPDATE",
            uid,
        )
        if not row:
            raise HTTPException(404, "user not found")
        old_role = row["role"]
        old_suspended = row["suspended_at"] is not None
        new_role = body.role if body.role is not None else old_role
        new_suspended = body.suspended if body.suspended is not None else old_suspended

        # last-admin guard: trip if this admin is demoted OR newly suspended
        becoming_non_admin = old_role == "admin" and new_role != "admin"
        becoming_suspended = old_role == "admin" and (not old_suspended) and new_suspended
        if becoming_non_admin or becoming_suspended:
            if await _count_other_active_admins(conn, uid) == 0:
                raise HTTPException(400, "would leave no admins")

        await conn.execute(
            "UPDATE users SET role=$1, suspended_at=$2, updated_at=now() WHERE user_id=$3",
            new_role,
            datetime.now(timezone.utc) if new_suspended else None,
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

        out = await conn.fetchrow(
            "SELECT user_id, email, name, avatar_url, role, suspended_at, created_at FROM users WHERE user_id=$1",
            uid,
        )
    return UserRow(**dict(out))


@router.delete("/users/{uid}", status_code=204)
async def delete_user(
    uid: int,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
):
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
        row = await conn.fetchrow(
            "SELECT role, suspended_at FROM users WHERE user_id=$1 FOR UPDATE",
            uid,
        )
        if not row:
            raise HTTPException(404, "user not found")
        if row["role"] == "admin" and row["suspended_at"] is None:
            if await _count_other_active_admins(conn, uid) == 0:
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
    return Response(status_code=204)


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
):
    """Read-only ops health snapshot. Graceful degradation: failing sub-checks return null."""
    from pipeline.health import aggregate_freshness, migration_status

    mig: MigrationStatusOut | None = None
    try:
        ms = await migration_status(conn)
        mig = MigrationStatusOut(applied=ms.applied, latest=ms.latest, behind=ms.behind)
    except Exception:
        pass  # mig stays None

    agencies_out: list[AgencyFreshnessOut] = []
    agencies_ok = True
    try:
        for af in await aggregate_freshness(conn):
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
        agencies_out = []
        agencies_ok = False

    return OpsHealth(migrations=mig, agencies=agencies_out, agencies_ok=agencies_ok)


@router.get("/agencies", response_model=list[AdminAgencyOut])
async def list_admin_agencies(
    _admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Admin list of ALL agencies including soft-deleted."""
    rows = await conn.fetch(
        "SELECT agency_id, agency_name, feed_url, static_url, ingest_strategy, trip_id_pattern, deleted_at "
        "FROM agencies ORDER BY agency_id"
    )
    return [dict(r) for r in rows]
