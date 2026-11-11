"""Each agency's feed-quality scorecard per calendar month of service date,
built by analyze into ``feed_quality_monthly``.

A month's window runs from its first day through its last, or for the month
in progress through the last closed service day, and never past the newest
day any agency the board lists has aggregates for. Its figures are counted as
the agencies board counts a range, so a month's completeness and clamp share
are what the board and the definition stamp state over it:

- the two completeness figures, measured against the plan, count its planned
  days only through ``through_date``: the newest day of the window that any
  listed agency has observations for, where the board's coverage stops
  (:func:`pipeline.reports.network.coverage_through`). A later day is a lag or
  an outage every feed shares, not trips one feed missed. A window no listed
  agency observed counts them through its end, as the board's coverage then
  runs to the end of its range;
- every other figure, and ``covered_days`` of ``window_days``, counts the
  whole window, as the board's clamp share counts its whole range.

The figures:

- **RT completeness**: the trips the feed reported of those the timetable
  planned, by the comparability gate's own rules and SQL
  (:func:`pipeline.reports.comparability.matched_trips` and
  :func:`~pipeline.reports.comparability.coverage_pct` over
  :func:`pipeline.reports.service_delivered.service_sql`).
- **Stop-event completeness**: the stop events the feed reported (one per
  trip, stop and service date, however often reported) of those the planned
  trips' stop times plan, over the planned days whose stop events are known.
  A day saved before the capture kept them is left out (``stop_plan_days``
  counts the rest). A stop event is reported when any poll of it carries a
  plausible departure or arrival delay or withdraws it (its trip CANCELED or
  its stop SKIPPED, :func:`pipeline.db.withdrawn_ch`). This is reporting
  coverage, not the service-delivered rate: a withdrawal is something the feed
  said about the stop, so it counts here, where the departure figures let the
  feed's last report decide whether the stop departed.
- **Message age**: per poll, the instant the poll was captured minus its
  FeedHeader timestamp, summarized by :data:`MESSAGE_AGE_QUANTILES`. A poll
  from the live path was captured at its captured_at. Archive ingest stamps
  an archived poll's captured_at from that same header, or from its name read
  as JST when the header is far from it
  (pipeline.strategies._pb.archive_captured_at), so its capture instant is
  read from its file name instead (:data:`ARCHIVE_POLL_INSTANT_CH`), whichever
  way it was stamped. An archived poll whose name gives no instant is left out
  and counted (``message_age_undated_polls``). A header frozen for hours reads
  as hours of age on every poll that received it. The name is taken as the
  fetch begins, so a message generated during a slow fetch reads a small
  negative age, which stands as measured.
- **Clamp share**: the gate's :func:`pipeline.reports.comparability.clamp_pct`
  over agg_feed_health's counts.
- **Field coverage**: per field of
  :data:`pipeline.strategies.static_join.RT_COVERAGE_FIELDS`, the share of
  stored stop_time_updates carrying it, the unit the RT coverage probe
  measures a single poll in (``static_join.field_coverage``). The rows ingest
  writes from the timetable for a trip cancelled without listing a stop are
  no stop_time_update the feed sent, though each still reports its stop
  event. They are told apart by their shape alone
  (:func:`pipeline.db.timetable_cancellation_ch`), so a stop update the feed
  does send for a cancelled trip with neither a delay nor a stop status is
  left out with them.
- **VehiclePositions available**: not recorded anywhere the app reads.

A figure the feed or the app cannot supply is ``None`` beside a
:data:`Missing` reason, never 0:

- ``coverage_unknown``: the ingest strategy's trip_ids are not the
  timetable's, so its trips cannot be counted against the plan (the gate's
  reason);
- ``no_plan_observed``: trips are planned in the window, but none through
  ``through_date`` (the board's reason);
- ``no_plan``: no trip is planned in the window;
- ``no_stop_plan``: trips are planned, but no planned day's stop events are
  known;
- ``no_data``: nothing was observed in the window. For either completeness
  figure, the agency has no analyzed observation in it, the gate's first
  check, which comes before every other reason; for the clamp share, no
  observation carried a delay;
- ``archive_stamped``: no poll's capture instant is known: every poll came
  through archive ingest, under a file name that gives none;
- ``no_header``: no poll whose capture instant is known carried a FeedHeader
  timestamp;
- ``feed_cannot_report``: the ingest strategy never stores the fields;
- ``not_recorded``: the app records no source for the figure.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pipeline.clickhouse import LIVE_SOURCED, TRIP_LEVEL_STOP_SEQUENCE
from pipeline.db import plausible_delay_ch, timetable_cancellation_ch, withdrawn_ch
from pipeline.reports import comparability
from pipeline.reports.comparability import clamp_pct, coverage_pct, matched_trips, trips_matchable
from pipeline.reports.network import coverage_through
from pipeline.reports.service_delivered import coverage_of, planned_days_ctes, service_sql
from pipeline.service_day import service_date_ch, service_date_of, service_day_start_utc
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS, RT_INGEST_STRATEGIES

logger = logging.getLogger(__name__)

TABLE = "feed_quality_monthly"

#: Why a figure is ``None``; the module docstring defines each.
Missing = Literal[
    "coverage_unknown",
    "no_plan_observed",
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
    "message_age_undated_polls",
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
# day after its last, a range on the stored instant so the (agency_id,
# captured_at) sort key prunes the scan to the window.
_WINDOW = "u.captured_at >= {window_start:DateTime64} AND u.captured_at < {window_end:DateTime64}"

# Per service date: the stop_time_updates stored, how many carry each RT
# coverage field, and the stop events reported. A cancellation listing no stop
# leaves a trip-level row or a row per stop written from the timetable, and
# neither is a stop_time_update; a row per stop still reports its stop event.
_SENT = f"NOT {timetable_cancellation_ch()}"
_FIELD_COUNTS = ", ".join(f"countIf(u.{field} IS NOT NULL AND {_SENT}) AS {field}_rows" for field in RT_COVERAGE_FIELDS)
_REPORTED = f"{plausible_delay_ch('u.dep_delay')} OR {plausible_delay_ch('u.arr_delay')} OR {withdrawn_ch()}"
ROWS_CH_SQL = f"""
    SELECT {service_date_ch("u.captured_at")} AS date, countIf({_SENT}) AS stop_time_updates, {_FIELD_COUNTS},
           uniqExactIf((u.trip_id, u.stop_sequence), {_REPORTED}) AS stop_events
    FROM updates AS u
    WHERE u.agency_id = {{agency_id:UInt16}} AND {_WINDOW} AND u.stop_sequence != {TRIP_LEVEL_STOP_SEQUENCE}
    GROUP BY date
