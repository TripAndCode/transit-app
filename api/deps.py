from fastapi import Depends, HTTPException, Request

from api.security import current_user, require_user


async def get_conn(request: Request):
    async with request.app.state.pool.acquire() as conn:
        yield conn


async def get_agency(agency_id: int, conn=Depends(get_conn)):
    row = await conn.fetchrow("SELECT agency_id FROM agencies WHERE agency_id=$1 AND deleted_at IS NULL", agency_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return agency_id


def get_locale(request: Request) -> str:
    """Read the per-request locale set by :class:`LocaleMiddleware`.

    Defaults to ``"ja"`` when the middleware hasn't run (e.g. ad-hoc test
    fixtures), so callers can always assume a non-empty supported value.
    """
    return getattr(request.state, "locale", "ja")


# Alias so routers can import get_current_user from api.deps and tests
# can override it via app.dependency_overrides without touching api.security.
get_current_user = require_user

# Optional variant — returns None for anonymous callers instead of 401.
get_current_user_optional = current_user
