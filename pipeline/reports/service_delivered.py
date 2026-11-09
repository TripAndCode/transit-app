"""Service-delivered rate: executed trips ÷ planned trips from the static
GTFS schedule.

``planned_trips`` is the number of (service day, trip_id) instances the
schedule says should run over the range, counted per day by
:mod:`pipeline.planned_trips`: a day's count saved in ``planned_trips_daily``
while its timetable was loaded, or else the count the loaded timetable plans
for it.

``executed_trips`` is ``planned_trips`` minus the trip-days GTFS-RT reports
as not executed — a trip whose latest ``schedule_relationship_trip`` is
CANCELED, or every stop of which it reports with a plausible delay or a
CANCELED or SKIPPED status has SKIPPED as its latest such
``schedule_relationship_stop`` (a stop reported only without either counts
neither way, and a trip that skipped only some stops ran), precomputed per
day into Postgres ``agg_service_delivered_daily`` by
``pipeline.analyze.analyze()`` (see ``pipeline.agg_sql.service_delivered_ch_sql``
for the ClickHouse query this reads from, which lives against raw `updates`,
not this module).

Both ``executed_trips`` and ``service_delivered_pct`` are ``None`` ("not
available") rather than a misleadingly perfect 100% whenever the ratio isn't
computable: only an agency with a live RT field-coverage verdict (see
``pipeline.strategies.static_join.rt_field_coverage_confirmed_agencies``)
has been confirmed to actually populate ``schedule_relationship_trip`` on
its live feed (``aomori_regex`` always leaves it NULL, and an
``ingest_strategy == 'static_join'`` agency with no live verdict is
untrusted until probed -- sharing the JOIN mechanism doesn't imply sharing
field coverage, see that module's docstring), or there is no static schedule
to plan against (``planned_trips == 0``).

A populated field is not yet a used one. A feed that sends every trip
SCHEDULED takes nothing off the plan, so its ratio reads 100% whatever ran.
The rate therefore also needs the feed to have reported at least one trip
not run (a trip whose last status is CANCELED, or every stop of which it
reports with a delay or a status SKIPPED) in the :data:`CANCELLATION_LOOKBACK_DAYS` days up to the range's
last day, or anywhere in a longer range. The evidence is a row in
``agg_service_delivered_daily``, the same trip-days the ratio subtracts, so
the check is an index probe in Postgres rather than a scan of ``updates``,
whose status columns are outside its sort key and would need every raw row
of the look-back read. The rate returns by itself once a feed reports one.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pipeline.planned_trips import plan_ctes
from pipeline.strategies.static_join import RT_INGEST_STRATEGIES, rt_field_coverage_confirmed_agencies

#: Why an agency's rate is ``None``. ``"unreadable"`` marks a read that failed
#: outright, so a caller never shows its 0 planned trips as a real plan.
DeliveredMissing = Literal[
    "no_plan", "feed_cannot_report", "feed_unverified", "feed_reports_no_cancellations", "unreadable"
]

#: How far back from a range's last day a reported cancellation shows that a
#: feed reports them at all.
CANCELLATION_LOOKBACK_DAYS = 90

# A day has a plan when planned_trips_daily saved one for it or the loaded
# timetable covers it, and the saved count wins. A capture records what the
# timetable loaded at the time planned, and a reload saves only the days before
# the incoming timetable starts, so a saved day keeps the plan a later reload
# would replace or drop. Only those days count, on both sides of the ratio:
# a reload replaces the agency's calendar, so a window can reach back to days
# no plan covers, and missed trips count only on the days the plan counted.
# agg_service_delivered_daily is built from GTFS-RT alone, so a saved day's
# missed trips are there whatever timetable is loaded now. It has a row only
# for a day with a missed trip: a planned day without one reads 0 missed, and
# an agency with no row at all in the look-back ($4 to $3) reported no
# cancellation in it, so reports_cancellations is false. Observed trips count
# over the same planned days, up to $5, as do the planned trips coverage is
# measured against, so the two make a ratio; a covered day that plans nothing
# still counts what the feed reported then.
_SERVICE_SQL = (
    "WITH"
    + plan_ctes("$1::int[]", "$2::date", "$3::date")
    + """,
    saved AS (
        SELECT agency_id, service_date AS day, planned_trips AS planned
        FROM planned_trips_daily
        WHERE agency_id = ANY($1::int[]) AND service_date BETWEEN $2::date AND $3::date
    ),
    planned_days AS (
        SELECT agency_id, day, planned FROM saved
        UNION ALL
        SELECT tp.agency_id, tp.day, tp.planned
        FROM timetable_plan tp
        WHERE NOT EXISTS (SELECT 1 FROM saved s WHERE s.agency_id = tp.agency_id AND s.day = tp.day)
    ),
    plan AS (
        SELECT agency_id, SUM(planned) AS planned, COUNT(*) AS covered_days,
               SUM(planned) FILTER (WHERE day <= $5::date) AS coverage_planned
        FROM planned_days
        GROUP BY agency_id
    ),
    missed AS (
        SELECT s.agency_id, SUM(s.non_executed_trips) AS non_executed
        FROM agg_service_delivered_daily s
        JOIN planned_days p ON p.agency_id = s.agency_id AND p.day = s.date
        GROUP BY s.agency_id
    ),
    observed AS (
        SELECT r.agency_id, SUM(r.trips_observed) AS observed
        FROM agg_route_daily r
        JOIN planned_days p ON p.agency_id = r.agency_id AND p.day = r.date
        WHERE r.agency_id = ANY($1::int[]) AND r.date BETWEEN $2::date AND $5::date
        GROUP BY r.agency_id
    )
    SELECT a.agency_id,
           COALESCE(p.planned, 0) AS planned,
           COALESCE(p.covered_days, 0) AS covered_days,
           COALESCE(m.non_executed, 0) AS non_executed,
           COALESCE(o.observed, 0) AS observed,
           COALESCE(p.coverage_planned, 0) AS coverage_planned,
           g.ingest_strategy,
           EXISTS (
               SELECT 1 FROM agg_service_delivered_daily r
               WHERE r.agency_id = a.agency_id
                 AND r.date BETWEEN $4::date AND $3::date
                 AND r.non_executed_trips > 0
           ) AS reports_cancellations
    FROM unnest($1::int[]) AS a(agency_id)
    LEFT JOIN plan p USING (agency_id)
    LEFT JOIN missed m USING (agency_id)
    LEFT JOIN observed o USING (agency_id)
    LEFT JOIN agencies g USING (agency_id)
