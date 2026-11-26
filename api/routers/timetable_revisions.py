"""Timetable revisions: each change of timetable an agency's archive knows,
and what one changed at every stop (pipeline.reports.timetable_diff).

Reader-facing, like the reports: it is drawn from the published timetables
alone, and its lists of stops that lost service or were left with a new gap
are meant for the councils and monitoring lists outside the operator.
"""

import csv
import io
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Literal

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.deps import get_agency, get_ch, get_conn, get_locale
from api.middleware.ratelimit import DEFAULT_LIMIT, limiter
from api.range import DAY_TYPE_SERVICE
from pipeline.data_sources import credit_line
from pipeline.reports.definition import CONTENT_HASH_DIGITS, DefinitionMeta, resolve_definition_meta, stamp_build
from pipeline.reports.rankings import DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC, RANKING_MIN_SAMPLES
from pipeline.reports.revision_effects import (
    ClassTally,
    Observed,
    RouteChange,
    RouteClass,
    Side,
    Tally,
    Windows,
    aggregated_days,
    class_tallies,
    load_observed,
    load_route_timetables,
    plan,
)
from pipeline.reports.revision_gaps import NewGap, load_departures, new_gaps
from pipeline.reports.schedule_revision import read_schedule_days
from pipeline.reports.stop_supply import FIRST_HOUR, LAST_HOUR
from pipeline.reports.timetable_diff import (
    DAY_TYPES,
    Comparison,
    DayTypeSummary,
    Revision,
    RevisionDayType,
    Run,
    StopChange,
    Unavailable,
    load_comparison,
    load_revisions,
)
from pipeline.stats import pct_is_uncertain
from pipeline.timetable_sampling import TypicalDay

router = APIRouter(prefix="/api/{agency_id}", tags=["timetable-revisions"])


class RevisionVersion(BaseModel):
    """One side of a revision: a version name in force over its run of
    service dates, its short content hash, and the run, both ends inclusive.
    A version still in force runs to its span's end."""

    version: str
    content: str
    first_day: date
    last_day: date


class RevisionOut(BaseModel):
    first_day: date
    before: RevisionVersion
    after: RevisionVersion


class RevisionsResponse(BaseModel):
    """The agency's revisions, newest first."""

    revisions: list[RevisionOut]


class HourChangeOut(BaseModel):
    hour: int
    before: int
    after: int


class StopChangeOut(BaseModel):
    """A stop's departures on one day type's typical day before and after.
    Times are seconds after the service day's midnight, past 24:00 for a
    departure after it. ``stop_name`` is the loaded timetable's, None for a
    stop it no longer has."""

    stop_id: str
    stop_name: str | None
    day_type: RevisionDayType
    before: int
    after: int
    lost: int
    gained: int
    first_before: int | None
    first_after: int | None
    last_before: int | None
    last_after: int | None
    last_shift_min: int | None
    ended: bool
    hours: list[HourChangeOut]


class DayTypeSummaryOut(BaseModel):
    """A day type's totals. A side with no typical day leaves its date and
    departures None, and the stop counts need both sides."""

    day_type: RevisionDayType
    before_date: date | None
    after_date: date | None
    departures_before: int | None
    departures_after: int | None
    stops_ended: int | None
    last_earlier: int | None


class RevisionDiffResponse(BaseModel):
    """``stops`` holds the changed stops of the requested day type, or of
    every day type when none is requested; ``day_types`` holds all three
    whenever the comparison is available."""

    available: bool
    reason: Literal["no_revision", "no_calendar"] | None = None
    revision: RevisionOut | None = None
    day_types: list[DayTypeSummaryOut] = []
    stops: list[StopChangeOut] = []


def _version(run: Run) -> RevisionVersion:
    return RevisionVersion(
        version=run.stem, content=run.content[:CONTENT_HASH_DIGITS], first_day=run.first, last_day=run.last
    )


