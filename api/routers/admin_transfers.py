"""Admin transfer-pair definitions: the hub transfers whose reliability the
Transfers view reads (pipeline.reports.transfers).

Every mutation checks the request's origin (``csrf_guard``) and writes its
``admin_audit`` row through ``api.admin_audit.record_admin_action`` in the
same transaction as the change. A definition names explicit stop ids on each
side, the arriving operator's and the departing one's; nothing here matches a
place across operators.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from api.admin_audit import record_admin_action
from api.deps import get_conn
from api.security import User, csrf_guard, require_admin
from pipeline.route_catalog import route_code_sql

router = APIRouter(prefix="/api/admin/transfer-pairs", tags=["admin"])

# Each pair is read live over a month of two routes' observations, so the
# list stays a short watch list rather than a network-wide catalogue.
MAX_TRANSFER_PAIRS = 50
MAX_STOPS_PER_SIDE = 8

PairDayType = Literal["weekday", "saturday", "sunday_holiday"]
_DAY_TYPE_ORDER: tuple[PairDayType, ...] = ("weekday", "saturday", "sunday_holiday")

_COLUMNS = (
    "id, name, from_agency_id, from_route_code, from_direction, from_stop_ids, "
    "to_agency_id, to_route_code, to_direction, to_stop_ids, walk_sec, day_types, active, created_at, updated_at"
)


class TransferPairIn(BaseModel):
    """A pair's whole definition, as created or replaced."""

    name: str = Field(min_length=1, max_length=120)
    from_agency_id: int
    from_route_code: str = Field(min_length=1, max_length=128)
    from_direction: Literal[0, 1] | None = None
    from_stop_ids: list[str] = Field(min_length=1, max_length=MAX_STOPS_PER_SIDE)
    to_agency_id: int
    to_route_code: str = Field(min_length=1, max_length=128)
    to_direction: Literal[0, 1] | None = None
    to_stop_ids: list[str] = Field(min_length=1, max_length=MAX_STOPS_PER_SIDE)
    walk_sec: int = Field(default=120, ge=0, le=1800)
    day_types: list[PairDayType] = Field(default_factory=lambda: list(_DAY_TYPE_ORDER), min_length=1)
    active: bool = True

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()

    @field_validator("from_stop_ids", "to_stop_ids")
    @classmethod
    def _distinct_stops(cls, v: list[str]) -> list[str]:
        stops = list(dict.fromkeys(s.strip() for s in v))
        if any(not s or len(s) > 128 for s in stops):
            raise ValueError("stop ids must be 1-128 characters")
        return stops

    @field_validator("day_types")
    @classmethod
    def _ordered_day_types(cls, v: list[PairDayType]) -> list[PairDayType]:
        return [d for d in _DAY_TYPE_ORDER if d in v]


class TransferPairReplace(TransferPairIn):
    """A replacement definition: every field is required, so a field left out
    is refused rather than reset to its create default."""

    from_direction: Literal[0, 1] | None
    to_direction: Literal[0, 1] | None
    walk_sec: int = Field(ge=0, le=1800)
    day_types: list[PairDayType] = Field(min_length=1)
    active: bool


class TransferPairOut(TransferPairIn):
    id: int
    created_at: datetime
    updated_at: datetime


def _row(record: asyncpg.Record) -> dict[str, Any]:
    return dict(record)


async def _validate_side(
    conn: asyncpg.Connection, side: str, agency_id: int, route_code: str, stops: list[str]
) -> None:
    """422 unless the agency is live, its loaded timetable has the route, and
    has every stop id."""
    agency = await conn.fetchval("SELECT 1 FROM agencies WHERE agency_id = $1 AND deleted_at IS NULL", agency_id)
    if agency is None:
        raise HTTPException(status_code=422, detail=f"{side}_agency_id: unknown agency")
    route = await conn.fetchval(
        f"SELECT 1 FROM static_routes WHERE agency_id = $1 AND {route_code_sql('route_id')} = $2 LIMIT 1",
        agency_id,
        route_code,
    )
    if route is None:
        raise HTTPException(status_code=422, detail=f"{side}_route_code: not in the agency's timetable")
    known = await conn.fetch(
        "SELECT stop_id FROM static_stops WHERE agency_id = $1 AND stop_id = ANY($2::text[])", agency_id, stops
    )
    missing = sorted(set(stops) - {r["stop_id"] for r in known})
    if missing:
        raise HTTPException(status_code=422, detail=f"{side}_stop_ids: unknown stop ids {missing}")


