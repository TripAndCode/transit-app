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
from itertools import pairwise
from typing import Any

from api.clickhouse import max_captured_at
from api.range import RangeCtx, build_updates_filter_ch
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.query.tools import unnamed_stop_name
from pipeline.reports.rankings import MIN_GROUP_SAMPLES
from pipeline.route_catalog import route_code_sql
from pipeline.service_day import service_date_ch


def _half_up(x: float, places: int) -> float:
    """``x`` rounded half away from zero to ``places`` decimals, as Postgres
    ``ROUND`` does, rather than Python's round-half-to-even."""
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


async def route_exists(conn, agency_id: int, route_code: str) -> bool:
    """True if ``route_code`` has ever been analyzed for this agency.

    Checks ``agg_route_daily``, not ``agg_route_stats``: agg_route_stats is
    built with ``WHERE service_type IS NOT NULL`` (pipeline/analyze.py), so
    it's a LOSSY existence oracle — a real, legitimately-observed route with
    an all-NULL service_type is invisible to it even though agg_route_daily
    (no such filter) holds a row for it with no baseline. Checking
    agg_route_daily instead matches the grain of the range-scoped,
    historical route lists this existence check gates (route-shape, route
    trips, stop profile) — today's live route list (today_route_summary)
    reads `updates_live` directly and is not built from this table at all.
    No secondary index on route_code (agg_route_daily's PK leads with
    (agency_id, date)), but the table holds per-agency route×day×service
    rows, not raw `updates`, so this stays cheap regardless of agency size,
    for both a fabricated and a real route_code. Accepted trade-off: a route
    first seen today reads as not found on these drill-downs until its
    first day is promoted and analyzed.
    """
    return (
        await conn.fetchval(
            "SELECT 1 FROM agg_route_daily WHERE agency_id = $1 AND route_code = $2 LIMIT 1",
            agency_id,
            route_code,
        )
    ) is not None


def _route_shape_vote_dedup_sql(ch_where_frag: str) -> str:
    """Per-(trip, day, stop) dedup used to weight `compute_route_shape`'s shape vote.

    Keyed on the JST service day as well, like `pipeline.db.build_dedup_ch_sql`:
    a GTFS trip_id recurs on every service day, so (trip, stop) alone would
    keep one day's observation out of a multi-day range.

    Broken out as a pure builder — like `api.routers.map.build_route_trips_sql`
    — so its shape, including the plausibility clamp, is unit-testable without
    ClickHouse. Clamps `dep_delay` the same way `pipeline.db.build_dedup_ch_sql`
    does (see `MAX_PLAUSIBLE_DELAY_SEC`'s docstring): map and aggregates must
    agree on plausibility, so a frozen feed can't tip the shape vote either.
    """
    return f"""
        SELECT u.trip_id, u.stop_sequence,
            argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay
        FROM updates AS u
        WHERE u.agency_id = {{agency_id:UInt16}} AND u.route_code = {{route:String}}
          AND u.dep_delay IS NOT NULL
          AND u.dep_delay BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}
          AND {ch_where_frag}
        GROUP BY u.trip_id, {service_date_ch("u.captured_at")}, u.stop_sequence
    """


def _route_shape_stats_dedup_sql(ch_where_frag: str, trip_filter_sql: str) -> str:
    """Per-(trip, day, stop) dedup backing `compute_route_shape`'s per-stop delay stats.

    Same key, clamp and pure-builder rationale as `_route_shape_vote_dedup_sql`.
    Each row carries its service day, so one trip's readings on one day can be
    paired stop to stop.
    """
    return f"""
        SELECT u.trip_id, u.stop_sequence,
            argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay,
            {service_date_ch("u.captured_at")} AS service_date
        FROM updates AS u
        WHERE u.agency_id = {{agency_id:UInt16}} AND u.route_code = {{route:String}}
          AND u.dep_delay IS NOT NULL
          AND u.dep_delay BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}
          {trip_filter_sql}
          AND {ch_where_frag}
        GROUP BY u.trip_id, {service_date_ch("u.captured_at")}, u.stop_sequence
        ORDER BY u.trip_id, u.stop_sequence, {service_date_ch("u.captured_at")}
    """


