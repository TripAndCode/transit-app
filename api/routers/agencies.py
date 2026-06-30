from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.deps import get_conn
from api.security import User, csrf_guard, require_admin
from pipeline.audit import record_event
from pipeline.url_guard import FeedURLError, validate_feed_url

router = APIRouter(prefix="/api/agencies", tags=["agencies"])

# Allow-list of valid ingest strategy names (keys accepted by get_ingest_strategy).
VALID_INGEST_STRATEGIES = frozenset(
    {"aomori_regex", "direct_url", "aomori_index_scrape", "static_join"}
)


class AgencyCreate(BaseModel):
    agency_name: str
    feed_url: str
    static_url: str | None = None
    ingest_strategy: str | None = None
    trip_id_pattern: str | None = None


class AgencyPatch(BaseModel):
    agency_name: str | None = None
    feed_url: str | None = None
    static_url: str | None = None
    ingest_strategy: str | None = None
    trip_id_pattern: str | None = None


class AgencyOut(BaseModel):
    agency_id: int
    agency_name: str
    feed_url: str
    static_url: str | None


class AdminAgencyOut(BaseModel):
    agency_id: int
    agency_name: str
    feed_url: str
    static_url: str | None
    ingest_strategy: str | None
    trip_id_pattern: str | None
    deleted_at: Any  # datetime | None — Any avoids asyncpg datetime serialization issues


@router.get("", response_model=list[AgencyOut])
async def list_agencies(conn=Depends(get_conn)):
    rows = await conn.fetch(
        "SELECT agency_id, agency_name, feed_url, static_url "
        "FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    return [dict(r) for r in rows]


@router.get("/{agency_id}", response_model=AgencyOut)
async def get_agency(agency_id: int, conn=Depends(get_conn)):
    row = await conn.fetchrow(
        "SELECT agency_id, agency_name, feed_url, static_url "
        "FROM agencies WHERE agency_id=$1 AND deleted_at IS NULL",
        agency_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return dict(row)


@router.post("", response_model=AgencyOut, status_code=201)
async def create_agency(
    body: AgencyCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    admin: User = Depends(require_admin),
):
    """Create an agency. Admin-only (feed_url is a server-side fetch sink). Validates feed_url."""
    csrf_guard(request)
    if body.ingest_strategy is not None and body.ingest_strategy not in VALID_INGEST_STRATEGIES:
        raise HTTPException(status_code=422, detail=f"Unknown ingest_strategy: {body.ingest_strategy!r}")
    try:
        validate_feed_url(body.feed_url)
    except FeedURLError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    row = await conn.fetchrow(
        """
        INSERT INTO agencies (agency_name, feed_url, static_url, ingest_strategy, trip_id_pattern)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING agency_id, agency_name, feed_url, static_url
        """,
        body.agency_name,
        body.feed_url,
        body.static_url,
        body.ingest_strategy,
        body.trip_id_pattern,
    )
    aid = row["agency_id"]
    await record_event(
        conn,
        user_id=None,
        actor_id=admin.user_id,
        kind="agency_created",
        meta={"agency_id": aid},
    )
    return dict(row)


@router.patch("/{agency_id}", response_model=AdminAgencyOut)
async def patch_agency(
    agency_id: int,
    body: AgencyPatch,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    admin: User = Depends(require_admin),
):
    """Partial update. Only provided fields change. Validates feed_url if present."""
    csrf_guard(request)
    row = await conn.fetchrow(
        "SELECT agency_id, agency_name, feed_url, static_url, ingest_strategy, trip_id_pattern, deleted_at "
        "FROM agencies WHERE agency_id=$1 AND deleted_at IS NULL",
        agency_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    if body.ingest_strategy is not None and body.ingest_strategy not in VALID_INGEST_STRATEGIES:
        raise HTTPException(status_code=422, detail=f"Unknown ingest_strategy: {body.ingest_strategy!r}")
    if body.feed_url is not None:
        try:
            validate_feed_url(body.feed_url)
        except FeedURLError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    updates: dict[str, Any] = {
        field: getattr(body, field)
        for field in body.model_fields_set
    }

    if updates:
        set_clauses = [f"{col}=${i+2}" for i, col in enumerate(updates)]
        sql = f"UPDATE agencies SET {', '.join(set_clauses)} WHERE agency_id=$1"
        await conn.execute(sql, agency_id, *updates.values())
        await record_event(
            conn,
            user_id=None,
            actor_id=admin.user_id,
            kind="agency_updated",
            meta={"agency_id": agency_id, "fields": list(updates.keys())},
        )

    out = await conn.fetchrow(
        "SELECT agency_id, agency_name, feed_url, static_url, ingest_strategy, trip_id_pattern, deleted_at "
        "FROM agencies WHERE agency_id=$1",
        agency_id,
    )
    return dict(out)


@router.delete("/{agency_id}", status_code=204)
async def delete_agency(
    agency_id: int,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    admin: User = Depends(require_admin),
):
    """Soft-delete: sets deleted_at. Idempotent — re-deleting a deleted agency is 204."""
    csrf_guard(request)
    row = await conn.fetchrow("SELECT agency_id FROM agencies WHERE agency_id=$1", agency_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    tag = await conn.execute(
        "UPDATE agencies SET deleted_at = now() WHERE agency_id=$1 AND deleted_at IS NULL",
        agency_id,
    )
    if tag == "UPDATE 1":
        await record_event(
            conn,
            user_id=None,
            actor_id=admin.user_id,
            kind="agency_deleted",
            meta={"agency_id": agency_id},
        )
    return Response(status_code=204)


@router.post("/{agency_id}/restore", response_model=AdminAgencyOut)
async def restore_agency(
    agency_id: int,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    admin: User = Depends(require_admin),
):
    """Clear deleted_at, making the agency active again."""
    csrf_guard(request)
    row = await conn.fetchrow("SELECT agency_id FROM agencies WHERE agency_id=$1", agency_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    await conn.execute("UPDATE agencies SET deleted_at = NULL WHERE agency_id=$1", agency_id)
    await record_event(
        conn,
        user_id=None,
        actor_id=admin.user_id,
        kind="agency_restored",
        meta={"agency_id": agency_id},
    )
    out = await conn.fetchrow(
        "SELECT agency_id, agency_name, feed_url, static_url, ingest_strategy, trip_id_pattern, deleted_at "
        "FROM agencies WHERE agency_id=$1",
        agency_id,
    )
    return dict(out)