"""
)


def unreadable_service_delivered(from_date: date, to_date: date) -> dict[str, Any]:
    """The figures :func:`compute_service_delivered_by_agency` gives an agency
    when its read fails outright: no plan counted, no rate, no observed
    count, and ``delivered_missing="unreadable"``, so a caller never shows
    the 0 as a real plan of nothing."""
    return {
        "planned_trips": 0,
        "executed_trips": None,
        "service_delivered_pct": None,
        "covered_days": 0,
        "window_days": (to_date - from_date).days + 1,
        "delivered_missing": "unreadable",
        "observed_trips": None,
        "coverage_planned_trips": None,
    }


async def compute_service_delivered_by_agency(
    conn, agency_ids: list[int], from_date: date, to_date: date, *, observed_through: date | None = None
) -> dict[int, dict[str, Any]]:
    """Per-agency ``{"planned_trips", "executed_trips", "service_delivered_pct",
    "covered_days", "window_days", "delivered_missing", "observed_trips",
    "coverage_planned_trips"}`` over ``[from_date, to_date]``.

    ``covered_days`` counts the window's days with a plan, saved in
    ``planned_trips_daily`` or covered by the loaded timetable; both sides of
    the ratio count only those days.

    ``observed_trips`` sums ``agg_route_daily.trips_observed`` over those same
    days, the trips the feed reported, and ``coverage_planned_trips`` the
    trips planned on them: the two sides of a coverage ratio. Both stop at
    ``observed_through`` (default ``to_date``), so a caller comparing
    agencies can end them at the newest day any of them has observations
    for: a day no agency's aggregates have reached yet is a lag they all
    share, not trips one feed missed. ``observed_trips`` is ``None`` when
    nothing is planned through then, since there is no plan to measure it
    against. Whether those trip_ids are the timetable's is the caller's
    question (see :mod:`pipeline.reports.comparability`).

    ``planned_trips`` is always a real (possibly zero) integer count.
    ``executed_trips``/``service_delivered_pct`` are ``None`` together
    whenever the ratio isn't computable for this agency/range (see module
    docstring) — never silently 0 canceled / 100% delivered — and
    ``delivered_missing`` then says why (:data:`DeliveredMissing`); it is
    ``None`` beside a rate. One query reads the saved and loaded plans and
    two precomputed aggregates in Postgres, ``agg_service_delivered_daily``,
    which also answers whether the feed reports cancellations at all, and
    ``agg_route_daily`` — no ClickHouse access on this read path.
    """
    if not agency_ids:
        return {}
    window_days = (to_date - from_date).days + 1
    lookback_from = min(from_date, to_date - timedelta(days=CANCELLATION_LOOKBACK_DAYS - 1))
    coverage_to = to_date if observed_through is None else min(observed_through, to_date)
    rows = {
        r["agency_id"]: r
        for r in await conn.fetch(_SERVICE_SQL, agency_ids, from_date, to_date, lookback_from, coverage_to)
    }

    # An agency must satisfy both halves of the shared gate: an ingest
    # strategy that CAN send this field, and a live probe verdict saying its
    # own feed actually DOES. The batch helper resolves both in one query so
    # this read path can't drift from the per-agency callers.
    populated_ids = await rt_field_coverage_confirmed_agencies(conn, agency_ids)

    result: dict[int, dict[str, Any]] = {}
    for aid in agency_ids:
        p = int(rows[aid]["planned"])
        coverage_planned = int(rows[aid]["coverage_planned"])
        coverage = {
            "covered_days": int(rows[aid]["covered_days"]),
            "window_days": window_days,
            "observed_trips": int(rows[aid]["observed"]) if coverage_planned > 0 else None,
            "coverage_planned_trips": coverage_planned,
        }
        missing: DeliveredMissing | None = None
        if p <= 0:
            missing = "no_plan"
        elif rows[aid]["ingest_strategy"] not in RT_INGEST_STRATEGIES:
            missing = "feed_cannot_report"
        elif aid not in populated_ids:
            missing = "feed_unverified"
        elif not rows[aid]["reports_cancellations"]:
            missing = "feed_reports_no_cancellations"
        if missing is not None:
            result[aid] = {
                "planned_trips": p,
                "executed_trips": None,
                "service_delivered_pct": None,
                **coverage,
                "delivered_missing": missing,
            }
            continue
        # Clamp at 0: a canceled/skipped trip-day whose trip_id doesn't
        # actually match anything in the static schedule (feed drift, or a
        # RT-only ADDED trip marked CANCELED) must not drive the ratio
        # negative.
        executed = max(p - int(rows[aid]["non_executed"]), 0)
        result[aid] = {
            "planned_trips": p,
            "executed_trips": executed,
            "service_delivered_pct": round(executed / p * 100, 1),
            **coverage,
            "delivered_missing": None,
        }
    return result
