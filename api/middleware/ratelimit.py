from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

FREE_LIMIT = "60/minute"
PRO_LIMIT = "600/minute"


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
