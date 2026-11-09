"""Each agency's feed-quality scorecard per calendar month of service date,
built by analyze into ``feed_quality_monthly``.

A month counts the service dates from its first day through its last, or for
the month in progress through the last closed service day, and every figure
counts that same window:

- **RT completeness**: the trips the feed reported of those the timetable
  planned, by the comparability gate's own rule and SQL
  (:func:`pipeline.reports.comparability.coverage_pct` over
  :func:`pipeline.reports.service_delivered.service_sql`), so a month reads
  what the agencies board reads over the same days.
- **Stop-event completeness**: the stop events the feed reported a departure
  or arrival delay for (one per trip, stop and service date, however often
  reported) of those the planned trips' stop times plan, over the planned days
  whose stop events are known. A day saved before the capture kept them is
  left out (``stop_plan_days`` counts the rest).
- **Message age**: per poll, the instant the poll was captured minus its
  FeedHeader timestamp, summarized by :data:`MESSAGE_AGE_QUANTILES`. Only a
  poll from the live path counts: archive ingest stamps a row's captured_at
  from that same header (pipeline.strategies._pb.archive_captured_at), so an
  archived poll's age reads zero by construction.
- **Clamp share**: the gate's :func:`pipeline.reports.comparability.clamp_pct`
  over agg_feed_health's counts.
- **Field coverage**: per field of
  :data:`pipeline.strategies.static_join.RT_COVERAGE_FIELDS`, the share of
  stored stop_time_updates carrying it, the unit the RT coverage probe
  measures a single poll in (``static_join.field_coverage``).
- **VehiclePositions available**: not recorded anywhere the app reads.

A figure the feed or the app cannot supply is ``None`` beside a
:data:`Missing` reason, never 0. Trip and stop-event completeness need trip_ids
matched to the timetable (``coverage_unknown`` otherwise, the gate's reason)
and a plan; field coverage needs an ingest strategy that stores the fields.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pipeline.clickhouse import LIVE_SOURCED, TRIP_LEVEL_STOP_SEQUENCE
from pipeline.reports import comparability
from pipeline.reports.comparability import clamp_pct, coverage_pct, trips_matchable
from pipeline.reports.service_delivered import coverage_of, planned_days_ctes, service_sql
from pipeline.service_day import service_date_ch, service_date_of, service_day_start_utc
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS, RT_INGEST_STRATEGIES

logger = logging.getLogger(__name__)

TABLE = "feed_quality_monthly"

#: Why a figure is ``None``:
#:
#: - ``coverage_unknown``: the ingest strategy's trip_ids are not the
#:   timetable's, so its trips cannot be counted against the plan;
#: - ``no_plan``: no trip is planned in the window;
#: - ``no_stop_plan``: trips are planned, but no planned day's stop events
#:   are known;
#: - ``no_data``: nothing was observed in the window (for the clamp share,
#:   no observation carried a delay);
#: - ``archive_stamped``: every poll came through archive ingest, whose
#:   captured_at is the header itself;
#: - ``no_header``: the live polls carried no FeedHeader timestamp;
#: - ``feed_cannot_report``: the ingest strategy never stores the fields;
#: - ``not_recorded``: the app records no source for the figure.
Missing = Literal[
    "coverage_unknown",
    "no_plan",
    "no_stop_plan",
    "no_data",
    "archive_stamped",
    "no_header",
    "feed_cannot_report",
    "not_recorded",
]

#: Message age's median and 95th percentile, by linear interpolation between
#: the closest ranks (ClickHouse's ``quantilesExactInclusive``, the rule of
#: numpy's default and Excel's PERCENTILE.INC), in seconds to one decimal.
MESSAGE_AGE_QUANTILES = (0.5, 0.95)

COLUMNS: tuple[str, ...] = (
    "agency_id",
    "month",
    "through_date",
    "covered_days",
    "window_days",
    "observed_trips",
    "planned_trips",
    "rt_completeness_pct",
    "rt_completeness_missing",
    "observed_stop_events",
    "planned_stop_events",
    "stop_plan_days",
    "stop_completeness_pct",
    "stop_completeness_missing",
    "message_age_polls",
    "message_age_p50_sec",
    "message_age_p95_sec",
    "message_age_missing",
    "raw_samples",
    "clamp_count",
    "clamp_pct",
    "clamp_missing",
    "stop_time_updates",
    *(f"{field}_pct" for field in RT_COVERAGE_FIELDS),
    "field_coverage_missing",
    "vehicle_positions_available",
    "vehicle_positions_missing",
)

# The window's rows: from the first service day's start to the start of the
# day after `through`, a range on the stored instant so the (agency_id,
# captured_at) sort key prunes the scan to the window.
_WINDOW = "u.captured_at >= {window_start:DateTime64} AND u.captured_at < {window_end:DateTime64}"

_FIELD_COUNTS = ", ".join(f"countIf(u.{field} IS NOT NULL) AS {field}_rows" for field in RT_COVERAGE_FIELDS)

# Per service date: the stop_time_updates stored, how many carry each RT
# coverage field, and the stop events reported with a delay. The trip-level
# row a cancellation without stops leaves is no stop_time_update.
ROWS_CH_SQL = f"""
    SELECT {service_date_ch("u.captured_at")} AS date, count() AS stop_time_updates, {_FIELD_COUNTS},
           uniqExactIf((u.trip_id, u.stop_sequence), u.dep_delay IS NOT NULL OR u.arr_delay IS NOT NULL)
               AS stop_events
    FROM updates AS u
    WHERE u.agency_id = {{agency_id:UInt16}} AND {_WINDOW} AND u.stop_sequence != {TRIP_LEVEL_STOP_SEQUENCE}
    GROUP BY date
