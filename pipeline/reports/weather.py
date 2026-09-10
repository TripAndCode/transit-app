"""Rain-vs-dry delay comparison: observed rainfall joined to service days.

Matches each in-range service day's already-aggregated delay
(`agg_route_daily`) against the observed daily rainfall at the agency's one
documented representative station (`agency_weather_stations` ->
`weather_daily_observations`, both populated by `pipeline.weather`), splits the
days into "it rained" and "it didn't", and reports each side's pooled average
delay plus the difference.

What this is and is not:

* It is a **historical observation** comparison. Both inputs describe days that
  have already happened; nothing here is a weather forecast or a delay
  prediction (contrast `pipeline.reports.forecast`, which is a seasonal-naive
  baseline and equally not a prediction).
* It is **not** a causal claim. Rainy days differ from dry days in more than
  rain -- events, roadworks, school terms and seasons all fall unevenly across
  them -- so the difference is an association, and the response's disclaimer
  says so in plain language wherever the figure is shown.
* It is keyed to **one station**, not an area average, because that is the
  bounded thing that can be documented and audited (see the migration's own
  notes on why the mapping is an operator judgement call). An agency with no
  station configured reads "not available", never a guessed nearest station.

Attribution comes from `pipeline.weather.attribution` and must travel with the
figures; the endpoint carries it in every response.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from api.range import RangeCtx, dow_clause
from api.triage import LOW_CONFIDENCE_SAMPLES

# A day counts as rainy at 1 mm or more of observed daily rainfall -- the
# source's own convention for "it rained" when it verifies whether a forecast
# of rain came true. Below that is a trace/drizzle day that neither the source
# nor a passenger would call rainy, and pooling those in would blur exactly the
# contrast this metric exists to show.
WET_DAY_PRECIP_MM = 1.0

# Below this many days on either side, the comparison is flagged low
# confidence: a two-group difference of daily averages built on a handful of
# days is dominated by whatever else was unusual about those particular days.
# The figures are still returned (with the flag) rather than withheld -- the
# same convention `api.triage`'s `low_confidence` uses for thin routes.
MIN_DAYS_PER_GROUP = 5

_STATION_SQL = """
    SELECT station_id, station_name, note
    FROM agency_weather_stations
    WHERE agency_id = $1