def _revision(revision: Revision) -> RevisionOut:
    return RevisionOut(first_day=revision.first_day, before=_version(revision.before), after=_version(revision.after))


def _revision_on(found: list[Revision], on: date | None) -> Revision | None:
    """The revision starting *on*, the newest when *on* is None, None when
    there is none; a day *on* that starts no revision is a 404."""
    if on is None:
        return found[-1] if found else None
    revision = next((r for r in found if r.first_day == on), None)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"No timetable revision starts on {on.isoformat()}")
    return revision


@router.get("/timetable-revisions", response_model=RevisionsResponse)
@limiter.limit(DEFAULT_LIMIT)
async def list_revisions(
    request: Request,
    agency_id: int = Depends(get_agency),
    ch: AsyncClient = Depends(get_ch),
) -> RevisionsResponse:
    """Each service date from which another timetable is in force than the
    day before, newest first. Only versions the archive keeps a schedule of
    are in force here, so a timetable loaded since the agency's last analyze
    joins the list once analyze keeps it."""
    found = await load_revisions(ch, agency_id)
    return RevisionsResponse(revisions=[_revision(r) for r in reversed(found)])


def _summary(s: DayTypeSummary) -> DayTypeSummaryOut:
    return DayTypeSummaryOut(
        day_type=s.day_type,
        before_date=None if s.before is None else s.before.day,
        after_date=None if s.after is None else s.after.day,
        departures_before=s.departures_before,
        departures_after=s.departures_after,
        stops_ended=s.stops_ended,
        last_earlier=s.last_earlier,
    )


def _stop(s: StopChange, names: dict[str, str | None]) -> StopChangeOut:
    return StopChangeOut(
        stop_id=s.stop_id,
        stop_name=names.get(s.stop_id),
        day_type=s.day_type,
        before=s.before,
        after=s.after,
        lost=s.lost,
        gained=s.gained,
        first_before=s.first_before,
        first_after=s.first_after,
        last_before=s.last_before,
        last_after=s.last_after,
        last_shift_min=s.last_shift_min,
        ended=s.ended,
        hours=[HourChangeOut(hour=h.hour, before=h.before, after=h.after) for h in s.hours],
    )


async def _stop_names(conn: asyncpg.Connection, agency_id: int, stop_ids: list[str]) -> dict[str, str | None]:
    rows = await conn.fetch(
        "SELECT stop_id, stop_name FROM static_stops WHERE agency_id = $1 AND stop_id = ANY($2::text[])",
        agency_id,
        stop_ids,
    )
    return {r["stop_id"]: r["stop_name"] for r in rows}


_CSV_NAME = "timetable_revision"
_CSV_COLUMNS = [
    "停留所ID",
    "停留所名",
    "曜日区分",
    "改正前の発車数",
    "改正後の発車数",
    "増減",
    "減った発車",
    "増えた発車",
    "改正前の始発",
    "改正後の始発",
    "改正前の最終",
    "改正後の最終",
    "最終の変化(分)",
    "発車がなくなった",
    "時間帯ごとの増減",
]

# The row standing in for the stops when there is no comparison, in the
# file reader's language, like the data-source credit.
UNAVAILABLE_CSV: dict[tuple[Unavailable, str], str] = {
    (Unavailable.NO_REVISION, "ja"): "この事業者の時刻表には、比べられる改正がまだありません。",
    (Unavailable.NO_REVISION, "en"): "This agency's timetables have no revision to compare yet.",
    (Unavailable.NO_CALENDAR, "ja"): "改正の前後どちらかの運行日カレンダーが保存されていないため、比べられません。",
    (Unavailable.NO_CALENDAR, "en"): (
        "The service calendar of the timetable before or after the revision is not kept, so it cannot be compared."
    ),
}


def _clock(sec: int | None) -> str:
    return "" if sec is None else f"{sec // 3600:02d}:{sec % 3600 // 60:02d}"