"""

#: An archived poll's capture instant, a UTC ``DateTime``, from the name
#: archive ingest stores it under: the UTC day and time of day the collector
#: (oracle_cloud/v3/bin/rt-poller.sh) takes from `date -u` as its fetch begins.
#: NULL unless the whole name has that shape and names a real day and time:
#: nothing establishes another name's zone, so none is guessed.
ARCHIVE_POLL_INSTANT_CH = "parseDateTimeOrNull(file_name, '%Y%m%d/TripUpdate_%H%i%s.pb', 'UTC')"

# Every poll in the window, one age each: its rows share the poll's file
# name, captured_at and header. A live poll was captured at its captured_at,
# an archived one at the instant its name gives. A header of 0 is protobuf's
# absent value.
MESSAGE_AGE_CH_SQL = f"""
    SELECT countIf(instant IS NOT NULL) AS dated_polls, countIf(instant IS NULL) AS undated_polls,
           countIf(age IS NOT NULL) AS polls,
           quantilesExactInclusive({", ".join(map(str, MESSAGE_AGE_QUANTILES))})(age) AS ages
    FROM (
        SELECT if({LIVE_SOURCED}, toInt64(toUnixTimestamp(min(u.captured_at))),
                  toInt64(toUnixTimestamp({ARCHIVE_POLL_INSTANT_CH}))) AS instant,
               min(ifNull(u.feed_timestamp, 0)) AS header,
               if(header > 0 AND instant IS NOT NULL, instant - toInt64(header), NULL) AS age
        FROM updates AS u
        WHERE u.agency_id = {{agency_id:UInt16}} AND {_WINDOW}
        GROUP BY u.file_name
    )
"""

# The stop-event plan per planned day through the coverage day, by the board's
# planned-day rule.
STOP_PLAN_SQL = (
    "WITH"
    + planned_days_ctes("ARRAY[%(agency_id)s]::int[]", "%(first)s::date", "%(through)s::date", stop_events=True)
    + " SELECT day, stop_events FROM planned_days"
)

# The board's service figures over the window, coverage measured through the
# coverage day, as the board measures its range's.
SERVICE_SQL = service_sql(
    "ARRAY[%(agency_id)s]::int[]", "%(first)s::date", "%(last)s::date", "%(first)s::date", "%(through)s::date"
)

FEED_HEALTH_SQL = """
    SELECT COALESCE(SUM(raw_samples), 0), COALESCE(SUM(clamp_count), 0), COALESCE(SUM(total_rows), 0)
    FROM agg_feed_health
    WHERE agency_id = %(agency_id)s AND date BETWEEN %(first)s AND %(last)s