async def _is_route_variant(conn, agency_id: int, route: str, shape_id: str) -> bool:
    """True if a timetable trip of ``route`` runs on ``shape_id``. A variant
    the window never observed is still the route's own, and a comparison
    window pins the one on screen whether it observed it or not."""
    return (
        await conn.fetchval(
            "SELECT 1 FROM static_trips "
            f"WHERE agency_id = $1 AND shape_id = $2 AND {route_code_sql('route_id')} = $3 LIMIT 1",
            agency_id,
            shape_id,
            route,
        )
    ) is not None


async def _variant_summaries(
    conn, agency_id: int, shape_counts: dict[str, int], route_samples: int, locale: str
) -> list[dict[str, Any]]:
    """Each observed variant's end stops, stop count, observations and share
    of the route's observations, most observed first.

    The stops are the ones the variant's stop list shows: per stop_sequence,
    the stop of its first trip by trip_id, as for ``unobserved_stops``.
    ``route_samples`` counts every observation of the route, including trips
    no variant claims, so the shares need not sum to 100.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (t.shape_id, sst.stop_sequence) t.shape_id, sst.stop_sequence, ss.stop_name
        FROM static_trips t
        JOIN static_stop_times sst ON sst.agency_id = t.agency_id AND sst.trip_id = t.trip_id
        LEFT JOIN static_stops ss ON ss.agency_id = sst.agency_id AND ss.stop_id = sst.stop_id
        WHERE t.agency_id = $1 AND t.shape_id = ANY($2)
        ORDER BY t.shape_id, sst.stop_sequence, t.trip_id
        """,
        agency_id,
        list(shape_counts),
    )
    stops: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        name = r["stop_name"] if r["stop_name"] is not None else unnamed_stop_name(r["stop_sequence"], locale)
        stops[r["shape_id"]].append((r["stop_sequence"], name))
    ordered = sorted(stops, key=lambda sid: (-shape_counts[sid], sid))
    return [
        {
            "shape_id": sid,
            "first_stop_name": stops[sid][0][1],
            "last_stop_name": stops[sid][-1][1],
            "stop_count": len(stops[sid]),
            "samples": shape_counts[sid],
            "share_pct": _half_up(100 * shape_counts[sid] / route_samples, 1),
        }
        for sid in ordered
    ]


def _segment_increases(dedup_rows, sequences: list[int], shown: set[int]) -> list[dict[str, Any]]:
    """How much later, on average, a trip leaves each stop than the stop
    before it, over the trip-days seen at both.

    Pairing one trip's own readings, rather than subtracting two stops'
    means, keeps a trip seen at only one of the stops out of the increase.
    ``sequences`` is the variant's whole stop list in order. The segment
    leaving its first stop is not measured: most feeds report every
    departure from a trip's first stop as exactly on time, before they track
    the vehicle, so the step from there is the feed starting to report, not
    delay growing. A segment needs ``MIN_GROUP_SAMPLES`` paired trip-days,
    and both its stops in ``shown``, the stops the response lists.
    """
    runs: dict[tuple, dict[int, int]] = defaultdict(dict)
    for trip_id, stop_sequence, dep_delay, service_date in dedup_rows:
        runs[(trip_id, service_date)][stop_sequence] = dep_delay
    segments = []
    for start, end in pairwise(sequences[1:]):
        if start not in shown or end not in shown:
            continue
        gains = [run[end] - run[start] for run in runs.values() if start in run and end in run]
        if len(gains) >= MIN_GROUP_SAMPLES:
            segments.append(
                {
                    "from_sequence": start,
                    "to_sequence": end,
                    "increase_min": _half_up(sum(gains) / len(gains) / 60.0, 2),
                    "pairs": len(gains),
                }
            )
    return segments


