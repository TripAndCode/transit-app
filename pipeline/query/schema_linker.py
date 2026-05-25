"""Japanese-alias resolution for route_code arguments.

Called from :func:`pipeline.query.tools.dispatch` before any handler that
takes a ``route`` arg, so the LLM can pass user-typed names like ``"A1"``,
``"1番"``, ``"中央大橋線"`` and still hit the correct row in
``static_routes``. Unresolved inputs return up to 5 candidates so the
caller can produce a "もしかして" message instead of either silently
running the wrong query or hard-failing with "登録されていない".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


ROUTE_CODE_RE = re.compile(r"^\d{3,5}$")


@dataclass
class RouteResolution:
    route_code: str | None
    candidates: list[tuple[str, str]] = field(default_factory=list)
    reason: Literal["exact", "alias", "fuzzy", "none"] = "none"


async def resolve_route(raw: str, conn, agency_id: int) -> RouteResolution:
    """Resolve a user-typed route reference to a canonical ``route_code``."""
    raw = (raw or "").strip()
    if not raw:
        return RouteResolution(route_code=None, reason="none")

    if ROUTE_CODE_RE.match(raw):
        row = await conn.fetchrow(
            "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
            "       route_short_name "
            "FROM static_routes "
            "WHERE agency_id = $1 "
            "  AND regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') = $2 "
            "LIMIT 1",
            agency_id,
            raw,
        )
        if row is not None:
            return RouteResolution(
                route_code=row["code"],
                reason="exact",
                candidates=[(row["code"], row["route_short_name"])],
            )

    return RouteResolution(route_code=None, reason="none")
