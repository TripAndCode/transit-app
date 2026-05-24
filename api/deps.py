from fastapi import Depends, HTTPException, Request


async def get_conn(request: Request):
    async with request.app.state.pool.acquire() as conn:
        yield conn


async def get_agency(agency_id: int, conn=Depends(get_conn)):
    row = await conn.fetchrow("SELECT agency_id FROM agencies WHERE agency_id=$1", agency_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return agency_id


def get_locale(request: Request) -> str:
    """Read the per-request locale set by :class:`LocaleMiddleware`.

    Defaults to ``"ja"`` when the middleware hasn't run (e.g. ad-hoc test
    fixtures), so callers can always assume a non-empty supported value.
    """
    return getattr(request.state, "locale", "ja")
