"""Bounded, source-derived evidence selection for follow-up questions, plus
the route_code -> display-name lookup that lets the grounded answer name a
route instead of just its code."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException

from pipeline.route_catalog import route_code_sql

# The follow-up shows the model only a table's first rows, so a route_code
# past them is never grounding the model actually saw.
MAX_CONTEXT_TABLE_ROWS = 50
_MAX_LABEL_CHARS = 120
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def select_context_row(result: dict | None, index: int | None) -> dict | None:
    if index is None:
        return result
    rows = result.get("rows") if result else None
    if (
        not result
        or result.get("kind") != "table"
        or not isinstance(rows, list)
        or isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(rows)
    ):
        raise HTTPException(status_code=400, detail="invalid_context_row")
    # A full-table summary may assert facts not supported by the selected row.
    return {**result, "summary": "", "rows": [rows[index]]}


def context_route_codes(args: dict | None, result: dict | None) -> list[str]:
    """Route codes a follow-up's context refers to, deduplicated in
    first-seen order.

    Codes come from two places: a ``kind == "table"`` result's ``route_code``
    column, over only the first :data:`MAX_CONTEXT_TABLE_ROWS` rows -- the ones
    :func:`pipeline.query.followup._serialize_context` actually shows the
    model -- and ``args["route"]``/``args["route_code"]``, where a
    route-scoped card keeps its route even when its result carries no such
    column (a non-table result, or a table one row scoped to a single
    route).
    """
    seen: set[str] = set()
    codes: list[str] = []

    def _add(value: object) -> None:
        if value is None:
            return
        s = str(value)
        if not s or s in seen:
            return
        seen.add(s)
        codes.append(s)

    if result and result.get("kind") == "table":
        columns = result.get("columns")
        rows = result.get("rows")
        if isinstance(columns, list) and "route_code" in columns and isinstance(rows, list):
            idx = columns.index("route_code")
            for row in rows[:MAX_CONTEXT_TABLE_ROWS]:
                if isinstance(row, (list, tuple)) and idx < len(row):
                    _add(row[idx])

    if args:
        _add(args.get("route"))
        _add(args.get("route_code"))

    return codes


def _sanitize(value: str | None) -> str:
    """``value`` with control characters (incl. ``\\n``/``\\r``/``\\t``)
    blanked and whitespace collapsed. GTFS route names are third-party text
    that reaches an LLM prompt, so a label built from them must not carry a
    newline a prompt-injection payload could use to open a new instruction
    line."""
    if not value:
        return ""
    cleaned = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value)
    return _WHITESPACE_RUN_RE.sub(" ", cleaned).strip()


def route_label(
    short: str | None,
    long_name: str | None,
    origin: str | None,
    via: str | None,
    destination: str | None,
) -> str:
    """A route's display label from its ``static_routes`` columns, e.g.
    ``W53 沖館・新田線 (西部営業所 → 野木和団地)``."""
    short_s = _sanitize(short)
    long_s = _sanitize(long_name)
    if short_s and long_s and long_s in short_s:
        long_s = ""
    elif short_s and long_s and _leads(short_s, long_s):
        short_s = ""
    name = " ".join(p for p in (short_s, long_s) if p)

    origin_s, via_s, destination_s = _sanitize(origin), _sanitize(via), _sanitize(destination)
    chain = " → ".join(p for p in (origin_s, via_s, destination_s) if p) if (origin_s or destination_s) else ""

    if chain:
        label = f"{name} ({chain})" if name else f"({chain})"
    else:
        label = name

    return label.strip()[:_MAX_LABEL_CHARS]


def _leads(short: str, long_name: str) -> bool:
    """True when ``long_name`` opens with ``short`` as a whole word, as in
    ``10`` / ``10号線``; ``1`` does not lead ``10号線``."""
    if not long_name.startswith(short) or len(long_name) == len(short):
        return False
    last, nxt = short[-1], long_name[len(short)]
    if last.isdigit() and nxt.isdigit():
        return False
    return not (last.isascii() and last.isalpha() and nxt.isascii() and nxt.isalpha())


async def fetch_route_labels(conn, agency_id: int, codes: Sequence[str]) -> dict[str, str]:
    """``{route_code: label}`` for the ``codes`` that ``static_routes`` can
    name, in ``codes``' order. Scoped to ``agency_id`` so a lookup can never
    read another agency's routes. Two route_ids can share a code across a
    timetable change; the first by short name, then route_id, names it.
    """
    ordered = list(dict.fromkeys(str(c) for c in codes))
    if not ordered:
        return {}
    code_sql = route_code_sql("route_id")
    rows = await conn.fetch(
        f"SELECT {code_sql} AS route_code, route_short_name, route_long_name, "
        "origin_name, via_name, destination_name FROM static_routes "
        f"WHERE agency_id = $1 AND {code_sql} = ANY($2::text[]) "
        "ORDER BY route_short_name, route_id",
        agency_id,
        ordered,
    )
    by_code: dict[str, Any] = {}
    for row in rows:
        by_code.setdefault(row["route_code"], row)
    labels: dict[str, str] = {}
    for code in ordered:
        row = by_code.get(code)
        if row is None:
            continue
        label = route_label(
            row["route_short_name"],
            row["route_long_name"],
            row["origin_name"],
            row["via_name"],
            row["destination_name"],
        )
        if label:
            labels[code] = label
    return labels
