"""Read side for the dwell-time / running-time decomposition report.

Reads `agg_route_daily_dwell_run` (built by `pipeline.analyze.analyze()`,
only for agencies in
`pipeline.strategies.static_join.RT_FIELD_COVERAGE_CONFIRMED_AGENCIES` --
see that module's docstring for why `ingest_strategy` alone isn't sufficient
trust: sharing the static_join JOIN mechanism doesn't imply a feed actually
populates `arr_delay`, only that its wire shape matches an agency that's
been confirmed to. See that builder and `pipeline.dwell_run`'s module
docstring for why `arr_delay` matters: only a confirmed agency's RT feed is
known to ever send `StopTimeUpdate.arrival`, so `arr_delay` -- and therefore
an actual arrival timestamp -- exists to derive dwell time (this stop's
departure minus its own arrival) and running time (this stop's arrival minus
the PREVIOUS stop's departure) from.

Unlike ranking/on_time/worst_5min, this report has no live ClickHouse
fallback for a time_band-filtered query: reconstructing dwell/running time
needs a same-trip previous-stop window function joined against the static
schedule, which the existing raw-scan fallbacks (keyed on `route_code,
service_type` alone, no per-trip ordering) aren't shaped for. A time_band-
filtered request instead gets an explicit `time_band_supported: false`
rather than silently ignoring the filter and showing an unfiltered number
under a filtered label.
"""

from __future__ import annotations

from api.range import RangeCtx
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.dwell_run import percentile_from_dwell_hist, percentile_from_run_hist
from pipeline.reports.filters import _dist_filter
from pipeline.strategies.static_join import RT_FIELD_COVERAGE_CONFIRMED_AGENCIES

# Ingest strategies that CAN ever populate `arr_delay` (see
# pipeline/strategies/static_join.py's parse_feed docstring); mirrors
# pipeline.reports.service_delivered's identical `_POPULATED_AGENCIES_SQL`
# real-strategy check for the same underlying reason (both need
# schedule_relationship_*/arr_delay, RT fields only static_join's
# Hiroshima-style feeds send). This alone is NOT sufficient trust -- see
# RT_FIELD_COVERAGE_CONFIRMED_AGENCIES below, which _agency_available also
# requires.
_AVAILABLE_STRATEGIES = frozenset({"static_join"})


async def _agency_available(agency_id: int, conn) -> bool:
    """True only when this agency both uses an ingest strategy that can send
    `arr_delay` AND is in the explicit confirmed-set gate
    (`pipeline.strategies.static_join.RT_FIELD_COVERAGE_CONFIRMED_AGENCIES`)
    -- an `ingest_strategy` match alone means a feed's wire shape merely
    matches a confirmed agency's, not that this agency's own live feed has
    been probed and found to actually populate the field."""
    if agency_id not in RT_FIELD_COVERAGE_CONFIRMED_AGENCIES:
        return False
    row = await conn.fetchrow("SELECT ingest_strategy FROM agencies WHERE agency_id = $1", agency_id)
    return bool(row and row["ingest_strategy"] in _AVAILABLE_STRATEGIES)


