from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

FREE_LIMIT = "60/minute"
PRO_LIMIT = "600/minute"


def _key_func(request: Request) -> str:
    if getattr(request.state, "tier", "free") == "pro":
        return f"pro:{request.headers.get('X-API-Key', 'anon')}"
    return get_remote_address(request)


limiter = Limiter(key_func=_key_func)