def _hours_cell(s: StopChange) -> str:
    return "; ".join(f"{h.hour}時台 {h.after - h.before:+d}" for h in s.hours)


def _csv_rows(comparison: Comparison, names: dict[str, str | None]) -> list[list]:
    return [
        [
            s.stop_id,
            names.get(s.stop_id) or "",
            DAY_TYPE_SERVICE[s.day_type],
            s.before,
            s.after,
            s.after - s.before,
            s.lost,
            s.gained,
            _clock(s.first_before),
            _clock(s.first_after),
            _clock(s.last_before),
            _clock(s.last_after),
            "" if s.last_shift_min is None else s.last_shift_min,
            "はい" if s.ended else "",
            _hours_cell(s),
        ]
        for s in comparison.stops
    ]


def _version_line(revision: Revision) -> str:
    before, after = revision.before, revision.after
    return (
        f"時刻表の改正 {revision.first_day.isoformat()}：{before.stem}（{before.content[:CONTENT_HASH_DIGITS]}）→ "
        f"{after.stem}（{after.content[:CONTENT_HASH_DIGITS]}）"
    )


def _typical_days_line(days: Iterable[tuple[RevisionDayType, TypicalDay | None, TypicalDay | None]]) -> str:
    line = " / ".join(
        f"{DAY_TYPE_SERVICE[day_type]} "
        f"{before.day.isoformat() if before else '不明'} → {after.day.isoformat() if after else '不明'}"
        for day_type, before, after in days
    )
    return f"代表日（改正前 → 改正後）：{line}"


def _csv_response(
    name: str,
    columns: Sequence[str],
    first_day: date | None,
    preamble: list[str],
    credit: str | None,
    rows: list[list],
    message: str | None,
) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # BOM, so Excel reads the file as UTF-8
    w = csv.writer(buf)
    for line in preamble:
        w.writerow([line])
    if credit is not None:
        w.writerow([credit])
    w.writerow(columns)
    if message is not None:
        w.writerow([message])
    w.writerows(rows)
    fname = f"{name}_{first_day.isoformat() if first_day else 'none'}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _check_format(format: str | None, lang: str | None, day_type: RevisionDayType | None) -> None:
    if lang is not None and format != "csv":
        raise HTTPException(status_code=400, detail="lang only applies to format=csv")
    if day_type is not None and format == "csv":
        raise HTTPException(
            status_code=400, detail="day_type does not apply to format=csv, which covers every day type"
        )


async def _csv_credit(
    request: Request, conn: asyncpg.Connection, agency_id: int, lang: str | None
) -> tuple[str, str | None]:
    """The file reader's locale and the data-source credit in it. A direct
    read, not Depends(get_locale): the JSON these routes return holds no
    localized text, so its client caches it whatever the language."""
    locale = lang or get_locale(request)
    credit = credit_line(await conn.fetchval("SELECT feed_url FROM agencies WHERE agency_id=$1", agency_id), locale)
    return locale, credit


