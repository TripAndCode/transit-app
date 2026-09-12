"""Per-route stop sequence, geometry and per-stop delay for the Map tab.

Read-only. The route overlay needs three things that only make sense
together: the ordered stops of one shape variant, the road geometry of that
same variant, and each stop's average delay over the range. Choosing the
shape is what ties them — stops and polyline must come from one variant or
the circles sit off the line.
"""

import json
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from api.clickhouse import max_captured_at
from api.range import RangeCtx, build_updates_filter_ch


async def route_exists(conn, agency_id: int, route_code: str) -> bool:
    """True if ``route_code`` has ever been analyzed for this agency.

    Checks ``agg_route_daily``, not ``agg_route_stats``: agg_route_stats is
    built with ``WHERE service_type IS NOT NULL`` (pipeline/analyze.py), so
    it's a LOSSY existence oracle — a real, legitimately-observed route with
    an all-NULL service_type is invisible to it even though
    today_route_summary's route list (built from agg_route_daily, no such
    filter) would show it with bucket="no_baseline". Checking agg_route_daily
    instead matches the grain of the table that actually populates the route
    list users click through from. No secondary index on route_code
    (agg_route_daily's PK leads with (agency_id, date)), but the table holds
    per-agency route×day×service rows, not raw `updates`, so this stays cheap
    regardless of agency size, for both a fabricated and a real route_code.
    Accepted trade-off: a brand-new route that's been ingested
    but not yet analyzed (no agg_route_daily row yet) reads as "not found"
    (or renders with no shape, for route_shape) for one cron cycle.
    """
    return (
        await conn.fetchval(
            "SELECT 1 FROM agg_route_daily WHERE agency_id = $1 AND route_code = $2 LIMIT 1",
            agency_id,
            route_code,
        )
    ) is not None


