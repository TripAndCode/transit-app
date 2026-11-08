"""An agency's headline figures before and after a rebuild that a method
version or fingerprint change forced, kept on its analyze run
(``pipeline_runs.rebuild_headline``) so what a method change did to the history
it rewrote is on record when it happens.

**Which runs** (:func:`is_rebuild`): one whose plan rebuilds a table in full
because its fingerprint moved, because it has none stored (every table after
``analyze --full``, or one new to the registry), or because there is no ledger
to scope it by. A run over changed days only refinalizes the whole-history
tables over them, and a store repair (``pipeline.analyze._ch_agg_scopes``)
rebuilds a table under the method it was built with, so neither is one. An
agency with nothing built yet has no "before" and records nothing.

**Which figures**: the council summary's whole-agency on-time rate and average
delay (``pipeline.reports.council``), under the on-time window a report applies
when its request names none (``resolve_definition_meta``), read through asyncpg
as the reports read them. The preset is kept beside them, so the log names the
definition when the default changes.

**Which dates**: both sides cover the service dates agg_route_daily_dist held
when the run began, the history the rebuild rewrites. Days the same run adds
fall outside them, so the change is the method's and not new data's.

**When**: each side reads on a connection of its own, which sees only
committed rows: "before" ahead of the run's purge, "after" once the run has
committed.

Bookkeeping never breaks the job (:mod:`pipeline.runs`): a side that cannot be
read logs a warning, and the run records no headline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import asyncpg

from api.range import RangeCtx
from pipeline.agg_registry import METHOD_VERSION
from pipeline.reports.council import _agency_on_time_pooled
from pipeline.reports.definition import DefinitionMeta, resolve_definition_meta

logger = logging.getLogger(__name__)

# The pipeline.agg_registry.table_scopes reasons that rebuild a table in full
# over the history that stood. "depends on" follows one of them upstream, and
# "source changed" refinalizes a whole-history table over changed days.
REBUILD_REASONS = frozenset({"fingerprint changed", "new", "no ledger"})

# The run waits on each read, and holds its transaction open through "before".
# Bounded, so a slow read costs the record rather than the rebuild's time.
_READ_TIMEOUT_SEC = 120

_SPAN_SQL = "SELECT MIN(date) AS first_day, MAX(date) AS last_day FROM agg_route_daily_dist WHERE agency_id = $1"


def is_rebuild(reasons: Mapping[str, str]) -> bool:
    """Whether a plan with these per-table reasons rebuilds history."""
    return any(reason in REBUILD_REASONS for reason in reasons.values())


@dataclass(frozen=True)
class Before:
    """The headline as the run found it, and what "after" reads to match it."""

    db_url: str
    agency_id: int
    first_day: date
    last_day: date
    definition: DefinitionMeta
    figures: dict[str, Any]


async def _connect(db_url: str) -> asyncpg.Connection:
    return await asyncpg.connect(db_url, timeout=_READ_TIMEOUT_SEC, command_timeout=_READ_TIMEOUT_SEC)


async def _figures(conn, agency_id: int, first_day: date, last_day: date, definition: DefinitionMeta) -> dict:
    # No ClickHouse client: a window the aggregates cannot answer would be read
    # off the observations, which a rebuild leaves as they were, so it raises
    # for want of one instead.
    on_time_pct, _early, avg_delay_min, samples = await _agency_on_time_pooled(
        agency_id,
        RangeCtx(from_date=first_day, to_date=last_day),
        conn,
        None,
        definition.early_tolerance_sec,
        definition.late_tolerance_sec,
    )
    return {"on_time_pct": on_time_pct, "avg_delay_min": avg_delay_min, "samples": int(samples)}


async def _read_before(db_url: str, agency_id: int) -> Before | None:
    conn = await _connect(db_url)
    try:
        span = await conn.fetchrow(_SPAN_SQL, agency_id)
        if span is None or span["first_day"] is None:
            return None
        definition = resolve_definition_meta("council_summary", None, None)
        figures = await _figures(conn, agency_id, span["first_day"], span["last_day"], definition)
    finally:
        await conn.close()
    return Before(db_url, agency_id, span["first_day"], span["last_day"], definition, figures)


async def _read_after(before: Before) -> dict:
    conn = await _connect(before.db_url)
    try:
        figures = await _figures(conn, before.agency_id, before.first_day, before.last_day, before.definition)
    finally:
        await conn.close()
    return {
        "first_day": before.first_day.isoformat(),
        "last_day": before.last_day.isoformat(),
        "preset": before.definition.preset,
        "method_version": METHOD_VERSION,
        "before": before.figures,
        "after": figures,
    }


def read_before(db_url: str, agency_id: int) -> Before | None:
    """The agency's headline over everything it has built, or None when it has
    built nothing or the read failed. Call from code with no event loop running."""
    try:
        return asyncio.run(_read_before(db_url, agency_id))
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
        headline = asyncio.run(_read_after(before))
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
