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
LETTER_PREFIX_RE = re.compile(r"^[A-Za-z]\d{1,2}$")
N_BAN_RE = re.compile(r"^(\d{1,3})番$")

_TRGM_THRESHOLD = 0.30
_TRGM_CONFIDENT = 0.60


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

    # 1. Exact route_code.
    if ROUTE_CODE_RE.match(raw):
        row = await conn.fetchrow(
            # Extract trailing-parenthesized digits from route_id (e.g. "国道線(1021)" → "1021")
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

    # 2. Letter-prefix short_name (A1, L21).
    if LETTER_PREFIX_RE.match(raw):
        row = await conn.fetchrow(
            "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
            "       route_short_name "
            "FROM static_routes "
            "WHERE agency_id = $1 "
            "  AND route_short_name ILIKE $2 || ' %' "
            "ORDER BY route_id "
            "LIMIT 1",
            agency_id,
            raw.upper(),
        )
        if row is not None:
            return RouteResolution(
                route_code=row["code"],
                reason="alias",
                candidates=[(row["code"], row["route_short_name"])],
            )

    # 3. N番 → try bare-digit prefix on route_short_name; fall through to trigram.
    m = N_BAN_RE.match(raw)
    if m:
        digit = m.group(1)
        row = await conn.fetchrow(
            "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
            "       route_short_name "
            "FROM static_routes "
            "WHERE agency_id = $1 "
            "  AND route_short_name ILIKE $2 || ' %' "
            "ORDER BY route_id "
            "LIMIT 1",
            agency_id,
            digit,
        )
        if row is not None:
            return RouteResolution(
                route_code=row["code"],
                reason="alias",
                candidates=[(row["code"], row["route_short_name"])],
            )

    # 4. Trigram similarity over route_short_name + route_long_name.
    rows = await conn.fetch(
        "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS code, "
        "       route_short_name, "
        "       GREATEST( "
        "         similarity(coalesce(route_short_name, ''), $2), "
        "         similarity(coalesce(route_long_name, ''), $2) "
        "       ) AS score "
        "FROM static_routes "
        "WHERE agency_id = $1 "
        "  AND (route_short_name % $2 OR route_long_name % $2) "
        "ORDER BY score DESC "
        "LIMIT 5",
        agency_id,
        raw,
    )
    if not rows:
        return RouteResolution(route_code=None, reason="none")

    top_score = rows[0]["score"]
    candidates = [(r["code"], r["route_short_name"]) for r in rows]
    if top_score >= _TRGM_CONFIDENT and len(rows) == 1:
        return RouteResolution(
            route_code=rows[0]["code"],
            reason="alias",
            candidates=candidates,
        )
    if top_score >= _TRGM_THRESHOLD:
        return RouteResolution(
            route_code=None,
            reason="fuzzy",
            candidates=candidates,
        )
    return RouteResolution(route_code=None, reason="none")