async def compute_route_shape(conn, ch, agency_id: int, route: str, ctx: RangeCtx) -> dict[str, Any]:
    """Ordered stop sequence + per-stop avg delay for one route over ``ctx``.

    Sorted by ``stop_sequence`` so the caller can draw a polyline straight
    from the result. Stops without coordinates are dropped, so the polyline
    never includes (NaN, NaN).
    """
    # Cheap existence precheck FIRST, before touching ClickHouse at all -- ahead
    # of the ctx-bounded dedup query below, not just inside the empty-window
    # fallback branch further down. A fabricated route_code must cost ~0
    # ClickHouse work: with the precheck misplaced inside the fallback branch,
    # a fabricated route_code under a wide ctx window still pays the full
    # ctx-bounded dedup query's cost, because that query runs to completion
    # first regardless (it's bounded, but still real, unnecessary work for a
    # route that doesn't exist at all). See `route_exists` for why this
    # checks agg_route_daily rather than agg_route_stats.
    if not await route_exists(conn, agency_id, str(route)):
        return {"route": route, "geometry": None, "stops": [], "unobserved_stops": []}

    # Most-frequent shape_id for this route, bridged via updates.trip_id
    # (route_code is regex-extracted and is not guaranteed equal to GTFS
    # route_id across feeds, so joining on trip_id keeps geometry tied
    # to trips actually observed for this route_code). The chosen shape
    # also pins the stops query below so the polyline and circles share
    # one variant — without this pin, multi-shape routes (e.g. Hiroshima
    # express bus with several variants) showed stops off the line.
    #
    # `updates` now lives in ClickHouse. The shape-vote runs FIRST — WITHOUT
    # any shape filter, since chosen_shape_id isn't known yet — as a per-trip
    # roll-up of the same dedup subquery the per-stop stats query (below,
    # once a shape is chosen) also uses. "Which trips appear, and how many
    # deduped stop-events each contributes" is a valid (arguably better)
    # proxy for shape-vote weight than the raw per-trip observation count.
    #
    # Trade-off (deliberate, not proven bit-for-bit equivalent to the old
    # two-query version): the dedup subquery is filtered by `dep_delay IS NOT
    # NULL`, so a trip whose every observed StopTimeUpdate is arrival-only (no
    # `dep_delay` — common at a route's last stop in GTFS-RT) contributes
    # ZERO weight to the vote here, where the old raw `COUNT(*)` query counted
    # it in full. We accept this: it reuses the dedup scan's own established
    # definition of "counted observation" (see `pipeline/db.py::build_dedup_ch_sql`
    # for the same filter-before-dedup ordering elsewhere in this codebase)
    # rather than inventing a second, looser one just for the vote. A route
    # where every trip on one shape variant happens to be arrival-only
    # everywhere could in principle tip the vote toward a less-observed
    # variant — accepted as a corner case, not chased further; see the
    # regression test asserting the vote stays sensible (lands on the shape
    # with real weighted support) when some trips are entirely NULL-delay.
    #
    # Bounded by the same `ctx`-derived filter (date range / DOW / time_band
    # / service) honored by every other analytical endpoint — an earlier
    # version scanned the route's ENTIRE history here with no date bound,
    # paying for a full-table scan to return only a small number of rows,
    # even though the shape should reflect what's actually being shown for the
    # user's selected range, not all-time history.
    ch_where_frag, ch_params = build_updates_filter_ch(ctx)
    # argMax-based dedup (see pipeline/db.py::build_dedup_ch_sql's docstring) —
    # only one non-key column (dep_delay) is read off the winning row, so a
    # single argMax suffices; base-table columns are qualified with the `u.`
    # alias per that same docstring's convention, in case ch_where_frag (built
    # by api.range.build_updates_filter_ch) ever references an output alias.
    # The per-stop stats query below (once a shape is chosen) keeps
    # `ORDER BY u.trip_id, u.stop_sequence` (matching route_trips' equivalent
    # dedup query) for the same reason that query needs it: `lon`/`lat` are
    # float means accumulated by summing `dedup_rows` in whatever order they
    # arrive, and a bare GROUP BY has no defined output order — without a
    # fixed order, floating-point summation is order-dependent and could
    # produce a last-bit-different average across otherwise-identical
    # requests. This vote query has no such float accumulation, so it needs
    # no ORDER BY.
    # Bounded by trip count, not trip x stop count: the vote only needs
    # "how many deduped stop-events did each trip contribute", so the
    # per-(trip_id, stop_sequence) dedup runs as a subquery and only its
    # per-trip roll-up crosses into Python. A prior version transferred every
    # (trip_id, stop_sequence, dep_delay) row for the whole ctx window here —
    # for a busy route over a wide window that's easily >200k rows, past the
    # async client's result_overflow_mode="throw" cap (api/clickhouse.py),
    # 500ing the endpoint instead of degrading.
    dedup_cte_sql = f"""
        SELECT u.trip_id, u.stop_sequence,
            argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay
        FROM updates AS u
        WHERE u.agency_id = {{agency_id:UInt16}} AND u.route_code = {{route:String}}
          AND u.dep_delay IS NOT NULL
          AND {ch_where_frag}
        GROUP BY u.trip_id, u.stop_sequence
    """
    vote_result = await ch.query(
        f"WITH dedup AS ({dedup_cte_sql}) SELECT trip_id, count() AS n FROM dedup GROUP BY trip_id",
        parameters={"agency_id": agency_id, "route": str(route), **ch_params},
    )
    trip_counts: dict[str, int] = dict(vote_result.result_rows)

    # If the ctx window has zero observations for this route (e.g. it only
    # runs on days outside the selected range, or a time_band excludes every
    # one of its trips), `dedup_rows`/`trip_counts` above come back empty —
    # there's no shape-vote signal to derive from them, but the map should
    # still be able to render the route's topology (geometry +
    # unobserved-stop markers), just with no delay data on it, matching
    # pre-ClickHouse-migration behavior. Run ONE fallback shape-vote query
    # (bounded to the last 30 days off the agency's own latest data — see
    # below), solely to pick a shape for rendering purposes — this only
    # fires on the empty-window edge case (not the common case), so even
    # its now-bounded form doesn't reintroduce the unbounded-full-history-scan
    # problem the ctx bound above exists to fix. The per-stop delay stats
    # (`avg_min`/`samples`) stay empty regardless, since there really are
    # zero observations in the user's selected window.
    if not trip_counts:
        # Existence is already confirmed by the precheck at the top of this
        # function, so this bound only needs to cap the cost for a route
        # that's real but has nothing in the ctx window: 30 days off the
        # agency's own latest captured_at (not wall-clock "now") so it's
        # meaningful against old/replayed data too, matching the uniform
        # bound used by route_trips/route_stop_profile in api.routers.map.
        agency_latest = await max_captured_at(ch, agency_id)
        if agency_latest is not None:
            fallback_bound = agency_latest - timedelta(days=30)
            fallback_vote_result = await ch.query(
                "SELECT trip_id, count() AS n FROM updates "
                "WHERE agency_id = {agency_id:UInt16} AND route_code = {route:String} "
                "  AND captured_at >= {bound:DateTime64} "
                "GROUP BY trip_id",
                parameters={"agency_id": agency_id, "route": str(route), "bound": fallback_bound},
            )
            trip_counts = {tid: n for tid, n in fallback_vote_result.result_rows}

    chosen_shape_id = None
    shape_counts: dict[str, int] = defaultdict(int)
    if trip_counts:
        shape_link_rows = await conn.fetch(
            "SELECT trip_id, shape_id FROM static_trips "
            "WHERE agency_id = $1 AND trip_id = ANY($2) "
            "  AND shape_id IS NOT NULL AND shape_id <> ''",
            agency_id,
            list(trip_counts.keys()),
        )
        for r in shape_link_rows:
            shape_counts[r["shape_id"]] += trip_counts.get(r["trip_id"], 0)
        if shape_counts:
            # Explicit tie-break key: `shape_counts` is a `defaultdict`
            # populated from an unordered Postgres query, so two shape
            # variants tied on vote count would otherwise pick whichever
            # happened to be inserted first that run — non-deterministic
            # polyline/unobserved_stops between identical requests. Same bug
            # class already fixed for movers ranking in overview.py.
            chosen_shape_id = max(shape_counts, key=lambda sid: (shape_counts[sid], sid))

    # Render every observed shape variant (系統), not just the majority one
    # `chosen_shape_id` above still pins for stops/unobserved_stops below —
    # this repo's Japanese bus operators distinguish 路線 (route_code, the
    # named line) from 系統 (a route's distinct stopping/geometry patterns,
    # e.g. the Hiroshima express-bus variants the shape-vote comment above
    # discusses), and collapsing to one shape here hid the others' road
    # geometry entirely rather than just under-detailing their stop stats.
    # `shape_counts` (built above from real observed trips, not static-only
    # noise) is the natural variant set: every shape with at least one
    # observed trip in the ctx window.
    geometry = None
    if shape_counts:
        variant_geom_rows = await conn.fetch(
            "SELECT shape_id, ST_AsGeoJSON(geom) AS geom_json FROM static_shapes "
            "WHERE agency_id = $1 AND shape_id = ANY($2)",
            agency_id,
            list(shape_counts.keys()),
        )
        # Order matches chosen_shape_id's own tie-break (highest vote weight
        # first, ties broken by shape_id) so the MultiLineString's sub-line
        # order — and which line is index 0 — is deterministic across
        # requests, not whatever order this unordered Postgres query returns.
        geom_by_shape_id = {r["shape_id"]: r["geom_json"] for r in variant_geom_rows if r["geom_json"] is not None}
        ordered_shape_ids = sorted(geom_by_shape_id, key=lambda sid: (shape_counts[sid], sid), reverse=True)
        lines = [json.loads(geom_by_shape_id[sid])["coordinates"] for sid in ordered_shape_ids]
        if len(lines) == 1:
            geometry = {"type": "LineString", "coordinates": lines[0]}
        elif len(lines) > 1:
            geometry = {"type": "MultiLineString", "coordinates": lines}

    # Fetch the per-stop delay stats, scoped to the chosen shape's trips WHEN
    # one was chosen — bounded by (trips on one shape variant x its stops),
    # not by the whole route's trip count over the ctx window, so a
    # multi-variant route (e.g. the Hiroshima express case the shape-vote
    # comment above discusses) doesn't pay for other variants' rows.
    #
    # Runs UNCONDITIONALLY, not just when chosen_shape_id is not None: an
    # agency with no shapes.txt at all has shape_id NULL for every trip, so
    # chosen_shape_id is always None there — gating this query on it (an
    # earlier version of this fix did) silently dropped `stops` to `[]` for
    # every route on such an agency, a real regression from `main`'s
    # always-run-the-stats-query behavior (main only used shape_id to PIN an
    # otherwise-always-populated stops list to one variant, never to gate
    # whether it ran at all). The trip filter is the only conditional part.
    #
    # This is a second ClickHouse scan (the vote query above no longer
    # returns per-stop rows to filter in Python), but it's the one that must
    # stay bounded, so the extra round trip is the right trade. In the
    # empty-ctx-window fallback case above, `{ch_where_frag}` still encodes
    # the real (empty) ctx window, so this query naturally comes back empty —
    # `stops` stays empty while `geometry`/`unobserved_stops` still render
    # from the fallback shape, matching pre-fix behavior.
    shape_trip_ids: list[str] = []
    if chosen_shape_id is not None:
        shape_trip_rows = await conn.fetch(
            "SELECT trip_id FROM static_trips WHERE agency_id = $1 AND shape_id = $2",
            agency_id,
            chosen_shape_id,
        )
        shape_trip_ids = [r["trip_id"] for r in shape_trip_rows]
    trip_filter_sql = "AND u.trip_id IN {shape_trip_ids:Array(String)}" if chosen_shape_id is not None else ""
    stats_params = {"agency_id": agency_id, "route": str(route), **ch_params}
    if chosen_shape_id is not None:
        stats_params["shape_trip_ids"] = shape_trip_ids
    stats_result = await ch.query(
        f"""
        SELECT u.trip_id, u.stop_sequence,
            argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay
        FROM updates AS u
        WHERE u.agency_id = {{agency_id:UInt16}} AND u.route_code = {{route:String}}
          AND u.dep_delay IS NOT NULL
          {trip_filter_sql}
          AND {ch_where_frag}
        GROUP BY u.trip_id, u.stop_sequence
        ORDER BY u.trip_id, u.stop_sequence
        """,
        parameters=stats_params,
    )
    dedup_rows = list(stats_result.result_rows)

    static_join_rows: list = []
    if dedup_rows:
        dedup_trip_ids = list({tid for tid, _, _ in dedup_rows})
        static_join_rows = await conn.fetch(
            "SELECT sst.trip_id, sst.stop_sequence, ss.stop_id, ss.stop_name, "
            "       ss.stop_code, ss.platform_code, ST_X(ss.geom) AS lon, ST_Y(ss.geom) AS lat "
            "FROM static_stop_times sst "
            "LEFT JOIN static_stops ss ON sst.stop_id = ss.stop_id AND ss.agency_id = $1 "
            "WHERE sst.agency_id = $1 AND sst.trip_id = ANY($2)",
            agency_id,
            dedup_trip_ids,
        )
    static_by_pair = {(r["trip_id"], r["stop_sequence"]): r for r in static_join_rows}

    # Local, not `api.routers.map._round_half_up_int`: this endpoint's
    # `avg_min` is minutes to 2 decimal places (matches rankings.py's avg_min
    # display contract), not whole seconds, so it needs its own half-up
    # rounding at a different quantize scale.
    def _round2(x) -> float:
        return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _new_seq_agg() -> dict:
        return {
            "delays": [],
            "stop_names": [],
            "stop_ids": [],
            "stop_codes": [],
            "platform_codes": [],
            "lons": [],
            "lats": [],
        }

    per_seq: dict[int, dict] = defaultdict(_new_seq_agg)
    for trip_id, stop_sequence, dep_delay in dedup_rows:
        a = per_seq[stop_sequence]
        a["delays"].append(dep_delay)
        info = static_by_pair.get((trip_id, stop_sequence))
        if info is not None:
            if info["stop_name"] is not None:
                a["stop_names"].append(info["stop_name"])
            if info["stop_id"] is not None:
                a["stop_ids"].append(info["stop_id"])
            if info["stop_code"] is not None:
                a["stop_codes"].append(info["stop_code"])
            if info["platform_code"] is not None:
                a["platform_codes"].append(info["platform_code"])
            if info["lon"] is not None:
                a["lons"].append(float(info["lon"]))
            if info["lat"] is not None:
                a["lats"].append(float(info["lat"]))

    rows = []
    for stop_sequence in sorted(per_seq):
        a = per_seq[stop_sequence]
        rows.append(
            {
                "stop_sequence": stop_sequence,
                "stop_name": max(a["stop_names"]) if a["stop_names"] else f"{stop_sequence}番停留所",
                "stop_id": max(a["stop_ids"]) if a["stop_ids"] else None,
                "stop_code": max(a["stop_codes"]) if a["stop_codes"] else None,
                "platform_code": max(a["platform_codes"]) if a["platform_codes"] else None,
                "avg_min": _round2(sum(a["delays"]) / len(a["delays"]) / 60.0) if a["delays"] else None,
                "samples": len(a["delays"]),
                "lon": sum(a["lons"]) / len(a["lons"]) if a["lons"] else None,
                "lat": sum(a["lats"]) / len(a["lats"]) if a["lats"] else None,
            }
        )
    observed_seqs = {r["stop_sequence"] for r in rows}

    # Unobserved stops on the chosen shape: every (stop_sequence, stop)
    # tuple from static_stop_times for trips on the chosen shape, minus
    # the sequences already returned with delay data. Hollow markers in
    # the frontend so the user can see the full route topology and the
    # observation gap (typical for Hiroshima-style incremental feeds
    # where early-trip sequences are rarely caught by 30s polling).
    unobserved = []
    if chosen_shape_id is not None:
        unobs_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (sst.stop_sequence)
                sst.stop_sequence,
                ss.stop_name,
                ss.stop_id,
                ss.stop_code,
                ss.platform_code,
                ST_X(ss.geom) AS lon,
                ST_Y(ss.geom) AS lat
            FROM static_trips t
            JOIN static_stop_times sst
              ON sst.agency_id = t.agency_id AND sst.trip_id = t.trip_id
            JOIN static_stops ss
              ON ss.agency_id = sst.agency_id AND ss.stop_id = sst.stop_id
            WHERE t.agency_id = $1 AND t.shape_id = $2
            ORDER BY sst.stop_sequence, sst.trip_id
            """,
            agency_id,
            chosen_shape_id,
        )
        unobserved = [
            {
                "stop_sequence": r["stop_sequence"],
                "stop_name": r["stop_name"],
                "stop_id": r["stop_id"],
                "stop_code": r["stop_code"],
                "platform_code": r["platform_code"],
                "lon": float(r["lon"]) if r["lon"] is not None else None,
                "lat": float(r["lat"]) if r["lat"] is not None else None,
            }
            for r in unobs_rows
            if r["stop_sequence"] not in observed_seqs and r["lon"] is not None and r["lat"] is not None
        ]

    return {
        "route": route,
        "geometry": geometry,
        "stops": [
            {
                "stop_sequence": r["stop_sequence"],
                "stop_name": r["stop_name"],
                "stop_id": r["stop_id"],
                "stop_code": r["stop_code"],
                "platform_code": r["platform_code"],
                "lon": float(r["lon"]) if r["lon"] is not None else None,
                "lat": float(r["lat"]) if r["lat"] is not None else None,
                "avg_min": float(r["avg_min"]) if r["avg_min"] is not None else None,
                "samples": r["samples"],
            }
            for r in rows
            if r["lon"] is not None and r["lat"] is not None
        ],
        "unobserved_stops": unobserved,
    }
