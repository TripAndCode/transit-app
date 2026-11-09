"""An agency's headline figures before and after a rebuild that a method
version or fingerprint change forced, kept on its analyze run
(``pipeline_runs.rebuild_headline``) so what a method change did to the history
it rewrote is on record when it happens.

**Which runs** (:func:`is_rebuild`): one whose plan rebuilds HEADLINE_TABLE,
the table the figures are read from, in full: because its fingerprint moved,
because it has none stored (every table after ``analyze --full``), because a
table it is computed from goes in full, or because there is no ledger to scope
it by. A plan that rebuilds only other tables leaves the figures' rows
standing. A run over changed days rebuilds only those days, and a store repair
(``pipeline.analyze._ch_agg_scopes``) rebuilds the table under the method it was
built with, so neither is one. An agency with nothing built yet has no "before"
and records nothing.

**Which figures**: the council summary's whole-agency on-time rate and average
delay (``pipeline.reports.council``), under the on-time window a report applies
when its request names none (``pipeline.reports.rankings.on_time_window``),
read through asyncpg as the reports read them. The preset is kept beside them,
so the log names the definition when the default changes.

**Which dates**: both sides cover the service dates HEADLINE_TABLE held when
the run began, the history the rebuild rewrites. Days the same run adds fall
outside them. A correction the ledger finds to a day inside them lands in the
same run, so the change counts it beside the method's.

**When**: each side reads on a connection of its own, which sees only
committed rows: "before" ahead of the run's purge, "after" once the run has
committed.

Bookkeeping never breaks the job (:mod:`pipeline.runs`): a side that cannot be
read logs a warning, and the run records no headline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypeVar

import asyncpg

from api.range import RangeCtx
from pipeline.agg_registry import METHOD_VERSION
from pipeline.reports.council import _agency_on_time_pooled
from pipeline.reports.definition import resolve_definition_meta
from pipeline.reports.rankings import on_time_window

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# The table _agency_on_time_pooled reads the figures from.
HEADLINE_TABLE = "agg_route_daily_dist"

# The run waits on each side, and holds its own transaction open through
# "before". The bound covers the whole side, connecting and every read, so a
# slow side costs the record rather than the rebuild's time.
_READ_TIMEOUT_SEC = 120

_SPAN_SQL = f"SELECT MIN(date) AS first_day, MAX(date) AS last_day FROM {HEADLINE_TABLE} WHERE agency_id = $1"


def is_rebuild(reasons: Mapping[str, str]) -> bool:
    """Whether a plan with these per-table reasons rebuilds the history the
    figures are read from. *reasons* names every table the plan rebuilds in
    full (``pipeline.agg_registry.table_scopes``), and HEADLINE_TABLE is a
    dated table, which goes in full only for a reason of its own or of a table
    it is computed from."""
    return HEADLINE_TABLE in reasons


@dataclass(frozen=True)
class Before:
    """The headline as the run found it, and what "after" reads to match it."""

    # Left out of the repr: the URL carries the database password.
    db_url: str = field(repr=False)
    agency_id: int
    first_day: date
    last_day: date
    # The on-time window, (early_tolerance_sec, late_tolerance_sec), and the
    # preset it is.
    window: tuple[int | None, int]
    preset: str | None
    figures: dict[str, Any]


def _run_bounded(side: Coroutine[Any, Any, _T]) -> _T:
    return asyncio.run(asyncio.wait_for(side, _READ_TIMEOUT_SEC))


async def _figures(conn, agency_id: int, first_day: date, last_day: date, window: tuple[int | None, int]) -> dict:
    # No ClickHouse client: a window the aggregates cannot answer would be read
    # off the observations, which a rebuild leaves as they were, so it raises
    # for want of one instead.
    on_time_pct, _early, avg_delay_min, samples = await _agency_on_time_pooled(
        agency_id,
        RangeCtx(from_date=first_day, to_date=last_day),
        conn,
        None,
        *window,
    )
    return {"on_time_pct": on_time_pct, "avg_delay_min": avg_delay_min, "samples": int(samples)}


async def _read_before(db_url: str, agency_id: int) -> Before | None:
    conn = await asyncpg.connect(db_url)
    try:
        span = await conn.fetchrow(_SPAN_SQL, agency_id)
        if span is None or span["first_day"] is None:
            return None
        # As get_report resolves a council summary whose request names no window.
        window = on_time_window()
        figures = await _figures(conn, agency_id, span["first_day"], span["last_day"], window)
    finally:
        await conn.close()
    preset = resolve_definition_meta("council_summary", *window).preset
    return Before(db_url, agency_id, span["first_day"], span["last_day"], window, preset, figures)


async def _read_after(before: Before) -> dict:
    conn = await asyncpg.connect(before.db_url)
    try:
        figures = await _figures(conn, before.agency_id, before.first_day, before.last_day, before.window)
    finally:
        await conn.close()
    return {
        "first_day": before.first_day.isoformat(),
        "last_day": before.last_day.isoformat(),
        "preset": before.preset,
        "method_version": METHOD_VERSION,
        "before": before.figures,
        "after": figures,
    }


def read_before(db_url: str, agency_id: int) -> Before | None:
    """The agency's headline over everything it has built, or None when it has
    built nothing or the read failed. Call from code with no event loop running."""
    try:
        return _run_bounded(_read_before(db_url, agency_id))
    except Exception:
        logger.warning(
            "rebuild headline: could not read agency %s's headline before its rebuild", agency_id, exc_info=True
        )
        return None


def read_after(before: Before) -> dict | None:
    """The run's headline document, *before* beside the figures the rebuild
    left over the same dates and definition, or None when they could not be
    read."""
    try:
        headline = _run_bounded(_read_after(before))
    except Exception:
        logger.warning(
            "rebuild headline: could not read agency %s's headline after its rebuild", before.agency_id, exc_info=True
        )
        return None
    old, new = headline["before"], headline["after"]
    logger.info(
        f"  headline {headline['first_day']}..{headline['last_day']} ({headline['preset']}): "
        f"on time {old['on_time_pct']}% -> {new['on_time_pct']}%, "
        f"average delay {old['avg_delay_min']} -> {new['avg_delay_min']} min"
    )
    return headline