async def _validate(conn: asyncpg.Connection, body: TransferPairIn) -> None:
    await _validate_side(conn, "from", body.from_agency_id, body.from_route_code, body.from_stop_ids)
    await _validate_side(conn, "to", body.to_agency_id, body.to_route_code, body.to_stop_ids)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=list[TransferPairOut])
async def list_pairs(
    _admin: User = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)
) -> list[dict[str, Any]]:
    """Every pair, inactive ones included, oldest first."""
    rows = await conn.fetch(f"SELECT {_COLUMNS} FROM transfer_pairs ORDER BY id")
    return [_row(r) for r in rows]


@router.post("", response_model=TransferPairOut, status_code=201)
async def create_pair(
    body: TransferPairIn,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    csrf_guard(request)
    await _validate(conn, body)
    async with conn.transaction():
        # Serialises concurrent creates, so two cannot both pass the cap.
        await conn.execute("LOCK TABLE transfer_pairs IN SHARE ROW EXCLUSIVE MODE")
        count = await conn.fetchval("SELECT count(*) FROM transfer_pairs")
        if count >= MAX_TRANSFER_PAIRS:
            raise HTTPException(status_code=409, detail=f"at most {MAX_TRANSFER_PAIRS} transfer pairs")
        row = await conn.fetchrow(
            f"""
            INSERT INTO transfer_pairs (name, from_agency_id, from_route_code, from_direction, from_stop_ids,
                to_agency_id, to_route_code, to_direction, to_stop_ids, walk_sec, day_types, active)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING {_COLUMNS}
            """,
            *_values(body),
        )
        after = _row(row)
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="transfer_pair.create",
            target_type="transfer_pair",
            target_id=after["id"],
            after=after,
            ip=_client_ip(request),
        )
    return after


def _values(body: TransferPairIn) -> tuple:
    return (
        body.name,
        body.from_agency_id,
        body.from_route_code,
        body.from_direction,
        body.from_stop_ids,
        body.to_agency_id,
        body.to_route_code,
        body.to_direction,
        body.to_stop_ids,
        body.walk_sec,
        list(body.day_types),
        body.active,
    )


@router.patch("/{pair_id}", response_model=TransferPairOut)
async def replace_pair(
    pair_id: int,
    body: TransferPairReplace,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Replace a pair's whole definition: the body is the full definition, as for a create."""
    csrf_guard(request)
    await _validate(conn, body)
    async with conn.transaction():
        before = await conn.fetchrow(f"SELECT {_COLUMNS} FROM transfer_pairs WHERE id = $1 FOR UPDATE", pair_id)
        if before is None:
            raise HTTPException(status_code=404, detail="unknown transfer pair")
        row = await conn.fetchrow(
            f"""
            UPDATE transfer_pairs SET name = $2, from_agency_id = $3, from_route_code = $4, from_direction = $5,
                from_stop_ids = $6, to_agency_id = $7, to_route_code = $8, to_direction = $9, to_stop_ids = $10,
                walk_sec = $11, day_types = $12, active = $13, updated_at = now()
            WHERE id = $1
            RETURNING {_COLUMNS}
            """,
            pair_id,
            *_values(body),
        )
        after = _row(row)
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="transfer_pair.update",
            target_type="transfer_pair",
            target_id=pair_id,
            before=_row(before),
            after=after,
            ip=_client_ip(request),
        )
    return after


@router.delete("/{pair_id}", status_code=204)
async def delete_pair(
    pair_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_conn),
) -> None:
    csrf_guard(request)
    async with conn.transaction():
        before = await conn.fetchrow(f"DELETE FROM transfer_pairs WHERE id = $1 RETURNING {_COLUMNS}", pair_id)
        if before is None:
            raise HTTPException(status_code=404, detail="unknown transfer pair")
        await record_admin_action(
            conn,
            actor_id=admin.user_id,
            action="transfer_pair.delete",
            target_type="transfer_pair",
            target_id=pair_id,
            before=_row(before),
            ip=_client_ip(request),
        )
