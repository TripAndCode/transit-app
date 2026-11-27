"""Segment speed and slow zones (pipeline.reports.slow_zones): every timetable
segment of an agency by its running time over the timetable, week by week,
and the segments that stayed slow. And one route's timetable fit
(pipeline.reports.segment_runtime): each of its segments' running time beside
the scheduled one, over the scope's dates.

Every figure is a feed-based estimate (``value_basis``). The list is ordered
by how long a segment has stayed slow, never by minutes lost: ranking
segments for bus priority waits for the agency's predictions to be validated.
For the same reason the timetable fit keeps the route's order and derives no
recommended running time.
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

from api.deps import get_agency, get_agency_range_ctx, get_ch, get_conn, get_locale
from api.middleware.ratelimit import DEFAULT_LIMIT, limiter
from api.range import DAY_TYPE_SERVICE, TIME_BAND_RANGES, RangeCtx, last_closed_jst_day
from api.scope_applied import scope_applied
from pipeline.agg_registry import METHOD_VERSION
from pipeline.data_sources import credit_line
from pipeline.reports.definition import METHODOLOGY_URLS, DefinitionMeta, value_basis_text
from pipeline.reports.report_stamp import report_definition
from pipeline.reports.segment_runtime import compute_segment_runtime
from pipeline.reports.slow_zones import (
    MIN_SCHEDULED_SEC,
    MIN_SLOW_WEEKS,
    MIN_WEEK_SAMPLES,
    SLOW_EXCESS_SEC,
    SegmentWeek,
    SlowZoneDayType,
    SlowZones,
    SlowZoneSegment,
    minutes_lost_per_day,
    read_slow_zones,
    speed_kmh,
    trips_per_day,
)

router = APIRouter(prefix="/api/{agency_id}", tags=["segments"])

# The keys of api.range.TIME_BAND_RANGES.
TimeBand = Literal["morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"]


class SegmentWeekOut(BaseModel):
    """One week of a segment. ``excess_sec`` is the median running time
    minus the scheduled one; ``speed_kmh`` reads the straight-line distance.
    ``enough_samples`` False: too few runs for the week to count either way."""

    samples: int
    days: int
    run_p50_sec: float | None
    run_p85_sec: float | None
    scheduled_run_sec: float | None
    excess_sec: float | None
    speed_kmh: float | None
    trips_per_day: float | None
    minutes_lost_per_day: float | None
    enough_samples: bool


class SegmentOut(BaseModel):
    """A stop pair of the agency, over every route that runs it.
    Coordinates and names are the loaded timetable's, None for a stop it
    lacks. ``slow_weeks`` is the newest weeks slow in a row. A slow zone alone
    carries ``slow_since``, the first of them, and ``weekly_excess_sec``, one
    entry per response week, None for a week with no run or too few."""

    from_stop_id: str
    from_stop_name: str | None
    from_lon: float | None
    from_lat: float | None
    to_stop_id: str
    to_stop_name: str | None
    to_lon: float | None
    to_lat: float | None
    distance_m: float | None
    routes: list[str]
    latest: SegmentWeekOut | None
    slow_weeks: int
    slow_since: date | None = None
    weekly_excess_sec: list[float | None] | None = None


class SlowZoneThresholds(BaseModel):
    excess_sec: int
    min_scheduled_sec: int
    min_weeks: int
    min_samples: int


class SlowZonesResponse(BaseModel):
    """``segments`` holds every segment run in the band, by stop ids;
    ``slow_zones`` the from/to ids of the slow zones, longest-standing first.
    ``weeks`` are the Mondays of the weeks read, oldest first."""

    available: bool
    unavailable_reason: str | None
    last_checked: str | None
    value_basis: str
    day_type: SlowZoneDayType
    time_band: TimeBand
    weeks: list[date]
    thresholds: SlowZoneThresholds
    segments: list[SegmentOut]
    slow_zones: list[tuple[str, str]]


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _week(week: SegmentWeek, distance_m: float | None) -> SegmentWeekOut:
    return SegmentWeekOut(
        samples=week.samples,
        days=week.days,
        run_p50_sec=_round(week.run_p50_sec),
        run_p85_sec=_round(week.run_p85_sec),
        scheduled_run_sec=_round(week.scheduled_run_sec),
        excess_sec=_round(week.excess_sec),
        speed_kmh=_round(speed_kmh(distance_m, week.run_p50_sec)),
        trips_per_day=_round(trips_per_day(week)),
        minutes_lost_per_day=_round(minutes_lost_per_day(week)),
        enough_samples=week.enough_samples,
    )


def _segment(s: SlowZoneSegment, weeks: list[date]) -> SegmentOut:
    latest = s.latest
    out = SegmentOut(
        from_stop_id=s.from_stop_id,
        from_stop_name=s.from_stop_name,
        from_lon=s.from_lon,
        from_lat=s.from_lat,
        to_stop_id=s.to_stop_id,
        to_stop_name=s.to_stop_name,
        to_lon=s.to_lon,
        to_lat=s.to_lat,
        distance_m=_round(s.distance_m, 0),
        routes=s.routes,
        latest=None if latest is None else _week(latest, s.distance_m),
        slow_weeks=s.slow_weeks,
    )
    if s.is_slow_zone:
        # Set only here, so the response (exclude_unset) carries them for the list alone.
        out.slow_since = weeks[-s.slow_weeks]
        out.weekly_excess_sec = [_round(w.excess_sec) if w is not None and w.enough_samples else None for w in s.weeks]
    return out


_CSV_COLUMNS = [
    "始点停留所ID",
    "始点停留所名",
    "終点停留所ID",
    "終点停留所名",
    "系統",
    "遅い状態が続いた週数",
    "開始週",
    "最新週の走行時間の中央値(秒)",
    "最新週の時刻表の走行時間(秒)",
    "時刻表からの超過(秒)",
    "直線距離(m)",
    "速度(km/h、直線距離による)",
    "1日あたりの便数",
    "1日あたりの損失(分)",
]

UNAVAILABLE_CSV = {
    "ja": "この事業者のフィードは到着時刻の配信が確認されていないため、区間の走行時間を求められません。",
    "en": "This agency's feed is not confirmed to send arrival times, so its segment running times are not computed.",
}


def _cell(value: float | None, digits: int = 1) -> str | float:
    return "" if value is None else round(value, digits)


def _csv_rows(result: SlowZones) -> list[list]:
    rows = []
    for s in result.slow_zones:
        latest = s.latest
        assert latest is not None  # a slow zone's newest week was slow
        rows.append(
            [
                s.from_stop_id,
                s.from_stop_name or "",
                s.to_stop_id,
                s.to_stop_name or "",
                " ".join(s.routes),
                s.slow_weeks,
                result.weeks[-s.slow_weeks].isoformat(),
                _cell(latest.run_p50_sec),
                _cell(latest.scheduled_run_sec),
                _cell(latest.excess_sec),
                _cell(s.distance_m, 0),
                _cell(speed_kmh(s.distance_m, latest.run_p50_sec)),
                _cell(trips_per_day(latest)),
                _cell(minutes_lost_per_day(latest)),
            ]
        )
    return rows


def _preamble(result: SlowZones) -> list[str]:
    start, end = TIME_BAND_RANGES[result.time_band]
    first, last = result.weeks[0], result.weeks[-1]
    return [
        f"区間の遅い状態の一覧：{DAY_TYPE_SERVICE[result.day_type]}、{start}–{end}発、"
        f"{first.isoformat()}の週から{last.isoformat()}の週まで",
        f"条件：中央値が時刻表より{SLOW_EXCESS_SEC}秒以上長い週（時刻表の走行時間{MIN_SCHEDULED_SEC}秒以上、"
        f"週{MIN_WEEK_SAMPLES}便以上）が、最新の週まで{MIN_SLOW_WEEKS}週以上続いた区間",
        "予測値の検証を終えるまで、バス優先策の候補の順位付けには使いません。",
        f"値の根拠={value_basis_text(result.value_basis, 'ja')}; "
        f"算出方法の説明={METHODOLOGY_URLS['ja']}; 算出方法={METHOD_VERSION}",
    ]


def _csv_response(result: SlowZones, credit: str | None, message: str | None) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # BOM, so Excel reads the file as UTF-8
    w = csv.writer(buf)
    for line in _preamble(result):
        w.writerow([line])
    if credit is not None:
        w.writerow([credit])
    w.writerow(_CSV_COLUMNS)
    if message is not None:
        w.writerow([message])
    w.writerows(_csv_rows(result))
    fname = f"slow_zones_{result.day_type}_{result.time_band}_{result.weeks[-1].isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/segments/slow-zones", response_model=SlowZonesResponse, response_model_exclude_unset=True)
@limiter.limit(DEFAULT_LIMIT)
async def slow_zones(
    request: Request,
    time_band: TimeBand = Query("morning", description="The band of the scheduled departure from the first stop."),
    day_type: SlowZoneDayType = Query("weekday", description="Dates by the calendar: weekdays, or 土日祝."),
    format: str | None = Query(default=None, pattern="^(json|csv)$"),
    lang: str | None = Query(
        default=None,
        pattern="^(ja|en)$",
        description="format=csv only: the language of the data-source credit and of the row standing in for "
        "the list when the agency's arrivals are not trusted; the rest of the file is Japanese. Unset, those "
        "rows follow Accept-Language.",
    ),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
) -> SlowZonesResponse | StreamingResponse:
    """Every segment's running time over the timetable in each of the
    complete weeks ending by the last closed day, and its slow streak.
    ``format=csv`` writes the slow zones as a monitoring list."""
    if lang is not None and format != "csv":
        raise HTTPException(status_code=400, detail="lang only applies to format=csv")
    result = await read_slow_zones(
        agency_id, conn, last_day=last_closed_jst_day(), day_type=day_type, time_band=time_band
    )
    if format == "csv":
        locale = lang or get_locale(request)
        credit = credit_line(await conn.fetchval("SELECT feed_url FROM agencies WHERE agency_id=$1", agency_id), locale)
        message = None if result.available else UNAVAILABLE_CSV["en" if locale == "en" else "ja"]
        return _csv_response(result, credit, message)
    return SlowZonesResponse(
        available=result.available,
        unavailable_reason=result.unavailable_reason,
        last_checked=result.last_checked,
        value_basis=result.value_basis,
        day_type=result.day_type,
        time_band=time_band,
        weeks=result.weeks,
        thresholds=SlowZoneThresholds(
            excess_sec=SLOW_EXCESS_SEC,
            min_scheduled_sec=MIN_SCHEDULED_SEC,
            min_weeks=MIN_SLOW_WEEKS,
            min_samples=MIN_WEEK_SAMPLES,
        ),
        segments=[_segment(s, result.weeks) for s in result.segments],
        slow_zones=[(s.from_stop_id, s.to_stop_id) for s in result.slow_zones],
    )


class TimetableFitSegment(BaseModel):
    """One segment of the route in one time band. ``padding_sec`` is the
    scheduled running time minus the median: positive is slack, negative too
    tight. ``time_band`` is None for a segment the timetable gives no
    departure time; a segment with no observed run in range has no
    percentiles and no padding."""

    from_stop_id: str
    from_stop_name: str | None
    to_stop_id: str
    to_stop_name: str | None
    time_band: TimeBand | None
    to_stop_sequence: int
    samples: int
    run_p50_sec: float | None
    run_p85_sec: float | None
    scheduled_run_sec: float | None
    padding_sec: float | None
    hold_eligible: int
    hold_rate: float | None


class TimetableFitResponse(BaseModel):
    """``segments`` in the route's order, then by band. ``static_version_id``
    is the timetable read: the one in force on the range's last day, whose
    dates alone are read (``days`` of them). ``terminus_early_rate`` is over
    the ``terminus_arrivals`` at a trip's last stop, None without one."""

    available: bool
    unavailable_reason: str | None
    last_checked: str | None
    value_basis: str
    static_version_id: str | None
    days: int
    terminus_arrivals: int
    terminus_early_rate: float | None
    segments: list[TimetableFitSegment]
    definition: DefinitionMeta
    scope_applied: dict[str, bool]


