"""Transfer pairs an agency takes part in, and how reliably each one's planned
connection held (pipeline.reports.transfers).

A report like the others: readable wherever the agency's reports are. A pair
spans two agencies, so a pair is listed and read only while both are live.
Admins define the pairs (api/routers/admin_transfers.py).
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Literal

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.csv_cells import inert
from api.deps import get_agency, get_ch, get_conn, get_locale
from api.middleware.ratelimit import DEFAULT_LIMIT, limiter
from api.range import DAY_TYPE_SERVICE, last_closed_jst_day
from pipeline.data_sources import credit_line
from pipeline.reports.definition import DefinitionMeta, format_definition_csv_line, resolve_definition_meta
from pipeline.reports.rankings import MIN_GROUP_SAMPLES
from pipeline.reports.transfers import (
    BUFFER_SHARE,
    WAIT_BANDS_SEC,
    PairDayType,
    PairSpec,
    Reliability,
    Tally,
    compute_reliability,
)
from pipeline.route_catalog import route_code_sql
from pipeline.strategies.static_join import CoverageGap, CoverageGapReason

router = APIRouter(prefix="/api/{agency_id}", tags=["transfers"])

DEFAULT_DAYS = 28
# Computed at read time over both routes' predictions, so the window stays a
# season rather than the year the aggregate-backed reports allow.
MAX_DAYS = 92


class PairSide(BaseModel):
    agency_id: int
    agency_name: str
    route_code: str
    route_name: str | None
    direction: int | None
    stop_ids: list[str]
    stop_names: list[str | None]


class TransferPairSummary(BaseModel):
    id: int
    name: str
    arriving: PairSide
    departing: PairSide
    walk_sec: int
    day_types: list[PairDayType]


class TransferPairsResponse(BaseModel):
    pairs: list[TransferPairSummary]


class TallyOut(BaseModel):
    """Connections planned, and how each turned out. ``held_share`` is held
    over held plus missed, None when nothing was observed; ``small_sample``
    says fewer than ``min_samples`` were."""

    planned: int
    held: int
    missed: int
    unobserved: int
    stranded: int
    held_share: float | None
    small_sample: bool


class HourOut(TallyOut):
    hour: int


class DayOut(TallyOut):
    day: date


class WaitBandOut(BaseModel):
    max_sec: int
    share: float


class TransferReliabilityResponse(BaseModel):
    """A pair over the days of one day type. ``held_share``, the wait
    figures and ``buffer_min`` are None below ``min_samples``. Days either
    agency's loaded timetable does not plan are in ``no_plan_days`` and in
    no other figure. ``arrivals_unread_reason`` says why the arriving
    agency's arrival delays were not read (its feed is not confirmed to send
    them), so its arrivals came from departure delays alone, and a route that
    ends at the hub reads unobserved; ``arrivals_last_checked`` is the day of
    the check behind it."""

    pair: TransferPairSummary
    from_date: date
    to_date: date
    day_type: PairDayType
    min_samples: int
    buffer_share: float
    total: TallyOut
    held_share: float | None
    no_onward: int
    waits: int
    wait_p50_sec: float | None
    wait_p90_sec: float | None
    wait_bands: list[WaitBandOut] | None
    buffer_min: int | None
    by_hour: list[HourOut]
    by_day: list[DayOut]
    worst_days: list[DayOut]
    no_plan_days: list[date]
    arrivals_unread_reason: CoverageGapReason | None = None
    arrivals_last_checked: date | None = None
    definition: DefinitionMeta


_PAIRS_SQL = f"""
    SELECT p.id, p.name, p.walk_sec, p.day_types,
           p.from_agency_id, fa.agency_name AS from_agency_name, p.from_route_code, p.from_direction,
           p.from_stop_ids, fa.feed_url AS from_feed_url,
           (SELECT r.route_short_name FROM static_routes r
            WHERE r.agency_id = p.from_agency_id AND {route_code_sql("r.route_id")} = p.from_route_code
            ORDER BY r.route_id LIMIT 1) AS from_route_name,
           ARRAY(SELECT s.stop_name FROM unnest(p.from_stop_ids) WITH ORDINALITY AS u(stop_id, n)
                 LEFT JOIN static_stops s ON s.agency_id = p.from_agency_id AND s.stop_id = u.stop_id
                 ORDER BY u.n) AS from_stop_names,
           p.to_agency_id, ta.agency_name AS to_agency_name, p.to_route_code, p.to_direction,
           p.to_stop_ids, ta.feed_url AS to_feed_url,
           (SELECT r.route_short_name FROM static_routes r
            WHERE r.agency_id = p.to_agency_id AND {route_code_sql("r.route_id")} = p.to_route_code
            ORDER BY r.route_id LIMIT 1) AS to_route_name,
           ARRAY(SELECT s.stop_name FROM unnest(p.to_stop_ids) WITH ORDINALITY AS u(stop_id, n)
                 LEFT JOIN static_stops s ON s.agency_id = p.to_agency_id AND s.stop_id = u.stop_id
                 ORDER BY u.n) AS to_stop_names
    FROM transfer_pairs p
    JOIN agencies fa ON fa.agency_id = p.from_agency_id AND fa.deleted_at IS NULL
    JOIN agencies ta ON ta.agency_id = p.to_agency_id AND ta.deleted_at IS NULL
    WHERE p.active AND $1 IN (p.from_agency_id, p.to_agency_id)
