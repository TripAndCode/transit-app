"""Pure display/derivation helpers for the admin Ask-ops surface.

``ask_query_log`` stores the pipeline stage that answered a question
(``router_stage``: ``rules`` / ``embedding`` / ``llm`` / ``no_history``) and
whether it succeeded (``success``: bool). It has no ``route``, ``status``,
``latency_ms``, ``provider`` or user-identity column — see
``db/migrations/0013_ask_query_log.up.sql``, which deliberately omits
identity, and ``pipeline/query/query_log.py``'s INSERT column list for the
full set that exists. The admin surface's "route" and "status" columns are
derived here from ``router_stage``/``success`` rather than stored directly,
using CLAUDE.md's architecture naming (rules → embedding nearest-neighbour →
RAG LLM) so the UI can speak "rules / nn / rag" without the DB needing a
redundant column.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# router_stage (DB value) -> display route, in pipeline order.
_STAGE_TO_ROUTE = {
    "rules": "rules",
    "embedding": "nn",
    "llm": "rag",
    "no_history": "no_history",
}
_ROUTE_TO_STAGE = {v: k for k, v in _STAGE_TO_ROUTE.items()}

# Canonical funnel order: pipeline stages first, then the early-exit bucket.
ROUTE_ORDER = ("rules", "nn", "rag", "no_history")


def derive_route(router_stage: str) -> str:
    """Map a stored ``router_stage`` value to its display route.

    Unknown stages pass through unchanged rather than raising, so a future
    stage added to the router doesn't 500 this admin page.
    """
    return _STAGE_TO_ROUTE.get(router_stage, router_stage)


def route_to_stage(route: str) -> str | None:
    """Reverse of :func:`derive_route`, for validating a `route` filter param.
    Returns ``None`` for a route value that isn't one of the known display
    routes."""
    return _ROUTE_TO_STAGE.get(route)


def derive_status(success: bool) -> str:
    """Map the stored ``success`` boolean to a display status string."""
    return "ok" if success else "error"


def status_to_success(status: str) -> bool | None:
    """Reverse of :func:`derive_status`, for validating a `status` filter
    param. Returns ``None`` for anything other than 'ok'/'error'."""
    return {"ok": True, "error": False}.get(status)


@dataclass(frozen=True)
class FunnelRouteCount:
    """One row of the route funnel: how many queries reached this stage,
    and how many of those succeeded."""

    route: str
    count: int
    success_count: int


@dataclass(frozen=True)
class AskFunnel:
    """Aggregated route funnel for the admin Ask-ops page.

    ``providers`` is always ``None``: no per-query LLM provider is persisted
    to ``ask_query_log`` today (only the configured provider ladder is known
    at call time, in ``pipeline/query/chat.py``), so provider counts can't be
    reconstructed after the fact. Surfaced explicitly rather than fabricated
    or silently dropped, so the UI can show "not tracked" instead of a false
    zero.
    """

    by_route: tuple[FunnelRouteCount, ...]
    total: int
    providers: None = None


def build_funnel(raw_counts: list[tuple[str, bool, int]]) -> AskFunnel:
    """Aggregate ``(router_stage, success, count)`` rows (as returned by a
    ``GROUP BY router_stage, success`` query) into a route funnel ordered by
    :data:`ROUTE_ORDER`.

    A route with no rows in ``raw_counts`` still appears with count 0, so the
    funnel always has one entry per known route instead of a client having to
    fill in the gaps.
    """
    totals: dict[str, int] = dict.fromkeys(ROUTE_ORDER, 0)
    successes: dict[str, int] = dict.fromkeys(ROUTE_ORDER, 0)
    for router_stage, success, count in raw_counts:
        route = derive_route(router_stage)
        totals[route] = totals.get(route, 0) + count
        if success:
            successes[route] = successes.get(route, 0) + count

    routes = list(ROUTE_ORDER) + [r for r in totals if r not in ROUTE_ORDER]
    by_route = tuple(FunnelRouteCount(route=r, count=totals[r], success_count=successes.get(r, 0)) for r in routes)
    return AskFunnel(by_route=by_route, total=sum(totals.values()))


def find_latest_eval_result(cache_dir: Path, pattern: str = "ask-eval-*.json") -> dict[str, Any] | None:
    """Read the newest local ask-eval artifact under ``cache_dir`` matching
    ``pattern`` (lexicographic order == chronological order for an
    ISO-date-prefixed filename), or ``None`` when none exists or the latest
    one isn't valid JSON.

    No current producer writes this file: ``scripts/ask_eval.py`` and the
    weekly workflow (``.github/workflows/ask-eval-weekly.yml``) report
    pass/fail via CI exit code and pytest output only, not a persisted JSON
    artifact under this path. This read path is forward-compatible — once
    either is extended to write here, the admin page picks it up with no
    further change — and returns ``None`` until then, which the admin UI
    renders as "not run yet" rather than fabricating a result.
    """
    if not cache_dir.is_dir():
        return None
    candidates = sorted(cache_dir.glob(pattern))
    if not candidates:
        return None
    try:
        data = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
