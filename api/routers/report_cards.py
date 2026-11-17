"""Route report cards (pipeline.reports.report_card): a route's monthly A to F
grade with the figures behind it, and the agency's list of them.

Admins only: a grade is shown to admins until operators have had the
methodology's advance notice and right of reply (docs/methodology.md,
"Publication process"), as the feed-quality grade is.

A month is a calendar month every day of which has closed; the default is the
newest such month. Every figure is a feed-based estimate, stated with the
method stamp beside it (``definition``).
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
from api.range import RangeCtx, last_closed_jst_day
from api.scope_applied import scope_applied
from api.security import User, require_admin
from pipeline.data_sources import credit_line
from pipeline.reports.definition import DefinitionMeta, format_definition_csv_line
from pipeline.reports.rankings import DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC
from pipeline.reports.report_card import (
    MIN_OBSERVATIONS,
    NotGradedReason,
    RouteCard,
    compute_report_cards,
    history_months,
    latest_closed_month,
    month_bounds,
)
from pipeline.reports.report_stamp import report_definition
from pipeline.route_grade import (
    BUNCHING_PCT_EDGES,
    EARLY_PCT_EDGES,
    EWT_MIN_EDGES,
    ON_TIME_PCT_EDGES,
    TRUANCY_CAP_PCT,
    TRUANCY_FAIL_PCT,
    FrequencyClass,
    Grade,
    Metric,
)

router = APIRouter(prefix="/api/{agency_id}", tags=["report_cards"])

_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


_NO_SCOPE = scope_applied()


class SubScoreOut(BaseModel):
    """One figure on a card: rates in percent, excess wait in minutes, each to
    a tenth as judged. ``value`` is None exactly when ``missing`` says why.
    The interval is a 95% Wilson interval over the route's trips; excess wait
    has none. ``letter`` is the figure's band, or for the service-delivered
    rate the best letter it allows; ``graded`` whether the route's class is
    graded on it."""

    metric: Metric
    value: float | None
    ci_low: float | None
    ci_high: float | None
    letter: Grade | None
    graded: bool
    missing: str | None


class MissingFigure(BaseModel):
    metric: Metric
    reason: str


class RaiseTo(BaseModel):
    """What one figure must reach for the next letter up."""

    metric: Metric
    value: float
    target: float


class RouteCardOut(BaseModel):
    """One route's card for one month. ``grade`` is None exactly when
    ``not_graded`` says why: a gate the route failed, with the figures it read
    (observations against ``floor``, ``coverage_pct`` of planned trips, the
    on-time interval), or ``not_judged`` with each ``missing`` figure."""

    route_code: str
    month: str
    frequency: FrequencyClass
    grade: Grade | None
    not_graded: NotGradedReason | None
    observations: int
    floor: int
    trips_observed: int | None
    planned_trips: int | None
    coverage_pct: float | None
    on_time_ci_low: float | None
    on_time_ci_high: float | None
    missing: list[MissingFigure]
    subscores: list[SubScoreOut]
    raise_to: list[RaiseTo]


class MonthGradeOut(BaseModel):
    """A month of a card's trend: its letter, or why it has none (``no_data``
    for a month the route was neither observed nor planned in)."""

    month: str
    grade: Grade | None
    not_graded: NotGradedReason | None


class GradingRules(BaseModel):
    """The edges of A, B, C and D, best first (pipeline.route_grade), and the
    observation floor."""

    ewt_min_edges: list[float]
    bunching_pct_edges: list[float]
    on_time_pct_edges: list[float]
    early_pct_edges: list[float]
    truancy_cap_pct: float
    truancy_fail_pct: float
    min_observations: int


class RouteReportCardResponse(BaseModel):
    """``card`` is None when the feed observed the route on no day of the
    month and its timetable planned none. ``history`` is the month and the
    ones before it, oldest first."""

    month: str
    latest_month: str
    card: RouteCardOut | None
    history: list[MonthGradeOut]
    rules: GradingRules
    definition: DefinitionMeta
    # A card reads its own month, none of the shared scope, so the scope bar
    # offers none of its conditions beside it.
    scope_applied: dict[str, bool] = _NO_SCOPE


class ReportCardListResponse(BaseModel):
    month: str
    latest_month: str
    cards: list[RouteCardOut]
    rules: GradingRules
    definition: DefinitionMeta
    # A card reads its own month, none of the shared scope, so the scope bar
    # offers none of its conditions beside it.
    scope_applied: dict[str, bool] = _NO_SCOPE


_RULES = GradingRules(
    ewt_min_edges=list(EWT_MIN_EDGES),
    bunching_pct_edges=list(BUNCHING_PCT_EDGES),
    on_time_pct_edges=list(ON_TIME_PCT_EDGES),
    early_pct_edges=list(EARLY_PCT_EDGES),
    truancy_cap_pct=TRUANCY_CAP_PCT,
    truancy_fail_pct=TRUANCY_FAIL_PCT,
    min_observations=MIN_OBSERVATIONS,
)


def _label(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def _resolve_month(month: str | None) -> tuple[date, date]:
    """The month asked for and the newest closed one. A month with a day still
    open has no card yet: its grade would move as the days arrive."""
    latest = latest_closed_month(last_closed_jst_day())
    if month is None:
        return latest, latest
    asked = date(int(month[:4]), int(month[5:7]), 1)
    if asked > latest:
        raise HTTPException(
            status_code=422,
            detail={"code": "month_not_closed", "latest_month": _label(latest)},
        )
    return asked, latest


def _card(card: RouteCard) -> RouteCardOut:
    return RouteCardOut(
        route_code=card.route_code,
        month=_label(card.month),
        frequency=card.frequency,
        grade=card.grade,
        not_graded=card.not_graded,
        observations=card.observations,
        floor=card.floor,
        trips_observed=card.trips_observed,
        planned_trips=card.planned_trips,
        coverage_pct=card.coverage_pct,
        on_time_ci_low=card.on_time_ci_low,
        on_time_ci_high=card.on_time_ci_high,
        missing=[MissingFigure(metric=m, reason=r) for m, r in card.missing],
        subscores=[SubScoreOut(**s.__dict__) for s in card.subscores],
        raise_to=[RaiseTo(metric=m, value=v, target=t) for m, v, t in card.raise_to],
    )


async def _definition(conn: asyncpg.Connection, ch: AsyncClient, agency_id: int, month: date) -> DefinitionMeta:
    first, last = month_bounds(month)
    definition, _ = await report_definition(
        conn,
        ch,
        agency_id,
        RangeCtx(from_date=first, to_date=last),
        "route_table",
        early_tolerance_sec=DEFAULT_EARLY_TOLERANCE_SEC,
        late_tolerance_sec=DEFAULT_LATE_TOLERANCE_SEC,
        live=False,
    )
    return definition


@router.get("/routes/{route}/report-card", response_model=RouteReportCardResponse)
@limiter.limit(DEFAULT_LIMIT)
async def route_report_card(
    request: Request,
    route: str,
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN, description="YYYY-MM; unset, the newest closed."),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
    _admin: User = Depends(require_admin),
) -> RouteReportCardResponse:
    """One route's card for a closed calendar month, with its letter over the
    months before it."""
    asked, latest = _resolve_month(month)
    # Stamped before the figures are computed: a rebuild since this process
    # last looked clears the caches they read.
    definition = await _definition(conn, ch, agency_id, asked)
    history = []
    card = None
    for each in history_months(asked):
        cards = await compute_report_cards(agency_id, each, conn, ch=ch)
        found = cards.get(route)
        history.append(
            MonthGradeOut(
                month=_label(each),
                grade=None if found is None else found.grade,
                not_graded="no_data" if found is None else found.not_graded,
            )
        )
        if each == asked and found is not None:
            card = _card(found)
    return RouteReportCardResponse(
        month=_label(asked),
        latest_month=_label(latest),
        card=card,
        history=history,
        rules=_RULES,
        definition=definition,
    )


_CSV_COLUMNS = [
    "系統コード",
    "月",
    "運行頻度",
    "評価",
    "評価なしの理由",
    "定時率(%)",
    "定時率下限(%)",
    "定時率上限(%)",
    "早発率(%)",
    "超過待ち時間(分)",
    "団子運転率(%)",
    "運行実施率(%)",
    "観測数",
    "観測便数",
    "計画便数",
    "観測率(%)",
    "欠けている値",
]

_FREQUENCY_CSV = {"high": "高頻度", "low": "低頻度"}


def _cell(value: float | int | None) -> str | float | int:
    return "" if value is None else value


def _csv_row(card: RouteCardOut) -> list:
    figures = {s.metric: s.value for s in card.subscores}
    return [
        card.route_code,
        card.month,
        _FREQUENCY_CSV[card.frequency],
        card.grade or "",
        card.not_graded or "",
        _cell(figures["on_time_pct"]),
        _cell(card.on_time_ci_low),
        _cell(card.on_time_ci_high),
        _cell(figures["early_pct"]),
        _cell(figures["ewt_min"]),
        _cell(figures["bunching_pct"]),
        _cell(figures["service_delivered_pct"]),
        card.observations,
        _cell(card.trips_observed),
        _cell(card.planned_trips),
        _cell(card.coverage_pct),
        "; ".join(f"{m.metric}={m.reason}" for m in card.missing),
    ]


def _grading_line() -> str:
    return (
        "評価基準: 高頻度系統は超過待ち時間と団子運転率の悪い方、低頻度系統は定時率（業界標準）と早発率の悪い方。"
        f"運行実施率{TRUANCY_CAP_PCT:g}%未満はD以下、{TRUANCY_FAIL_PCT:g}%未満はF。"
        f"観測{MIN_OBSERVATIONS}件未満と比較可能性の条件を満たさない系統は評価なし"
    )


def _csv_response(response: ReportCardListResponse, credit: str | None) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # BOM, so Excel reads the file as UTF-8
    w = csv.writer(buf)
    w.writerow([format_definition_csv_line(response.definition)])
    w.writerow([_grading_line()])
    if credit is not None:
        w.writerow([credit])
    w.writerow(_CSV_COLUMNS)
    w.writerows(_csv_row(card) for card in response.cards)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report_cards_{response.month}.csv"'},
    )


@router.get("/report-cards", response_model=ReportCardListResponse)
@limiter.limit(DEFAULT_LIMIT)
async def report_cards(
    request: Request,
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN, description="YYYY-MM; unset, the newest closed."),
    format: Literal["json", "csv"] | None = Query(default=None),
    lang: Literal["ja", "en"] | None = Query(
        default=None, description="format=csv only: the language of the data-source credit; unset, Accept-Language."
    ),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
    _admin: User = Depends(require_admin),
) -> ReportCardListResponse | StreamingResponse:
    """Every route's card for a closed calendar month, one row per route: its
    letter or why it has none, and its figures. ``format=csv`` writes them with
    the reports' definition preamble."""
    if lang is not None and format != "csv":
        raise HTTPException(status_code=400, detail="lang only applies to format=csv")
    asked, latest = _resolve_month(month)
    definition = await _definition(conn, ch, agency_id, asked)
    cards = await compute_report_cards(agency_id, asked, conn, ch=ch)
    response = ReportCardListResponse(
        month=_label(asked),
        latest_month=_label(latest),
        cards=[_card(card) for card in cards.values()],
        rules=_RULES,
        definition=definition,
    )
    if format == "csv":
        locale = lang or get_locale(request)
        feed_url = await conn.fetchval("SELECT feed_url FROM agencies WHERE agency_id = $1", agency_id)
        return _csv_response(response, credit_line(feed_url, locale))
    return response