@router.get("/timetable-revisions/diff", response_model=RevisionDiffResponse)
@limiter.limit(DEFAULT_LIMIT)
async def revision_diff(
    request: Request,
    on: date | None = Query(
        default=None,
        description="The revision's first day, as the revisions list gives it. Unset, the newest revision.",
    ),
    day_type: RevisionDayType | None = Query(
        default=None,
        description="JSON only (400 with format=csv, which covers all three): list the changed stops of this "
        "day type alone. The day-type totals always cover all three.",
    ),
    format: str | None = Query(default=None, pattern="^(json|csv)$"),
    lang: str | None = Query(
        default=None,
        pattern="^(ja|en)$",
        description="format=csv only: the language of the data-source credit and of the row standing in for "
        "the stops when there is no comparison; the rest of the file is Japanese, like the reports' CSV. The "
        "app sets it to its UI language because a download link sends the browser's Accept-Language instead. "
        "Unset, those rows follow Accept-Language.",
    ),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> RevisionDiffResponse | StreamingResponse:
    """What a revision changed at every stop, on each day type's typical day
    before and after it (pipeline.reports.timetable_diff): departures lost
    and gained per hour, the first and last departure, and the stops left with
    none. ``format=csv`` writes the changed stops as a monitoring list. A
    day ``on`` that starts no revision is a 404."""
    _check_format(format, lang, day_type)
    revision = _revision_on(await load_revisions(ch, agency_id), on)
    result = Unavailable.NO_REVISION if revision is None else await load_comparison(ch, agency_id, revision)

    if format == "csv":
        locale, credit = await _csv_credit(request, conn, agency_id, lang)
        if isinstance(result, Unavailable):
            message = UNAVAILABLE_CSV[(result, "en" if locale == "en" else "ja")]
            preamble = [] if revision is None else [_version_line(revision)]
            first_day = None if revision is None else revision.first_day
            return _csv_response(_CSV_NAME, _CSV_COLUMNS, first_day, preamble, credit, [], message)
        names = await _stop_names(conn, agency_id, sorted({s.stop_id for s in result.stops}))
        days = _typical_days_line((s.day_type, s.before, s.after) for s in result.day_types)
        preamble = [_version_line(result.revision), days]
        rows = _csv_rows(result, names)
        return _csv_response(_CSV_NAME, _CSV_COLUMNS, result.revision.first_day, preamble, credit, rows, None)

    if isinstance(result, Unavailable):
        return RevisionDiffResponse(
            available=False,
            reason="no_revision" if result is Unavailable.NO_REVISION else "no_calendar",
            revision=None if revision is None else _revision(revision),
        )
    stops = [s for s in result.stops if day_type is None or s.day_type == day_type]
    names = await _stop_names(conn, agency_id, sorted({s.stop_id for s in stops}))
    return RevisionDiffResponse(
        available=True,
        revision=_revision(result.revision),
        day_types=[_summary(s) for s in result.day_types],
        stops=[_stop(s, names) for s in stops],
    )


class NewGapOut(BaseModel):
    """A stop whose longest stretch without a departure over the band exceeds
    ``gap_min`` on the typical day after the revision, and did not on the one
    before. Departures are those within the band; gaps are seconds.
    ``stop_name`` is the loaded timetable's, None for a stop it no longer has."""

    stop_id: str
    stop_name: str | None
    day_type: RevisionDayType
    departures_before: int
    departures_after: int
    longest_gap_before: int
    longest_gap_after: int


class NewGapDayTypeOut(BaseModel):
    """How many stops a day type has new gaps at, None when either side has
    no typical day of it."""

    day_type: RevisionDayType
    stops: int | None


class RevisionNewGapsResponse(BaseModel):
    """``stops`` holds the new gaps of the requested day type, or of every day
    type when none is requested, the longest after the revision first;
    ``day_types`` counts them on all three whenever the comparison is
    available."""

    available: bool
    reason: Literal["no_revision", "no_calendar"] | None = None
    revision: RevisionOut | None = None
    from_hour: int
    to_hour: int
    gap_min: int
    day_types: list[NewGapDayTypeOut] = []
    stops: list[NewGapOut] = []


_NEW_GAPS_CSV_NAME = "timetable_revision_new_gaps"
_NEW_GAPS_CSV_COLUMNS = [
    "停留所ID",
    "停留所名",
    "曜日区分",
    "改正前の最も長い間隔(分)",
    "改正後の最も長い間隔(分)",
    "改正前の発車数(時間帯内)",
    "改正後の発車数(時間帯内)",
]


def _minutes(sec: int) -> int | float:
    minutes = round(sec / 60, 1)
    return int(minutes) if minutes.is_integer() else minutes


def _criterion_line(from_hour: int, to_hour: int, gap_min: int) -> str:
    return (
        f"新たな運行空白：{from_hour}:00〜{to_hour}:00 の発車のない最も長い間隔が、"
        f"改正前は{gap_min}分以下で、改正後は{gap_min}分を超えた停留所"
    )


def _new_gap_rows(gaps: list[NewGap], names: dict[str, str | None]) -> list[list]:
    return [
        [
            g.stop_id,
            names.get(g.stop_id) or "",
            DAY_TYPE_SERVICE[g.day_type],
            _minutes(g.gap_before),
            _minutes(g.gap_after),
            g.departures_before,
            g.departures_after,
        ]
        for g in gaps
    ]


def _new_gap(g: NewGap, names: dict[str, str | None]) -> NewGapOut:
    return NewGapOut(
        stop_id=g.stop_id,
        stop_name=names.get(g.stop_id),
        day_type=g.day_type,
        departures_before=g.departures_before,
        departures_after=g.departures_after,
        longest_gap_before=g.gap_before,
        longest_gap_after=g.gap_after,
    )


@router.get("/timetable-revisions/new-gaps", response_model=RevisionNewGapsResponse)
@limiter.limit(DEFAULT_LIMIT)
async def revision_new_gaps(
    request: Request,
    on: date | None = Query(
        default=None,
        description="The revision's first day, as the revisions list gives it. Unset, the newest revision.",
    ),
    day_type: RevisionDayType | None = Query(
        default=None,
        description="JSON only (400 with format=csv, which covers all three): list the new gaps of this day "
        "type alone. The day-type counts always cover all three.",
    ),
    from_hour: int = Query(10, ge=FIRST_HOUR, lt=LAST_HOUR, description="The band's first service-day hour."),
    to_hour: int = Query(
        16, gt=FIRST_HOUR, le=LAST_HOUR, description="The service-day hour the band ends at, exclusive."
    ),
    gap_min: int = Query(
        60,
        ge=1,
        description="The threshold in minutes, shorter than the band (422 otherwise): a gap exactly at it is "
        "not over it.",
    ),
    format: str | None = Query(default=None, pattern="^(json|csv)$"),
    lang: str | None = Query(
        default=None,
        pattern="^(ja|en)$",
        description="format=csv only, as on /timetable-revisions/diff.",
    ),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> RevisionNewGapsResponse | StreamingResponse:
    """The stops a revision left with a new gap
    (pipeline.reports.revision_gaps): on each day type's typical day, the
    longest stretch of [from_hour, to_hour) with no departure was at most
    ``gap_min`` before the revision and exceeds it after. The band's hours
    are service-day hours, 24 the night's midnight, as on the supply map.
    ``format=csv`` writes them as a monitoring list. A day ``on`` that starts
    no revision is a 404."""
    _check_format(format, lang, day_type)
    if to_hour <= from_hour:
        raise HTTPException(status_code=422, detail="to_hour must be after from_hour")
    # No gap exceeds the band, so a threshold the band does not exceed lists nothing.
    if gap_min * 60 >= (to_hour - from_hour) * 3600:
        raise HTTPException(status_code=422, detail="gap_min must be shorter than the band")
    revision = _revision_on(await load_revisions(ch, agency_id), on)
    result = Unavailable.NO_REVISION if revision is None else await load_departures(ch, agency_id, revision)
    gaps = (
        []
        if isinstance(result, Unavailable)
        else new_gaps(result.changed, result.known, from_hour, to_hour, gap_min * 60)
    )

    if format == "csv":
        locale, credit = await _csv_credit(request, conn, agency_id, lang)
        criterion = _criterion_line(from_hour, to_hour, gap_min)
        first_day = None if revision is None else revision.first_day
        if isinstance(result, Unavailable):
            message = UNAVAILABLE_CSV[(result, "en" if locale == "en" else "ja")]
            preamble = [criterion] if revision is None else [_version_line(revision), criterion]
            return _csv_response(_NEW_GAPS_CSV_NAME, _NEW_GAPS_CSV_COLUMNS, first_day, preamble, credit, [], message)
        names = await _stop_names(conn, agency_id, sorted({g.stop_id for g in gaps}))
        before, after = result.sides
        days = _typical_days_line((dt, before.get(dt), after.get(dt)) for dt in DAY_TYPES)
        preamble = [_version_line(result.revision), days, criterion]
        rows = _new_gap_rows(gaps, names)
        return _csv_response(_NEW_GAPS_CSV_NAME, _NEW_GAPS_CSV_COLUMNS, first_day, preamble, credit, rows, None)

    if isinstance(result, Unavailable):
        return RevisionNewGapsResponse(
            available=False,
            reason="no_revision" if result is Unavailable.NO_REVISION else "no_calendar",
            revision=None if revision is None else _revision(revision),
            from_hour=from_hour,
            to_hour=to_hour,
            gap_min=gap_min,
        )
    counts = Counter(g.day_type for g in gaps)
    shown = [g for g in gaps if day_type is None or g.day_type == day_type]
    names = await _stop_names(conn, agency_id, sorted({g.stop_id for g in shown}))
    return RevisionNewGapsResponse(
        available=True,
        revision=_revision(result.revision),
        from_hour=from_hour,
        to_hour=to_hour,
        gap_min=gap_min,
        day_types=[NewGapDayTypeOut(day_type=dt, stops=counts[dt] if dt in result.known else None) for dt in DAY_TYPES],
        stops=[_new_gap(g, names) for g in shown],
    )


class TallyOut(BaseModel):
    """Departures observed on one side, pooled. ``on_time_pct`` and
    ``mean_delay_sec`` are None under the ranking floor of observations,
    and ``uncertain`` marks a shown rate whose interval is too wide to read."""

    observations: int
    on_time_pct: float | None
    mean_delay_sec: float | None
    uncertain: bool


class RouteRowOut(BaseModel):
    """One route on the requested day type's typical days before and after,
    classed on that day type. Times and gaps are seconds; ``main_stop_id``
    is the stop both gaps are measured at. ``observed_*`` is None until the
    windows exist."""

    route_code: str
    route_class: RouteClass
    trips_before: int
    trips_after: int
    last_before: int | None
    last_after: int | None
    main_stop_id: str | None
    main_stop_name: str | None
    longest_gap_before: int | None
    longest_gap_after: int | None
    observed_before: TallyOut | None
    observed_after: TallyOut | None


class ClassOut(BaseModel):
    """A class of routes observed on both sides, pooled."""

    route_class: RouteClass
    routes: int
    before: TallyOut
    after: TallyOut


class WindowOut(BaseModel):
    """The whole weeks pooled on each side, both ends inclusive."""

    weeks: int
    before_first: date
    before_last: date
    after_first: date
    after_last: date


class RevisionRoutesResponse(BaseModel):
    """What a revision did to each route on one day type.

    ``weeks_cap`` is how many whole weeks each side can ever pool, up to four;
    ``capped_by`` says what holds it under four for good: ``timetable`` (a
    timetable's span) or ``history`` (the agency's aggregated days before
    the revision). ``full_through`` is the last date the after side needs
    aggregated to reach the cap, None for a cap under one week. ``complete``
    is whether ``window`` pools the cap. ``definition`` stamps the observed
    figures and is None without them."""

    available: bool
    reason: Literal["no_revision", "no_calendar"] | None = None
    day_type: RevisionDayType
    day_type_known: bool = False
    weeks_cap: int = 0
    capped_by: Literal["timetable", "history"] | None = None
    full_through: date | None = None
    complete: bool = False
    window: WindowOut | None = None
    routes: list[RouteRowOut] = []
    classes: list[ClassOut] = []
    definition: DefinitionMeta | None = None


def _tally(t: Tally) -> TallyOut:
    shown = t.observations >= RANKING_MIN_SAMPLES
    return TallyOut(
        observations=t.observations,
        on_time_pct=t.on_time_pct if shown else None,
        mean_delay_sec=t.mean_delay_sec if shown else None,
        uncertain=shown and pct_is_uncertain(t.on_time, t.observations),
    )


def _route_row(
    c: RouteChange, route_class: RouteClass, names: dict[str, str | None], observed: Observed, spans: Windows | None
) -> RouteRowOut:
    def side(which: Side) -> TallyOut | None:
        return None if spans is None else _tally(observed.get((which, c.day_type, c.route_code), Tally()))

    return RouteRowOut(
        route_code=c.route_code,
        route_class=route_class,
        trips_before=c.trips_before,
        trips_after=c.trips_after,
        last_before=c.last_before,
        last_after=c.last_after,
        main_stop_id=c.main_stop,
        main_stop_name=None if c.main_stop is None else names.get(c.main_stop),
        longest_gap_before=c.longest_gap_before,
        longest_gap_after=c.longest_gap_after,
        observed_before=side("before"),
        observed_after=side("after"),
    )


def _class(c: ClassTally) -> ClassOut:
    return ClassOut(route_class=c.route_class, routes=c.routes, before=_tally(c.before), after=_tally(c.after))


def _window(spans: Windows) -> WindowOut:
    return WindowOut(
        weeks=spans.weeks,
        before_first=spans.before[0],
        before_last=spans.before[1],
        after_first=spans.after[0],
        after_last=spans.after[1],
    )


@router.get("/timetable-revisions/routes", response_model=RevisionRoutesResponse)
@limiter.limit(DEFAULT_LIMIT)
async def revision_routes(
    request: Request,
    on: date | None = Query(
        default=None,
        description="The revision's first day, as the revisions list gives it. Unset, the newest revision.",
    ),
    day_type: RevisionDayType = Query(default="weekday", description="The day type the routes are compared on."),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> RevisionRoutesResponse:
    """What a revision did to each route on one day type
    (pipeline.reports.revision_effects): its trips, last trip and
    longest gap at its main stop on the typical days before and after, and,
    once a whole week after it is aggregated, its observed on-time rate and
    mean delay over the same whole weeks on each side, with each class of
    route pooled. The observed figures are the feed's predicted delays under
    the default on-time window, as ``definition`` states. A day ``on`` that
    starts no revision is a 404."""
    revision = _revision_on(await load_revisions(ch, agency_id), on)
    if revision is None:
        return RevisionRoutesResponse(available=False, reason="no_revision", day_type=day_type)
    timetables = await load_route_timetables(ch, agency_id, revision)
    if isinstance(timetables, Unavailable):
        return RevisionRoutesResponse(available=False, reason="no_calendar", day_type=day_type)
    weeks = plan(revision, *await aggregated_days(conn, agency_id))
    spans = weeks.windows
    observed = {} if spans is None else await load_observed(conn, agency_id, spans)
    on_day, classes = timetables.on(day_type)
    changes = sorted(on_day, key=lambda c: (c.trips_after - c.trips_before, c.route_code))
    names = await _stop_names(conn, agency_id, sorted({c.main_stop for c in changes if c.main_stop is not None}))
    definition = None
    if spans is not None:
        meta = resolve_definition_meta("on_time", DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC)
        schedule = await read_schedule_days(conn, agency_id, spans.before[0], spans.after[1])
        definition = await stamp_build(conn, meta, agency_id, schedule)
    return RevisionRoutesResponse(
        available=True,
        day_type=day_type,
        day_type_known=day_type in timetables.known,
        weeks_cap=weeks.cap,
        capped_by=weeks.capped_by,
        full_through=weeks.full_through,
        complete=weeks.complete,
        window=None if spans is None else _window(spans),
        routes=[_route_row(c, classes[c.route_code], names, observed, spans) for c in changes],
        classes=[] if spans is None else [_class(c) for c in class_tallies(observed, classes, day_type)],
        definition=definition,
    )