@perf.timed("reports.dwell_run")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_dwell_run_decomposition(agency_id: int, ctx: RangeCtx, conn) -> dict:
    """Per-route dwell/running-time distribution summary over ``ctx``'s range.

    Returns ``{"available": bool, "time_band_supported": bool, "routes": [...]}``.

    ``available=False`` means this agency's ingest strategy never populates
    `arr_delay` -- the caller must render an explicit "not available" state,
    never a zero/blank decomposition. ``time_band_supported=False`` (only
    possible when ``available`` is True) means the current time-band filter
    can't be served by this report yet (see module docstring) -- again, a
    caller must render an explicit state, not silently ignore the filter.

    Each ``routes`` entry is
    ``{"route_code", "service_type", "dwell_samples", "dwell_avg_sec",
    "dwell_p50_sec", "dwell_p90_sec", "run_samples", "run_avg_sec",
    "run_p50_sec", "run_p90_sec"}``. No minimum-sample gate is applied here
    (unlike ranking/on_time's ``> 20``/``> 10`` gates) -- `arr_delay`'s own
    sparse coverage already means legitimate samples counts are frequently
    thin, so every route is returned with its own transparent sample count
    for the caller to weigh, rather than being hidden below a threshold.
    """
    if not await _agency_available(agency_id, conn):
        return {"available": False, "time_band_supported": ctx.time_band == "all", "routes": []}
    if ctx.time_band != "all":
        return {"available": True, "time_band_supported": False, "routes": []}

    where, params, _ = _dist_filter(ctx, next_param=2)
    sql = (
        "WITH ranged AS (\n"
        "    SELECT route_code, service_type, dwell_samples, dwell_sum_sec, hist_dwell,\n"
        "           run_samples, run_sum_sec, hist_run\n"
        "    FROM agg_route_daily_dwell_run\n"
        f"    WHERE agency_id = $1 AND {where}\n"
        "),\n"
        "merged_dwell AS (\n"
        "    SELECT route_code, service_type, i, SUM(h) AS c\n"
        "    FROM ranged, unnest(hist_dwell) WITH ORDINALITY u(h, i)\n"
        "    GROUP BY route_code, service_type, i\n"
        "),\n"
        "merged_run AS (\n"
        "    SELECT route_code, service_type, i, SUM(h) AS c\n"
        "    FROM ranged, unnest(hist_run) WITH ORDINALITY u(h, i)\n"
        "    GROUP BY route_code, service_type, i\n"
        "),\n"
        "dwell_hists AS (\n"
        "    SELECT route_code, service_type, array_agg(c ORDER BY i) AS hist_dwell\n"
        "    FROM merged_dwell GROUP BY route_code, service_type\n"
        "),\n"
        "run_hists AS (\n"
        "    SELECT route_code, service_type, array_agg(c ORDER BY i) AS hist_run\n"
        "    FROM merged_run GROUP BY route_code, service_type\n"
        "),\n"
        "scalars AS (\n"
        "    SELECT route_code, service_type,\n"
        "           SUM(dwell_samples) AS dwell_samples, SUM(dwell_sum_sec) AS dwell_sum_sec,\n"
        "           SUM(run_samples) AS run_samples, SUM(run_sum_sec) AS run_sum_sec\n"
        "    FROM ranged GROUP BY route_code, service_type\n"
        ")\n"
        "SELECT s.route_code, s.service_type,\n"
        "       s.dwell_samples, s.dwell_sum_sec, dh.hist_dwell,\n"
        "       s.run_samples, s.run_sum_sec, rh.hist_run\n"
        "FROM scalars s\n"
        "JOIN dwell_hists dh USING (route_code, service_type)\n"
        "JOIN run_hists rh USING (route_code, service_type)\n"
        "ORDER BY route_code"
    )
    rows = await conn.fetch(sql, agency_id, *params)

    routes = []
    for r in rows:
        dwell_samples = r["dwell_samples"] or 0
        run_samples = r["run_samples"] or 0
        routes.append(
            {
                "route_code": r["route_code"],
                "service_type": r["service_type"] or None,
                "dwell_samples": dwell_samples,
                # Postgres SUM(bigint) returns NUMERIC, which asyncpg maps to
                # Decimal -- float() here matches rankings.py's identical
                # Decimal-from-SUM() -> float cast (e.g. its own
                # `float(_round2(sum_sec / n / 60.0))`), since the default
                # JSON encoding of a bare Decimal renders it as a string
                # ("45"), not a number.
                "dwell_avg_sec": float(r["dwell_sum_sec"] / dwell_samples) if dwell_samples else None,
                "dwell_p50_sec": percentile_from_dwell_hist(r["hist_dwell"], 0.5),
                "dwell_p90_sec": percentile_from_dwell_hist(r["hist_dwell"], 0.9),
                "run_samples": run_samples,
                "run_avg_sec": float(r["run_sum_sec"] / run_samples) if run_samples else None,
                "run_p50_sec": percentile_from_run_hist(r["hist_run"], 0.5),
                "run_p90_sec": percentile_from_run_hist(r["hist_run"], 0.9),
            }
        )
    return {"available": True, "time_band_supported": True, "routes": routes}