"""

# Every poll of the live path in the window, one age each: its rows share the
# poll's captured_at and header. A header of 0 is protobuf's absent value.
MESSAGE_AGE_CH_SQL = f"""
    SELECT count() AS live_polls, countIf(has_header) AS polls,
           quantilesExactInclusive({", ".join(map(str, MESSAGE_AGE_QUANTILES))})(age) AS ages
    FROM (
        SELECT u.file_name AS poll, min(ifNull(u.feed_timestamp, 0)) > 0 AS has_header,
               if(has_header, toInt64(toUnixTimestamp(min(u.captured_at))) - toInt64(min(u.feed_timestamp)), NULL)
                   AS age
        FROM updates AS u
        WHERE u.agency_id = {{agency_id:UInt16}} AND {_WINDOW} AND {LIVE_SOURCED}
        GROUP BY u.file_name
    )
"""

# The window's stop-event plan per planned day, by the board's planned-day rule.
STOP_PLAN_SQL = (
    "WITH"
    + planned_days_ctes("ARRAY[%(agency_id)s]::int[]", "%(first)s::date", "%(through)s::date", stop_events=True)
    + " SELECT day, stop_events FROM planned_days"
)

# The board's service figures over the window, coverage measured through its end.
SERVICE_SQL = service_sql(
    "ARRAY[%(agency_id)s]::int[]", "%(first)s::date", "%(through)s::date", "%(through)s::date", "%(through)s::date"
)

FEED_HEALTH_SQL = """
    SELECT COALESCE(SUM(raw_samples), 0), COALESCE(SUM(clamp_count), 0), COALESCE(SUM(total_rows), 0)
    FROM agg_feed_health
    WHERE agency_id = %(agency_id)s AND date BETWEEN %(first)s AND %(through)s