"""


def _side(row: asyncpg.Record, prefix: str) -> PairSide:
    return PairSide(
        agency_id=row[f"{prefix}_agency_id"],
        agency_name=row[f"{prefix}_agency_name"],
        route_code=row[f"{prefix}_route_code"],
        route_name=row[f"{prefix}_route_name"],
        direction=row[f"{prefix}_direction"],
        stop_ids=list(row[f"{prefix}_stop_ids"]),
        stop_names=list(row[f"{prefix}_stop_names"]),
    )


def _summary(row: asyncpg.Record) -> TransferPairSummary:
    return TransferPairSummary(
        id=row["id"],
        name=row["name"],
        arriving=_side(row, "from"),
        departing=_side(row, "to"),
        walk_sec=row["walk_sec"],
        day_types=list(row["day_types"]),
    )


@router.get("/transfer-pairs", response_model=TransferPairsResponse)
@limiter.limit(DEFAULT_LIMIT)
async def list_transfer_pairs(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
) -> TransferPairsResponse:
    """The active pairs this agency arrives or departs in, by name."""
    rows = await conn.fetch(_PAIRS_SQL + " ORDER BY p.name, p.id", agency_id)
    return TransferPairsResponse(pairs=[_summary(r) for r in rows])


def _tally(t: Tally) -> dict:
    return {
        "planned": t.planned,
        "held": t.held,
        "missed": t.missed,
        "unobserved": t.unobserved,
        "stranded": t.stranded,
        "held_share": t.held_share,
        "small_sample": t.small_sample,
    }


def _window(from_date: date | None, to_date: date | None) -> tuple[date, date]:
    last = last_closed_jst_day()
    end = to_date or last
    start = from_date or end - timedelta(days=DEFAULT_DAYS - 1)
    if end > last:
        raise HTTPException(status_code=422, detail=f"to must be on or before {last.isoformat()}")
    if start > end:
        raise HTTPException(status_code=422, detail="from must be on or before to")
    if (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"at most {MAX_DAYS} days")
    return start, end


_CSV_COLUMNS = [
    "日付",
    "計画した接続",
    "接続できた",
    "接続できなかった",
    "観測なし",
    "接続できた割合(%)",
    "観測が少ない",
    "当日中に乗り継げる便がなかった",
]


def _pct(share: float | None) -> str:
    return "" if share is None else f"{share * 100:.1f}"


# Why the arriving agency's arrival delays were not read, in the file's
# Japanese, as the board says it in the reader's language
# (transfers.arrivals_unread.*).
_FROM_DEPARTURES = "到着は発車の遅延から判定しています"
_ARRIVALS_UNREAD_CSV: dict[CoverageGapReason, str] = {
    "feed_unsupported": "到着の遅延は使っていません：この事業者の配信形式は到着の遅延を伝えられません。"
    + _FROM_DEPARTURES,
    "probe_refuted": "到着の遅延は使っていません：直近の確認（{day}）で、この事業者の配信は到着の遅延を"
    "伝えていませんでした。" + _FROM_DEPARTURES,
    "probe_expired": "到着の遅延は使っていません：配信が到着の遅延を伝えるかの確認（{day}）が期限切れです。"
    + _FROM_DEPARTURES,
    "never_probed": "到着の遅延は使っていません：この事業者の配信が到着の遅延を伝えるかは、まだ確認していません。"
    + _FROM_DEPARTURES,
}


def _checked_day(gap: CoverageGap) -> str:
    return "日付不明" if gap.last_checked is None else gap.last_checked.isoformat()


def _side_text(side: PairSide) -> str:
    return f"{side.agency_name} {side.route_name or side.route_code} 停留所ID {'・'.join(side.stop_ids)}"


def _csv_lines(pair: TransferPairSummary, r: Reliability, start: date, end: date, day_type: PairDayType) -> list[str]:
    lines = [
        f"乗継ぎ：{pair.name}（{_side_text(pair.arriving)} → {_side_text(pair.departing)}；"
        f"乗継ぎの歩行 {pair.walk_sec}秒）",
        f"期間：{start.isoformat()}〜{end.isoformat()}（{DAY_TYPE_SERVICE[day_type]}）",
        f"計画した接続 {r.total.planned}；接続できた {r.total.held}；接続できなかった {r.total.missed}；"
        f"観測なし {r.total.unobserved}；接続できた割合 {_pct(r.held_share) or '標本不足'}；"
        f"当日中に乗り継ぐ便が計画されていない到着 {r.no_onward}",
    ]
    if r.arrivals_gap is not None:
        lines.append(_ARRIVALS_UNREAD_CSV[r.arrivals_gap.reason].format(day=_checked_day(r.arrivals_gap)))
    if r.buffer_min is not None:
        lines.append(
            f"計画上の乗継ぎ時間を{r.buffer_min}分とっていれば、観測した{DAY_TYPE_SERVICE[day_type]}の接続の"
            f"{BUFFER_SHARE:.0%}が接続できた（フィードの予測値にもとづく）"
        )
    if r.no_plan_days:
        lines.append(f"時刻表が計画していない日（集計外）：{'、'.join(d.isoformat() for d in r.no_plan_days)}")
    return lines


def csv_text(preamble: list[str], rows: list[list]) -> str:
    """The monitoring list: a BOM, so Excel reads the file as UTF-8, one
    preamble line per row, then the columns and a row per day."""
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    for line in preamble:
        w.writerow([inert(line)])
    w.writerow(_CSV_COLUMNS)
    w.writerows(rows)
    return buf.getvalue()


def _csv_response(preamble: list[str], rows: list[list], fname: str) -> StreamingResponse:
    return StreamingResponse(
        iter([csv_text(preamble, rows)]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/transfer-pairs/{pair_id}/reliability", response_model=TransferReliabilityResponse)
@limiter.limit(DEFAULT_LIMIT)
async def transfer_reliability(
    request: Request,
    pair_id: int,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    day_type: PairDayType | None = Query(
        default=None, description="One of the pair's day types; unset, its first (weekday when it has one)."
    ),
    format: Literal["json", "csv"] = "json",
    lang: Literal["ja", "en"] | None = Query(
        default=None,
        description="format=csv only: the language of the data-source credit; the rest of the file is Japanese, "
        "like the reports' CSV. Unset, it follows Accept-Language.",
    ),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
) -> TransferReliabilityResponse | StreamingResponse:
    """How the pair's planned connections held over the days of one day type,
    the last 28 closed days unless ``from``/``to`` say otherwise.
    ``format=csv`` writes the days as a monitoring list."""
    if lang is not None and format != "csv":
        raise HTTPException(status_code=400, detail="lang only applies to format=csv")
    row = await conn.fetchrow(_PAIRS_SQL + " AND p.id = $2", agency_id, pair_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown transfer pair")
    pair = _summary(row)
    chosen = day_type or pair.day_types[0]
    if chosen not in pair.day_types:
        raise HTTPException(status_code=422, detail=f"the pair is not read on {chosen} days")
    start, end = _window(from_date, to_date)
    spec = PairSpec(
        pair.arriving.agency_id,
        pair.arriving.route_code,
        pair.arriving.direction,
        tuple(pair.arriving.stop_ids),
        pair.departing.agency_id,
        pair.departing.route_code,
        pair.departing.direction,
        tuple(pair.departing.stop_ids),
        pair.walk_sec,
    )
    result = await compute_reliability(conn, ch, spec, start, end, chosen)
    definition = resolve_definition_meta("transfer_reliability", None, None, live=True)

    if format == "csv":
        locale = lang or get_locale(request)
        credits = [c for c in dict.fromkeys(credit_line(row[f"{s}_feed_url"], locale) for s in ("from", "to")) if c]
        preamble = [format_definition_csv_line(definition), *_csv_lines(pair, result, start, end, chosen), *credits]
        rows = [
            [
                d.isoformat(),
                t.planned,
                t.held,
                t.missed,
                t.unobserved,
                _pct(t.held_share),
                "はい" if t.small_sample else "",
                t.stranded,
            ]
            for d, t in result.by_day.items()
        ]
        return _csv_response(preamble, rows, f"transfer_{pair.id}_{start.isoformat()}_{end.isoformat()}.csv")

    by_day = {d: DayOut(day=d, **_tally(t)) for d, t in result.by_day.items()}
    return TransferReliabilityResponse(
        pair=pair,
        from_date=start,
        to_date=end,
        day_type=chosen,
        min_samples=MIN_GROUP_SAMPLES,
        buffer_share=BUFFER_SHARE,
        total=TallyOut(**_tally(result.total)),
        held_share=result.held_share,
        no_onward=result.no_onward,
        waits=result.waits,
        wait_p50_sec=result.wait_p50_sec,
        wait_p90_sec=result.wait_p90_sec,
        wait_bands=(
            None
            if result.within_band_share is None
            else [WaitBandOut(max_sec=band, share=result.within_band_share[band]) for band in WAIT_BANDS_SEC]
        ),
        buffer_min=result.buffer_min,
        by_hour=[HourOut(hour=h, **_tally(t)) for h, t in result.by_hour.items()],
        by_day=list(by_day.values()),
        worst_days=[by_day[d] for d in result.worst_days],
        no_plan_days=result.no_plan_days,
        arrivals_unread_reason=None if result.arrivals_gap is None else result.arrivals_gap.reason,
        arrivals_last_checked=None if result.arrivals_gap is None else result.arrivals_gap.last_checked,
        definition=definition,
    )
