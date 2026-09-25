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

FREE_LIMIT = "60/minute"
PRO_LIMIT = "600/minute"

#: Budget for admin-triggered pipeline/agency actions (manual run trigger,
#: feed probe, reanalyze) -- generous enough that an operator working a board
#: isn't throttled, tight enough to blunt a scripted retry loop against a
#: route that queues real pipeline work.
ADMIN_ACTION_LIMIT = "20/minute"


def _key_func(request: Request) -> str:
    # get_remote_address() reads request.client.host, which uvicorn's
    # ProxyHeadersMiddleware (--forwarded-allow-ips='*', see Dockerfile) sets
    # from X-Forwarded-For. That trust is only as good as Railway's edge
    # fully replacing (not appending to) any client-supplied XFF — see the
    # CAVEAT in the Dockerfile. Unverified; this is the free-tier IP bucket
    # that assumption protects.
    if getattr(request.state, "tier", "free") == "pro":
        return f"pro:{request.headers.get('X-API-Key', 'anon')}"
    return get_remote_address(request)


limiter = Limiter(key_func=_key_func)
