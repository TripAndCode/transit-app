"""The method stamp of a report's figures, built one way for every surface that
shows them: the report itself and an Ask card computing the same figures."""

from __future__ import annotations

from api.range import RangeCtx
from pipeline.live_filing import schedule_join
from pipeline.reports.definition import DefinitionMeta, resolve_definition_meta, stamp_build
from pipeline.reports.rankings import report_reads_aggregates
from pipeline.reports.schedule_revision import ScheduleDays, read_schedule_days


async def scanned_schedule(
    conn, ch, agency_id: int, ctx: RangeCtx, live: bool
) -> tuple[ScheduleDays, dict[tuple[int, str], str] | None]:
    """The timetables the request's figures were computed under, for its
    definition stamp: as analyze recorded them, or for a live scan, each day
    named after the version the scan filed it under, with the content the
    scan's join maps each (agency, name) to. Read here first in the request,
    it is the join the process's cache then files the rows under, unless that
    cache is cleared or its archive state expires before they are read. No
    contents beside aggregates, or for an agency whose ingest stamps no
    version, which has no join."""
    schedule = await read_schedule_days(conn, agency_id, ctx.from_date, ctx.to_date)
    if not live:
        return schedule, None
    join = await schedule_join(conn, ch, agency_id)
    if join is None:
        return schedule, None
    joined = {(agency_id, stem): content for stem, content in zip(join.stems, join.hashes, strict=True)}
    return schedule.filed_under(agency_id, join.force), joined


async def report_definition(
    conn,
    ch,
    agency_id: int,
    ctx: RangeCtx,
    report_type: str,
    *,
    early_tolerance_sec: int | None = None,
    late_tolerance_sec: int | None = None,
    csv: bool = False,
    live: bool | None = None,
    read_feed_quality: bool = True,
) -> tuple[DefinitionMeta, ScheduleDays]:
    """*report_type*'s stamp for *agency_id* over *ctx*, and the timetable days
    it names, under the resolved tolerances the figures are computed with.

    *live* is whether the figures come from a live scan; unset, it is the
    report's own rule (:func:`report_reads_aggregates`, *csv* naming the
    export). A caller computing the figures by another rule states it.

    Call it before computing the figures, as :func:`stamp_build` requires.
    """
    if live is None:
        live = not report_reads_aggregates(
            report_type,
            ctx,
            csv=csv,
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=late_tolerance_sec,
        )
    schedule, joined = await scanned_schedule(conn, ch, agency_id, ctx, live)
    definition = await stamp_build(
        conn,
        resolve_definition_meta(report_type, early_tolerance_sec, late_tolerance_sec, live=live),
        agency_id,
        schedule,
        joined,
        read_feed_quality=read_feed_quality,
    )
    return definition, schedule
