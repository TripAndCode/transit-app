"""Rate limiting: the generic per-minute ``slowapi`` limiter applied to most
mutating/expensive routes.

This is the only budget control in front of the LLM-backed routes, and it is
deliberately coarse. What actually bounds LLM spend is admission, not
metering: ``users.llm_approved`` (``api/security.py``) means every caller who
can reach a provider is a signed-in account an admin vouched for. Per-caller
LLM accounting, if it is ever wanted, keys on that user row — not on a
client IP or an anonymous cookie, which is all this module can see.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from api.security import current_user

#: The per-minute budget every caller is held to, keyed on the address
#: ``ForwardedClientMiddleware`` resolved.
DEFAULT_LIMIT = "60/minute"

#: Budget for admin-triggered pipeline/agency actions (manual run trigger,
#: feed probe, reanalyze) -- generous enough that an operator working a board
#: isn't throttled, tight enough to blunt a scripted retry loop against a
#: route that queues real pipeline work.
ADMIN_ACTION_LIMIT = "20/minute"

#: Budget for a snapshot's figures recomputed for today
#: (``GET /api/snapshots/{id}/today``), the same for every caller: a public
#: path that runs a report computation for whoever holds a link. A reader asks
#: once each time they turn the comparison on, again on a language switch, so
#: this leaves room for reading several snapshots while capping a scripted
#: loop well below the report endpoints' own budget.
SNAPSHOT_TODAY_LIMIT = "10/minute"


def user_key(request: Request) -> str:
    """Bucket a signed-in caller's writes by account, not by address.

    Session middleware attaches the resolved user before routing. A
    signed-out caller is bucketed by address.
    """
    user = current_user(request)
    if user is not None:
        return f"user:{user.user_id}"
    return get_remote_address(request)


limiter = Limiter(key_func=get_remote_address)
