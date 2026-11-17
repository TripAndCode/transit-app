"""Timetable revisions: each change of timetable an agency's archive knows,
and what one changed at every stop (pipeline.reports.timetable_diff).

Reader-facing, like the reports: it is drawn from the published timetables
alone, and its list of stops that lost service is meant for the councils and
monitoring lists outside the operator.
"""

import csv
import io
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
from pipeline.reports.definition import CONTENT_HASH_DIGITS
from pipeline.reports.timetable_diff import (
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
    every day type when none is requested; ``day_types`` always all three."""

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


def _typical_days_line(comparison: Comparison) -> str:
    days = " / ".join(
        f"{DAY_TYPE_SERVICE[s.day_type]} "
        f"{s.before.day.isoformat() if s.before else '不明'} → {s.after.day.isoformat() if s.after else '不明'}"
        for s in comparison.day_types
    )
    return f"代表日（改正前 → 改正後）：{days}"


def _csv_response(
    first_day: date | None, preamble: list[str], credit: str | None, rows: list[list], message: str | None
) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # BOM, so Excel reads the file as UTF-8
    w = csv.writer(buf)
    for line in preamble:
        w.writerow([line])
    if credit is not None:
        w.writerow([credit])
    w.writerow(_CSV_COLUMNS)
    if message is not None:
        w.writerow([message])
    w.writerows(rows)
    fname = f"timetable_revision_{first_day.isoformat() if first_day else 'none'}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
        description="JSON only: list the changed stops of this day type alone. The day-type totals and the "
        "CSV always cover all three.",
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
    if lang is not None and format != "csv":
        raise HTTPException(status_code=400, detail="lang only applies to format=csv")
    found = await load_revisions(ch, agency_id)
    if on is None:
        revision = found[-1] if found else None
    else:
        revision = next((r for r in found if r.first_day == on), None)
        if revision is None:
            raise HTTPException(status_code=404, detail=f"No timetable revision starts on {on.isoformat()}")
    result = Unavailable.NO_REVISION if revision is None else await load_comparison(ch, agency_id, revision)

    if format == "csv":
        # A direct read, not Depends(get_locale): the JSON this route returns
        # holds no localized text, so its client caches it whatever the language.
        locale = lang or get_locale(request)
        credit = credit_line(await conn.fetchval("SELECT feed_url FROM agencies WHERE agency_id=$1", agency_id), locale)
        if isinstance(result, Unavailable):
            message = UNAVAILABLE_CSV[(result, "en" if locale == "en" else "ja")]
            preamble = [] if revision is None else [_version_line(revision)]
            return _csv_response(None if revision is None else revision.first_day, preamble, credit, [], message)
        names = await _stop_names(conn, agency_id, sorted({s.stop_id for s in result.stops}))
        preamble = [_version_line(result.revision), _typical_days_line(result)]
        return _csv_response(result.revision.first_day, preamble, credit, _csv_rows(result, names), None)

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
