"""Admin feature-flags API: current values/provenance, and overriding one.

``GET`` returns every registered flag's resolved value plus where it came
from (``env`` vs a DB ``override``) — see ``pipeline.flags`` for the
registry and the resolution rule. ``PATCH`` writes an override; ``reason``
is mandatory, and every call additionally logs through
``api.admin_audit.record_admin_action`` -- the shared seam every admin
mutation across the admin surface writes its audit trail through.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.admin_audit import record_admin_action
from api.deps import get_conn
from api.security import User, csrf_guard, require_admin
from pipeline.flags import REGISTRY, FlagDefinition, FlagState, get_flag_state, invalidate

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
    return [_to_out(d, get_flag_state(d.key)) for d in REGISTRY]


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

    before_value = get_flag_state(key).value

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
    invalidate()

    await record_admin_action(
        conn,
        actor_id=admin.user_id,
        action="flag.set",
        target_type="feature_flag",
        target_id=key,
        before={"value": before_value},
        after={"value": body.value},
        reason=body.reason,
    )

    return _to_out(definition, get_flag_state(key))
