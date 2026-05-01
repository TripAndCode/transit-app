from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_conn

router = APIRouter(prefix="/agencies", tags=["agencies"])


class AgencyCreate(BaseModel):
    agency_name: str
    feed_url: str
    static_url: str | None = None


class AgencyOut(BaseModel):
    agency_id: int
    agency_name: str
    feed_url: str
    static_url: str | None


@router.get("", response_model=list[AgencyOut])
async def list_agencies(conn=Depends(get_conn)):
    rows = await conn.fetch(
        "SELECT agency_id, agency_name, feed_url, static_url FROM agencies ORDER BY agency_id"
    )
    return [dict(r) for r in rows]


@router.get("/{agency_id}", response_model=AgencyOut)
async def get_agency(agency_id: int, conn=Depends(get_conn)):
    row = await conn.fetchrow(
        "SELECT agency_id, agency_name, feed_url, static_url FROM agencies WHERE agency_id=$1",
        agency_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return dict(row)


@router.post("", response_model=AgencyOut, status_code=201)
async def create_agency(body: AgencyCreate, conn=Depends(get_conn)):
    row = await conn.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url, static_url) VALUES ($1, $2, $3) "
        "RETURNING agency_id, agency_name, feed_url, static_url",
        body.agency_name, body.feed_url, body.static_url,
    )
    return dict(row)