"""


def last_closed_day() -> date:
    """The last service day that has closed (pipeline.service_day): the newest
    day `updates` holds in production, and the end of the month in progress."""
    return service_date_of(datetime.now(timezone.utc)) - timedelta(days=1)


def month_of(day: date) -> date:
    return day.replace(day=1)


def month_end(month: date) -> date:
    return (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def through_of(month: date, last_closed: date) -> date:
    """The last service date *month* counts: its last day, or *last_closed*,
    the last closed service day, for the month in progress."""
    return min(month_end(month), last_closed)


def _months_between(first: date, last: date) -> list[date]:
    months, month = [], month_of(first)
    while month <= last:
        months.append(month)
        month = month_end(month) + timedelta(days=1)
    return months


def plan_months(
    scope: list[date] | None, first_day: date | None, last_closed: date, stored: Mapping[date, date]
) -> tuple[list[date] | None, list[date]]:
    """``(purge, build)``: the months whose rows this run deletes (None for
    every month) and those it builds.

    The agency's months run from that of its first observed day, *first_day*,
    to that of *last_closed*. *scope* is the service dates whose rows changed
    (None for all of them), and *stored* each built month's ``through_date``.
    Besides the months holding a changed date, a month is built when it has no
    row yet or its row stops short of its window, which grows as days close,
    with or without new observations: a feed that stops reporting still has
    its planned days counted against it. A stored month outside the agency's
    months goes.
    """
    months = [] if first_day is None or first_day > last_closed else _months_between(first_day, last_closed)
    if scope is None:
        return None, months
    wanted = {month_of(day) for day in scope}
    wanted |= {m for m in months if m not in stored or stored[m] < through_of(m, last_closed)}
    wanted |= {m for m in stored if m not in months}
    return sorted(wanted), [m for m in months if m in wanted]


def _share_pct(count: int, total: int) -> float:
    return round(count / total * 100, 1)


def _completeness_missing(matchable: bool, planned_any: bool, stop_events_known: bool) -> Missing:
    """Why a completeness figure is None: unmatched trips first, then no
    trip planned, then, for stop events, no known stop plan on the days that
    plan trips."""
    if not matchable:
        return "coverage_unknown"
    if not planned_any:
        return "no_plan"
    return "no_plan" if stop_events_known else "no_stop_plan"


def shape(
    *,
    agency_id: int,
    month: date,
    through: date,
    ingest_strategy: str | None,
    service: Mapping[str, Any],
    stop_plan: Mapping[date, int | None],
    day_rows: Mapping[date, Mapping[str, int]],
    live_polls: int,
    polls: int,
    ages: tuple[float, float] | None,
    raw_samples: int,
    clamp_count: int,
    total_rows: int,
) -> dict[str, Any]:
    """One ``feed_quality_monthly`` row from what the window's reads returned.

    *service* is the window's :func:`service_sql` row; *stop_plan* each
    planned day's stop events (None where unknown); *day_rows* each service
    date's ``stop_time_updates``, ``<field>_rows`` and ``stop_events`` counts;
    *live_polls* of which *polls* carried a header, whose age quantiles are
    *ages*; and agg_feed_health's sums.
    """
    coverage = coverage_of(service, (through - month).days + 1)
    matchable = trips_matchable(ingest_strategy)
    observed = coverage["observed_trips"] if matchable else None
    planned = coverage["coverage_planned_trips"] if matchable else None
    rt_pct = coverage_pct(observed, planned)
    planned_any = coverage["observed_trips"] is not None

    known = {day: n for day, n in stop_plan.items() if n is not None}
    events_planned = sum(known.values()) if matchable and known else None
    events_observed = (
        sum(day_rows[day]["stop_events"] for day in known if day in day_rows) if events_planned is not None else None
    )
    stop_pct = coverage_pct(events_observed, events_planned)
    # A day that plans trips but whose stop events are unknown leaves the
    # stop-event figure without a plan; one that plans no trip has none either way.
    stop_events_known = bool(events_planned)

    stop_time_updates = sum(row["stop_time_updates"] for row in day_rows.values())
    stores_fields = ingest_strategy in RT_INGEST_STRATEGIES
    field_missing: Missing | None = None
    if not stores_fields:
        field_missing = "feed_cannot_report"
    elif stop_time_updates == 0:
        field_missing = "no_data"
    fields = {
        f"{field}_pct": None
        if field_missing
        else _share_pct(sum(row[f"{field}_rows"] for row in day_rows.values()), stop_time_updates)
        for field in RT_COVERAGE_FIELDS
    }

    age_missing: Missing | None = None
    if total_rows == 0:
        age_missing = "no_data"
    elif live_polls == 0:
        age_missing = "archive_stamped"
    elif polls == 0 or ages is None:
        age_missing = "no_header"
    clamp = clamp_pct(clamp_count, raw_samples)
    return {
        "agency_id": agency_id,
        "month": month,
        "through_date": through,
        "covered_days": coverage["covered_days"],
        "window_days": coverage["window_days"],
        "observed_trips": observed,
        "planned_trips": planned,
        "rt_completeness_pct": rt_pct,
        "rt_completeness_missing": None if rt_pct is not None else _completeness_missing(matchable, planned_any, True),
        "observed_stop_events": events_observed,
        "planned_stop_events": events_planned,
        "stop_plan_days": len(known),
        "stop_completeness_pct": stop_pct,
        "stop_completeness_missing": None
        if stop_pct is not None
        else _completeness_missing(matchable, planned_any, stop_events_known),
        "message_age_polls": polls,
        "message_age_p50_sec": ages[0] if ages and not age_missing else None,
        "message_age_p95_sec": ages[1] if ages and not age_missing else None,
        "message_age_missing": age_missing,
        "raw_samples": raw_samples,
        "clamp_count": clamp_count,
        "clamp_pct": clamp,
        "clamp_missing": None if clamp is not None else "no_data",
        "stop_time_updates": stop_time_updates,
        **fields,
        "field_coverage_missing": field_missing,
        "vehicle_positions_available": None,
        "vehicle_positions_missing": "not_recorded",
    }


def _window_params(agency_id: int, month: date, through: date) -> dict[str, Any]:
    return {
        "agency_id": agency_id,
        "first": month,
        "through": through,
        "window_start": service_day_start_utc(month),
        "window_end": service_day_start_utc(through + timedelta(days=1)),
    }


def build_month(
    agency_id: int, month: date, through: date, ingest_strategy: str | None, conn, ch_client
) -> dict[str, Any]:
    """Read one month's window from both stores and shape its row."""
    params = _window_params(agency_id, month, through)
    with conn.cursor() as cur:
        cur.execute(SERVICE_SQL, params)
        names = [column.name for column in cur.description]
        service = dict(zip(names, cur.fetchone(), strict=True))
        cur.execute(STOP_PLAN_SQL, params)
        stop_plan = dict(cur.fetchall())
        cur.execute(FEED_HEALTH_SQL, params)
        raw_samples, clamp_count, total_rows = (int(v) for v in cur.fetchone())
    rows = ch_client.query(ROWS_CH_SQL, parameters=params)
    day_rows = {row[0]: dict(zip(rows.column_names[1:], row[1:], strict=True)) for row in rows.result_rows}
    live_polls, polls, ages = ch_client.query(MESSAGE_AGE_CH_SQL, parameters=params).result_rows[0]
    return shape(
        agency_id=agency_id,
        month=month,
        through=through,
        ingest_strategy=ingest_strategy,
        service=service,
        stop_plan=stop_plan,
        day_rows=day_rows,
        live_polls=int(live_polls),
        polls=int(polls),
        ages=(round(float(ages[0]), 1), round(float(ages[1]), 1)) if polls else None,
        raw_samples=raw_samples,
        clamp_count=clamp_count,
        total_rows=total_rows,
    )


