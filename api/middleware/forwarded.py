"""The client address behind the deployment's edge proxies.

Every proxy in front of the app appends the address it received the request
from to ``X-Forwarded-For``, so the entry ``hops`` places from the right is the
one the outermost trusted proxy saw. Whatever a client wrote into the header
itself sits to the left of it and is never read. uvicorn's own handling cannot
be used for this: told to trust every peer, it takes the leftmost entry, which
is the client's own when an edge appends to the header instead of replacing it.

``hops`` is 0 for an app reached directly, where the headers are the client's
own and are ignored.
"""

import os

from starlette.types import ASGIApp, Receive, Scope, Send


def forwarded_hops() -> int:
    """``FORWARDED_HOPS``: how many proxies in front of the app append to
    ``X-Forwarded-For``. A value that is not a whole number fails startup, since
    guessing would key every caller's rate limit on a proxy's address."""
    raw = os.environ.get("FORWARDED_HOPS", "").strip() or "0"
    hops = int(raw)
    if hops < 0:
        raise ValueError(f"FORWARDED_HOPS must be 0 or more, got {raw!r}")
    return hops


def _entries(scope: Scope, name: bytes) -> list[str]:
    # Repeated header lines are one list, in order (RFC 9110 §5.3).
    values = [value.decode("latin-1") for key, value in scope["headers"] if key == name]
    return [entry.strip() for entry in ",".join(values).split(",") if entry.strip()]


class ForwardedClientMiddleware:
    def __init__(self, app: ASGIApp, hops: int) -> None:
        self.app = app
        self.hops = hops

    def _trusted(self, scope: Scope, name: bytes) -> str | None:
        entries = _entries(scope, name)
        # Fewer entries than proxies: the request did not come through all of
        # them, so no entry is known to be one a trusted proxy wrote.
        return entries[-self.hops] if len(entries) >= self.hops else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.hops and scope["type"] in ("http", "websocket"):
            client = self._trusted(scope, b"x-forwarded-for")
            if client is not None:
                scope["client"] = (client, 0)
            proto = self._trusted(scope, b"x-forwarded-proto")
            if proto in ("http", "https"):
                scope["scheme"] = proto if scope["type"] == "http" else proto.replace("http", "ws")
        await self.app(scope, receive, send)