# The route is named by ``route``; the scope's routes filter does not narrow it.
_TIMETABLE_FIT_SCOPE = scope_applied("from", "to", "dow", "time_band", "service")


@router.get("/segments/timetable-fit", response_model=TimetableFitResponse)
@limiter.limit(DEFAULT_LIMIT)
async def timetable_fit(
    request: Request,
    route: str = Query(
        ...,
        min_length=1,
        description="The route whose segments are read. The scope's routes filter does not narrow it.",
    ),
    agency_id: int = Depends(get_agency),
    ctx: RangeCtx = Depends(get_agency_range_ctx),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> TimetableFitResponse:
    """The route's running time per timetable segment and time band over the
    scope's dates, beside the scheduled running time, for its own page."""
    definition, _ = await report_definition(conn, ch, agency_id, ctx, "segment_runtime")
    fit = await compute_segment_runtime(agency_id, ctx, conn, route=route)
    return TimetableFitResponse(
        available=fit["available"],
        unavailable_reason=fit["unavailable_reason"],
        last_checked=fit["last_checked"],
        value_basis=fit["value_basis"],
        static_version_id=fit["static_version_id"],
        days=fit["days"],
        terminus_arrivals=fit["terminus_arrivals"],
        terminus_early_rate=fit["terminus_early_rate"],
        segments=[
            TimetableFitSegment(
                **{
                    **seg,
                    "run_p50_sec": _round(seg["run_p50_sec"]),
                    "run_p85_sec": _round(seg["run_p85_sec"]),
                    "scheduled_run_sec": _round(seg["scheduled_run_sec"]),
                    "padding_sec": _round(seg["padding_sec"]),
                }
            )
            for seg in fit["segments"]
        ],
        definition=definition.read_under(fit["static_version_id"]) if fit["days"] else definition,
        scope_applied=_TIMETABLE_FIT_SCOPE,
    )