def rebuild(
    agency_id: int, ingest_strategy: str | None, scope: list[date] | None, last_closed: date, conn, ch_client
) -> bool:
    """Replace the agency's rows of the months :func:`plan_months` names, in
    the caller's transaction, and return whether any row was replaced. Reads
    agg_feed_health and agg_route_daily as this run left them, so it runs
    after both."""
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(date) FROM agg_feed_health WHERE agency_id = %s", (agency_id,))
        first_day = cur.fetchone()[0]
        cur.execute(f"SELECT month, through_date FROM {TABLE} WHERE agency_id = %s", (agency_id,))
        stored = dict(cur.fetchall())
    purge, build = plan_months(scope, first_day, last_closed, stored)
    rows = [
        build_month(agency_id, month, through_of(month, last_closed), ingest_strategy, conn, ch_client)
        for month in build
    ]
    with conn.cursor() as cur:
        if purge is None:
            cur.execute(f"DELETE FROM {TABLE} WHERE agency_id = %s", (agency_id,))
        elif purge:
            cur.execute(f"DELETE FROM {TABLE} WHERE agency_id = %s AND month = ANY(%s)", (agency_id, purge))
        for row in rows:
            cur.execute(
                f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))})",
                [row[column] for column in COLUMNS],
            )
    logger.info(f"  {TABLE}: {len(rows)} month(s)")
    return purge is None or bool(purge)


def render() -> list[str]:
    """What decides this table's rows, for its registry fingerprint: the
    reads, the shaping, and the gate rules and strategy sets they apply."""
    return [
        SERVICE_SQL,
        STOP_PLAN_SQL,
        FEED_HEALTH_SQL,
        ROWS_CH_SQL,
        MESSAGE_AGE_CH_SQL,
        inspect.getsource(shape),
        inspect.getsource(_share_pct),
        inspect.getsource(_completeness_missing),
        inspect.getsource(through_of),
        inspect.getsource(month_end),
        inspect.getsource(comparability.coverage_pct),
        inspect.getsource(comparability.clamp_pct),
        inspect.getsource(coverage_of),
        f"COMPARABLE_INGEST_STRATEGIES={sorted(comparability.COMPARABLE_INGEST_STRATEGIES)!r}",
        f"RT_INGEST_STRATEGIES={sorted(RT_INGEST_STRATEGIES)!r}",
    ]
