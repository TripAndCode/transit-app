"""A live read of `updates` over a period that starts before the agency's
retention floor answers 422, not 500.

``pipeline.live_filing.require_retained`` refuses such a read before it
scans, and ``pipeline.retained_reads`` once its scans are done if the floor
has since passed its start: `updates` no longer holds every row there, and
the aggregates keep the period instead. The same request is refused again,
so the client asks for a later start rather than offering a retry. The
response carries a localized ``detail``, a machine ``code`` and the
``floor`` it names.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.responses import JSONResponse

from api.deps import get_locale
from pipeline import retained_reads
from pipeline.query.tools import _summary
from pipeline.retained_reads import RANGE_NOT_RETAINED_CODE, RangeNotRetained


async def range_not_retained_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map RangeNotRetained to a localized 422 that names the floor."""
    assert isinstance(exc, RangeNotRetained)
    floor = exc.floor.isoformat()
    detail = _summary(RANGE_NOT_RETAINED_CODE, lang=get_locale(request), floor=floor)
    return JSONResponse(status_code=422, content={"detail": detail, "code": RANGE_NOT_RETAINED_CODE, "floor": floor})


async def recheck_live_reads() -> AsyncIterator[None]:
    """Run the request as one computation (pipeline.retained_reads), and once
    its handler has returned, refuse it if the floor has passed a live read
    it made outside every cached compute and Ask tool, each of which checks
    its own. Registered at function scope, so this runs before the response
    is sent and while the request still holds the connection its reads were
    checked on."""
    with retained_reads.recording() as reads:
        yield
    await reads.verify()