async def compute_route_shape(
    conn, ch, agency_id: int, route: str, ctx: RangeCtx, *, locale: str, shape: str | None = None
) -> dict[str, Any]:
    """Ordered stop sequence + per-stop avg delay for one route over ``ctx``.

    Sorted by ``stop_sequence`` so the caller can draw a polyline straight
    from the result. Stops without coordinates are dropped, so the polyline
    never includes (NaN, NaN). A stop that stops.txt gives no name is named
    in ``locale``.

    The stops are one variant's: ``shape`` when it is one of the route's,
    else the most observed. ``variants`` lists every variant the window
    observed, with its share of the route's observations, and is empty when
    the window observed none. ``segments`` is the variant's delay increase
    from stop to stop (see :func:`_segment_increases`).
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
        return {
            "route": route,
            "shape_id": None,
            "variants": [],
            "segments": [],
            "geometry": None,
            "stops": [],
            "unobserved_stops": [],
        }

    # Most-frequent shape_id for this route, bridged via updates.trip_id
    # (route_code is regex-extracted and is not guaranteed equal to GTFS
    # route_id across feeds, so joining on trip_id keeps geometry tied
    # to trips actually observed for this route_code). The chosen shape
    # also pins the stops query below so the polyline and circles share
    # one variant — without this pin, a multi-shape route (e.g. a Hiroshima
    # express bus with several variants) shows stops off the line.
    #
    # `updates` lives in ClickHouse. The shape-vote runs FIRST — WITHOUT
    # any shape filter, since chosen_shape_id isn't known yet — as a per-trip
    # roll-up of the same dedup subquery the per-stop stats query (below,
    # once a shape is chosen) also uses. "Which trips appear, and how many
    # deduped stop-events each contributes" is a valid (arguably better)
    # proxy for shape-vote weight than the raw per-trip observation count.
    #
    # Trade-off (deliberate): the dedup subquery is filtered by `dep_delay IS NOT
    # NULL`, so a trip whose every observed StopTimeUpdate is arrival-only (no
    # `dep_delay` — common at a route's last stop in GTFS-RT) contributes
    # ZERO weight to the vote here, where a raw per-trip `COUNT(*)` would count
    # it in full. We accept this: it reuses the dedup scan's own established
    # definition of "counted observation" (see `pipeline/db.py::build_dedup_ch_sql`
    # for the same filter-before-dedup ordering elsewhere in this codebase)
    # rather than inventing a second, looser one just for the vote. A route
    # where every trip on one shape variant happens to be arrival-only
    # everywhere could in principle tip the vote toward a less-observed
    # variant — accepted as a corner case, not chased further;
    # tests/api/test_api_map.py::test_route_shape_shape_vote_ignores_null_delay_only_trips
    # asserts the vote stays sensible (lands on the shape
    # with real weighted support) when some trips are entirely NULL-delay.
    #
    # Bounded by the same `ctx`-derived filter (date range / DOW / time_band
    # / service) honored by every other analytical endpoint: the shape should
    # reflect what's actually being shown for the user's selected range, not
    # all-time history, and an unbounded scan of the route's ENTIRE history
    # would pay for a full-table scan to return only a small number of rows.
    ch_where_frag, ch_params = build_updates_filter_ch(ctx)
    # argMax-based dedup (see pipeline/db.py::build_dedup_ch_sql's docstring) —
    # only one non-key column (dep_delay) is read off the winning row, so a
    # single argMax suffices; base-table columns are qualified with the `u.`
    # alias per that same docstring's convention, in case ch_where_frag (built
    # by api.range.build_updates_filter_ch) ever references an output alias.
    # The per-stop stats query below (once a shape is chosen) keeps a full
    # `ORDER BY` over its dedup key (as route_trips' equivalent dedup query
    # does) for the same reason that query needs it: `lon`/`lat` are
    # float means accumulated by summing `dedup_rows` in whatever order they
    # arrive, and a bare GROUP BY has no defined output order — without a
    # fixed order, floating-point summation is order-dependent and could
    # produce a last-bit-different average across otherwise-identical
    # requests. This vote query has no such float accumulation, so it needs
    # no ORDER BY.
    # Bounded by trip count, not trip x stop count: the vote only needs
    # "how many deduped stop-events did each trip contribute", so the
    # per-(trip, day, stop) dedup runs as a subquery and only its per-trip
    # roll-up crosses into Python. Transferring every
    # (trip_id, stop_sequence, dep_delay) row for the whole ctx window instead
    # would, for a busy route over a wide window, exceed the async client's
    # result_overflow_mode="throw" cap (api/clickhouse.py) and 500 the
    # endpoint instead of degrading.
    dedup_cte_sql = _route_shape_vote_dedup_sql(ch_where_frag)
    vote_result = await ch.query(
        f"WITH dedup AS ({dedup_cte_sql}) SELECT trip_id, count() AS n FROM dedup GROUP BY trip_id",
        parameters={"agency_id": agency_id, "route": str(route), **ch_params},
    )
    trip_counts: dict[str, int] = dict(vote_result.result_rows)
    window_samples = sum(trip_counts.values())

    # If the ctx window has zero observations for this route (e.g. it only
    # runs on days outside the selected range, or a time_band excludes every
    # one of its trips), `trip_counts` above comes back empty —
    # there's no shape-vote signal to derive from it, but the map should
    # still be able to render the route's topology (geometry +
    # unobserved-stop markers), just with no delay data on it. Run ONE
    # fallback shape-vote query
    # (bounded to the last 30 days off the agency's own latest data — see
    # below), solely to pick a shape for rendering purposes — this only
    # fires on the empty-window edge case (not the common case), and its own
    # bound keeps it from becoming the unbounded full-history scan the ctx
    # bound above exists to avoid. The per-stop delay stats
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
            # polyline/unobserved_stops between identical requests. Movers
            # ranking in overview.py breaks ties the same way.
            chosen_shape_id = max(shape_counts, key=lambda sid: (shape_counts[sid], sid))
    if (
        shape is not None
        and shape != chosen_shape_id
        and (shape in shape_counts or await _is_route_variant(conn, agency_id, str(route), shape))
    ):
        chosen_shape_id = shape
    variants = (
        await _variant_summaries(conn, agency_id, shape_counts, window_samples, locale)
        if window_samples and shape_counts
        else []
    )

    # Render every observed shape variant (系統), not just the majority one
    # `chosen_shape_id` above still pins for stops/unobserved_stops below —
    # this repo's Japanese bus operators distinguish 路線 (route_code, the
    # named line) from 系統 (a route's distinct stopping/geometry patterns,
    # e.g. a Hiroshima express bus's several variants), and collapsing to one
    # shape here would hide the others' road
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
    # multi-variant route (e.g. a Hiroshima express bus with several
    # variants) doesn't pay for other variants' rows.
    #
    # Runs UNCONDITIONALLY, not just when chosen_shape_id is not None: an
    # agency with no shapes.txt at all has shape_id NULL for every trip, so
    # chosen_shape_id is always None there — gating this query on it would
    # silently drop `stops` to `[]` for every route on such an agency.
    # shape_id only PINS the stops list to one variant; it never gates
    # whether the query runs. The trip filter is the only conditional part.
    #
    # This is a second ClickHouse scan (the vote query above returns only
    # per-trip counts, not per-stop rows to filter in Python), but it's the one that must
    # stay bounded, so the extra round trip is the right trade. In the
    # empty-ctx-window fallback case above, `{ch_where_frag}` still encodes
    # the real (empty) ctx window, so this query naturally comes back empty —
    # `stops` stays empty while `geometry`/`unobserved_stops` still render
    # from the fallback shape.
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
        _route_shape_stats_dedup_sql(ch_where_frag, trip_filter_sql),
        parameters=stats_params,
    )
    dedup_rows = list(stats_result.result_rows)

    static_join_rows: list = []
    if dedup_rows:
        dedup_trip_ids = list({row[0] for row in dedup_rows})
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
    for trip_id, stop_sequence, dep_delay, _service_date in dedup_rows:
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
                "stop_name": max(a["stop_names"]) if a["stop_names"] else unnamed_stop_name(stop_sequence, locale),
                "stop_id": max(a["stop_ids"]) if a["stop_ids"] else None,
                "stop_code": max(a["stop_codes"]) if a["stop_codes"] else None,
                "platform_code": max(a["platform_codes"]) if a["platform_codes"] else None,
                # Minutes to 2 dp, half-up: rankings.py's avg_min display contract.
                "avg_min": _half_up(sum(a["delays"]) / len(a["delays"]) / 60.0, 2) if a["delays"] else None,
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
    variant_seqs: set[int] = set()
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
                "stop_name": (
                    r["stop_name"] if r["stop_name"] is not None else unnamed_stop_name(r["stop_sequence"], locale)
                ),
                "stop_id": r["stop_id"],
                "stop_code": r["stop_code"],
                "platform_code": r["platform_code"],
                "lon": float(r["lon"]) if r["lon"] is not None else None,
                "lat": float(r["lat"]) if r["lat"] is not None else None,
            }
            for r in unobs_rows
            if r["stop_sequence"] not in observed_seqs and r["lon"] is not None and r["lat"] is not None
        ]
        variant_seqs = {r["stop_sequence"] for r in unobs_rows}

    # The stops the response lists: observed ones with coordinates, and the
    # unobserved ones of the variant.
    shown = {seq for seq, a in per_seq.items() if a["lons"] and a["lats"]}
    shown |= {u["stop_sequence"] for u in unobserved}
    segments = _segment_increases(dedup_rows, sorted(variant_seqs | set(per_seq)), shown)

    return {
        "route": route,
        "shape_id": chosen_shape_id,
        "variants": variants,
        "segments": segments,
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
