"""Which scope fields a screen's endpoint honoured.

Every endpoint behind an analysis screen answers with ``scope_applied``: one
boolean per field of the shared URL scope, so the scope bar can grey a
condition the screen did not use instead of the endpoint ignoring it silently.
The field names are the URL parameter names.
"""

from __future__ import annotations

SCOPE_FIELDS: tuple[str, ...] = (
    "from",
    "to",
    "dow",
    "time_band",
    "hour",
    "service",
    "routes",
    "stop",
    "dir",
    "late",
    "early",
)

ALL_SIX: tuple[str, ...] = ("from", "to", "dow", "time_band", "service", "routes")


def scope_applied(*honoured: str) -> dict[str, bool]:
    unknown = set(honoured) - set(SCOPE_FIELDS)
    if unknown:
        raise ValueError(f"unknown scope fields: {sorted(unknown)}")
    return {field: field in honoured for field in SCOPE_FIELDS}