"""


# The newest service day any agency the board lists has aggregates for, which
# bounds the months an agency's rows cover (last_counted_day). Each agency's is
# one probe of the (agency_id, date) key rather than a scan of every agency's rows.
NEWEST_DAY_SQL = """
    SELECT MAX(newest.date)
    FROM agencies a
    CROSS JOIN LATERAL (
        SELECT r.date FROM agg_route_daily_dist r WHERE r.agency_id = a.agency_id ORDER BY r.date DESC LIMIT 1
    ) newest
    WHERE a.deleted_at IS NULL
"""

# Per month from %(first)s, a month's first day, to %(last)s, the last counted
# day: each listed agency's newest observed day in the month's window, and this
# agency's whether listed or not, as the definition stamp reads a range's
# (pipeline.reports.network._AGENCY_DAYS_SQL). One query, one probe of
# agg_route_daily_dist's (agency_id, date) key per agency and month; a month
# an agency has no observation in yields no row for it. The series is of
# timestamps, as one of dates would be of timestamptz, read in the session's zone.
MONTH_DAYS_SQL = """
    SELECT m.month::date, a.agency_id, a.deleted_at IS NULL AS listed, newest.date
    FROM generate_series(%(first)s::timestamp, %(last)s::timestamp, interval '1 month') AS m(month)
    CROSS JOIN agencies a
    CROSS JOIN LATERAL (
        SELECT r.date FROM agg_route_daily_dist r
        WHERE r.agency_id = a.agency_id
          AND r.date BETWEEN m.month::date AND LEAST((m.month + interval '1 month - 1 day')::date, %(last)s::date)
        ORDER BY r.date DESC LIMIT 1
    ) newest
    WHERE a.deleted_at IS NULL OR a.agency_id = %(agency_id)s
"""


def last_closed_day() -> date:
    """The last service day that has closed (pipeline.service_day): the newest
    day `updates` can hold in production."""
    return service_date_of(datetime.now(timezone.utc)) - timedelta(days=1)


def last_counted_day(last_closed: date, newest: date | None) -> date | None:
    """The last service day any month counts: the last closed one, and no
    later than *newest*, the newest day any listed agency's aggregates reach.
    None when no agency's aggregates reach any day."""
    return None if newest is None else min(last_closed, newest)


def month_of(day: date) -> date:
    return day.replace(day=1)


