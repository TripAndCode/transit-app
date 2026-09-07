"""Service-delivered rate: executed trips ÷ planned trips from the static
GTFS schedule.

``planned_trips`` is the number of (service day, trip_id) instances the
static schedule says should run over the range — from Postgres
``static_calendar_dates`` (``exception_type = 1`` rows only; this codebase's
GTFS feeds enumerate running days via ``calendar_dates.txt`` explicitly,
never a weekly ``calendar.txt``) joined to ``static_trips``.

``executed_trips`` is ``planned_trips`` minus the trip-days GTFS-RT reports
as not executed — identified via ``schedule_relationship_trip = CANCELED``
and any populated ``schedule_relationship_stop = SKIPPED`` (see
``api.clickhouse.service_delivered_probe_by_agency``, the only place these
columns live).

Both ``executed_trips`` and ``service_delivered_pct`` are ``None`` ("not
available") rather than a misleadingly perfect 100% whenever the ratio isn't
computable: the agency's RT feed doesn't populate ``schedule_relationship_trip``
at all in this range (item 89 only wires this up per confirmed-sending feed),
or there is no static schedule to plan against (``planned_trips == 0``).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from api.clickhouse import service_delivered_probe_by_agency

_PLANNED_TRIPS_SQL = """
    SELECT cd.agency_id, COUNT(*) AS planned
    FROM static_calendar_dates cd
    JOIN static_trips t ON t.agency_id = cd.agency_id AND t.service_id = cd.service_id
    WHERE cd.exception_type = 1
      AND cd.date BETWEEN $1 AND $2
    GROUP BY cd.agency_id
"""


async def compute_service_delivered_by_agency(
    conn, ch, agency_ids: list[int], from_date: date, to_date: date, log: logging.Logger
) -> dict[int, dict[str, Any]]:
    """Per-agency ``{"planned_trips", "executed_trips", "service_delivered_pct"}``
    over ``[from_date, to_date]``.

    ``planned_trips`` is always a real (possibly zero) integer count.
    ``executed_trips``/``service_delivered_pct`` are ``None`` together
    whenever the ratio isn't computable for this agency/range (see module
    docstring) — never silently 0 canceled / 100% delivered.
    """
    if not agency_ids:
        return {}
    # static_calendar_dates.date is GTFS's raw calendar_dates.txt text, always
    # YYYYMMDD (8 zero-padded digits) — lexicographic BETWEEN on that fixed
    # width agrees with calendar ordering, so no cast to a Postgres DATE is
    # needed (nor possible: some rows may have non-parseable garbage from a
    # malformed feed, which a DATE cast would fail hard on for every agency).
    from_str, to_str = from_date.strftime("%Y%m%d"), to_date.strftime("%Y%m%d")
    planned_rows = await conn.fetch(_PLANNED_TRIPS_SQL, from_str, to_str)
    planned = {r["agency_id"]: int(r["planned"]) for r in planned_rows}

    probed = await service_delivered_probe_by_agency(ch, agency_ids, from_date, to_date, log)

    result: dict[int, dict[str, Any]] = {}
    for aid in agency_ids:
        p = planned.get(aid, 0)
        populated, non_executed = probed.get(aid, (False, 0))
        if not populated or p <= 0:
            result[aid] = {"planned_trips": p, "executed_trips": None, "service_delivered_pct": None}
            continue
        # Clamp at 0: a canceled/skipped trip-day whose trip_id doesn't
        # actually match anything in the static schedule (feed drift, or a
        # RT-only ADDED trip marked CANCELED) must not drive the ratio
        # negative.
        executed = max(p - non_executed, 0)
        result[aid] = {
            "planned_trips": p,
            "executed_trips": executed,
            "service_delivered_pct": round(executed / p * 100, 1),
        }
    return result
