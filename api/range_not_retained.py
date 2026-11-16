"""A live read of `updates` over a period that starts before the agency's
retention floor answers 422, not 500.

``pipeline.live_filing.require_retained`` refuses such a read before it
scans: `updates` no longer holds every row there, and the aggregates keep the
period instead. The same request is refused again, so the client asks for a
later start rather than offering a retry. The response carries a localized
``detail``, a machine ``code`` and the ``floor`` it names.
"""

from fastapi import Request
from fastapi.responses import JSONResponse

from api.deps import get_locale
from pipeline.live_filing import RANGE_NOT_RETAINED_CODE, RangeNotRetained
from pipeline.query.tools import _summary


async def range_not_retained_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map RangeNotRetained to a localized 422 that names the floor."""
    assert isinstance(exc, RangeNotRetained)
    floor = exc.floor.isoformat()
    detail = _summary(RANGE_NOT_RETAINED_CODE, lang=get_locale(request), floor=floor)
    return JSONResponse(status_code=422, content={"detail": detail, "code": RANGE_NOT_RETAINED_CODE, "floor": floor})