def month_end(month: date) -> date:
    return (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def through_of(month: date, last_day: date) -> date:
    """The last service date *month*'s window can reach: its last day, or
    *last_day* (:func:`last_counted_day`) for the month in progress."""
    return min(month_end(month), last_day)


def _months_between(first: date, last: date) -> list[date]:
    months, month = [], month_of(first)
    while month <= last:
        months.append(month)
        month = month_end(month) + timedelta(days=1)
    return months


def month_windows(first_day: date | None, last_day: date | None) -> dict[date, date]:
    """Each month the agency's rows cover, from that of its first observed
    day, *first_day*, to that of *last_day*, the last day any month counts
    (:func:`last_counted_day`), and the last day its window can reach
    (:func:`through_of`). None of them without either day, or when the first
    is the later."""
    if first_day is None or last_day is None or first_day > last_day:
        return {}
    return {month: through_of(month, last_day) for month in _months_between(first_day, last_day)}


def month_through(last: date, newest_days: Iterable[date | None]) -> date:
    """The coverage day of a month whose window ends at *last*: the newest of
    *newest_days*, the listed agencies' newest observed days within the
    window, where the board's coverage stops
    (:func:`~pipeline.reports.network.coverage_through`). With none, *last*,
    as the board's coverage then runs to its range's end."""
    return coverage_through(newest_days) or last


def month_bounds(
    agency_id: int, windows: Mapping[date, date], days: Iterable[tuple[date, int, bool, date]]
) -> dict[date, tuple[date, bool]]:
    """``(through, observed)`` per month of *windows* (each month and its
    window's last day): its coverage day (:func:`month_through`), and whether
    *agency_id* has an analyzed observation in its window, the gate's first
    check, from *days*, the rows :data:`MONTH_DAYS_SQL` reads."""
    newest: dict[date, list[date]] = {month: [] for month in windows}
    observed: set[date] = set()
    for month, day_agency, listed, day in days:
        if listed:
            newest[month].append(day)
        if day_agency == agency_id:
            observed.add(month)
    return {month: (month_through(last, newest[month]), month in observed) for month, last in windows.items()}


def read_month_bounds(agency_id: int, windows: Mapping[date, date], cur) -> dict[date, tuple[date, bool]]:
    """:func:`month_bounds` of *windows*, read in one query."""
    if not windows:
        return {}
    cur.execute(MONTH_DAYS_SQL, {"agency_id": agency_id, "first": min(windows), "last": max(windows.values())})
    return month_bounds(agency_id, windows, cur.fetchall())


def plan_months(
    scope: list[date] | None, spans: Mapping[date, tuple[date, date]], stored: Mapping[date, tuple[date, date]]
) -> tuple[list[date] | None, list[date]]:
    """``(purge, build)``: the months whose rows this run deletes (None for
    every month) and those it builds.

    *spans* is each month the agency's rows cover (:func:`month_windows`) and
    its ``(through, last)``: its coverage day (:func:`month_through`) and its
    window's last day. *scope* is the service dates whose rows changed (None
    for all of them), and *stored* each built month's span as its row records
    it (``through_date``, and the last of its ``window_days``). Besides the
    months whose window holds a changed date, a month is built when it has no
    row yet or its row counts another span than it has now: the window and the
    coverage day grow as the listed agencies' aggregates reach later days,
    with or without this feed's own observations, so a feed that stops
    reporting still has its planned days counted against it, and shrink when
    the agency holding the newest day goes. A stored month outside the
    agency's months goes.
    """
    months = sorted(spans)
    if scope is None:
        return None, months
    # A date past its month's window is in no window, so it rebuilds nothing.
    wanted = {month_of(day) for day in scope if month_of(day) in spans and day <= spans[month_of(day)][1]}
    wanted |= {month for month, span in spans.items() if stored.get(month) != span}
    wanted |= {month for month in stored if month not in spans}
    return sorted(wanted), [month for month in months if month in wanted]


def _share_pct(count: int, total: int) -> float:
    return round(count / total * 100, 1)


def _completeness_missing(
    observed: bool, matchable: bool, planned_any: bool, planned_in_window: bool, stop_events_known: bool
) -> Missing:
    """Why a completeness figure is None, in the gate's order
    (:func:`pipeline.reports.comparability.coverage_unknown_why`): no analyzed
    observation first, then unmatched trips, then no trip planned through the
    coverage day, though the window plans some or not, then, for stop events,
    no known stop plan on the days that plan trips."""
    if not observed:
        return "no_data"
    if not matchable:
        return "coverage_unknown"
    if not planned_any:
        return "no_plan_observed" if planned_in_window else "no_plan"
    return "no_plan" if stop_events_known else "no_stop_plan"


def shape(
    *,
    agency_id: int,
    month: date,
    through: date,
    last: date,
    observed: bool,
    ingest_strategy: str | None,
    service: Mapping[str, Any],
    stop_plan: Mapping[date, int | None],
    day_rows: Mapping[date, Mapping[str, int]],
    dated_polls: int,
    undated_polls: int,
    polls: int,
    ages: tuple[float, float] | None,
    raw_samples: int,
    clamp_count: int,
    total_rows: int,
) -> dict[str, Any]:
    """One ``feed_quality_monthly`` row from what the window's reads returned.

    The window runs from *month* to *last*, and the completeness figures
    count its planned days through *through*, its coverage day. *observed* is
    whether the agency has an analyzed observation in the window
    (:func:`month_bounds`), without which neither completeness figure is
    counted; *service* is the window's :func:`service_sql` row; *stop_plan*
    each planned day's stop events through *through* (None where unknown);
    *day_rows* each service date's ``stop_time_updates``, ``<field>_rows`` and
    ``stop_events`` counts; *dated_polls* whose capture instant is known and
    *undated_polls* whose is not, of which *polls* carried a header, whose age
    quantiles are *ages*; and agg_feed_health's sums.
    """
    coverage = coverage_of(service, (last - month).days + 1)
    matchable = trips_matchable(ingest_strategy)
    observed_trips, planned = (None, None)
    if observed:
        observed_trips, planned = matched_trips(
            ingest_strategy, coverage["observed_trips"], coverage["coverage_planned_trips"]
        )
    rt_pct = coverage_pct(observed_trips, planned)
    planned_any = coverage["observed_trips"] is not None
    planned_in_window = int(service["planned"]) > 0

    known = {day: n for day, n in stop_plan.items() if n is not None}
    events_observed, events_planned = (None, None)
    if observed and known:
        events_observed, events_planned = matched_trips(
            ingest_strategy,
            sum(day_rows[day]["stop_events"] for day in known if day in day_rows),
            sum(known.values()),
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
    elif dated_polls == 0:
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
        "observed_trips": observed_trips,
        "planned_trips": planned,
        "rt_completeness_pct": rt_pct,
        "rt_completeness_missing": None
        if rt_pct is not None
        else _completeness_missing(observed, matchable, planned_any, planned_in_window, True),
        "observed_stop_events": events_observed,
        "planned_stop_events": events_planned,
        "stop_plan_days": len(known),
        "stop_completeness_pct": stop_pct,
        "stop_completeness_missing": None
        if stop_pct is not None
        else _completeness_missing(observed, matchable, planned_any, planned_in_window, stop_events_known),
        "message_age_polls": polls,
        "message_age_undated_polls": undated_polls,
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


def _window_params(agency_id: int, month: date, through: date, last: date) -> dict[str, Any]:
    """The window's service dates, from *first* to *last* with its coverage
    day *through*, and its instants for ClickHouse, *window_end* exclusive."""
    return {
        "agency_id": agency_id,
        "first": month,
        "through": through,
        "last": last,
        "window_start": service_day_start_utc(month),
        "window_end": service_day_start_utc(last + timedelta(days=1)),
    }


def build_month(
    agency_id: int,
    month: date,
    through: date,
    last: date,
    observed: bool,
    ingest_strategy: str | None,
    conn,
    ch_client,
) -> dict[str, Any]:
    """Read one month's window from both stores and shape its row."""
    params = _window_params(agency_id, month, through, last)
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
    dated_polls, undated_polls, polls, ages = ch_client.query(MESSAGE_AGE_CH_SQL, parameters=params).result_rows[0]
    return shape(
        agency_id=agency_id,
        month=month,
        through=through,
        last=last,
        observed=observed,
        ingest_strategy=ingest_strategy,
        service=service,
        stop_plan=stop_plan,
        day_rows=day_rows,
        dated_polls=int(dated_polls),
        undated_polls=int(undated_polls),
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
    agg_feed_health, agg_route_daily and agg_route_daily_dist as this run left
    them, so it runs after all three; the other agencies' as their last runs did."""
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(date) FROM agg_feed_health WHERE agency_id = %s", (agency_id,))
        first_day = cur.fetchone()[0]
        cur.execute(NEWEST_DAY_SQL)
        last_day = last_counted_day(last_closed, cur.fetchone()[0])
        windows = month_windows(first_day, last_day)
        bounds = read_month_bounds(agency_id, windows, cur)
        cur.execute(f"SELECT month, through_date, window_days FROM {TABLE} WHERE agency_id = %s", (agency_id,))
        stored = {month: (through, month + timedelta(days=days - 1)) for month, through, days in cur.fetchall()}
    spans = {month: (bounds[month][0], last) for month, last in windows.items()}
    purge, build = plan_months(scope, spans, stored)
    rows = [
        build_month(agency_id, month, *spans[month], bounds[month][1], ingest_strategy, conn, ch_client)
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
        inspect.getsource(build_month),
        inspect.getsource(_window_params),
        inspect.getsource(shape),
        inspect.getsource(_share_pct),
        inspect.getsource(_completeness_missing),
        inspect.getsource(through_of),
        inspect.getsource(last_counted_day),
        NEWEST_DAY_SQL,
        inspect.getsource(month_windows),
        inspect.getsource(month_end),
        MONTH_DAYS_SQL,
        inspect.getsource(read_month_bounds),
        inspect.getsource(month_bounds),
        inspect.getsource(month_through),
        inspect.getsource(coverage_through),
        inspect.getsource(comparability.matched_trips),
        inspect.getsource(comparability.coverage_pct),
        inspect.getsource(comparability.clamp_pct),
        inspect.getsource(coverage_of),
        f"COMPARABLE_INGEST_STRATEGIES={sorted(comparability.COMPARABLE_INGEST_STRATEGIES)!r}",
        f"RT_INGEST_STRATEGIES={sorted(RT_INGEST_STRATEGIES)!r}",
    ]
