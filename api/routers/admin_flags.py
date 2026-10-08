"""Admin feature-flags API: current values/provenance, and overriding one.

``GET`` returns every registered flag's resolved value plus where it came
from (``env`` vs a DB ``override``) — see ``pipeline.flags`` for the
registry and the resolution rule. ``PATCH`` writes an override; ``DELETE``
removes one, returning the flag to whatever its environment resolves to.
``reason`` is mandatory on a ``PATCH``, and both mutations additionally log
through ``api.admin_audit.record_admin_action`` -- the shared seam every
admin mutation across the admin surface writes its audit trail through.

Every flag read here goes through ``pipeline.flags``'s async variants: the
refresh behind them is a blocking psycopg2 round trip, and these handlers
run on the event loop.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.admin_audit import record_admin_action
from api.deps import get_conn
from api.security import User, csrf_guard, require_admin
from pipeline.flags import REGISTRY, FlagDefinition, FlagState, aget_flag_state, invalidate

router = APIRouter(prefix="/api/admin/flags", tags=["admin"])

_BY_KEY = {d.key: d for d in REGISTRY}


class FlagOut(BaseModel):
    """One flag's resolved value plus provenance, for the admin UI."""

    key: str
    label_key: str
    value: bool
    source: str  # "env" | "override"
    env_default: bool
    updated_by: int | None
    updated_at: datetime | None
    reason: str | None


class FlagPatch(BaseModel):
    """PATCH body: the new value and the mandatory reason for the change."""

    value: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be blank")
        return v.strip()


def _to_out(definition: FlagDefinition, state: FlagState) -> FlagOut:
    return FlagOut(
        key=state.key,
        label_key=definition.label_key,
        value=state.value,
        source=state.source,
        env_default=state.env_default,
        updated_by=state.updated_by,
        updated_at=state.updated_at,
        reason=state.reason,
    )


@router.get("", response_model=list[FlagOut])
async def list_flags(_admin: User = Depends(require_admin)) -> list[FlagOut]:
    """Every registered flag, resolved."""
    return [_to_out(d, await aget_flag_state(d.key)) for d in REGISTRY]


@router.patch("/{key}", response_model=FlagOut)
async def patch_flag(
    key: str,
    body: FlagPatch,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> FlagOut:
    """Set (or replace) this flag's DB override.

    Upserts one row keyed on ``key`` -- the ``feature_flags`` table itself
    keeps only the latest reason/actor/timestamp per flag, not a history;
    ``api.admin_audit.record_admin_action`` additionally logs every call
    here regardless. Invalidates the in-process cache immediately after the
    write so this response, the next GET, and the very next gated request
    anywhere in the process all see the new value without waiting out the
    30s TTL.
    """
    csrf_guard(request)
    definition = _BY_KEY.get(key)
    if definition is None:
        raise HTTPException(status_code=404, detail="unknown flag")

    before_value = (await aget_flag_state(key)).value

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO feature_flags (key, value, reason, updated_by, updated_at)
            VALUES ($1, $2, $3, $4, now())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                reason = EXCLUDED.reason,
                updated_by = EXCLUDED.updated_by,
                updated_at = EXCLUDED.updated_at
            """,
            key,
            body.value,
            body.reason,
            admin.user_id,
        )
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="flag.set",
            target_type="feature_flag",
            target_id=key,
            before={"value": before_value},
            after={"value": body.value},
            reason=body.reason,
            ip=request.client.host if request.client else None,
        )
    invalidate()

    return _to_out(definition, await aget_flag_state(key))


@router.delete("/{key}", response_model=FlagOut)
async def clear_flag(
    key: str,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> FlagOut:
    """Remove this flag's DB override, returning it to its env resolution.

    The complement of ``PATCH``: an operator who switched something off
    during an incident needs a way back to the deployment's own setting
    that does not require knowing what that setting is. The response
    therefore reports the resolved state after the delete, whose ``source``
    is ``env``.

    Idempotent. A flag with no override row is already where this endpoint
    would put it, so the request succeeds and writes no audit entry --
    recording ``flag.cleared`` for a change that did not happen would put a
    false event in the trail. Unknown (unregistered) keys still 404.
    """
    csrf_guard(request)
    definition = _BY_KEY.get(key)
    if definition is None:
        raise HTTPException(status_code=404, detail="unknown flag")

    before = await aget_flag_state(key)

    async with conn.transaction():
        deleted = await conn.fetchrow("DELETE FROM feature_flags WHERE key = $1 RETURNING value", key)
        if deleted is not None:
            await record_admin_action(
                conn,
                actor_id=admin.user_id,
                action="flag.cleared",
                target_type="feature_flag",
                target_id=key,
                before={"value": before.value},
                # Where the flag lands, which is the env resolution the
                # removed override was masking.
                after={"value": before.env_default},
                ip=request.client.host if request.client else None,
            )
    invalidate()

    return _to_out(definition, await aget_flag_state(key))
