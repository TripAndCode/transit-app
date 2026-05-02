from fastapi import Depends, HTTPException, Request


async def get_conn(request: Request):
    async with request.app.state.pool.acquire() as conn:
        yield conn


async def get_agency(agency_id: int, conn=Depends(get_conn)):
    row = await conn.fetchrow("SELECT agency_id FROM agencies WHERE agency_id=$1", agency_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return agency_id