"""


def _daily_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for `agg_route_daily.date` -- date range + DOW + service
    + optional route filter.

    `d.date` is a native DATE column, so -- like
    `pipeline.reports.filters._dist_filter` -- the cast stays on the parameter
    side to keep the `(agency_id, date)` PK prefix serving the range scan.

    No `time_band` predicate: this table rolls up to (date, route, service) and
    has no hour-of-day column, and a rainfall figure is itself a daily total,
    so there is nothing narrower to fall back to. A caller with a time_band
    filter set still gets a whole-day answer here.

    `ctx.routes` IS applied: unlike a configured-policy table, this metric is a
    plain read over the same aggregates the surrounding page filters, so
    narrowing to a route must narrow the comparison too.
    """
    parts = [f"d.date >= (${next_param}::text)::date AND d.date <= (${next_param + 1}::text)::date"]
    params: list = [str(ctx.from_date), str(ctx.to_date)]
    n = next_param + 2

    frag, p, n = dow_clause("d.date", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)

    if ctx.service != "all":
        parts.append(f"d.service_type = ${n}")
        params.append(ctx.service)
        n += 1

    if ctx.routes:
        parts.append(f"d.route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1

    return " AND ".join(parts), params, n


def _group(rows_by_wet: Mapping[bool, Mapping[str, Any]], is_wet: bool) -> dict[str, Any]:
    """One side of the comparison, or an all-zero/None side when it has no days."""
    row = rows_by_wet.get(is_wet)
    if row is None:
        return {"days": 0, "samples": 0, "avg_delay_sec": None, "avg_precip_mm": None}

    days = int(row["days"] or 0)
    samples = int(row["samples"] or 0)
    sum_delay_sec = row["sum_delay_sec"]
    avg_precip_mm = row["avg_precip_mm"]
    return {
        "days": days,
        "samples": samples,
        # Pooled over every measurement on those days -- a sum of raw seconds
        # over a sum of samples, never an average of per-day averages, which
        # would weight a quiet day the same as a busy one. The sum is exact
        # wherever `agg_route_daily.sum_delay_sec` is populated; for a route-day
        # predating that column it is reconstructed as `avg_delay_sec *
        # samples`, so such a day is approximate to within the rounding of an
        # integer average. Other reports FILTER those rows out of the pooled
        # average instead; this one keeps them because a wet/dry comparison
        # needs both sides to span the same service days, and dropping the
        # unbackfilled ones can empty one side entirely on an older range.
        "avg_delay_sec": (None if not samples or sum_delay_sec is None else round(float(sum_delay_sec) / samples, 1)),
        # Each matched day contributes its rainfall once, regardless of how
        # many routes ran that day (the SQL de-duplicates to day grain first).
        "avg_precip_mm": None if avg_precip_mm is None else round(float(avg_precip_mm), 1),
    }


def summarize_rain_delay(
    rows: Iterable[Mapping[str, Any]],
    station: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Shape the two-row wet/dry aggregate into the response body. Pure.

    *rows* are the per-bucket aggregates (keys ``is_wet``, ``days``,
    ``samples``, ``sum_delay_sec``, ``avg_precip_mm``); *station* is the
    agency's representative station mapping, or ``None`` if it has none.

    ``available`` is False when there is no station configured, or when no
    in-range service day could be matched to an observation at all -- there is
    nothing to show, and callers render nothing rather than an empty frame. A
    window that DID match days but happens to contain no rainy ones is
    available with ``delta_sec`` of ``None``: "no rainy days in this period" is
    a real answer, not missing data.
    """
    by_wet = {bool(r["is_wet"]): r for r in rows}
    wet = _group(by_wet, True)
    dry = _group(by_wet, False)

    delta_sec: float | None = None
    if wet["avg_delay_sec"] is not None and dry["avg_delay_sec"] is not None:
        delta_sec = round(wet["avg_delay_sec"] - dry["avg_delay_sec"], 1)

    thin = any(g["days"] < MIN_DAYS_PER_GROUP or g["samples"] < LOW_CONFIDENCE_SAMPLES for g in (wet, dry))

    return {
        "available": station is not None and (wet["days"] + dry["days"]) > 0,
        "station": (
            None
            if station is None
            else {
                "station_id": station["station_id"],
                "station_name": station["station_name"],
                "note": station["note"],
            }
        ),
        "wet_day_threshold_mm": WET_DAY_PRECIP_MM,
        "wet": wet,
        "dry": dry,
        "delta_sec": delta_sec,
        "low_confidence": delta_sec is None or thin,
    }


async def compute_rain_delay(agency_id: int, ctx: RangeCtx, conn) -> dict[str, Any]:
    """Pooled average delay on observed-rainy vs non-rainy service days.

    Reads `agg_route_daily` (never raw `updates`) and joins it to the observed
    rainfall for the same civil day -- both are bucketed on the JST calendar
    day, so the join is date-to-date with no timezone translation.

    A service day with no stored observation, or one whose observation has no
    rainfall value, is excluded from BOTH sides rather than assumed dry: a
    missing measurement is not a measurement of no rain.
    """
    station = await conn.fetchrow(_STATION_SQL, agency_id)
    if station is None:
        return summarize_rain_delay([], None)

    frag, params, n = _daily_filter(ctx, 2)
    sql = f"""
        WITH day AS (
            SELECT d.date AS date,
                   w.precip_mm AS precip_mm,
                   SUM(d.samples)::bigint AS samples,
                   SUM(COALESCE(d.sum_delay_sec, d.avg_delay_sec::bigint * d.samples))::bigint
                       AS sum_delay_sec
            FROM agg_route_daily d
            JOIN agency_weather_stations s ON s.agency_id = d.agency_id
            JOIN weather_daily_observations w
              ON w.station_id = s.station_id AND w.obs_date = d.date
            WHERE d.agency_id = $1
              AND w.precip_mm IS NOT NULL
              AND {frag}
            GROUP BY d.date, w.precip_mm
        )
        SELECT (precip_mm >= ${n}) AS is_wet,
               COUNT(*)::bigint AS days,
               SUM(samples)::bigint AS samples,
               SUM(sum_delay_sec)::bigint AS sum_delay_sec,
               AVG(precip_mm) AS avg_precip_mm
        FROM day
        GROUP BY 1
        ORDER BY 1
    """
    rows = await conn.fetch(sql, agency_id, *params, WET_DAY_PRECIP_MM)
    return summarize_rain_delay(rows, station)


# Plain-language, jargon-free (no "pooled"/"association"/"n="), and explicit on
# all three of: these are observations, not a forecast; the comparison is not a
# cause; and what a "measurement" is, so the sample counts aren't ambiguous.
_DISCLAIMER: dict[str, str] = {
    "ja": (
        "実際に観測された日々の降水量（雨が降った日＝日降水量1mm以上）と、その日の遅れの記録を"
        "日付で突き合わせて平均を比べたものです。天気予報や将来の遅れの予測ではありません。"
        "雨が遅れの原因であることを示すものでもありません（行事や工事なども同じ日に重なります）。"
        "遅れの記録1回＝ある日のある停留所での1計測です。"
    ),
    "en": (
        "Compares the average recorded delay on days when rain was actually observed "
        "(1 mm or more for the day) against days when it was not, matched by date. It is "
        "not a weather forecast and not a prediction of future delays, and it does not show "
        "that rain caused the difference -- events, roadworks and the like fall on the same "
        "days. Each delay measurement is one stop, on one run, on one day."
    ),
}


def observation_disclaimer(locale: str) -> str:
    """Localized disclaimer. Unknown locale falls back to ja."""
    return _DISCLAIMER.get(locale, _DISCLAIMER["ja"])
